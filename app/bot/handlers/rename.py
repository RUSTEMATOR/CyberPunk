"""Renaming a living character from the profile (owner request).

Profile -> "Сменить имя" -> the window waits for a typed name -> saved. One word changes the first
name; two or more words change the first name and the surname. Gender and the family stay as they are.
"""

from __future__ import annotations

from app.bot import texts, views
from app.bot.handlers.onboarding import validate_name
from app.bot.ui.callbacks import Ctx, goto, on_screen, on_text
from app.bot.ui.screen import Button, Screen
from app.db import session as db
from app.services import game


def rename_screen(ctx: Ctx, current: str, error: str | None = None) -> Screen:
    ch = ctx.content.balance.character
    text = texts.RENAME_PROMPT.format(current=texts.esc(current), min=ch.name_length_min, max=ch.name_length_max)
    if error:
        text = f"<i>{texts.esc(error)}</i>\n\n" + text
    return Screen(text, [[Button(texts.CANCEL, "cancel")]], awaiting_text="char_rename", name="rename")


async def open_rename(ctx: Ctx) -> Screen | None:
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
    if ch is None:
        return None
    return rename_screen(ctx, views.full_name(ch.state))


@on_screen("rename")
async def rename_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    return await goto(ctx, "profile")  # Cancel or any stale button: back to the profile, the wait is dropped


@on_text("char_rename")
async def typed_rename(ctx: Ctx, text: str) -> Screen | None:
    name, error = validate_name(ctx.content, text)
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
        if ch is None:
            return None
        if name is None:
            return rename_screen(ctx, views.full_name(ch.state), error)
        parts = name.split()
        ch.state.name = parts[0]
        if len(parts) > 1:
            ch.state.surname = " ".join(parts[1:])
        await game.save_active(s, ch)
    return await goto(ctx, "profile")
