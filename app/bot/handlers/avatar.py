"""The character's avatar (owner request 26.10: «возможность юзерам загружать аватарку на персонажа. Аватарка умирает с
персонажем»). The picture lives in the character's own flag `avatar` = {file_id, at}: a new life starts without one.
It shows in «Профиль» → «🖼 Аватар» (a short-lived photo in the chat) and over the obituary when the character dies.

Upload: the window waits for the next photo of its player (`awaiting_text` AWAIT); `router` takes the photo, keeps its
Telegram file_id and deletes the message."""

from __future__ import annotations

import time

from aiogram import F, Router
from aiogram.types import Message

from app.bot import texts
from app.bot.handlers.menu import menu_screen
from app.bot.ui import style
from app.bot.ui.callbacks import Ctx, _snapshot, goto, on_screen
from app.bot.ui.screen import Button, Screen
from app.db import repo
from app.db import session as db
from app.services import game

router = Router(name="avatar")
AWAIT = "avatar_photo"
FLAG = "avatar"
SHOW_TTL = 180


def avatar_of(st) -> str | None:  # noqa: ANN001
    rec = st.flags.get(FLAG)
    return str(rec.get("file_id")) if isinstance(rec, dict) and rec.get("file_id") else None


async def avatar_screen(ctx: Ctx, note: str | None = None, waiting: bool = False) -> Screen:
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, ctx.user_id)
    if ch is None:
        return await menu_screen(ctx)
    has = avatar_of(ch.state) is not None
    lines = [style.header("profile", f"Аватар: {texts.esc(ch.state.name)} {texts.esc(ch.state.surname)}"),
             "Картинка персонажа. Её видно, когда показываешь аватар, и над некрологом, когда персонаж умрёт. "
             "Аватар живёт и умирает вместе с персонажем: у следующей жизни его не будет."]
    lines.append("Аватар есть." if has else "Аватара пока нет.")
    if waiting:
        lines += ["", "<b>Отправь картинку следующим сообщением в этот чат, как фото (не файлом).</b>"]
    if note:
        lines += ["", note]
    rows: list[list[Button]] = []
    if waiting:
        rows.append([Button(texts.CANCEL, "open")])
        return Screen("\n".join(lines), rows, name="avatar", awaiting_text=AWAIT)
    if has:
        rows.append([Button("Показать", "show", style="success"), Button("Сменить", "upload")])
        rows.append([Button("Удалить", "drop", style="danger")])
    else:
        rows.append([Button("Загрузить аватар", "upload", style="success")])
    rows.append([Button(texts.BACK, "profile"), Button(texts.MENU, "menu")])
    return Screen("\n".join(lines), rows, name="avatar")


@on_screen("avatar")
async def avatar_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "profile":
        return await goto(ctx, "profile", "open")
    if action == "upload":
        return await avatar_screen(ctx, waiting=True)
    if action in ("show", "drop"):
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, ctx.user_id)
            if ch is None:
                return await menu_screen(ctx)
            fid = avatar_of(ch.state)
            if action == "drop":
                ch.state.flags.pop(FLAG, None)
                await game.save_active(s, ch, ctx.ui.settings.timezone)
                return await avatar_screen(ctx, "Аватар удалён.")
        if fid:
            caption = f"{texts.esc(ch.state.name)} {texts.esc(ch.state.surname)}".strip()
            res = await ctx.ui.outbox.send_photo(ctx.chat_id, fid, caption)
            if res.ok and res.message_id is not None:
                await ctx.ui.cleanup.schedule_delete(ctx.chat_id, res.message_id, SHOW_TTL, "temp")
                return await avatar_screen(ctx, "Аватар в чате на три минуты.")
            return await avatar_screen(ctx, "Telegram не принял картинку. Загрузи её заново.")
    return await avatar_screen(ctx)


@router.message(F.photo)
async def on_photo(message: Message, ui) -> None:  # noqa: ANN001
    """The photo the avatar window waits for; other photos are left alone."""
    user = message.from_user
    if user is None or not message.photo:
        return
    async with db.session_scope() as s:
        snap = _snapshot(await repo.get_window(s, user.id))
    if snap is None or snap.collapsed or snap.awaiting_text != AWAIT or snap.chat_id != message.chat.id:
        return
    fid = message.photo[-1].file_id  # the largest size
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, user.id)
        if ch is None:
            return
        ch.state.flags[FLAG] = {"file_id": fid, "at": time.time()}
        await game.save_active(s, ch, ui.settings.timezone)
    ui.outbox.delete(message.chat.id, message.message_id)
    ctx = Ctx(ui, user.id, user.username, user.full_name or "", message.chat.id, snap)
    await ui.windows.show(user.id, await avatar_screen(ctx, "Аватар сохранён."), message.chat.id)
