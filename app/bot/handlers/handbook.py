"""The handbook (owner request): all commands and mechanics, read inside the window. /faq or «Профиль» → «Справочник»."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.bot import texts
from app.bot import texts_handbook as H
from app.bot.handlers.menu import menu_screen
from app.bot.ui.callbacks import Ctx, make_ctx, on_screen
from app.bot.ui.kit import UiKit
from app.bot.ui.screen import Button, Screen, pack

router = Router(name="handbook")


def index_screen(ctx: Ctx) -> Screen:
    rows = pack([Button(label, "t", key) for key, label, _ in H.TOPICS], ctx.content.balance.ui.button_chars,
                max_per_row=2)
    rows.append([Button(texts.MENU, "menu")])
    return Screen(H.TITLE + "\n" + H.INTRO, rows, name="handbook")


def page_screen(ctx: Ctx, key: str) -> Screen:
    keys = [k for k, _, _ in H.TOPICS]
    i = keys.index(key)
    nav = []
    if i > 0:
        nav.append(Button("← " + H.TOPICS[i - 1][1], "t", keys[i - 1]))
    if i + 1 < len(keys):
        nav.append(Button(H.TOPICS[i + 1][1] + " →", "t", keys[i + 1]))
    rows = [[b] for b in nav] + [[Button("Все разделы", "index"), Button(texts.MENU, "menu")]]
    return Screen(H.handbook_page(key, ctx.content.balance), rows, name="handbook")


@on_screen("handbook")
async def handbook_handler(ctx: Ctx, action: str, arg: str | None) -> Screen | None:
    if action == "menu":
        return await menu_screen(ctx)
    if action == "t" and arg and H.topic(arg) is not None:
        return page_screen(ctx, arg)
    return index_screen(ctx)


@router.message(Command("faq"))
async def cmd_faq(message: Message, ui: UiKit) -> None:
    user = message.from_user
    if user is None:
        return
    ctx = await make_ctx(ui, user, message.chat.id)
    await ui.windows.open(message.chat.id, user.id, index_screen(ctx))


