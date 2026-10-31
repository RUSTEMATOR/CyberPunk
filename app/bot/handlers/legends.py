"""/legends: the three reputation tops with a switch (PLAN 4.9, DESIGN.md 5).

A temporary group message with the buttons Улица / Статус / Слава. The buttons belong to the message,
not to a player window, so this router sits BEFORE the window callback router and takes only `lg:`
callbacks. A press edits the same message; its self-delete timer keeps running.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot import texts, texts_world
from app.bot.ui import style
from app.bot.ui.kit import UiKit
from app.db import session as db
from app.services import legends

router = Router(name="legends")

PREFIX = "lg"
_DEFAULT = "street"


def switch(active: str) -> InlineKeyboardMarkup:
    row = []
    for scale in legends.SCALES:
        label = texts.LEGENDS_SCALES[scale]
        label = texts.LEGENDS_TABS[scale]
        # "•" without a space: three tabs to a row leave about 7 letters each on a narrow phone
        row.append(InlineKeyboardButton(text=f"•{label}" if scale == active else label, callback_data=f"{PREFIX}:s:{scale}"))
    return InlineKeyboardMarkup(inline_keyboard=[row])


async def legends_text(ui: UiKit, scale: str) -> str:
    async with db.session_scope() as s:
        rows = await legends.tops(s, scale, ui.content.balance.reminders.legends_board_n, include_dead=True)
    lines = [texts.LEGENDS_TITLE.format(scale=texts.LEGENDS_SCALES[scale].lower()), f"<i>{texts.LEGENDS_HINT[scale]}</i>", ""]
    if not rows:
        lines.append(texts.LEGENDS_EMPTY)
    for n, row in enumerate(rows, 1):
        tpl = texts_world.LEGENDS_ROW_DEAD if row.dead else texts.LEGENDS_ROW
        lines.append(tpl.format(n=style.rank(n), who=texts.esc(row.name), v=row.value))
    return "\n".join(lines)


async def post_legends(ui: UiKit, chat_id: int, scale: str = _DEFAULT) -> None:
    ui.reply_temp(chat_id, await legends_text(ui, scale), markup=switch(scale))


@router.message(Command("legends"))
async def cmd_legends(message: Message, ui: UiKit) -> None:
    await post_legends(ui, message.chat.id)


@router.callback_query(F.data.startswith(f"{PREFIX}:"))
async def on_switch(query: CallbackQuery, ui: UiKit) -> None:
    parts = (query.data or "").split(":")
    scale = parts[2] if len(parts) == 3 and parts[1] == "s" else _DEFAULT
    msg = query.message
    if scale not in legends.SCALES or msg is None:
        await _answer(query, texts.WINDOW_STALE)
        return
    current = _active_scale(query)
    if scale == current:
        await _answer(query, texts.LEGENDS_ALREADY.format(scale=texts.LEGENDS_TABS[scale]))
        return
    ui.outbox.edit(msg.chat.id, msg.message_id, await legends_text(ui, scale), switch(scale))
    await _answer(query)


def _active_scale(query: CallbackQuery) -> str | None:
    """The tab marked as active in the message the button belongs to."""
    markup = query.message.reply_markup if query.message is not None else None
    if markup is None:
        return None
    for row in markup.inline_keyboard:
        for btn in row:
            if btn.text.startswith("•") and btn.callback_data:
                return btn.callback_data.split(":")[-1]
    return None


async def _answer(query: CallbackQuery, text: str | None = None) -> None:
    try:
        await query.answer(text)
    except Exception:  # a stale query must not break the switch
        pass
