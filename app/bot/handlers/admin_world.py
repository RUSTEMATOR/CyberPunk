"""World admin commands (PLAN 10.7, DESIGN.md 16): /admin_tick, /admin_year, /admin_history, /admin_veto.

Time is personal (DESIGN.md 9a): there is no world clock to read or move, so /admin_pace is gone and /admin_year shows
the date of a player. Only ADMIN_IDS (and the bootstrap admin). Every answer is a temporary message.
"""

from __future__ import annotations

import random

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app.bot import texts, texts_world
from app.bot.handlers.admin import _target_id
from app.bot.ui.kit import UiKit
from app.db import repo
from app.db import session as db
from app.engine import world as world_engine
from app.scheduler import tick
from app.services import world as world_service
from app.services import worldfx

router = Router(name="admin_world")


def _is_admin(message: Message, ui: UiKit) -> bool:
    return message.from_user is not None and ui.is_admin(message.from_user.id)


@router.message(Command("admin_news"))
async def admin_news(message: Message, ui: UiKit) -> None:
    """Owner request 24.10: post the news of the last 24 hours now (the day tick does not run)."""
    if not _is_admin(message, ui):
        return
    ui.reply_temp(message.chat.id, "Собираю сводку за сутки…")
    ui.spawn(tick.post_news_now(ui))


@router.message(Command("admin_tick"))
async def admin_tick(message: Message, command: CommandObject, ui: UiKit) -> None:
    """Run the real-time daily tick now (an extra day of heat, gang wars, the coin and the night news)."""
    if not _is_admin(message, ui):
        return
    if (command.args or "").strip():
        ui.reply_temp(message.chat.id, texts_world.ADMIN_USAGE_TICK)
        return
    rep = await tick.run_tick(ui, force=True, rng=random.Random())
    ui.reply_temp(message.chat.id, texts_world.ADMIN_TICK_DONE.format(
        days=rep.days, rate=rep.coin_rate, news=1 if rep.news else 0, wars=len(rep.wars_started),
    ))


def _arg(command: CommandObject) -> str:
    return (command.args or "").strip()


@router.message(Command("admin_year"))
async def admin_year(message: Message, command: CommandObject, ui: UiKit) -> None:
    """The personal date of a player: @user, a number, a reply, else the author of the command."""
    if not _is_admin(message, ui) or message.from_user is None:
        return
    content = ui.content
    token = _arg(command) or None
    if token is not None and len(token.split()) != 1:
        ui.reply_temp(message.chat.id, texts_world.ADMIN_USAGE_YEAR)
        return
    tg_id = await _target_id(message, token) if (token or message.reply_to_message) else message.from_user.id
    if tg_id is None:
        ui.reply_temp(message.chat.id, texts.ADMIN_NO_PLAYER)
        return
    async with db.session_scope() as s:
        row = await world_service.latest_character(s, tg_id)
        who = texts.esc(await repo.display_name(s, tg_id, f"id {tg_id}"))
        wk = await world_service.personal_week(s, content, tg_id)
        chron = await worldfx.chronicle(s, content)
    year, woy = world_engine.year_week(content, wk)
    if row is None:
        ui.reply_temp(message.chat.id, texts_world.ADMIN_YEAR_START.format(who=who, year=year))
        return
    nxt = next((b for b in chron if b.week > wk), None)
    nxt_text = (
        texts_world.ADMIN_YEAR_NEXT.format(title=texts.esc(nxt.title), year=nxt.year, week=world_engine.year_week(content, nxt.week)[1])
        if nxt else texts_world.ADMIN_YEAR_NO_NEXT
    )
    ui.reply_temp(message.chat.id, texts_world.ADMIN_YEAR_SHOW.format(
        who=who, year=year, woy=woy, era=texts.esc(content.era_for_year(year).name), lived=int(row.weeks_lived or 0),
        next_event=nxt_text,
    ))


@router.message(Command("admin_history"))
async def admin_history(message: Message, ui: UiKit) -> None:
    """The last 10 rows of the world history (canon, local, generated), newest first."""
    if not _is_admin(message, ui):
        return
    async with db.session_scope() as s:
        rows = await repo.history_rows(s, 10)
    if not rows:
        ui.reply_temp(message.chat.id, texts_world.ADMIN_HISTORY_EMPTY)
        return
    lines = [texts_world.ADMIN_HISTORY_TITLE.format(n=len(rows))]
    for r in rows:
        woy = world_engine.year_week(ui.content, r.world_week)[1]
        lines.append(texts_world.ADMIN_HISTORY_ROW.format(
            id=r.id, kind=r.kind, status=", отменено" if r.status == "vetoed" else "", year=r.year, week=woy,
            title=texts.esc(r.title),
        ))
    ui.reply_temp(message.chat.id, "\n".join(lines))


@router.message(Command("admin_veto"))
async def admin_veto(message: Message, command: CommandObject, ui: UiKit) -> None:
    """Cancel a generated world event within `world.history_veto_hours`; its effects are rolled back."""
    if not _is_admin(message, ui):
        return
    arg = _arg(command).lstrip("#")
    if not arg.isdigit():
        ui.reply_temp(message.chat.id, texts_world.ADMIN_USAGE_VETO)
        return
    row_id = int(arg)
    async with db.session_scope() as s:
        res = await worldfx.veto(s, ui.content, row_id)
    if res.ok:
        text = texts_world.ADMIN_VETO_DONE.format(id=row_id, title=texts.esc(res.title))
    elif res.reason == "not_found":
        text = texts_world.ADMIN_VETO_NOT_FOUND.format(id=row_id)
    elif res.reason == "not_generated":
        text = texts_world.ADMIN_VETO_NOT_GENERATED.format(id=row_id, kind=res.kind)
    elif res.reason == "already":
        text = texts_world.ADMIN_VETO_ALREADY.format(id=row_id)
    else:
        text = texts_world.ADMIN_VETO_LATE.format(id=row_id, hours=ui.content.balance.world.history_veto_hours)
    ui.reply_temp(message.chat.id, text)
