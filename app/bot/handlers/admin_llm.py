"""Admin command of the narrator (PLAN 8.3, DESIGN.md 16): /admin_budget.

Shows the month's spending against `MONTHLY_BUDGET_USD`, the mode of the narrator and the calls by kind. The answer is a
temporary message, like every admin answer.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.bot import texts_narrator as T
from app.bot.ui.kit import UiKit
from app.narrator import narrator
from app.narrator.budget import Report


def render(report: Report, ui: UiKit) -> str:
    s = ui.settings
    lines = [T.BUDGET_TITLE.format(month=report.month)]
    lines.append(T.BUDGET_TOTAL.format(spent=f"{report.spent:.2f}", limit=f"{report.limit:.2f}", pct=int(round(report.pct * 100))))
    if not narrator.has_client():
        lines.append(T.BUDGET_STATE_OFF)
    else:
        lines.append(T.BUDGET_MODES[report.mode])
        if narrator.pause_left() > 0:
            lines.append(T.BUDGET_STATE_PAUSED.format(seconds=narrator.pause_left()))
        if narrator.budget.prices_missing():
            lines.append(T.BUDGET_NO_PRICES)
    lines.append(T.BUDGET_MODELS.format(narrative=s.model_narrative, fast=s.model_fast))
    lines.append("")
    if not report.rows:
        lines.append(T.BUDGET_EMPTY)
    else:
        lines.append(T.BUDGET_KINDS_TITLE)
        for r in report.rows:
            lines.append("- " + T.BUDGET_KIND_ROW.format(
                kind=r.kind, calls=r.calls, cost=f"{r.cost:.3f}", inp=r.input_tokens, cache=f"r{r.cache_read}/w{r.cache_write}",
                out=r.output_tokens,
            ))
    return "\n".join(lines)


router = Router(name="admin_llm")


@router.message(Command("admin_budget"))
async def admin_budget(message: Message, ui: UiKit) -> None:
    if message.from_user is None or not ui.is_admin(message.from_user.id):
        return
    report = await narrator.budget.report()
    ui.reply_temp(message.chat.id, render(report, ui))
