"""Outbox: every send, edit and delete in the game chat goes through here (DESIGN.md section 3).

Telegram lets a bot post about 20 messages a minute into a group. The outbox keeps one queue per
chat and spends at most `balance.ui.max_ops_per_minute` operations a minute on it (a sliding 60 s
window). Edits of one message are coalesced: only the latest text and keyboard stay queued, so a
burst of button presses ends in the final state, not in every step. Interactive operations (send,
edit) go before deletes. `RetryAfter` is waited out and the operation is repeated; "message is not
modified" is not an error.

Every method returns a Future that resolves to an `OpResult` and never raises, so a caller that
does not care can ignore it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import InlineKeyboardMarkup, InputMediaPhoto, InputRichMessage

from app.bot.ui import names

log = logging.getLogger(__name__)

WINDOW_S = 60.0
# Owner reports 23.10: one budget of 15 a minute for everything made the windows wait; edits without a budget made
# Telegram answer RetryAfter every few minutes (12-38 s for the whole chat). Now: sends and edits share the budget,
# a late refinement of a screen and the deletes only go while there is headroom, so they never take a window's slot.
LOW_HEADROOM = 4        # a late refinement (the narrator's text) waits until this many slots are free
DELETE_HEADROOM = 7     # a delete waits until this many slots are free
PRIO_LOW = 1            # a late refinement: after the interactive ones, before the deletes
MAX_RETRIES = 3
NET_RETRY_DELAY_S = 2.0

PRIO_INTERACTIVE = 0  # send, edit
PRIO_DELETE = 2

_GONE_MARKERS = (
    "message to edit not found",
    "message to delete not found",
    "message can't be edited",
    "message can't be deleted",
    "message_id_invalid",
    "message identifier is not specified",
    "chat not found",
)


@dataclass(frozen=True)
class OpResult:
    ok: bool
    message_id: int | None = None  # for a send: the new message
    error: str | None = None
    gone: bool = False  # the target message does not exist or can no longer be touched
    retryable: bool = False  # a network failure: trying again later may work
    superseded: bool = False  # the operation was dropped because a later one replaced it
    file_id: str | None = None  # for a photo: the Telegram file_id of the uploaded picture (cached by services/images)
    dice_value: int | None = None  # for a dice: the face Telegram rolled (🎲 1..6, 🎰 1..64)


class _Bot(Protocol):
    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> Any: ...
    async def edit_message_text(self, text: str | None = None, **kwargs: Any) -> Any: ...
    async def send_photo(self, chat_id: int, photo: Any, **kwargs: Any) -> Any: ...
    async def send_dice(self, chat_id: int, **kwargs: Any) -> Any: ...
    async def edit_message_media(self, media: Any, **kwargs: Any) -> Any: ...
    async def delete_message(self, chat_id: int, message_id: int) -> Any: ...


@dataclass
class _Op:
    kind: str  # send | edit | delete | photo | dice (`text` is then the dice emoji) | media (a new photo in a message)
    chat_id: int
    message_id: int | None
    text: str | None
    markup: InlineKeyboardMarkup | None
    priority: int
    seq: int
    future: "asyncio.Future[OpResult]"
    attempts: int = 0
    photo: Any = None  # for kind "photo": a file_id string or an input file; `text` is then the caption
    rich: "tuple[str, InlineKeyboardMarkup | None] | None" = None  # send/edit: rich HTML and its keyboard; text/markup = fallback
    queued_at: float = 0.0  # owner report 24.10: how long an operation waited for the chat budget (logged when long)


@dataclass
class _Chat:
    ops: list[_Op] = field(default_factory=list)
    stamps: deque[float] = field(default_factory=deque)  # monotonic times of recent operations
    wake: asyncio.Event = field(default_factory=asyncio.Event)  # a new operation came while the worker waits
    task: "asyncio.Task[None] | None" = None


class Outbox:
    def __init__(
        self,
        bot: _Bot,
        max_ops_per_minute: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._bot = bot
        self.max_ops = max_ops_per_minute
        self._clock = clock
        self._sleep = sleep
        self._chats: dict[int, _Chat] = {}
        self._seq = 0

    # -- public API ------------------------------------------------------------

    def send(
        self, chat_id: int, text: str, reply_markup: InlineKeyboardMarkup | None = None,
        rich: "tuple[str, InlineKeyboardMarkup | None] | None" = None,
    ) -> "asyncio.Future[OpResult]":
        """`rich` = (rich HTML, keyboard): sent as a rich message; `text`/`reply_markup` are the plain fallback."""
        fut = self._enqueue("send", chat_id, None, text, reply_markup, PRIO_INTERACTIVE)
        if rich is not None:
            self._chat(chat_id).ops[-1].rich = rich
        return fut

    def send_photo(
        self, chat_id: int, photo: Any, caption: str | None = None
    ) -> "asyncio.Future[OpResult]":
        """A picture (a Telegram file_id or an input file) with an optional caption, in the same queue and budget."""
        return self._enqueue("photo", chat_id, None, caption, None, PRIO_INTERACTIVE, photo)

    def edit_photo(self, chat_id: int, message_id: int, photo: Any) -> "asyncio.Future[OpResult]":
        """Put another picture into a photo message (the picture over a window); a burst keeps only the newest."""
        chat = self._chat(chat_id)
        for op in chat.ops:
            if op.kind == "media" and op.message_id == message_id:
                op.photo = photo
                return op.future
        return self._enqueue("media", chat_id, message_id, None, None, PRIO_INTERACTIVE, photo)

    def send_dice(self, chat_id: int, emoji: str) -> "asyncio.Future[OpResult]":
        """An animated Telegram dice (🎲 🎯 🏀 ⚽ 🎳 🎰); the result carries the rolled face in `dice_value`."""
        return self._enqueue("dice", chat_id, None, emoji, None, PRIO_INTERACTIVE)

    def edit(
        self, chat_id: int, message_id: int, text: str, reply_markup: InlineKeyboardMarkup | None = None,
        rich: "tuple[str, InlineKeyboardMarkup | None] | None" = None, low: bool = False,
    ) -> "asyncio.Future[OpResult]":
        """`low`: a late refinement of a screen (the narrator's text), sent only while the budget has headroom."""
        chat = self._chat(chat_id)
        for op in chat.ops:
            if op.kind == "edit" and op.message_id == message_id:
                op.text, op.markup, op.rich = text, reply_markup, rich  # coalesce: the newest state wins
                if not low:
                    op.priority = PRIO_INTERACTIVE
                return op.future
        fut = self._enqueue("edit", chat_id, message_id, text, reply_markup, PRIO_LOW if low else PRIO_INTERACTIVE)
        if rich is not None:
            chat.ops[-1].rich = rich
        return fut

    def delete(self, chat_id: int, message_id: int) -> "asyncio.Future[OpResult]":
        chat = self._chat(chat_id)
        for op in list(chat.ops):
            if op.message_id != message_id:
                continue
            if op.kind == "delete":
                return op.future
            if op.kind == "edit":  # no point editing what is about to vanish
                chat.ops.remove(op)
                self._resolve(op, OpResult(False, error="superseded by delete", superseded=True))
        return self._enqueue("delete", chat_id, message_id, None, None, PRIO_DELETE)

    def pending(self, chat_id: int | None = None) -> int:
        if chat_id is not None:
            return len(self._chat(chat_id).ops)
        return sum(len(c.ops) for c in self._chats.values())

    async def drain(self, timeout: float | None = None) -> None:
        """Wait until every queued operation has been tried (shutdown, scratch scripts)."""
        tasks = [c.task for c in self._chats.values() if c.task is not None and not c.task.done()]
        if tasks:
            await asyncio.wait(tasks, timeout=timeout)

    async def close(self) -> None:
        """Cancel the workers; operations still queued resolve as failed."""
        for chat in self._chats.values():
            if chat.task is not None and not chat.task.done():
                chat.task.cancel()
            for op in chat.ops:
                self._resolve(op, OpResult(False, error="outbox closed"))
            chat.ops.clear()
        await asyncio.gather(
            *[c.task for c in self._chats.values() if c.task is not None], return_exceptions=True
        )

    # -- queue -----------------------------------------------------------------

    def _chat(self, chat_id: int) -> _Chat:
        chat = self._chats.get(chat_id)
        if chat is None:
            chat = self._chats[chat_id] = _Chat()
        return chat

    def _enqueue(
        self, kind: str, chat_id: int, message_id: int | None, text: str | None,
        markup: InlineKeyboardMarkup | None, priority: int, photo: Any = None,
    ) -> "asyncio.Future[OpResult]":
        loop = asyncio.get_running_loop()
        self._seq += 1
        op = _Op(kind, chat_id, message_id, text, markup, priority, self._seq, loop.create_future(), photo=photo,
                 queued_at=self._clock())
        chat = self._chat(chat_id)
        chat.ops.append(op)
        chat.wake.set()
        if chat.task is None or chat.task.done():
            chat.task = loop.create_task(self._run(chat_id, chat))
        return op.future

    @staticmethod
    def _resolve(op: _Op, result: OpResult) -> None:
        if not op.future.done():
            op.future.set_result(result)

    @staticmethod
    def _window_wait(stamps: deque[float], limit: int, now: float) -> float:
        while stamps and now - stamps[0] >= WINDOW_S:
            stamps.popleft()
        return 0.0 if len(stamps) < limit else stamps[0] + WINDOW_S - now

    def _ready_in(self, chat: _Chat, op: _Op, now: float) -> float:
        """Seconds until `op` may go: the shared budget, with headroom kept for the windows (see LOW_HEADROOM)."""
        if op.priority == PRIO_DELETE:
            return self._window_wait(chat.stamps, max(1, self.max_ops - DELETE_HEADROOM), now)
        if op.priority == PRIO_LOW:
            return self._window_wait(chat.stamps, max(1, self.max_ops - LOW_HEADROOM), now)
        return self._window_wait(chat.stamps, self.max_ops, now)

    def _pick(self, chat: _Chat) -> tuple[_Op | None, float]:
        """The first operation (interactive before deletes, then in order) that may go now, else the shortest wait."""
        now = self._clock()
        soonest = WINDOW_S
        for op in sorted(chat.ops, key=lambda o: (o.priority, o.seq)):
            w = self._ready_in(chat, op, now)
            if w <= 0:
                return op, 0.0
            soonest = min(soonest, w)
        return None, soonest

    def _stamp(self, chat: _Chat, op: _Op) -> None:
        chat.stamps.append(self._clock())

    async def _run(self, chat_id: int, chat: _Chat) -> None:
        try:
            while chat.ops:
                op, wait_s = self._pick(chat)
                if op is None:  # nothing may go yet: wait for the budget or for a new operation (an edit goes at once)
                    chat.wake.clear()
                    try:
                        await asyncio.wait_for(chat.wake.wait(), timeout=max(0.05, wait_s))
                    except TimeoutError:
                        pass
                    continue
                chat.ops.remove(op)
                waited = self._clock() - op.queued_at if op.queued_at else 0.0
                if waited > 3 and op.priority == PRIO_INTERACTIVE and op.attempts == 0:
                    log.info("outbox: %s waited %.1fs in chat %s (queue %d, budget %d/min)", op.kind, waited, chat_id,
                             len(chat.ops), self.max_ops)
                self._stamp(chat, op)
                op.attempts += 1
                result, wait = await self._execute(op)
                if wait is not None and op.attempts <= MAX_RETRIES:
                    # RetryAfter or a network failure: put it back at the front and wait.
                    # An edit that came in meanwhile for the same message is newer, so keep it.
                    if not (op.kind in ("edit", "media") and any(
                        o.kind == op.kind and o.message_id == op.message_id for o in chat.ops
                    )):
                        chat.ops.append(op)
                    else:
                        self._resolve(op, OpResult(False, error="superseded", superseded=True))
                    await self._sleep(wait)
                    continue
                self._resolve(op, result)
        except asyncio.CancelledError:
            raise
        except Exception:  # the worker must not die with operations queued
            log.exception("outbox worker for chat %s crashed", chat_id)
            for op in chat.ops:
                self._resolve(op, OpResult(False, error="outbox crash"))
            chat.ops.clear()
        finally:
            if chat.task is asyncio.current_task():
                chat.task = None

    async def _execute_rich(self, op: _Op) -> tuple[OpResult, float | None] | None:
        """A rich send or edit. None = Telegram refused the rich form: the caller sends the plain fallback."""
        assert op.rich is not None
        body, markup = op.rich
        body = names.decorate(body) or body  # owner request 24.10: the player's handle after a character's name
        rich = InputRichMessage(html=body, skip_entity_detection=True)
        try:
            if op.kind == "send":
                msg = await self._bot.send_rich_message(op.chat_id, rich, reply_markup=markup)  # type: ignore[attr-defined]
                return OpResult(True, message_id=getattr(msg, "message_id", None)), None
            await self._bot.edit_message_text(  # type: ignore[call-arg]
                chat_id=op.chat_id, message_id=op.message_id, rich_message=rich, reply_markup=markup
            )
            return OpResult(True, message_id=op.message_id), None
        except TelegramBadRequest as e:
            msg = str(e).lower()
            if "message is not modified" in msg:
                return OpResult(True, message_id=op.message_id), None
            if any(m in msg for m in _GONE_MARKERS):
                return OpResult(False, error=str(e), gone=True), None
            log.warning("outbox: rich %s refused in chat %s, plain fallback: %s", op.kind, op.chat_id, e)
            return None
        except TelegramRetryAfter as e:
            return OpResult(False, error="retry_after"), float(e.retry_after) + 0.5
        except (TelegramNetworkError, TelegramServerError) as e:
            log.warning("outbox: network error in chat %s: %s", op.chat_id, e)
            if op.attempts <= MAX_RETRIES:
                return OpResult(False, error=str(e), retryable=True), NET_RETRY_DELAY_S * op.attempts
            return OpResult(False, error=str(e), retryable=True), None
        except TelegramAPIError as e:
            log.warning("outbox: rich API error in chat %s, plain fallback: %s", op.chat_id, e)
            return None
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("outbox: rich %s crashed in chat %s, plain fallback", op.kind, op.chat_id)
            return None

    async def _execute(self, op: _Op) -> tuple[OpResult, float | None]:
        """Do one operation. Returns (result, wait): wait is a delay after which to retry, or None."""
        if op.rich is not None and op.kind in ("send", "edit"):
            done = await self._execute_rich(op)
            if done is not None:
                return done
        if op.kind in ("send", "edit"):  # owner request 24.10: the player's handle after a character's name
            op.text = names.decorate(op.text)
        elif op.kind == "photo":
            op.text = names.decorate(op.text, 1024)
        try:
            if op.kind == "send":
                msg = await self._bot.send_message(op.chat_id, op.text or "", reply_markup=op.markup)
                return OpResult(True, message_id=getattr(msg, "message_id", None)), None
            if op.kind == "dice":
                msg = await self._bot.send_dice(op.chat_id, emoji=op.text or None)
                dice = getattr(msg, "dice", None)
                return OpResult(True, message_id=getattr(msg, "message_id", None),
                                dice_value=getattr(dice, "value", None)), None
            if op.kind == "photo":
                msg = await self._bot.send_photo(op.chat_id, op.photo, caption=op.text or None)
                sizes = getattr(msg, "photo", None) or []
                file_id = getattr(sizes[-1], "file_id", None) if sizes else None
                return OpResult(True, message_id=getattr(msg, "message_id", None), file_id=file_id), None
            if op.kind == "media":
                await self._bot.edit_message_media(
                    media=InputMediaPhoto(media=op.photo), chat_id=op.chat_id, message_id=op.message_id
                )
                return OpResult(True, message_id=op.message_id), None
            if op.kind == "edit":
                await self._bot.edit_message_text(
                    op.text or "", chat_id=op.chat_id, message_id=op.message_id, reply_markup=op.markup
                )
                return OpResult(True, message_id=op.message_id), None
            await self._bot.delete_message(op.chat_id, op.message_id or 0)
            return OpResult(True, message_id=op.message_id), None
        except TelegramRetryAfter as e:
            log.warning("outbox: RetryAfter %ss in chat %s", e.retry_after, op.chat_id)
            return OpResult(False, error="retry_after"), float(e.retry_after) + 0.5
        except TelegramBadRequest as e:
            msg = str(e).lower()
            if "message is not modified" in msg:
                return OpResult(True, message_id=op.message_id), None
            gone = any(m in msg for m in _GONE_MARKERS)
            if not gone:
                log.warning("outbox: %s failed in chat %s: %s", op.kind, op.chat_id, e)
            return OpResult(False, error=str(e), gone=gone), None
        except TelegramForbiddenError as e:
            log.warning("outbox: forbidden in chat %s: %s", op.chat_id, e)
            return OpResult(False, error=str(e)), None
        except (TelegramNetworkError, TelegramServerError) as e:
            log.warning("outbox: network error in chat %s: %s", op.chat_id, e)
            if op.attempts <= MAX_RETRIES:
                return OpResult(False, error=str(e), retryable=True), NET_RETRY_DELAY_S * op.attempts
            return OpResult(False, error=str(e), retryable=True), None
        except TelegramAPIError as e:
            log.warning("outbox: API error in chat %s: %s", op.chat_id, e)
            return OpResult(False, error=str(e)), None
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - a bad payload must not stop the queue
            log.exception("outbox: %s crashed in chat %s", op.kind, op.chat_id)
            return OpResult(False, error=repr(e)), None
