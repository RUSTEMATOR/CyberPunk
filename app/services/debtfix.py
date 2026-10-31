"""Startup migration of the debt model (DESIGN.md 5, 11; QUESTIONS.md "Event payoffs").

Before the change a debt grew 5% a week while the minimum payment was 2%: the debt could only grow. The new model
(`balance.debt`: a low weekly rate and a minimum with a floor) pays a starting debt off in 1-2 years. Debts saved
under the old rate are moved to the new one here, and the interest they already gathered is recomputed at the new
rate: the `interest` of every logged `week_lived` of that character, scaled by new_rate / old_rate, replaces the old
interest in the principal. The missed weeks are forgiven once, so the collector does not come for weeks the old
rate made impossible to pay.

Idempotent: only debts with `weekly_rate > balance.debt.legacy_rate_above` are touched, and they leave with the new
rate. Every change is logged and summed in `world_state.debt_model_v2`.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.db import repo
from app.db.models import Debt, EventLog

log = logging.getLogger(__name__)

MIGRATION_KEY = "debt_model_v2"


async def migrate_debt_rates(session: AsyncSession, content: Content) -> int:
    """Move debts of the old rate to the new model. Returns the number of characters changed (0 = nothing to do)."""
    bal = content.balance.debt
    res = await session.execute(select(Debt).where(Debt.weekly_rate > bal.legacy_rate_above).order_by(Debt.id))
    by_char: dict[int, list[Debt]] = defaultdict(list)
    for row in res.scalars():
        by_char[int(row.character_id)].append(row)
    if not by_char:
        return 0
    summary: list[dict[str, int]] = []
    for cid, rows in by_char.items():
        old_rate = max(float(r.weekly_rate) for r in rows)
        logged = await session.execute(
            select(EventLog.payload).where(EventLog.character_id == cid, EventLog.kind == "week_lived")
        )
        interest = sum(int((p or {}).get("interest", 0) or 0) for p in logged.scalars())
        excess = round(interest * (1 - bal.weekly_rate / old_rate)) if old_rate > 0 else 0
        before = sum(int(r.principal) for r in rows)
        left = excess
        for r in sorted(rows, key=lambda x: -int(x.principal)):
            cut = min(int(r.principal), max(0, left))
            r.principal = int(r.principal) - cut
            left -= cut
            r.weekly_rate = bal.weekly_rate
            r.missed_weeks = 0
            if r.principal <= 0:
                await session.delete(r)
        after = before - (excess - max(0, left))
        summary.append({"character": cid, "before": before, "after": after, "interest_logged": interest})
        log.warning(
            "debt model: character %d: debt %d -> %d (logged interest %d at %.2f%%/week recomputed at %.2f%%), "
            "rate set to %.2f%%, missed weeks reset",
            cid, before, after, interest, old_rate * 100, bal.weekly_rate * 100, bal.weekly_rate * 100,
        )
    await session.flush()
    done = list(await repo.ws_get(session, MIGRATION_KEY, []) or [])
    await repo.ws_set(session, MIGRATION_KEY, done + summary)
    return len(by_char)
