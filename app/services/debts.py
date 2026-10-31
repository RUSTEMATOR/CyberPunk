"""Paying a debt off in full (owner request 24.10). Through apply_effects: eddies down, the debt principal down."""

from __future__ import annotations

import random

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.engine.effects import AppliedEffects, apply_effects
from app.services.game import ActiveCharacter, save_active


async def pay_off(session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, tz: str,
                  index: int | None) -> tuple[str | None, AppliedEffects | None, str]:
    """Pay one debt (by index) or all of them (None). Returns (blocked, applied, creditor)."""
    st = ch.state
    if not st.debts:
        return "none", None, ""
    if index is None:
        amount, who = sum(d.principal for d in st.debts), "все кредиторы"
    else:
        if not 0 <= index < len(st.debts):
            return "gone", None, ""
        d = st.debts.pop(index)
        st.debts.insert(0, d)  # the effect pays the first debts first
        amount, who = d.principal, d.creditor
    if st.eddies < amount:
        return "eddies", None, who
    applied = apply_effects(st, {"eddies": -amount, "debt": -amount}, content, random.Random(), year)
    await save_active(session, ch, tz)
    return None, applied, who
