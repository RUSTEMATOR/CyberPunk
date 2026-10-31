"""UiKit: the bundle of UI services every handler reaches through `ctx.ui` (outbox, cleanup, windows, locks)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup

from app.bot.ui.cleanup import KIND_ALERT, KIND_NEWS, KIND_TEMP, Cleanup
from app.bot.ui.outbox import Outbox
from app.bot.ui.window import WindowManager
from app.config import Settings
from app.content.loader import Content
from app.db import repo
from app.db import session as db
from app.engine.feed import ImageRequest
from app.services import images

log = logging.getLogger(__name__)

GROUP_KEY = "group_chat_id"
BOOTSTRAP_ADMINS_KEY = "bootstrap_admin_ids"


class UiKit:
    def __init__(self, bot: Bot, content: Content, settings: Settings) -> None:
        self.bot = bot
        self.content = content
        self.settings = settings
        cfg = content.balance.ui
        self.cfg = cfg
        self.outbox = Outbox(bot, cfg.max_ops_per_minute)
        self.cleanup = Cleanup(self.outbox, cfg.cleanup_interval_s, cfg.cleanup_batch)
        self._locks: dict[int, asyncio.Lock] = {}
        self.windows = WindowManager(
            self.outbox,
            max_screen_chars=cfg.max_screen_chars,
            idle_minutes=cfg.window_idle_minutes,
            sweep_interval_s=cfg.idle_sweep_interval_s,
            lock_for=self.lock_for,
            button_chars=cfg.button_chars,
            rich=cfg.rich_windows,
        )
        self.windows.auto_delete_minutes = cfg.auto_delete_minutes
        self.windows.pic_ttl_s = float(content.balance.look.pic_ttl_h) * 3600
        self.images = images.Library(settings.images_path)
        self.group_id: int | None = None
        self.bootstrap_admins: set[int] = set()  # admins claimed via first /admin_setgroup when ADMIN_IDS is empty
        self.bot_username: str | None = None
        self._bg: set[asyncio.Task[Any]] = set()

    # -- per-user lock -----------------------------------------------------------

    def lock_for(self, user_id: int) -> asyncio.Lock:
        lock = self._locks.get(user_id)
        if lock is None:
            lock = self._locks[user_id] = asyncio.Lock()
        return lock

    # -- the game group ------------------------------------------------------------

    async def load_group_id(self) -> int | None:
        """world_state.group_chat_id (set by /admin_setgroup), else GROUP_CHAT_ID from .env."""
        async with db.session_scope() as s:
            stored = await repo.ws_get(s, GROUP_KEY)
        self.group_id = int(stored) if stored is not None else self.settings.group_chat_id
        async with db.session_scope() as s:
            claimed = await repo.ws_get(s, BOOTSTRAP_ADMINS_KEY)
        self.bootstrap_admins = {int(x) for x in (claimed or [])}
        return self.group_id

    async def set_group_id(self, chat_id: int) -> None:
        async with db.session_scope() as s:
            await repo.ws_set(s, GROUP_KEY, chat_id)
        self.group_id = chat_id

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.settings.admin_ids or user_id in self.bootstrap_admins

    def can_bootstrap_admin(self) -> bool:
        """No admin configured anywhere yet: the first /admin_setgroup in a group claims admin."""
        return not self.settings.admin_ids and not self.bootstrap_admins

    async def claim_bootstrap_admin(self, user_id: int) -> None:
        self.bootstrap_admins.add(user_id)
        async with db.session_scope() as s:
            await repo.ws_set(s, BOOTSTRAP_ADMINS_KEY, sorted(self.bootstrap_admins))
        log.warning("ADMIN_IDS is empty: user %s claimed admin via /admin_setgroup", user_id)

    # -- temporary messages ----------------------------------------------------------

    def spawn(self, coro: Coroutine[Any, Any, Any]) -> None:
        """Run a coroutine in the background without blocking the user lock; failures are logged."""
        task = asyncio.get_running_loop().create_task(coro)
        self._bg.add(task)

        def _done(t: "asyncio.Task[Any]") -> None:
            self._bg.discard(t)
            if not t.cancelled() and t.exception() is not None:
                log.error("background task failed", exc_info=t.exception())

        task.add_done_callback(_done)

    def reply_temp(
        self, chat_id: int, text: str, ttl_s: float | None = None, kind: str = KIND_TEMP,
        markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        """Post a message that deletes itself (default balance.ui.temp_message_ttl_s). Fire and forget.

        With a `markup` the buttons belong to the message, not to a player window (the /legends switch)."""
        self.spawn(self._send_temp(chat_id, text, ttl_s, kind, markup))

    async def _send_temp(
        self, chat_id: int, text: str, ttl_s: float | None, kind: str, markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        res = await self.outbox.send(chat_id, text, markup)
        if res.ok and res.message_id is not None:
            delay = self.cfg.temp_message_ttl_s if ttl_s is None else ttl_s
            await self.cleanup.schedule_delete(chat_id, res.message_id, self.cap_ttl(delay, kind), kind)

    async def roll_dice(self, chat_id: int, emoji: str) -> int | None:
        """Send an animated Telegram dice into the chat and return the face it rolled (None when the send failed:
        the caller then rolls with its own rng). The dice message is a temporary one: it is deleted after
        `ui.temp_message_ttl_s`. Wait `reveal()` before showing a result, so the animation lands first."""
        res = await self.outbox.send_dice(chat_id, emoji)
        if not res.ok or res.message_id is None or res.dice_value is None:
            log.warning("dice: send failed in chat %s (%s)", chat_id, res.error)
            return None
        await self.cleanup.schedule_delete(chat_id, res.message_id, self.cfg.temp_message_ttl_s, KIND_TEMP)
        return int(res.dice_value)

    async def reveal(self) -> None:
        """Let a dice that was just sent finish its animation (balance.ui.dice_reveal_s)."""
        if self.cfg.dice_reveal_s > 0:
            await asyncio.sleep(self.cfg.dice_reveal_s)

    def cap_ttl(self, ttl_s: float | None, kind: str = KIND_TEMP) -> float | None:
        """Owner request 23.10: no bot message lives longer than `ui.auto_delete_minutes` (None = for ever when off).
        The city feed and the digests (kinds news and alert) are the exception: they keep their own lifetime."""
        cap = self.cfg.auto_delete_minutes * 60
        if not cap or kind in (KIND_NEWS, KIND_ALERT):
            return ttl_s
        return cap if ttl_s is None else min(ttl_s, cap)

    def post(self, text: str, ttl_s: float | None = None, kind: str = KIND_TEMP) -> None:
        """A post into the game group that deletes itself after `ttl_s` (the city feed and public results).

        Nothing happens before a group is set. Fire and forget.
        """
        if self.group_id is None:
            return
        self.spawn(self._send_temp(self.group_id, text, ttl_s, kind))
        self._relay(text, self.cap_ttl(self.cfg.temp_message_ttl_s if ttl_s is None else ttl_s, kind), kind)

    def _relay(self, text: str, ttl_s: float | None, kind: str) -> None:
        """Owner request 26.10: the same post in private for the players outside the group (services/dm_relay)."""
        from app.services import dm_relay

        self.spawn(dm_relay.relay(self, text, ttl_s, kind))

    async def send_feed(
        self, text: str, ttl_s: float | None, kind: str, image: ImageRequest | None = None,
    ) -> int | None:
        """A post of the city feed into the game group. `ttl_s` None = permanent (an obituary is never deleted).

        With an `image` request the post is a picture with the text as its caption (or the text right after it when
        it is too long for a caption); without a fitting picture, or when sending it fails, it is plain text.
        Returns the message id, or None when nothing was sent (no group, or the send failed)."""
        if self.group_id is None:
            return None
        ttl_s = self.cap_ttl(ttl_s, kind)
        self._relay(text, ttl_s, kind)
        if image is not None:
            try:
                sent = await images.send_post(self, image, text, ttl_s, kind)
            except Exception:  # a broken picture must never cost the post
                log.exception("city feed: picture failed, posting plain text")
                sent = None
            if sent is not None:
                return sent
        res = await self.outbox.send(self.group_id, text, None)
        if not res.ok or res.message_id is None:
            log.warning("city feed: post failed (%s)", res.error)
            return None
        if ttl_s is not None:
            await self.cleanup.schedule_delete(self.group_id, res.message_id, ttl_s, kind)
        return res.message_id

    async def settle(self) -> None:
        """Wait for background tasks and the outbox (shutdown, scratch scripts)."""
        while self._bg:
            await asyncio.gather(*list(self._bg), return_exceptions=True)
        await self.outbox.drain()

    # -- lifecycle ----------------------------------------------------------------------

    async def start(self) -> None:
        me = await self.bot.get_me()
        self.bot_username = (me.username or "").lower() or None
        await self.load_group_id()
        await self.cleanup.pickup()
        self.cleanup.start()
        self.windows.start()

    async def stop(self) -> None:
        await self.windows.stop()
        await self.cleanup.stop()
        await self.settle()
        await self.outbox.close()
