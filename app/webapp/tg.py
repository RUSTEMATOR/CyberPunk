"""Telegram side of the Mini App: `/app` in the game group. The private-chat entry is the menu button that
activation.py sets once the app is reachable."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.webapp import activation
from app.webapp import settings as wsettings

APP_BUTTON = "Открыть Найт-Сити"
APP_GROUP = "Мини-приложение: твоё окно из группы, синхронно с ним. Кнопка ниже."
APP_NOT_READY = "Мини-приложение ещё не подключено."

router = Router(name="webapp")


@router.message(Command("app"))
async def cmd_app(message: Message, ui) -> None:  # noqa: ANN001
    """In a group Telegram allows only URL buttons: the BotFather direct link opens the app at once; without it the
    link opens the private chat with the bot, where the menu button opens the app."""
    if not activation.is_live():
        ui.reply_temp(message.chat.id, APP_NOT_READY)
        return
    link = wsettings.get().direct_link.strip() or f"https://t.me/{ui.bot_username}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=APP_BUTTON, url=link)]])
    ui.reply_temp(message.chat.id, APP_GROUP, markup=kb)
