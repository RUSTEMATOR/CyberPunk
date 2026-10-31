"""Commands that are in the "/" menu but have no handler yet: a temporary "Скоро".

The router is included after every real command router, so a milestone that adds a real handler
for `/who` or `/daily` simply registers it earlier and the stub stops answering.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.bot import commands
from app.bot.ui.kit import UiKit


def build_soon_router() -> Router:
    router = Router(name="soon_commands")
    names = commands.soon_command_names()
    if not names:  # every command of the menu has a handler
        return router

    @router.message(Command(*names))
    async def soon(message: Message, ui: UiKit) -> None:
        ui.reply_temp(message.chat.id, commands.SOON)

    return router
