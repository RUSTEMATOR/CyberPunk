"""Ripper clinics as a service (PLAN 6.1-6.5, 6.1a, 6.7): install, remove, scan, cure, software.

Every action checks first (a refused action changes nothing), spends AP through `services.ap`, moves eddies and
the body through `apply_effects` / `apply_install_cyberware` / `apply_remove_cyberware`, saves and logs. The
followups of the humanity change (a warning, the breakdown) go to `services/followups.py`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.db import repo
from app.engine import derived
from app.engine import ripper as eng
from app.engine.checks import CheckResult
from app.engine.effects import (
    apply_effects,
    apply_install_cyberware,
    apply_remove_cyberware,
)
from app.engine.state import FLAG_INFECTION, KIND_QUICKHACK, InventoryItem
from app.services import ap as ap_services
from app.services import followups, lifecheck
from app.engine.world import WorldFx
from app.services import worldfx
from app.services.game import ActiveCharacter, save_active
from app.services.lifecheck import LifeResult

NO_AP = "no_ap"
CURE = "cure"


async def world_mult(session: AsyncSession, content: Content, tg_id: int | None = None) -> WorldFx:
    """The world's effects at the player's own date: prices by implant category, banned and unlocked implants
    (PLAN 10.6, DESIGN.md 9a)."""
    return await worldfx.load_fx(session, content, tg_id)


def _spend(ch: ActiveCharacter, content: Content, ap: int) -> bool:
    if not ap_services.can_spend(ch, ap):
        return False
    ap_services.spend(ch, content, ap)
    return True


@dataclass
class OpResult:
    """What an operation did, for its screen. `reason` is set when it was refused and nothing changed."""

    ok: bool = False
    reason: str | None = None
    key: str = ""
    name: str = ""
    paid: int = 0
    loss: int = 0  # humanity the character lost (the permanent part for an installation)
    complication: bool = False
    malfunction: bool = False
    infection: bool = False
    replaced: str = ""  # name of the implant that was taken out for the new one
    cascade: list[str] = field(default_factory=list)  # names that went with a removed implant
    returned: int = 0  # humanity that came back
    family: list[str] = field(default_factory=list)
    borg: bool = False  # owner request 25.10: the full conversion is in
    banned_heat: int = 0  # heat after installing a banned implant (0 = none)
    lines: list[str] = field(default_factory=list)  # followup lines
    died: bool = False
    cause: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


# -- installing -----------------------------------------------------------------------------------------------------------------


async def install(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    ripper_key: str, key: str,
) -> OpResult:
    st = ch.state
    res = OpResult(key=key)
    ripper = eng.ripper_by_key(st, content, year, ripper_key)
    if ripper is None:
        res.reason = eng.R_GONE
        return res
    mult = await world_mult(session, content, ch.player_tg_id)
    offer = eng.offer_for(st, content, year, ripper, key, mult)
    if offer is None:
        res.reason = eng.R_GONE
        return res
    cw = content.cyberware[key]
    res.name = cw.name
    if offer.reason is not None:
        res.reason = offer.reason
        return res
    if cw.smasher_only and not eng.smasher_real(st):  # the scan lied: the doctors see the truth before they cut
        eng.apply_scan(st, eng.ScanResult(st.cyber_tolerance, st.cyber_tolerance, False))
        await save_active(session, ch, tz)
        res.reason = eng.R_TOLERANCE
        return res
    ap = content.balance.ripper.install_ap
    if not _spend(ch, content, ap):
        res.reason = NO_AP
        return res
    if cw.borg_risk:  # owner request 27.10: the underground conversion may end on the table
        chance = eng.black_death_chance(st, content)
        res.data["death_chance"] = chance
        if rng.random() < chance:
            return await _table_death(session, content, ch, rng, year, tz, res, offer.total, ripper.name)

    roll = eng.roll_operation(content, ripper, rng)
    res.complication, res.malfunction, res.infection = roll.complication, roll.malfunction, roll.infection
    extra = roll.complication and not roll.malfunction
    loss = eng.install_loss(st, content, year, cw, extra=extra)
    fu = []
    if roll.malfunction:  # the implant does not take: the fee is gone, the body is shocked (current humanity only)
        res.paid = offer.price
        fx = apply_effects(st, {"eddies": -res.paid}, content, rng, year)
        shock = eng.install_loss(st, content, year, cw, extra=False)
        res.loss = shock
        if shock:
            fx2 = apply_effects(st, {"humanity": -shock}, content, rng, year)
            fu.extend(fx2.followups)
        fu.extend(fx.followups)
    else:
        res.paid = offer.total
        old = content.cyberware.get(offer.replaces) if offer.replaces else None
        res.replaced = old.name if old is not None else ""
        before_humanity_lost = st.humanity_lost
        fx = apply_effects(st, {"eddies": -res.paid}, content, rng, year)
        inst = apply_install_cyberware(st, content, key, rng, year, humanity_paid=loss, complication=extra)
        res.loss = loss
        res.returned = max(0, loss - (st.humanity_lost - before_humanity_lost)) if offer.replaces else 0
        fu.extend(fx.followups)
        fu.extend(inst.followups)
        if cw.smasher_only or cw.borg_risk:
            res.family = eng.break_family(st, content)
            res.borg = True
            await announce_borg(session, ch, ripper.name)
        elif not st.flags.get("borg_announced") and derived.is_borg(st, content):  # owner request 27.10: by chrome
            res.borg = True
            res.data["borg_chrome"] = True
            await announce_borg(session, ch, ripper.name, chrome=True)
    if roll.infection:
        apply_effects(st, {"infection": True}, content, rng, year)
    if offer.banned and not roll.malfunction:  # a banned implant from an underground clinic: the police hear of it
        heat = apply_effects(st, {"heat": content.balance.world.banned_install_heat}, content, rng, year)
        fu.extend(heat.followups)
        res.banned_heat = st.heat
    out = await followups.handle(session, content, ch, fu, rng, year, tz)
    res.lines, res.died, res.cause = out.lines, out.died, out.cause
    res.ok = not roll.malfunction
    if res.died:
        return res
    await save_active(session, ch, tz)
    await repo.log_event(
        session, "implant_install",
        {"ripper": ripper_key, "item": key, "paid": res.paid, "loss": res.loss, "complication": roll.complication,
         "malfunction": roll.malfunction, "infection": roll.infection, "replaced": offer.replaces or ""},
        character_id=ch.id,
    )
    if res.ok:
        from app.services import game_events as bus

        await bus.publish("cyberware_installed", player_tg_id=ch.player_tg_id, character_id=ch.id, session=session, ch=ch)
    return res


async def announce_borg(session: AsyncSession, ch: ActiveCharacter, ripper_name: str | None = None,
                        chrome: bool = False) -> bool:
    """The city learns of a new borg at once (owner request 25.10; 27.10: also one with so much chrome that the body
    is more machine than meat). Once a life. True when the post went out."""
    st = ch.state
    if st.flags.get("borg_announced"):
        return False
    st.flags["borg_announced"] = True
    from app.bot import texts

    who = texts.esc(f"{st.name} {st.surname}".strip())
    if chrome:
        text = (f"🤖 <b>В Найт-Сити новый борг.</b> У {who} в теле уже {len(st.cyberware)} имплантов: железа больше, "
                "чем мяса. Ни стола, ни конверсии — просто однажды утром в зеркале не остаётся человека. Сканеры "
                "на входах пищат, патрули уступают дорогу.")
    else:
        where = f" ({texts.esc(ripper_name)})" if ripper_name else ""
        text = (f"🤖 <b>В Найт-Сити новый борг.</b> {who} ложится на стол{where} и встаёт полной конверсией: от "
                "человека остались имя и мозг в бронированном корпусе. Хром больше не сводит с ума, но родные уже не "
                "узнают этот голос.")
    await repo.log_event(session, "borg", {"text": text, "ref": "borg"}, character_id=ch.id, public=True)
    from app.services import deeds  # owner request 26.10: the city remembers

    await deeds.add(session, ch.id, "borg", f"{st.name} {st.surname}".strip() + (
        ": столько хрома, что в Найт-Сити новый борг." if chrome else ": полная конверсия, в Найт-Сити новый борг."),
        district=st.district, w=2)
    return True


async def sweep_borgs(session: AsyncSession, content: Content, tz: str, force: bool = False) -> int:
    """Owner request 27.10: characters who became borgs by chrome before the rule existed (or by a grant) get their
    announcement too."""
    from app.services import game

    if not (force or content.balance.cyber.borg_sweep):
        return 0
    n = 0
    for row in await repo.living_characters(session):
        ch = await game.get_active_character(session, row.player_tg_id)
        if ch is None or ch.id != row.id or ch.state.flags.get("borg_announced"):
            continue
        if derived.is_borg(ch.state, content):
            await announce_borg(session, ch, chrome=not derived.is_full_conversion(ch.state, content))
            await save_active(session, ch, tz)
            n += 1
    return n


async def _table_death(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    res: OpResult, price: int, ripper_name: str,
) -> OpResult:
    """Owner request 27.10: the body did not take the underground conversion. The money is gone; the heart stops on
    the table, and the usual last chances (a second heart, a clone, luck) still apply."""
    from app.services import death

    st = ch.state
    res.paid = price
    apply_effects(st, {"eddies": -price}, content, rng, year)
    st.hp = 0
    res.lines.append(f"☠️ Тело не принимает железо. {ripper_name} ещё минуту качает тебе грудь, потом снимает перчатки.")
    zr = await death.resolve_zero_hp(session, content, ch, rng, year, tz, cause="implant_failure",
                                     source="full_conversion_black", allow_evac=False)
    res.lines.extend(zr.lines)
    if zr.died:
        res.died, res.cause = True, "implant_failure"
        return res
    if not zr.pending:
        res.lines.append("Ты всё-таки открываешь глаза. Конверсия не прижилась: тело своё, деньги ушли.")
        await save_active(session, ch, tz)
    await repo.log_event(session, "implant_install",
                         {"item": res.key, "paid": price, "table_death": True, "survived": not zr.died},
                         character_id=ch.id)
    return res


# -- removing ---------------------------------------------------------------------------------------------------------------------


def removable(content: Content, key: str) -> bool:
    cw = content.cyberware.get(key)
    return cw is not None and not cw.not_for_sale and not cw.smasher_only and not cw.borg_risk


async def remove(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    ripper_key: str, key: str,
) -> OpResult:
    """Take an implant out (and what stood on it): 20% of the price, half of the humanity cost comes back (PLAN 6.3)."""
    st = ch.state
    res = OpResult(key=key)
    ripper = eng.ripper_by_key(st, content, year, ripper_key)
    item = st.cyber_item(key)
    if ripper is None or item is None or key not in content.cyberware:
        res.reason = eng.R_GONE
        return res
    cw = content.cyberware[key]
    res.name = cw.name
    if not removable(content, key):
        res.reason = eng.R_FIXED
        return res
    mult = await world_mult(session, content, ch.player_tg_id)
    price = eng.removal_price(st, content, year, key, ripper, mult)
    if st.eddies < price:
        res.reason = eng.R_EDDIES
        return res
    if not _spend(ch, content, content.balance.ripper.remove_ap):
        res.reason = NO_AP
        return res
    had = {c.key for c in st.cyberware}
    humanity_lost_before = st.humanity_lost
    pay = apply_effects(st, {"eddies": -price}, content, rng, year)
    out = apply_remove_cyberware(st, content, key, rng, year)
    res.paid = price
    res.returned = humanity_lost_before - st.humanity_lost
    res.cascade = [content.cyberware[k].name for k in out.cyberware_removed if k != key and k in content.cyberware]
    res.ok = key not in {c.key for c in st.cyberware} and key in had
    fu = await followups.handle(session, content, ch, [*pay.followups, *out.followups], rng, year, tz)
    res.lines, res.died, res.cause = fu.lines, fu.died, fu.cause
    if res.died:
        return res
    await save_active(session, ch, tz)
    await repo.log_event(session, "implant_remove", {"ripper": ripper_key, "item": key, "paid": price}, character_id=ch.id)
    return res


# -- the scan ---------------------------------------------------------------------------------------------------------------------


async def scan_tolerance(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    ripper_key: str,
) -> OpResult:
    st = ch.state
    res = OpResult()
    ripper = eng.ripper_by_key(st, content, year, ripper_key)
    if ripper is None:
        res.reason = eng.R_GONE
        return res
    price = content.balance.cyber.scan_cost
    if st.eddies < price:
        res.reason = eng.R_EDDIES
        return res
    if not _spend(ch, content, content.balance.ripper.scan_ap):
        res.reason = NO_AP
        return res
    apply_effects(st, {"eddies": -price}, content, rng, year)
    result = eng.scan(st, ripper, rng)
    eng.apply_scan(st, result)
    res.ok, res.paid = True, price
    res.data = {"shown": result.shown, "lied": result.lied, "smasher": result.shown == eng.SMASHER}
    await save_active(session, ch, tz)
    await repo.log_event(session, "tolerance_scan", {"ripper": ripper_key, "lied": result.lied}, character_id=ch.id)
    return res


# -- the infection --------------------------------------------------------------------------------------------------------------------


async def cure_at_clinic(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
) -> OpResult:
    st = ch.state
    res = OpResult()
    if not eng.infected(st):
        res.reason = eng.R_GONE
        return res
    price = eng.cure_price(content)
    if st.eddies < price:
        res.reason = eng.R_EDDIES
        return res
    if not _spend(ch, content, content.balance.ripper.cure_ap):
        res.reason = NO_AP
        return res
    apply_effects(st, {"eddies": -price}, content, rng, year)
    st.flags.pop(FLAG_INFECTION, None)
    res.ok, res.paid = True, price
    await save_active(session, ch, tz)
    await repo.log_event(session, "infection_cured", {"how": "clinic", "paid": price}, character_id=ch.id)
    return res


async def cure_by_check(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
) -> LifeResult | CheckResult:
    """Treat the wound yourself: TECH + Medicine against `cyber.infection_cure_check` (luck and glitch apply)."""
    st = ch.state
    if not eng.infected(st):
        return LifeResult(CURE, blocked=eng.R_GONE)
    if not _spend(ch, content, content.balance.ripper.cure_ap):
        return LifeResult(CURE, no_ap=True)
    check = eng.roll_cure(st, content, rng)
    return await lifecheck.start(session, content, ch, rng, year, tz, CURE, check, {})


async def _settle_cure(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    args: dict[str, Any], check: CheckResult,
) -> LifeResult:
    res = LifeResult(CURE, ok=check.ok, check=check)
    if check.ok:
        ch.state.flags.pop(FLAG_INFECTION, None)
    await save_active(session, ch, tz)
    await repo.log_event(session, "infection_cured", {"how": "check", "ok": check.ok}, character_id=ch.id)
    return res


lifecheck.register(CURE, _settle_cure)


# -- software -----------------------------------------------------------------------------------------------------------------------


async def buy_program(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    ripper_key: str | None, key: str,
) -> OpResult:
    """Buy a quickhack into the inventory. `ripper_key` None = the dealer of the district."""
    st = ch.state
    res = OpResult(key=key)
    ripper = None
    if ripper_key is not None:
        ripper = eng.ripper_by_key(st, content, year, ripper_key)
        if ripper is None or not ripper.sells_software:
            res.reason = eng.R_GONE
            return res
    elif not eng.software_dealer_open(st, content, year):
        res.reason = eng.R_GONE
        return res
    mult = await world_mult(session, content, ch.player_tg_id)
    offer = next((o for o in eng.software_offers(st, content, year, ripper=ripper, world_mult=mult) if o.key == key), None)
    if offer is None:
        res.reason = eng.R_GONE
        return res
    res.name = offer.name
    if offer.reason is not None:
        res.reason = offer.reason
        return res
    if not _spend(ch, content, content.balance.ripper.software_ap):
        res.reason = NO_AP
        return res
    apply_effects(st, {"eddies": -offer.price}, content, rng, year)
    st.inventory.append(InventoryItem(key, KIND_QUICKHACK, False))
    res.ok, res.paid = True, offer.price
    await save_active(session, ch, tz)
    await repo.log_event(session, "software", {"item": key, "price": offer.price, "dealer": ripper_key or "dealer"}, character_id=ch.id)
    return res


def has_deck(content: Content, ch: ActiveCharacter) -> bool:
    return any((cw := content.cyberware.get(c.key)) is not None and cw.deck is not None for c in ch.state.cyberware)


# -- wounds for money (owner request 24.10) -----------------------------------------------------------------------------


def heal_price(content: Content, hp: int) -> int:
    cfg = content.balance.ripper
    return 0 if hp <= 0 else max(cfg.heal_min, hp * cfg.heal_per_hp)


def heal_missing(ch: ActiveCharacter, content: Content) -> int:
    return max(0, derived.max_hp(ch.state, content) - ch.state.hp)


async def heal_wounds(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                      tz: str, amount: int | None) -> tuple[str | None, int, int]:
    """Patch up `amount` health (None = all that is missing). Returns (blocked, healed, paid)."""
    missing = heal_missing(ch, content)
    if missing <= 0:
        return "full", 0, 0
    hp = missing if amount is None else min(missing, max(1, amount))
    price = heal_price(content, hp)
    if ch.state.eddies < price:
        return "eddies", hp, price
    apply_effects(ch.state, {"hp": hp, "eddies": -price}, content, rng, year)
    await save_active(session, ch, tz)
    await repo.log_event(session, "clinic_heal", {"hp": hp, "paid": price}, character_id=ch.id)
    return None, hp, price
