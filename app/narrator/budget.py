"""Money accounting of the narrator (PLAN 8.3, DESIGN.md section 14).

Every successful call is one row of `llm_usage` (tokens, cache tokens, cost). The cost comes from the prices in `.env`
(per 1M tokens: `PRICE_NARRATIVE_IN/OUT`, `PRICE_FAST_IN/OUT`; the cache write and read prices are multiples of the input
price). The month is the calendar month in the game time zone.

Rows are written by a background task, never inside the caller's unit of work: a narrator call may run while another
unit of work holds the SQLite write lock, and a write here would wait for it. The month's total lives in memory, so
the decision "is there money left" costs nothing.

- from `budget_warn_pct` of `MONTHLY_BUDGET_USD`: one notice to the admins;
- from `budget_fallback_pct`: the narrator stops calling Claude (fallback texts, authored texts), one notice to the
  admins; the next month starts clean. The notices are remembered in `world_state.llm_budget_notice`, so a restart
  does not repeat them.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.bot import texts_narrator as T
from app.clock import utcnow
from app.config import Settings
from app.db import repo
from app.db import session as db
from app.db.models import LlmUsage
from app.narrator.client import Usage

log = logging.getLogger(__name__)

NOTICE_KEY = "llm_budget_notice"
MODE_OK = "ok"
MODE_WARN = "warn"
MODE_FALLBACK = "fallback"

Notifier = Callable[[str], Awaitable[None]]


@dataclass
class KindRow:
    kind: str
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cost: float = 0.0


@dataclass
class Report:
    month: str
    spent: float
    limit: float
    mode: str
    rows: list[KindRow] = field(default_factory=list)

    @property
    def pct(self) -> float:
        return (self.spent / self.limit) if self.limit > 0 else 0.0


def month_key(tz: str, now: datetime | None = None) -> str:
    moment = now.astimezone(ZoneInfo(tz)) if now is not None else datetime.now(ZoneInfo(tz))
    return f"{moment.year:04d}-{moment.month:02d}"


def month_start_utc(tz: str, now: datetime | None = None) -> datetime:
    """First instant of the current local month as naive UTC (the format of `llm_usage.ts`)."""
    zone = ZoneInfo(tz)
    local = (now.astimezone(zone) if now is not None else datetime.now(zone)).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    return local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


class Budget:
    def __init__(self) -> None:
        self.settings: Settings | None = None
        self.limit = 0.0
        self.warn_pct = 0.75
        self.fallback_pct = 0.90
        self.tz = "Europe/Kyiv"
        self.month = ""
        self.spent = 0.0
        self.notified: dict[str, Any] = {"month": "", "warn": False, "fallback": False}
        self.on_mode_change: Callable[[], None] | None = None
        self._pending: list[LlmUsage] = []
        self._flush_lock = asyncio.Lock()
        self._notifier: Notifier | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._last_mode = MODE_OK

    # -- setup -----------------------------------------------------------------------------------------------

    def configure(self, settings: Settings, warn_pct: float, fallback_pct: float) -> None:
        self.settings = settings
        self.limit = float(settings.monthly_budget_usd)
        self.warn_pct = warn_pct
        self.fallback_pct = fallback_pct
        self.tz = settings.timezone

    def set_notifier(self, fn: Notifier | None) -> None:
        self._notifier = fn

    async def load(self) -> None:
        """At start: this month's total from `llm_usage` and the notices already sent."""
        self.month = month_key(self.tz)
        async with db.session_scope() as s:
            total = (await s.execute(
                select(func.coalesce(func.sum(LlmUsage.cost_usd), 0.0)).where(LlmUsage.ts >= month_start_utc(self.tz))
            )).scalar_one()
            stored = await repo.ws_get(s, NOTICE_KEY, {}) or {}
        self.spent = float(total or 0.0)
        self.notified = {
            "month": self.month,
            "warn": bool(stored.get("warn")) if stored.get("month") == self.month else False,
            "fallback": bool(stored.get("fallback")) if stored.get("month") == self.month else False,
        }
        self._last_mode = self.mode()

    # -- state -----------------------------------------------------------------------------------------------

    def _roll_month(self) -> None:
        key = month_key(self.tz)
        if key != self.month:
            log.info("narrator budget: new month %s, the counter starts from zero", key)
            self.month, self.spent = key, 0.0
            self.notified = {"month": key, "warn": False, "fallback": False}
            self._last_mode = MODE_OK
            self._changed()

    def mode(self) -> str:
        self._roll_month()
        if self.limit <= 0:
            return MODE_FALLBACK
        share = self.spent / self.limit
        if share >= self.fallback_pct:
            return MODE_FALLBACK
        if share >= self.warn_pct:
            return MODE_WARN
        return MODE_OK

    def allowed(self) -> bool:
        return self.mode() != MODE_FALLBACK

    def _changed(self) -> None:
        if self.on_mode_change is not None:
            try:
                self.on_mode_change()
            except Exception:
                log.exception("narrator budget: mode callback failed")

    # -- prices ----------------------------------------------------------------------------------------------

    def cost_of(self, model: str, usage: Usage) -> float:
        s = self.settings
        if s is None:
            return 0.0
        if model == s.model_fast and model != s.model_narrative:
            p_in, p_out = s.price_fast_in, s.price_fast_out
        else:
            p_in, p_out = s.price_narrative_in, s.price_narrative_out
        total = (
            usage.input_tokens * p_in + usage.output_tokens * p_out
            + usage.cache_write * p_in * s.price_cache_write_mult + usage.cache_read * p_in * s.price_cache_read_mult
        )
        return total / 1_000_000.0

    def prices_missing(self) -> bool:
        s = self.settings
        return s is None or (s.price_narrative_in <= 0 and s.price_narrative_out <= 0)

    # -- recording -------------------------------------------------------------------------------------------

    def record(self, kind: str, model: str, usage: Usage) -> float:
        """Count one finished call. Synchronous and free of database access; the row is written in the background."""
        self._roll_month()
        cost = self.cost_of(model, usage)
        self.spent += cost
        self._pending.append(LlmUsage(
            ts=utcnow(), kind=kind[:32], model=model[:48], input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens, cache_read=usage.cache_read, cache_write=usage.cache_write, cost_usd=cost,
        ))
        self._spawn(self.flush())
        mode = self.mode()
        if mode != self._last_mode:
            self._last_mode = mode
            self._changed()
            if mode == MODE_WARN and not self.notified.get("warn"):
                self.notified["warn"] = True
                self._spawn(self._notice(MODE_WARN))
            if mode == MODE_FALLBACK and not self.notified.get("fallback"):
                self.notified["fallback"] = True
                self._spawn(self._notice(MODE_FALLBACK))
        return cost

    def _spawn(self, coro: Awaitable[Any]) -> None:
        try:
            task = asyncio.get_running_loop().create_task(coro)  # type: ignore[arg-type]
        except RuntimeError:  # no loop: a plain script; the caller flushes by hand
            if hasattr(coro, "close"):
                coro.close()
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def flush(self) -> None:
        """Write the waiting rows. A failure keeps them for the next flush."""
        async with self._flush_lock:
            if not self._pending:
                return
            batch, self._pending = self._pending, []
            try:
                async with db.session_scope() as s:
                    s.add_all(batch)
            except Exception:
                log.exception("narrator budget: could not write %d usage rows, will retry", len(batch))
                self._pending = batch + self._pending

    async def drain(self) -> None:
        """Wait for background work (shutdown, scratch scripts)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
        await self.flush()

    async def _notice(self, mode: str) -> None:
        try:
            async with db.session_scope() as s:
                await repo.ws_set(s, NOTICE_KEY, dict(self.notified))
        except Exception:
            log.exception("narrator budget: could not store the notice flag")
        text = (T.BUDGET_NOTICE_FALLBACK if mode == MODE_FALLBACK else T.BUDGET_NOTICE_WARN).format(
            spent=f"{self.spent:.2f}", limit=f"{self.limit:.2f}", pct=int(round(100 * self.spent / self.limit)) if self.limit else 0,
        )
        log.warning("narrator budget: %s", text)
        if self._notifier is not None:
            try:
                await self._notifier(text)
            except Exception:
                log.exception("narrator budget: the admin notice failed")

    # -- the /admin_budget report -------------------------------------------------------------------------------

    async def report(self) -> Report:
        await self.flush()
        self._roll_month()
        async with db.session_scope() as s:
            res = await s.execute(
                select(
                    LlmUsage.kind, func.count(), func.sum(LlmUsage.input_tokens), func.sum(LlmUsage.output_tokens),
                    func.sum(LlmUsage.cache_read), func.sum(LlmUsage.cache_write), func.sum(LlmUsage.cost_usd),
                ).where(LlmUsage.ts >= month_start_utc(self.tz)).group_by(LlmUsage.kind).order_by(func.sum(LlmUsage.cost_usd).desc())
            )
            rows = [
                KindRow(str(k), int(n), int(i or 0), int(o or 0), int(cr or 0), int(cw or 0), float(c or 0.0))
                for k, n, i, o, cr, cw, c in res.all()
            ]
        return Report(self.month, self.spent, self.limit, self.mode(), rows)


budget = Budget()
