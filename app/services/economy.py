"""Choices of the upkeep (owner request, docs/PLAN_ECONOMY.md): the suppressant course, the lifestyle, the staff,
a dose from the dealer and the rehab. The weekly payments themselves are in engine/upkeep.py."""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.engine import upkeep as up
from app.engine.effects import AppliedEffects, apply_effects
from app.services import ap as ap_services
from app.services.game import ActiveCharacter, save_active

R_GONE = "gone"
R_BLACK = "black"
R_EDDIES = "eddies"
R_STATUS = "status"
R_HAVE = "have"
R_NO_AP = "no_ap"
R_JAIL = "jail"
R_CLEAN = "clean"


def _jailed(ch: ActiveCharacter) -> bool:
    return int(ch.state.flags.get("jail_weeks", 0) or 0) > 0


async def set_immuno(session: AsyncSession, content: Content, ch: ActiveCharacter, tz: str, key: str | None) -> str | None:
    if key is None:
        ch.state.flags.pop(up.FLAG_IMMUNO, None)
    else:
        g = up.grade(content, key)
        if g is None:
            return R_GONE
        if not up.can_take(ch.state, g):
            return R_BLACK
        ch.state.flags[up.FLAG_IMMUNO] = key
    await save_active(session, ch, tz)
    return None


async def set_lifestyle(session: AsyncSession, content: Content, ch: ActiveCharacter, tz: str, key: str) -> str | None:
    if key not in {x.key for x in up.levels(content)}:
        return R_GONE
    up.set_lifestyle(ch.state, content, key)
    await save_active(session, ch, tz)
    return None


async def hire(session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, tz: str, key: str) -> str | None:
    mem = next((m for m in content.balance.economy.staff if m.key == key), None)
    if mem is None:
        return R_GONE
    keys = list(ch.state.flags.get(up.FLAG_STAFF) or [])
    if key in keys:
        return R_HAVE
    if ch.state.status < mem.min_status:
        return R_STATUS
    if ch.state.eddies < mem.hire_fee:
        return R_EDDIES
    if mem.hire_fee:
        apply_effects(ch.state, {"eddies": -mem.hire_fee}, content, random.Random(), year)
    ch.state.flags[up.FLAG_STAFF] = keys + [key]
    await save_active(session, ch, tz)
    return None


async def fire(session: AsyncSession, content: Content, ch: ActiveCharacter, tz: str, key: str) -> str | None:
    keys = [k for k in (ch.state.flags.get(up.FLAG_STAFF) or []) if k != key]
    if keys:
        ch.state.flags[up.FLAG_STAFF] = keys
    else:
        ch.state.flags.pop(up.FLAG_STAFF, None)
    await save_active(session, ch, tz)
    return None


@dataclass
class DoseResult:
    blocked: str | None = None
    name: str = ""
    overdose: bool = False
    hooked: bool = False  # this dose made it an addiction
    applied: AppliedEffects | None = None


async def take_dose(
    session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, tz: str, key: str,
    rng: random.Random, black_market: bool = False,
) -> DoseResult:
    d = up.drug(content, key)
    if d is None or (d.black_market and not black_market):
        return DoseResult(R_GONE)
    if _jailed(ch):
        return DoseResult(R_JAIL)
    st = ch.state
    if st.eddies < d.price:
        return DoseResult(R_EDDIES)
    res = DoseResult(name=d.name)
    fx: dict = {"eddies": -d.price, **dict(d.effects)}
    taken = up.dosed(st)
    if key in taken and rng.random() < d.overdose_chance * (1 + taken.count(key)):
        fx["hp"] = int(fx.get("hp", 0)) - d.overdose_hp
        res.overdose = True
    th = content.balance.economy.drugs.addiction_threshold
    hb = up.habit(st)
    before = hb.get(key, 0)
    hb[key] = before + d.addiction
    st.flags[up.FLAG_HABIT] = hb
    res.hooked = before < th <= hb[key]
    st.flags[up.FLAG_DOSE] = taken + [key]
    res.applied = apply_effects(st, fx, content, rng, year)
    await save_active(session, ch, tz)
    return res


@dataclass
class RehabResult:
    blocked: str | None = None
    ok: bool = False
    price: int = 0
    chance: float = 0.0
    name: str = ""


