"""Screen registry and the dispatch of button presses and typed text (PLAN 3.0).

Handlers register under a screen name:

    @on_screen("menu")
    async def menu(ctx: Ctx, action: str, arg: str | None) -> Screen | None: ...

    @on_text("char_name")
    async def typed_name(ctx: Ctx, text: str) -> Screen | None: ...

A handler returns the next `Screen` (shown in the player's window) or None (nothing to change).
Before a handler runs, `process_callback` makes sure the pressed message is the CURRENT window of
the player who pressed it, and that the button belongs to the screen the window shows now; so a
button pressed twice, or an old button, never runs twice.
"""

from __future__ import annotations

import functools
from contextvars import ContextVar

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from aiogram.types import CallbackQuery, Message

from app.bot import texts
from app.bot.ui import callbacks_codec as codec
from app.bot.ui.screen import Screen
from app.db import repo
from app.db import session as db

if TYPE_CHECKING:
    from app.bot.ui.kit import UiKit
    from app.content.loader import Content

log = logging.getLogger(__name__)


async def _safe_answer(query, *args, **kwargs) -> None:  # noqa: ANN001
    """Answer a callback query; a stale query (bot restarted, >15 s old) must not abort the click."""
    try:
        await query.answer(*args, **kwargs)
    except Exception as exc:  # TelegramBadRequest "query is too old" and network hiccups
        log.info("callback answer skipped: %s", exc)


@dataclass
class WindowSnapshot:
    """A detached copy of a `windows` row."""

    player_tg_id: int
    chat_id: int
    message_id: int
    screen: str
    state: dict[str, Any] = field(default_factory=dict)
    awaiting_text: str | None = None
    collapsed: bool = False


def _snapshot(row: Any) -> WindowSnapshot | None:
    if row is None:
        return None
    return WindowSnapshot(
        row.player_tg_id, row.chat_id, row.message_id, row.screen,
        dict(row.screen_state or {}), row.awaiting_text, bool(row.collapsed),
    )


@dataclass
class Ctx:
    """Everything a screen handler needs about who pressed what."""

    ui: "UiKit"
    user_id: int
    username: str | None
    full_name: str
    chat_id: int
    window: WindowSnapshot | None = None

    @property
    def content(self) -> "Content":
        return self.ui.content

    @property
    def state(self) -> dict[str, Any]:
        """The working state of the screen the window shows now."""
        return self.window.state if self.window else {}

    async def show(self, screen: Screen) -> None:
        await self.ui.windows.show(self.user_id, screen, self.chat_id)

    async def open_window(self, screen: Screen) -> None:
        await self.ui.windows.open(self.chat_id, self.user_id, screen)


ScreenHandler = Callable[[Ctx, str, "str | None"], Awaitable["Screen | None"]]
TextHandler = Callable[[Ctx, str], Awaitable["Screen | None"]]

_screens: dict[str, ScreenHandler] = {}
_texts: dict[str, TextHandler] = {}


# owner report 29.10 («не знает, почему умер»): the button that is being handled, for the zero-hp and death records
LAST_ACT: ContextVar[str] = ContextVar("last_act", default="")


def on_screen(name: str) -> Callable[[ScreenHandler], ScreenHandler]:
    def deco(fn: ScreenHandler) -> ScreenHandler:
        if name in _screens:
            raise ValueError(f"screen {name!r} is already registered")

        @functools.wraps(fn)
        async def traced(ctx, action, arg):  # noqa: ANN001, ANN202
            if name not in ("menu", "deathoffer", "deathback", "death"):
                LAST_ACT.set(f"{name}:{action}")
            return await fn(ctx, action, arg)

        _screens[name] = traced
        return fn

    return deco


_keep_texts: set[str] = set()  # owner request: lines typed to an NPC stay in the chat as the character's words


def on_text(key: str, *, keep: bool = False) -> Callable[[TextHandler], TextHandler]:
    def deco(fn: TextHandler) -> TextHandler:
        if key in _texts:
            raise ValueError(f"text handler {key!r} is already registered")

        @functools.wraps(fn)
        async def traced(ctx, text):  # noqa: ANN001, ANN202
            LAST_ACT.set(f"text:{key}")
            return await fn(ctx, text)

        _texts[key] = traced
        if keep:
            _keep_texts.add(key)
        return fn

    return deco


