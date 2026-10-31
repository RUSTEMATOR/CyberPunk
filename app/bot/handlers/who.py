"""/who: the living characters of the group, their roles and where they are (PLAN 4.10, DESIGN.md 3).

A temporary group message (balance.ui.temp_message_ttl_s). The role is the earned one: the main and the
second role with their ranks, as in the profile.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.bot import texts, views
from app.bot.ui.kit import UiKit
from app.db import repo
from app.db import session as db
from app.services import legends

router = Router(name="who")


@router.message(Command("who"))
async def cmd_who(message: Message, ui: UiKit) -> None:
    content = ui.content
    lines = [texts.WHO_TITLE]
    async with db.session_scope() as s:
        rows = await repo.living_characters(s)
        for row in rows:
            name = " ".join(x for x in (row.name, row.surname) if x)
            lines.append(texts.WHO_ROW.format(
                name=texts.esc(name), roles=legends.roles_label(content, row.role_affinity or {}),
                district=views.district_name(content, row.district), age=views.years(int(row.age_years)),
            ))
    if not rows:
        lines.append(texts.WHO_EMPTY)
    ui.reply_temp(message.chat.id, "\n".join(lines))
