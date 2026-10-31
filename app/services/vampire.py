"""Feeding of a night-taxi vampire (owner request 24.10; engine/vampire.py)."""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.engine import jail as jail_eng
from app.engine import vampire as eng
from app.engine.effects import AppliedEffects, apply_effects
from app.services import ap as ap_services
from app.services.game import ActiveCharacter, save_active


@dataclass
class Result:
    blocked: str | None = None
    plan: eng.FeedPlan | None = None
    applied: AppliedEffects | None = None


async def feed(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
               key: str) -> Result:
    st = ch.state
    if not eng.is_vampire(st):
        return Result("not")
    if jail_eng.in_jail(st):
        return Result("jail")
    f = eng.cfg(content).feeds.get(key)
    if f is None:
        return Result("none")
    if f.eddies < 0 and st.eddies < -f.eddies:
        return Result("eddies")
    if eng.blood(st) >= eng.cfg(content).blood_max:
        return Result("full")
    if not ap_services.can_spend(ch, f.ap):
        return Result("ap")
    ap_services.spend(ch, content, f.ap)
    plan = eng.plan_feed(st, content, rng, key)
    applied = apply_effects(st, dict(plan.effects), content, rng, year) if plan else None
    if st.hp <= 0:
        st.hp = 1
    await save_active(session, ch, tz)
    return Result(None, plan, applied)
