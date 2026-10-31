"""Taking a loan (owner request 24.10; engine/loans.py)."""

from __future__ import annotations

import random

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.engine import loans as eng
from app.engine.effects import AppliedEffects
from app.engine.week import weekly_salary
from app.services.game import ActiveCharacter, save_active


def offers(content: Content, ch: ActiveCharacter) -> list[eng.Offer]:
    sal = weekly_salary(ch.state, content)
    return [eng.offer(ch.state, content, k, sal) for k in content.balance.loans.lenders]


async def take(session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, tz: str, key: str,
               pct: int) -> tuple[str | None, AppliedEffects | None, str, int]:
    sal = weekly_salary(ch.state, content)
    o = eng.offer(ch.state, content, key, sal)
    if o.blocked:
        return o.blocked, None, "", 0
    amount = max(100, o.amount * max(1, min(100, pct)) // 100 // 100 * 100)
    why, applied, who = eng.take(ch.state, content, key, amount, sal, random.Random(), year)
    if why:
        return why, None, "", 0
    await save_active(session, ch, tz)
    return None, applied, who, amount