async def rehab(
    session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, tz: str, key: str, rng: random.Random,
) -> RehabResult:
    """A course in the rehab clinic: the price is paid, the habit goes with the chance (lower the longer the use)."""
    d = up.drug(content, key)
    st = ch.state
    if d is None or up.habit(st).get(key, 0) <= 0:
        return RehabResult(R_CLEAN)
    price = up.rehab_price(st, content, key)
    chance = up.rehab_chance(st, content, key)
    if st.eddies < price:
        return RehabResult(R_EDDIES, price=price, chance=chance)
    if not ap_services.can_spend(ch, content.balance.economy.drugs.rehab_ap):
        return RehabResult(R_NO_AP, price=price, chance=chance)
    ap_services.spend(ch, content, content.balance.economy.drugs.rehab_ap)
    apply_effects(st, {"eddies": -price}, content, rng, year)
    ok = rng.random() < chance
    if ok:
        hb = up.habit(st)
        hb.pop(key, None)
        if hb:
            st.flags[up.FLAG_HABIT] = hb
        else:
            st.flags.pop(up.FLAG_HABIT, None)
        apply_effects(st, {"mood": 5, "humanity": 1}, content, rng, year)
    await save_active(session, ch, tz)
    return RehabResult(None, ok, price, chance, d.name)


# -- humanity back and Mk steps (owner request) --------------------------------------------------------------------------


def neuro_price(content: Content, ch: ActiveCharacter) -> int:
    cfg = content.balance.economy.neuro
    n = int(ch.state.flags.get("neuro_n", 0) or 0)
    return int(cfg.base_price * cfg.growth ** n)


def neuro_block(content: Content, ch: ActiveCharacter) -> str | None:
    st = ch.state
    if st.humanity_lost <= 0:
        return R_CLEAN
    if int(st.flags.get("neuro_week", -1)) == st.weeks_lived:
        return R_HAVE  # one course a month: the brain needs time
    if st.eddies < neuro_price(content, ch):
        return R_EDDIES
    if not ap_services.can_spend(ch, content.balance.economy.neuro.ap):
        return R_NO_AP
    return None


async def neuro_rehab(session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, tz: str) -> tuple[str | None, int, int]:
    """A course of neuro rehab: part of the humanity the implants took comes back for good. Returns (blocked, paid, back)."""
    why = neuro_block(content, ch)
    if why:
        return why, 0, 0
    st = ch.state
    price = neuro_price(content, ch)
    ap_services.spend(ch, content, content.balance.economy.neuro.ap)
    back = min(content.balance.economy.neuro.humanity, st.humanity_lost)
    st.humanity_lost -= back
    apply_effects(st, {"eddies": -price, "humanity": back}, content, random.Random(), year)
    st.flags["neuro_n"] = int(st.flags.get("neuro_n", 0) or 0) + 1
    st.flags["neuro_week"] = st.weeks_lived
    await save_active(session, ch, tz)
    return None, price, back


def mk_price(content: Content, key: str, step: int) -> int | None:
    """The price of the Mk `step` (1 = Mk2) of an implant; None when there is no such step."""
    steps = content.balance.economy.mk.steps
    cw = content.cyberware.get(key)
    if cw is None or not (1 <= step <= len(steps)):
        return None
    return int(cw.price * steps[step - 1].price_mult)


async def mk_up(session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, tz: str, key: str) -> tuple[str | None, int]:
    st = ch.state
    if not st.has_cyberware(key):
        return R_GONE, 0
    cur = up.mk_of(st, key)
    price = mk_price(content, key, cur + 1)
    if price is None:
        return R_HAVE, 0
    if st.eddies < price:
        return R_EDDIES, price
    if not ap_services.can_spend(ch, content.balance.economy.mk.ap):
        return R_NO_AP, price
    ap_services.spend(ch, content, content.balance.economy.mk.ap)
    apply_effects(st, {"eddies": -price, "skill_xp:tech": 1}, content, random.Random(), year)
    mk = dict(st.flags.get(up.FLAG_MK) or {})
    mk[key] = cur + 1
    st.flags[up.FLAG_MK] = mk
    await save_active(session, ch, tz)
    return None, price
