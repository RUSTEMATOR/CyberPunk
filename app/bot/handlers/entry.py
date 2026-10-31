"""Player entry points: /play (/menu, /start), /help, button presses, typed text."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.bot import commands, texts_misc
from app.bot.handlers.onboarding import entry_screen
from app.bot.ui.callbacks import make_ctx, process_callback, process_text
from app.bot.ui.kit import UiKit

play_router = Router(name="play")
text_router = Router(name="text_input")


@play_router.message(Command(*commands.PLAY_COMMANDS))
async def cmd_play(message: Message, ui: UiKit) -> None:
    """Open the player's window at the bottom of the chat; the old one is deleted."""
    user = message.from_user
    if user is None:
        return
    ctx = await make_ctx(ui, user, message.chat.id)
    await ui.windows.open(message.chat.id, user.id, await entry_screen(ctx))


@play_router.message(Command("help"))
async def cmd_help(message: Message, ui: UiKit) -> None:
    ui.reply_temp(message.chat.id, texts_misc.help_text(ui.content.balance), ui.cfg.help_ttl_s)


@play_router.callback_query()
async def on_button(query: CallbackQuery, ui: UiKit) -> None:
    await process_callback(ui, query)


@text_router.message(F.text)
async def on_text(message: Message, ui: UiKit) -> None:
    """Must be included last: it takes every text message and passes the ones nobody waits for."""
    if await process_text(ui, message):
        return
    from app.bot.handlers import chatgames  # owner request: words of the chat games

    await chatgames.on_text(ui, message)
