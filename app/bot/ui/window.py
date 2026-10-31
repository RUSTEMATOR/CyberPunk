"""WindowManager: one editable message per player (DESIGN.md section 3, architecture rule 8).

The `windows` table holds, per player, the chat and message id of the window, the screen it shows
and the state of that screen. The DB row is updated at once; the Telegram edit goes through the
outbox, which coalesces a burst of edits into the last one. If an edit finds the message gone, the
window is sent again at the bottom of the chat.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from datetime import timedelta
from typing import Awaitable, Callable, Sequence

from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from app.bot import texts
from app.bot.ui import rich as rich_ui
from app.bot.ui.outbox import OpResult, Outbox
from app.bot.ui.screen import DEFAULT_BUTTON_CHARS, Screen, render
from app.clock import utcnow
from app.db import repo
from app.db import session as db

log = logging.getLogger(__name__)

_EMPTY_KEYBOARD = InlineKeyboardMarkup(inline_keyboard=[])
Rich = tuple[str, InlineKeyboardMarkup] | None
PICS_KEY = "pics"  # world_state: the pictures over windows (survive a restart, so none is left behind)
APP_KEY = "app_windows"  # owner request 27.10: players who play from the Mini App; their window stays out of the group
# True while the update came from the player's own action in a chat (a button, a typed line, /play): the window
# comes back into the chat then. Set by app/bot/ui/callbacks.py and the /play command.
GROUP_ACTION: "contextvars.ContextVar[bool]" = contextvars.ContextVar("group_action", default=False)


def from_app() -> bool:
    """The update came from the Mini App (app/webapp/origin.py)."""
    try:
        from app.webapp.origin import ORIGIN
    except ImportError:
        return False
    return ORIGIN.get() == "webapp"


def _with_rows(markup: InlineKeyboardMarkup, extra: list[tuple[str, str, str | None]]) -> InlineKeyboardMarkup:
    """The keyboard plus the buttons a screen put into its text (plain windows show them under the text)."""
    if not extra:
        return markup
    rows = list(markup.inline_keyboard)
    rows[:0] = [[InlineKeyboardButton(text=label, callback_data=data, style=style)] for label, data, style in extra]
    return InlineKeyboardMarkup(inline_keyboard=rows)


class WindowManager:
    def __init__(
        self,
        outbox: Outbox,
        *,
        max_screen_chars: int,
        idle_minutes: int,
        sweep_interval_s: int,
        lock_for: Callable[[int], asyncio.Lock] | None = None,
        button_chars: Sequence[int] = DEFAULT_BUTTON_CHARS,
        rich: bool = False,
    ) -> None:
        self.rich = rich
        self.auto_delete_minutes = 0  # set by the kit from balance.ui.auto_delete_minutes
        self._outbox = outbox
        self._max_chars = max_screen_chars
        self._button_chars = tuple(button_chars)
        self._idle = timedelta(minutes=idle_minutes)
        self._sweep_interval = sweep_interval_s
        self._lock_for = lock_for
        self._sweeper: asyncio.Task[None] | None = None
        self._bg: set[asyncio.Task[None]] = set()
        self._owners: dict[int, str] = {}  # tg user id -> Telegram display name, for the window header
        # owner request 26.10: the picture over a window (Screen.picture): tg user id -> {"c", "m", "keep"}
        self._pics: dict[int, dict] | None = None
        self._app: set[int] | None = None  # owner request 27.10: the players whose window lives only in the Mini App
        self.pic_ttl_s = 3 * 3600.0  # set by the kit from balance.look.pic_ttl_h

    def set_owner(self, user_id: int, display_name: str) -> None:
        """Remember whose window this is; called by the middleware on every update from the player."""
        if display_name:
            self._owners[user_id] = display_name

    def _render(self, user_id: int, screen: Screen) -> tuple[str, InlineKeyboardMarkup, Rich]:
        """Screen text with an owner header, so players in a shared group see whose window it is.

        Returns the plain text and keyboard (the fallback) and, for a rich window, (rich HTML, keyboard)."""
        text, markup = render(screen, self._max_chars - 120, self._button_chars)
        head = texts.window_header(self._owners.get(user_id), user_id)
        if not self.rich:
            plain, extra = rich_ui.split_inline_buttons(text)
            return head + plain, _with_rows(markup, extra), None
        plain, extra = rich_ui.split_inline_buttons(text)
        body = "<p>" + head.strip().replace("\n", "<br/>") + "</p>" + rich_ui.to_rich_html(text)
        return head + plain, _with_rows(markup, extra), (body, markup)

    # -- open / show -----------------------------------------------------------

    async def open(self, chat_id: int, user_id: int, screen: Screen) -> int | None:
        """Send a fresh window at the bottom of the chat and delete the player's old one.

        Returns the new message id, or None when the send failed (the old window stays).
        """
        if from_app():  # owner request 27.10: playing from the Mini App, nothing opens in the chat
            await self._app_save(user_id, chat_id, screen)
            return None
        await self._app_leave(user_id)
        if screen.picture is None or (screen.picture_private and chat_id < 0):
            await self.drop_picture(user_id)
        else:
            await self._picture(user_id, screen, chat_id, fresh=True)
        return await self._open(chat_id, user_id, screen)

    async def _open(self, chat_id: int, user_id: int, screen: Screen) -> int | None:
        text, markup, rich = self._render(user_id, screen)
        res = await self._outbox.send(chat_id, text, markup, rich)
        if not res.ok or res.message_id is None:
            log.warning("window: could not open for %s in %s: %s", user_id, chat_id, res.error)
            return None
        async with db.session_scope() as s:
            old = await repo.get_window(s, user_id)
            old_ref = (old.chat_id, old.message_id) if old is not None else None
            await repo.save_window(
                s, user_id, chat_id, res.message_id, screen.name, screen.state, screen.awaiting_text
            )
        if old_ref is not None and old_ref != (chat_id, res.message_id):
            self._outbox.delete(*old_ref)
        return res.message_id

    async def show(self, user_id: int, screen: Screen, chat_id: int | None = None) -> None:
        """Put `screen` into the player's window. Without a window it is opened in `chat_id`."""
        if await self._app_window(user_id, screen, chat_id):
            return
        text, markup, rich = self._render(user_id, screen)
        async with db.session_scope() as s:
            row = await repo.get_window(s, user_id)
            if row is None:
                target = None
            else:
                target = (row.chat_id, row.message_id)
                await repo.save_window(
                    s, user_id, row.chat_id, row.message_id, screen.name, screen.state, screen.awaiting_text
                )
        if target is None and chat_id is None:
            raise LookupError(f"player {user_id} has no window and no chat to open one in")
        if target is not None and not target[1]:  # the window lived only in the Mini App: it opens anew in the chat
            target = None
        where = target[0] if target is not None else int(chat_id)  # type: ignore[arg-type]
        if await self._picture(user_id, screen, where) or target is None:
            await self._open(where, user_id, screen)  # a new picture went out: the window opens again right under it
            return
        fut = self._outbox.edit(target[0], target[1], text, markup, rich)
        fut.add_done_callback(lambda f: self._after_edit(f, user_id, target, screen))

    async def refine(self, user_id: int, token: str, screen: Screen) -> bool:
        """Put a better version of the screen the window still shows (owner request: the narrator's text arrives
        after the numbers). Skipped when the player has moved on: the window's state no longer carries `token`."""
        lock = self._lock_for(user_id) if self._lock_for else None
        if lock is not None:
            async with lock:
                return await self._refine(user_id, token, screen)
        return await self._refine(user_id, token, screen)

    async def _refine(self, user_id: int, token: str, screen: Screen) -> bool:
        screen.state = {**(screen.state or {}), "late": token}
        async with db.session_scope() as s:
            row = await repo.get_window(s, user_id)
            if row is None or row.collapsed or (row.screen_state or {}).get("late") != token:
                return False
            target = (row.chat_id, row.message_id)
            await repo.save_window(s, user_id, row.chat_id, row.message_id, screen.name, screen.state, screen.awaiting_text)
        if await self._in_app(user_id) or not target[1]:  # owner request 27.10: the Mini App shows it, the chat does not
            return True
        text, markup, rich = self._render(user_id, screen)
        fut = self._outbox.edit(target[0], target[1], text, markup, rich, low=True)  # never ahead of a window
        fut.add_done_callback(lambda f: self._after_edit(f, user_id, target, screen))
        return True

    def _after_edit(
        self, fut: "asyncio.Future[OpResult]", user_id: int, target: tuple[int, int], screen: Screen
    ) -> None:
        if fut.cancelled():
            return
        res = fut.result()
        if res.ok or res.superseded or not res.gone:
            return
        task = asyncio.get_running_loop().create_task(self._resend(user_id, target, screen))
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def _resend(self, user_id: int, old: tuple[int, int], screen: Screen) -> None:
        """The window message vanished (someone deleted it): send the screen again and re-point the row."""
        try:
            text, markup, rich = self._render(user_id, screen)
            res = await self._outbox.send(old[0], text, markup, rich)
            if not res.ok or res.message_id is None:
                return
            async with db.session_scope() as s:
                row = await repo.get_window(s, user_id)
                if row is not None and (row.chat_id, row.message_id) == old:
                    await repo.save_window(
                        s, user_id, old[0], res.message_id, row.screen, row.screen_state,
                        row.awaiting_text, row.collapsed,
                    )
                    return
            self._outbox.delete(old[0], res.message_id)  # the player opened a newer window meanwhile
        except Exception:
            log.exception("window: resend failed for %s", user_id)

    # -- the window that lives in the Mini App (owner request 27.10) ----------------

    async def _load_app(self) -> set[int]:
        if self._app is None:
            async with db.session_scope() as s:
                raw = await repo.ws_get(s, APP_KEY, []) or []
            self._app = {int(x) for x in raw}
        return self._app

    async def _store_app(self) -> None:
        async with db.session_scope() as s:
            await repo.ws_set(s, APP_KEY, sorted(self._app or set()))

    async def _in_app(self, user_id: int) -> bool:
        return user_id in await self._load_app()

    async def _app_leave(self, user_id: int) -> None:
        app = await self._load_app()
        if user_id in app:
            app.discard(user_id)
            await self._store_app()

    async def _app_save(self, user_id: int, chat_id: int | None, screen: Screen) -> None:
        """The window row without a message in the chat (message id 0)."""
        async with db.session_scope() as s:
            row = await repo.get_window(s, user_id)
            chat = row.chat_id if row is not None else int(chat_id or 0)
            mid = row.message_id if row is not None else 0
            await repo.save_window(s, user_id, chat, mid, screen.name, screen.state, screen.awaiting_text)
        app = await self._load_app()
        if user_id not in app:
            app.add(user_id)
            await self._store_app()

    async def _app_window(self, user_id: int, screen: Screen, chat_id: int | None) -> bool:
        """True when the chat is left alone: the update came from the Mini App (the first time the window in the chat
        turns into a short note), or the player plays from the app and this is not their own action in the chat."""
        if from_app():
            first = not await self._in_app(user_id)
            async with db.session_scope() as s:
                row = await repo.get_window(s, user_id)
                ref = (row.chat_id, row.message_id) if row is not None else None
            await self._app_save(user_id, chat_id, screen)
            await self.drop_picture(user_id)
            if first and ref is not None and ref[1]:
                name = self._owners.get(user_id) or "Игрок"
                self._outbox.edit(ref[0], ref[1], f"📱 {name} играет в приложении. /play — вернуть окно сюда.",
                                  _EMPTY_KEYBOARD)
            return True
        if await self._in_app(user_id):
            if not GROUP_ACTION.get():
                async with db.session_scope() as s:
                    row = await repo.get_window(s, user_id)
                    if row is not None:
                        await repo.save_window(s, user_id, row.chat_id, row.message_id, screen.name, screen.state,
                                               screen.awaiting_text)
                return row is not None
            await self._app_leave(user_id)
        return False

    # -- the picture over the window ---------------------------------------------

    async def _load_pics(self) -> dict[int, dict]:
        if self._pics is None:
            async with db.session_scope() as s:
                raw = await repo.ws_get(s, PICS_KEY, {}) or {}
            self._pics = {int(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}
        return self._pics

    async def _save_pics(self) -> None:
        async with db.session_scope() as s:
            await repo.ws_set(s, PICS_KEY, {str(k): v for k, v in (self._pics or {}).items()})

    async def drop_picture(self, user_id: int) -> None:
        pics = await self._load_pics()
        rec = pics.pop(user_id, None)
        if rec is None:
            return
        self._outbox.delete(int(rec["c"]), int(rec["m"]))
        await self._save_pics()

    async def _picture(self, user_id: int, screen: Screen, chat_id: int, fresh: bool = False) -> bool:
        """Show, change or take away the picture over the window. True: a new picture was sent, so the window must
        be opened again under it."""
        try:
            pics = await self._load_pics()
            rec = pics.get(user_id)
            if screen.picture_private and chat_id < 0:  # a group: no picture, and an old one goes away
                if rec is not None:
                    await self.drop_picture(user_id)
                return False
            if screen.picture is None:
                if rec is not None and not (rec.get("keep") and screen.name.startswith(str(rec["keep"]))):
                    await self.drop_picture(user_id)
                return False
            photo = BufferedInputFile(screen.picture, filename="scene.png")
            if rec is not None and int(rec["c"]) == chat_id and not fresh:
                if rec.get("keep") != screen.picture_keep:
                    rec["keep"] = screen.picture_keep
                    await self._save_pics()
                fut = self._outbox.edit_photo(chat_id, int(rec["m"]), photo)
                fut.add_done_callback(lambda f, uid=user_id, mid=int(rec["m"]): self._after_photo(f, uid, mid))
                return False
            if rec is not None:
                await self.drop_picture(user_id)
            res = await self._outbox.send_photo(chat_id, photo)
            if not res.ok or res.message_id is None:
                return False
            pics[user_id] = {"c": chat_id, "m": res.message_id, "keep": screen.picture_keep}
            await self._save_pics()
            async with db.session_scope() as s:
                await repo.add_cleanup(s, chat_id, res.message_id, utcnow() + timedelta(seconds=self.pic_ttl_s), "temp")
            return True
        except Exception:  # noqa: BLE001 - a picture never stops the window
            log.exception("window: picture failed for %s", user_id)
            return False

    def _after_photo(self, fut: "asyncio.Future[OpResult]", user_id: int, message_id: int) -> None:
        """The picture message is gone (deleted by hand or by the cleanup): forget it, the next screen sends anew."""
        if fut.cancelled():
            return
        res = fut.result()
        if res.ok or not res.gone or self._pics is None:
            return
        rec = self._pics.get(user_id)
        if rec is not None and int(rec["m"]) == message_id:
            self._pics.pop(user_id, None)
            task = asyncio.get_running_loop().create_task(self._save_pics())
            self._bg.add(task)
            task.add_done_callback(self._bg.discard)

    # -- collapse --------------------------------------------------------------

    async def collapse(self, user_id: int, name: str | None = None) -> bool:
        """Replace the window with '<name> - окно закрыто. /play' and drop its buttons."""
        async with db.session_scope() as s:
            row = await repo.get_window(s, user_id)
            if row is None or row.collapsed:
                return False
            label = name or await repo.display_name(s, user_id)
            target = (row.chat_id, row.message_id)
            row.collapsed = True
            row.awaiting_text = None
        self._outbox.edit(target[0], target[1], texts.window_closed(label), _EMPTY_KEYBOARD)
        await self.drop_picture(user_id)
        return True

    def collapse_message(self, chat_id: int, message_id: int) -> None:
        """Close a message that is not (or no longer) anybody's current window."""
        self._outbox.edit(chat_id, message_id, texts.WINDOW_CLOSED_ANON, _EMPTY_KEYBOARD)

    async def touch(self, user_id: int) -> None:
        async with db.session_scope() as s:
            await repo.touch_window(s, user_id)

    # -- idle sweeper ----------------------------------------------------------

    async def sweep_idle(self) -> int:
        """Collapse windows nobody touched for `window_idle_minutes`. Returns how many were closed."""
        cutoff = utcnow() - self._idle
        async with db.session_scope() as s:
            candidates = [w.player_tg_id for w in await repo.idle_windows(s, cutoff)]
        closed = 0
        for uid in candidates:
            lock = self._lock_for(uid) if self._lock_for else None
            if lock is not None:
                await lock.acquire()
            try:
                async with db.session_scope() as s:  # re-check under the lock: a click may have just happened
                    row = await repo.get_window(s, uid)
                    still_idle = row is not None and not row.collapsed and row.last_active < cutoff
                if still_idle and await self.collapse(uid):
                    closed += 1
            finally:
                if lock is not None:
                    lock.release()
        return closed

    async def sweep_stale(self, after_minutes: int) -> int:
        """Delete closed windows untouched for `after_minutes` (owner request 23.10): the message goes, the row too,
        /play opens a new one."""
        if after_minutes <= 0:
            return 0
        cutoff = utcnow() - timedelta(minutes=after_minutes)
        async with db.session_scope() as s:
            rows = [(w.player_tg_id, w.chat_id, w.message_id) for w in await repo.stale_windows(s, cutoff)]
        done = 0
        for uid, chat_id, mid in rows:
            lock = self._lock_for(uid) if self._lock_for else None
            if lock is not None:
                await lock.acquire()
            try:
                async with db.session_scope() as s:
                    row = await repo.get_window(s, uid)
                    if row is None or not row.collapsed or row.last_active >= cutoff or row.message_id != mid:
                        continue
                    await s.delete(row)
                self._outbox.delete(chat_id, mid)
                done += 1
            finally:
                if lock is not None:
                    lock.release()
        return done

    async def _sweep_loop(self) -> None:
        while True:
            await asyncio.sleep(self._sweep_interval)
            try:
                await self.sweep_idle()
                await self.sweep_stale(self.auto_delete_minutes)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("window: idle sweep failed")

    def start(self) -> None:
        if self._sweeper is None or self._sweeper.done():
            self._sweeper = asyncio.get_running_loop().create_task(self._sweep_loop())

    async def stop(self) -> None:
        if self._sweeper is not None:
            self._sweeper.cancel()
            await asyncio.gather(self._sweeper, return_exceptions=True)
            self._sweeper = None
        if self._bg:
            await asyncio.gather(*list(self._bg), return_exceptions=True)
