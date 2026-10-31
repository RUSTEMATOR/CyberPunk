"""The gym (owner request 23.10): health grows with sport. Flag `fitness` = points up to `gym.cap`; derived.py turns
them into hp_max and BODY."""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.engine import checks
from app.engine.effects import AppliedEffects, apply_effects
from app.services import ap as ap_services
from app.services.game import ActiveCharacter, save_active


@dataclass
class GymResult:
    blocked: str | None = None
    gained: int = 0
    check: object | None = None
    applied: AppliedEffects | None = None


def points(ch: ActiveCharacter) -> int:
    return int(ch.state.flags.get("fitness", 0) or 0)


async def train(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
                coach: bool = False) -> GymResult:
    g = content.balance.gym
    st = ch.state
    if int(st.flags.get("jail_weeks", 0) or 0) > 0:
        return GymResult("jail")
    if points(ch) >= g.cap:
        return GymResult("cap")
    price = g.coach_price if coach else g.price
    if st.eddies < price:
        return GymResult("eddies")
    if not ap_services.can_spend(ch, g.ap):
        return GymResult("ap")
    ap_services.spend(ch, content, g.ap)
    res = GymResult()
    if coach:
        gain = g.coach_points
    else:
        res.check = checks.roll_check(st, content, rng, "BODY", None, g.dv)
        gain = 2 if res.check.ok else 1
    res.gained = min(gain, g.cap - points(ch))
    st.flags["fitness"] = points(ch) + res.gained
    res.applied = apply_effects(st, {"eddies": -price, "mood": g.mood}, content, rng, year)
    await save_active(session, ch, tz)
    return res
