"""The history digest (PLAN 8.9, DESIGN.md 9a): the world's past, compressed, given to Claude as a cached system block.

There is no shared world clock: every lineage has its own date. So a digest belongs to a year, not to "now", and it
holds only what happened up to that year (canon from `timeline.yaml`, the future beats Claude wrote, the legends of
players), so a character in 2030 never hears about 2045. One digest is kept per `history_digest_bucket_years` years
of the calendar (2010, 2015, ...), stored in `world_state` as `history_digest:<year>`, so the number of calls stays
small: one per bucket, plus a rebuild when new entries appeared in that part of the past.

`text_for(year)` is synchronous and never waits: it returns the digest of the year's bucket, or the nearest earlier one
(older, still true) and starts a background build when the bucket has none. Builds run outside every unit of work.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.db import repo
from app.db import session as db
from app.db.models import WorldHistory, WorldState

log = logging.getLogger(__name__)

KEY_PREFIX = "history_digest:"
KINDS = ("generated", "local")  # canon comes from the content files, not from the table


@dataclass
class Entry:
    text: str
    built: float = 0.0  # monotonic time of the build
    stale: bool = False  # entries were added to this part of the past since the build


_cache: dict[int, Entry] = {}
_building: set[int] = set()
_failed_at: dict[int, float] = {}
_tasks: set[asyncio.Task[Any]] = set()


def _balance() -> Any:
    from app.narrator import narrator  # local: the narrator imports this module

    return narrator._get_content().balance.narrator


def bucket(year: int) -> int:
    from app.narrator import narrator

    n = _balance().history_digest_bucket_years
    start = narrator._get_content().balance.world.start_year
    return start + max(0, year - start) // n * n if year >= start else year


async def load() -> None:
    """At start: the stored digests."""
    async with db.session_scope() as s:
        rows = (await s.execute(select(WorldState).where(WorldState.key.like(KEY_PREFIX + "%")))).scalars().all()
    for row in rows:
        try:
            year = int(row.key[len(KEY_PREFIX):])
        except ValueError:
            continue
        text = str((row.value or {}).get("text") or "")
        _cache[year] = Entry(text, built=time.monotonic(), stale=bool((row.value or {}).get("stale")))


def text_for(year: int) -> str:
    """The digest to hand to Claude for a call about `year`. Never blocks, never raises."""
    try:
        b = bucket(year)
        entry = _cache.get(b)
        now = time.monotonic()
        if entry is None or (entry.stale and now - entry.built >= _balance().history_digest_refresh_seconds):
            _schedule(b)
        if entry is not None:
            return entry.text
        earlier = [k for k in _cache if k < b]
        return _cache[max(earlier)].text if earlier else ""
    except Exception:
        log.exception("digest: lookup failed")
        return ""


def invalidate(year: int) -> None:
    """A history entry appeared at `year`: every digest from that bucket on is out of date (rebuilt lazily)."""
    try:
        b = bucket(year)
        for key, entry in _cache.items():
            if key >= b:
                entry.stale = True
    except Exception:
        log.exception("digest: invalidate failed")


def _schedule(b: int) -> None:
    from app.narrator import narrator

    if b in _building or not narrator.is_live():
        return
    if time.monotonic() - _failed_at.get(b, -1e9) < _balance().history_digest_retry_seconds:
        return
    try:
        task = asyncio.get_running_loop().create_task(_build(b))
    except RuntimeError:  # no loop (a plain script)
        return
    _building.add(b)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def drain() -> None:
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


def _entries(content: Any, upto: int, after: int | None, rows: list[WorldHistory]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ev in content.timeline.values():
        if ev.year <= upto and (after is None or ev.year > after):
            out.append({"year": ev.year, "kind": "canon", "title": ev.title, "text": ev.description})
    for r in rows:
        if r.year <= upto and (after is None or r.year > after):
            out.append({"year": r.year, "kind": r.kind, "title": r.title, "text": (r.text or "")[:200]})
    out.sort(key=lambda e: (e["year"], e["kind"], e["title"]))
    return out[-_balance().history_digest_entries_max:]


async def _build(b: int) -> None:
    from app.narrator import narrator

    content = narrator._get_content()
    nb = content.balance.narrator
    try:
        async with db.session_scope() as s:
            res = await s.execute(
                select(WorldHistory).where(
                    WorldHistory.status == "active", WorldHistory.kind.in_(KINDS), WorldHistory.year <= b,
                ).order_by(WorldHistory.year, WorldHistory.id)
            )
            rows = list(res.scalars())
        earlier = [k for k in _cache if k < b and _cache[k].text]
        prev_year = max(earlier) if earlier else None
        previous = _cache[prev_year].text if prev_year is not None else ""
        entries = _entries(content, b, prev_year, rows)
        if not entries and not previous:
            text = ""
        else:
            payload = {"year": b, "previous": previous, "new": entries}
            text = await narrator.generate_text("history_digest", payload, year=b, limit=nb.history_digest_max_chars)
            if text is None:
                _failed_at[b] = time.monotonic()
                return
        _cache[b] = Entry(text, built=time.monotonic(), stale=False)
        async with db.session_scope() as s:
            await repo.ws_set(s, f"{KEY_PREFIX}{b}", {"text": text, "stale": False, "entries": len(entries)})
        log.info("digest: built for %d (%d entries, %d chars)", b, len(entries), len(text))
    except Exception:
        log.exception("digest: build for %d failed", b)
        _failed_at[b] = time.monotonic()
    finally:
        _building.discard(b)
