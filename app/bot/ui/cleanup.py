"""Cleanup: delayed deletion of bot messages, persisted so it survives a restart (DESIGN.md section 3).

`schedule_delete` writes a row to `cleanup_queue`. A loop wakes up every `balance.ui.cleanup_interval_s`
seconds and deletes what is due through the outbox. On start `pickup` runs at once, so whatever
was due while the bot was down goes away right after startup.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from app.bot.ui.outbox import Outbox
from app.clock import utcnow
from app.db import repo
from app.db import session as db

log = logging.getLogger(__name__)

KIND_TEMP = "temp"        # errors, /who, admin confirmations
KIND_NEWS = "news"        # night news in the city feed
KIND_ALERT = "alert"      # cyberpsycho, MaxTac, arrests
KIND_COMMAND = "command"  # a player command that could not be deleted at once


class Cleanup:
    def __init__(self, outbox: Outbox, interval_s: int, batch: int) -> None:
        self._outbox = outbox
        self._interval = interval_s
        self._batch = batch
        self._task: asyncio.Task[None] | None = None

    async def schedule_delete(self, chat_id: int, message_id: int, delay_s: float, kind: str = KIND_TEMP) -> None:
        """Queue a message for deletion `delay_s` seconds from now."""
        async with db.session_scope() as s:
            await repo.add_cleanup(s, chat_id, message_id, utcnow() + timedelta(seconds=delay_s), kind)

    async def process_due(self) -> int:
        """Delete everything that is due (at most one batch). Returns how many rows were finished."""
        async with db.session_scope() as s:
            items = [(i.id, i.chat_id, i.message_id) for i in await repo.due_cleanup(s, utcnow(), self._batch)]
        if not items:
            return 0
        results = await asyncio.gather(*[self._outbox.delete(chat, mid) for _, chat, mid in items])
        # A gone message or a refusal ("can't be deleted") is final; only network failures are retried.
        done = [row_id for (row_id, _, _), res in zip(items, results) if not res.retryable]
        async with db.session_scope() as s:
            await repo.delete_cleanup(s, done)
        return len(done)

    async def pickup(self) -> int:
        """Run once at startup: log what is waiting and delete what is already due."""
        async with db.session_scope() as s:
            waiting = await repo.cleanup_count(s)
        log.info("cleanup: %d message(s) in the queue at startup", waiting)
        total = 0
        while True:
            n = await self.process_due()
            total += n
            if n < self._batch:
                return total

    async def _loop(self) -> None:
        while True:
            try:
                await self.process_due()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("cleanup: run failed")
            await asyncio.sleep(self._interval)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