def has_screen(name: str) -> bool:
    return name in _screens


async def goto(ctx: Ctx, name: str, action: str = "open", arg: str | None = None) -> Screen | None:
    """Call another screen's handler directly (a 'Back' button handing over to the menu)."""
    return await _screens[name](ctx, action, arg)


async def make_ctx(ui: "UiKit", user: Any, chat_id: int, with_window: bool = True) -> Ctx:
    window = None
    if with_window:
        async with db.session_scope() as s:
            window = _snapshot(await repo.get_window(s, user.id))
    return Ctx(ui, user.id, getattr(user, "username", None), getattr(user, "full_name", "") or "", chat_id, window)


async def process_callback(ui: "UiKit", query: CallbackQuery) -> None:
    """Route a button press. Refusals are answered with a toast; nothing else changes."""
    user = query.from_user
    msg = query.message
    data = codec.decode(query.data)
    if data is None or msg is None:
        await _safe_answer(query, texts.WINDOW_STALE)
        return
    chat_id, message_id = msg.chat.id, msg.message_id
    async with db.session_scope() as s:
        snap = _snapshot(await repo.get_window_by_message(s, chat_id, message_id))
        if snap is not None and snap.player_tg_id == user.id and not snap.collapsed:
            await repo.touch_window(s, user.id)
    if snap is None or snap.collapsed:
        ui.windows.collapse_message(chat_id, message_id)
        await _safe_answer(query, texts.WINDOW_STALE)
        return
    if snap.player_tg_id != user.id:
        await _safe_answer(query, texts.NOT_YOUR_WINDOW, show_alert=False)
        return
    await _safe_answer(query)
    if data.screen != snap.screen:
        return  # a button of a screen that has already been replaced: ignore it
    handler = _screens.get(data.screen)
    if handler is None:
        log.warning("no handler for screen %r", data.screen)
        handler = _screens["menu"]
        data = codec.CallbackData("menu", "open")
    ctx = Ctx(ui, user.id, user.username, user.full_name or "", chat_id, snap)
    if data.screen not in ("deathoffer", "deathback") and "deathoffer" in _screens:
        from app.services import death as death_service  # local: services import the UI texts

        async with db.session_scope() as s:
            waiting = await death_service.has_pending(s, user.id)
        if waiting:  # a failed death save comes before everything else
            handler, data = _screens["deathoffer"], codec.CallbackData("deathoffer", "open")
    from app.bot.ui.window import GROUP_ACTION  # owner request 27.10: the player's own action in the chat

    token = GROUP_ACTION.set(True)
    try:
        screen = await handler(ctx, data.action, data.arg)
        if screen is not None:
            await ui.windows.show(user.id, screen, chat_id)
    finally:
        GROUP_ACTION.reset(token)


async def process_text(ui: "UiKit", message: Message) -> bool:
    """Hand a typed message to the text handler its author's window waits for.

    Returns True when the message was taken (and then it is deleted), False when nobody waits for it.
    """
    user = message.from_user
    text = message.text
    if user is None or not text or text.startswith("/"):
        return False
    async with db.session_scope() as s:
        snap = _snapshot(await repo.get_window(s, user.id))
    if snap is None or snap.collapsed or not snap.awaiting_text or snap.chat_id != message.chat.id:
        return False
    handler = _texts.get(snap.awaiting_text)
    if handler is None:
        return False
    if snap.awaiting_text not in _keep_texts:
        ui.outbox.delete(message.chat.id, message.message_id)
    ctx = Ctx(ui, user.id, user.username, user.full_name or "", message.chat.id, snap)
    from app.bot.ui.window import GROUP_ACTION  # owner request 27.10: the player's own action in the chat

    token = GROUP_ACTION.set(True)
    try:
        screen = await handler(ctx, text)
        if screen is not None:
            await ui.windows.show(user.id, screen, message.chat.id)
        else:
            await ui.windows.touch(user.id)
    finally:
        GROUP_ACTION.reset(token)
    return True
