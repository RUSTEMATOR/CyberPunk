"""The black market (owner request, docs/PLAN_ECONOMY.md stage 4): prototypes of the coming eras before their time.

A new showcase every month of the character's life (seeded by the character and the month), prices x2..x4 by how far
ahead the thing comes from, a chance that the seller vanishes with the money, heat for the deal. Implants are put in
by the market's own ripper (humanity and a complication chance as at an underground clinic).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.engine import ripper as rip_eng
from app.engine.effects import AppliedEffects, apply_effects, apply_install_cyberware
from app.services import followups
from app.services.game import ActiveCharacter, save_active

R_CRED = "cred"
R_GONE = "gone"
R_EDDIES = "eddies"
R_HAVE = "have"
R_JAIL = "jail"

KIND_NAMES = {"cyberware": "имплант", "weapons": "оружие", "armor": "броня", "vehicles": "транспорт"}


@dataclass
class Lot:
    kind: str
    key: str
    name: str
    year: int  # the year it comes from
    price: int


def _month(ch: ActiveCharacter, content: Content) -> int:
    return ch.state.weeks_lived // max(1, content.balance.time.weeks_per_press)


def showcase(content: Content, ch: ActiveCharacter, year: int) -> list[Lot]:
    cfg = content.balance.economy.black_market
    rng = random.Random(f"bm:{ch.id}:{_month(ch, content)}")
    pool: list[Lot] = []
    for kind in ("cyberware", "weapons", "armor", "vehicles"):
        for key, rec in getattr(content, kind).items():
            frm = getattr(rec, "available_from", None)
            if frm is None or frm <= year or frm > year + cfg.max_gap_years:
                continue
            if getattr(rec, "not_for_sale", False) or getattr(rec, "smasher_only", False) or getattr(rec, "borg_risk", False):
                continue
            mult = min(cfg.price_mult_max, cfg.price_mult_base + cfg.price_mult_per_10y * (frm - year) / 10)
            pool.append(Lot(kind, key, rec.name, frm, int(rec.price * mult)))
    rng.shuffle(pool)
    picked: list[Lot] = []
    kinds_seen: dict[str, int] = {}
    for lot in pool:  # a mix: at most two of a kind
        if kinds_seen.get(lot.kind, 0) >= 2:
            continue
        picked.append(lot)
        kinds_seen[lot.kind] = kinds_seen.get(lot.kind, 0) + 1
        if len(picked) >= cfg.slots:
            break
    bought = set((ch.state.flags.get("bm_bought") or {}).get(str(_month(ch, content)), []))
    return [x for x in picked if f"{x.kind}:{x.key}" not in bought]


def blocked(content: Content, ch: ActiveCharacter) -> str | None:
    if int(ch.state.flags.get("jail_weeks", 0) or 0) > 0:
        return R_JAIL
    if ch.state.street_cred < content.balance.economy.black_market.min_street_cred:
        return R_CRED
    return None


@dataclass
class BuyResult:
    blocked: str | None = None
    lot: Lot | None = None
    scam: bool = False
    applied: list[AppliedEffects] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    complication: bool = False


async def buy(
    session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, tz: str, ident: str, rng: random.Random,
) -> BuyResult:
    why = blocked(content, ch)
    if why:
        return BuyResult(why)
    lot = next((x for x in showcase(content, ch, year) if f"{x.kind}:{x.key}" == ident), None)
    if lot is None:
        return BuyResult(R_GONE)
    st = ch.state
    if lot.kind == "cyberware" and st.has_cyberware(lot.key):
        return BuyResult(R_HAVE, lot)
    if st.eddies < lot.price:
        return BuyResult(R_EDDIES, lot)
    cfg = content.balance.economy.black_market
    res = BuyResult(lot=lot)
    res.applied.append(apply_effects(st, {"eddies": -lot.price, "heat": cfg.heat}, content, rng, year))
    book = dict(st.flags.get("bm_bought") or {})
    month = str(_month(ch, content))
    book = {month: list(book.get(month, [])) + [ident]}  # only this month is remembered
    st.flags["bm_bought"] = book
    if rng.random() < cfg.scam_chance:
        res.scam = True
    elif lot.kind == "cyberware":
        cw = content.cyberware[lot.key]
        res.complication = rng.random() < cfg.complication_chance
        loss = rip_eng.install_loss(st, content, year, cw, extra=res.complication)
        res.applied.append(apply_install_cyberware(st, content, lot.key, rng, year, humanity_paid=loss,
                                                   complication=res.complication))
    elif lot.kind == "vehicles":
        res.applied.append(apply_effects(st, {f"vehicle_grant:{lot.key}": True}, content, rng, year))
    else:
        res.applied.append(apply_effects(st, {f"item_grant:{lot.key}": True}, content, rng, year))
    fus = [fu for a in res.applied for fu in a.followups]
    if fus:
        out = await followups.handle(session, content, ch, fus, rng, year, tz)
        res.lines = out.lines
        if out.died:
            return res
    await save_active(session, ch, tz)
    return res


async def black_ice_contact(session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, tz: str) -> str | None:
    why = blocked(content, ch)
    if why:
        return why
    if ch.state.flags.get("immuno_black"):
        return R_HAVE
    price = content.balance.economy.black_market.black_ice_contact
    if ch.state.eddies < price:
        return R_EDDIES
    apply_effects(ch.state, {"eddies": -price, "heat": 1}, content, random.Random(), year)
    ch.state.flags["immuno_black"] = True
    await save_active(session, ch, tz)
    return None
