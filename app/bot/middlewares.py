"""The gate in front of every game update (PLAN 3.0, architecture rule 9).

- The bot plays in the group saved by /admin_setgroup (or GROUP_CHAT_ID from .env) and (owner request 26.10,
  balance.dm_relay.private_play) in the bot's private chat. In other groups it answers "Игра идёт в группе".
- A player's command is deleted right after it has been handled.
- One lock per player: their updates run one at a time, so five fast clicks on one button run once
  (the second click meets an already replaced screen).
- Any exception ends in the error screen in the player's window (or a temporary message for a
  command) and a log entry, never in silence.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.bot import texts
from app.bot.ui.common import error_screen
from app.bot.ui.kit import UiKit
from app.services import world as world_service

log = logging.getLogger(__name__)

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]

SETGROUP_COMMAND = "admin_setgroup"


def command_name(message: Message, bot_username: str | None) -> str | None:
    """The lower-case command of a message addressed to this bot (`/play`, `/play@thisbot`), else None."""
    text = message.text or ""
    if not text.startswith("/"):
        return None
    entities = message.entities or []
    if not any(e.type == "bot_command" and e.offset == 0 for e in entities):
        return None
    head = text.split(maxsplit=1)[0][1:]
    name, _, mention = head.partition("@")
    if mention and bot_username and mention.lower() != bot_username.lower():
        return None
    return name.lower() or None


class GameMiddleware(BaseMiddleware):
    def __init__(self, ui: UiKit) -> None:
        self.ui = ui

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        if isinstance(event, Message):
            return await self._message(handler, event, data)
        if isinstance(event, CallbackQuery):
            return await self._callback(handler, event, data)
        return await handler(event, data)

    # -- messages ------------------------------------------------------------------------

    async def _message(self, handler: Handler, event: Message, data: dict[str, Any]) -> Any:
        ui = self.ui
        user = event.from_user
        if user is None or user.is_bot:
            return None
        ui.windows.set_owner(user.id, user.full_name)
        chat = event.chat
        cmd = command_name(event, ui.bot_username)
        private = chat.type == "private"
        if private and not ui.content.balance.dm_relay.private_play:
            ui.outbox.send(chat.id, texts.PRIVATE_ONLY)
            return None
        if not private and chat.type not in ("group", "supergroup"):
            return None
        setgroup = cmd == SETGROUP_COMMAND and (ui.is_admin(user.id) or ui.can_bootstrap_admin())
        if not setgroup and not private:  # owner request 26.10: the bot's private chat is a game window too
            if ui.group_id is None:
                if cmd:
                    ui.reply_temp(chat.id, texts.NO_GROUP)
                return None
            if chat.id != ui.group_id:
                if cmd:
                    ui.reply_temp(chat.id, texts.PRIVATE_ONLY)
                return None
        async with ui.lock_for(user.id):
            actor = world_service.set_actor(user.id)  # the date of this player drives the year of everything he does
            try:
                return await handler(event, data)
            except Exception:
                log.exception("update failed: %r", event.text)
                await self._report_error(chat.id, user.id, as_window=cmd is None)
                return None
            finally:
                world_service.reset_actor(actor)
                # the command is deleted once handled; /admin_setgroup may have just made this the game group
                if cmd and chat.id == ui.group_id:
                    ui.outbox.delete(chat.id, event.message_id)

    async def _report_error(self, chat_id: int, user_id: int, as_window: bool) -> None:
        ui = self.ui
        try:
            if as_window:
                await ui.windows.show(user_id, error_screen(), chat_id)
            else:
                ui.reply_temp(chat_id, texts.ERROR_TEMP)
        except Exception:
            log.exception("could not show the error screen")

    # -- callbacks -----------------------------------------------------------------------

    async def _callback(self, handler: Handler, event: CallbackQuery, data: dict[str, Any]) -> Any:
        ui = self.ui
        user = event.from_user
        msg = event.message
        if user is not None and not user.is_bot:
            ui.windows.set_owner(user.id, user.full_name)
        private_ok = (msg is not None and user is not None and msg.chat.type == "private" and msg.chat.id == user.id
                      and ui.content.balance.dm_relay.private_play)
        if not private_ok and (msg is None or ui.group_id is None or msg.chat.id != ui.group_id):
            await event.answer(texts.PRIVATE_ONLY)
            return None
        async with ui.lock_for(user.id):
            actor = world_service.set_actor(user.id)
            try:
                return await handler(event, data)
            except Exception:
                log.exception("callback failed: %r", event.data)
                try:
                    await event.answer()
                except Exception:
                    pass
                await self._report_error(msg.chat.id, user.id, as_window=True)
                return None
            finally:
                world_service.reset_actor(actor)
