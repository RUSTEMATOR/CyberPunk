"""The generated future after the canon (PLAN 8.9, DESIGN.md 9a "Личное время").

There is no shared world clock, so the future is stored by date and is the same for everybody: when the first character
reaches a year beyond the last canon event, Claude writes the big events of that year (one every
`world.future_beat_weeks` weeks of the calendar); every character who comes to that date later gets the same stored
events. A beat is a `world_history` row of kind `generated` with `ref_id = beat_<year>_<week>`, dated at its week, with
the effects the code accepted (from the menu, inside the corridors of `balance.world_effects`). The world code folds the
rows dated not later than a character's own date, so a beat that is written a little late still reaches the character
who asked for it as soon as the world code reads the history again.

Two entry points, both generic about who calls them:

- `ensure_future_beats(session, content, year, week=None)` is for the world code when a character advances. It reads with
  the caller's session, never calls Claude inside the caller's unit of work, and starts the writing in the background
  (the SQLite write lock is held by the caller while it runs). It returns how many dates are still missing.
- `generate_future_beats(content, year, week=None)` does the writing itself and waits for it. For scratch scripts and
  admin tools; never call it while holding a unit of work.

A failed or invalid beat leaves no row and is asked again after `future_beat_retry_seconds`. `/admin_veto` marks a row as
vetoed (worldfx.veto); a vetoed date is not written again.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.content.schemas import EVENT_CATEGORIES, WORLD_PRICE_TARGETS, parse_world_effect_key, validate_world_effects
from app.db import repo
from app.db import session as db
from app.db.models import WorldHistory
from app.engine import world as world_engine
from app.engine.world import WorldFx
from app.narrator import digest, guard, narrator

log = logging.getLogger(__name__)

REF_PREFIX = "beat_"
ITEM_KEYS_MAX = 60  # implant keys offered to ban.<key> / unlock.<key>


@dataclass
class Beat:
    """A future event that was just written."""

    history_id: int
    year: int
    week: int
    title: str
    announcement: str
    effects: dict[str, Any] = field(default_factory=dict)


Announcer = Callable[[Beat], Awaitable[None]]

_announcer: Announcer | None = None
_inflight: set[tuple[int, int]] = set()
_failed_at: dict[tuple[int, int], float] = {}
_tasks: set[asyncio.Task[Any]] = set()
_write_lock: asyncio.Lock | None = None


def set_announcer(fn: Announcer | None) -> None:
    """The world code's poster: called once for every beat written (the city feed post)."""
    global _announcer
    _announcer = fn


def ref_of(year: int, week: int) -> str:
    return f"{REF_PREFIX}{year}_{week}"


# -- the calendar of beats -----------------------------------------------------------------------------------------------


def canon_end(content: Content) -> int:
    """Calendar position (`week_of`) of the last canon event; the day before the start year when there is none."""
    positions = [world_engine.event_week(content, e) for e in content.timeline.values()]
    return max(positions) if positions else world_engine.week_of(content, content.balance.world.start_year, 1) - 1


def slot_list(content: Content, year: int, week: int | None = None) -> list[tuple[int, int]]:
    """Every (year, week) at which a beat belongs, after the canon and not later than `year`/`week` (default: the year's end)."""
    wpy = content.balance.time.weeks_per_year
    step = content.balance.world.future_beat_weeks
    last = world_engine.week_of(content, year, week if week is not None else wpy)
    end = canon_end(content)
    first_year = world_engine.year_week(content, max(0, end))[0]
    out: list[tuple[int, int]] = []
    for y in range(first_year, year + 1):
        for w in range(1, wpy + 1, step):
            pos = world_engine.week_of(content, y, w)
            if end < pos <= last:
                out.append((y, w))
    return out


async def _existing(session: AsyncSession) -> set[str]:
    res = await session.execute(select(WorldHistory.ref_id).where(WorldHistory.kind == "generated", WorldHistory.ref_id.like(REF_PREFIX + "%")))
    return {str(r) for r in res.scalars()}


async def missing(session: AsyncSession, content: Content, year: int, week: int | None = None) -> list[tuple[int, int]]:
    have = await _existing(session)
    return [s for s in slot_list(content, year, week) if ref_of(*s) not in have]


# -- what Claude is shown and how its answer is checked -------------------------------------------------------------------


def menu_for(content: Content, year: int) -> dict[str, Any]:
    """The effect menu of a beat: only what exists in `year`, with the corridors of balance.world_effects."""
    cor = content.balance.world_effects
    gangs = sorted(k for k, g in content.available("gangs", year).items() if not g.fights_as)  # owner request 25.10
    corps = sorted(content.available("corps", year))
    return {
        "price_targets": list(WORLD_PRICE_TARGETS),
        "districts": sorted(k for k, d in content.available("districts", year).items() if d.city == "nc"),
        "gangs": gangs,
        "factions": gangs + corps,
        "event_categories": list(EVENT_CATEGORIES),
        "items": sorted(content.available("cyberware", year))[:ITEM_KEYS_MAX],
        "ranges": {
            "price_mult": [cor.price_mult.min, cor.price_mult.max],
            "district_danger": [cor.district_danger.min, cor.district_danger.max],
            "gang_war_weeks": [cor.gang_war_weeks.min, cor.gang_war_weeks.max],
            "faction_power": [cor.faction_power.min, cor.faction_power.max],
            "heat_mult": [cor.heat_mult.min, cor.heat_mult.max],
            "gig_pay_mult": [cor.gig_pay_mult.min, cor.gig_pay_mult.max],
            "event_weight": [cor.event_weight.min, cor.event_weight.max],
            "coin_shock": [cor.coin_shock.min, cor.coin_shock.max],
        },
    }


class _Invalid(Exception):
    pass


def validate(content: Content, data: dict[str, Any], year: int) -> tuple[str, str, dict[str, Any]]:
    """(title, announcement, effects as applied) or `_Invalid`. Effects pass through the world engine on a scratch
    `WorldFx`, so the stored values are the clamped ones and an unknown key rejects the whole beat."""
    nb = content.balance.narrator
    title = guard.clean(str(data.get("title") or ""))
    text = guard.clean(str(data.get("announcement") or ""))
    if not title or not text:
        raise _Invalid("title or announcement is empty")
    title = guard.fit(title, nb.future_beat_title_max_chars, nb.min_keep_ratio) or ""
    text = guard.fit(text, nb.news_max_chars, nb.min_keep_ratio) or ""
    if not title or not text:
        raise _Invalid("title or announcement too long")
    foreign = guard.foreign_numbers(title + " " + text, {str(year)})
    if foreign:
        raise _Invalid(f"numbers other than the year: {foreign}")
    raw = data.get("effects")
    if not isinstance(raw, dict) or not 1 <= len(raw) <= nb.future_beat_max_effects:
        raise _Invalid(f"effects: need 1..{nb.future_beat_max_effects} keys")
    try:
        validate_world_effects(raw)
        for key in raw:
            base, param = parse_world_effect_key(key)
            if base == "era":
                raise _Invalid("the era cannot be changed")
            kind = {"district_danger": "districts", "district_open": "districts", "district_close": "districts",
                    "gang_war": "gangs"}.get(base)
            if kind is not None and param not in content.available(kind, year):
                raise _Invalid(f"{key}: does not exist in {year}")
            if base == "faction_power" and param not in content.available("gangs", year) and param not in content.available("corps", year):
                raise _Invalid(f"{key}: does not exist in {year}")
    except ValueError as err:
        raise _Invalid(str(err)[:200]) from err
    res = world_engine.apply_world_effects(WorldFx(), raw, content)
    if res.rejected or not res.applied:
        raise _Invalid(f"effects rejected: {res.rejected}")
    return title, text, dict(res.applied)


async def _previous(session: AsyncSession, content: Content, year: int, week: int) -> list[dict[str, Any]]:
    """The latest history before the date: canon from the content files, earlier beats and legends from the table."""
    pos = world_engine.week_of(content, year, week)
    rows: list[dict[str, Any]] = [
        {"year": e.year, "title": e.title, "text": e.description[:200]}
        for e in content.timeline.values() if world_engine.event_week(content, e) < pos
    ]
    res = await session.execute(
        select(WorldHistory).where(
            WorldHistory.status == "active", WorldHistory.kind.in_(("generated", "local")), WorldHistory.world_week < pos,
        ).order_by(WorldHistory.world_week.desc(), WorldHistory.id.desc()).limit(10)
    )
    rows += [{"year": r.year, "title": r.title, "text": (r.text or "")[:200]} for r in res.scalars()]
    rows.sort(key=lambda r: r["year"])
    return rows[-8:]


# -- writing -------------------------------------------------------------------------------------------------------------


def _lock() -> asyncio.Lock:
    global _write_lock
    if _write_lock is None:
        _write_lock = asyncio.Lock()
    return _write_lock


async def _write_one(content: Content, year: int, week: int) -> Beat | None:
    """Ask for one beat and store it. Own sessions only; the caller holds no unit of work."""
    nb = content.balance.narrator
    async with db.session_scope() as s:
        if ref_of(year, week) in await _existing(s):
            return None
        previous = await _previous(s, content, year, week)
    payload = {
        "kind": "future_beat", "date": {"year": year, "week": week}, "previous": previous, "menu": menu_for(content, year),
    }
    data = await narrator.generate_json(
        "future_beat", payload, year=year, title_max=nb.future_beat_title_max_chars,
        text_max=int(nb.news_max_chars * nb.target_ratio), max_effects=nb.future_beat_max_effects,
    )
    if data is None:
        return None
    try:
        title, text, effects = validate(content, data, year)
    except (_Invalid, ValidationError) as err:
        log.warning("future beat %d/%d dropped: %s", year, week, str(err).splitlines()[0][:200])
        return None
    async with _lock():
        async with db.session_scope() as s:
            if ref_of(year, week) in await _existing(s):  # another writer was first
                return None
            row = await repo.add_history(
                s, world_week=world_engine.week_of(content, year, week), year=year, kind="generated",
                ref_id=ref_of(year, week), title=title, text=text, effects=effects,
            )
            beat = Beat(row.id, year, week, title, text, effects)
    digest.invalidate(year)
    log.info("future beat %d/%d written: %s", year, week, title)
    if _announcer is not None:
        try:
            await _announcer(beat)
        except Exception:
            log.exception("future beat: the announcement failed")
    return beat


async def _write_many(content: Content, slots: list[tuple[int, int]]) -> list[Beat]:
    out: list[Beat] = []
    for year, week in slots:
        key = (year, week)
        if key in _inflight or time.monotonic() - _failed_at.get(key, -1e9) < content.balance.narrator.future_beat_retry_seconds:
            continue
        _inflight.add(key)
        try:
            beat = await _write_one(content, year, week)
        except Exception:
            log.exception("future beat %d/%d failed", year, week)
            beat = None
        finally:
            _inflight.discard(key)
        if beat is None:
            _failed_at[key] = time.monotonic()
            break  # the next dates build on this one: stop, try again later
        out.append(beat)
    return out


async def generate_future_beats(content: Content, year: int, week: int | None = None) -> list[Beat]:
    """Write the missing beats up to the date and wait for them. Never inside a unit of work."""
    if not narrator.is_live():
        return []
    async with db.session_scope() as s:
        todo = await missing(s, content, year, week)
    return await _write_many(content, todo[: content.balance.narrator.future_beat_per_call])


async def ensure_future_beats(session: AsyncSession, content: Content, year: int, week: int | None = None) -> int:
    """Called by the world code when a character advances to `year`/`week`: make sure the future up to that date exists.

    Returns the number of dates that have no beat yet. Safe inside a unit of work: it only reads through `session`
    and starts the writing in the background."""
    if not narrator.is_live() or not slot_list(content, year, week):
        return 0
    todo = await missing(session, content, year, week)
    if not todo:
        return 0
    todo = [s for s in todo if s not in _inflight]
    if todo:
        try:
            task = asyncio.get_running_loop().create_task(_write_many(content, todo[: content.balance.narrator.future_beat_per_call]))
        except RuntimeError:
            return len(todo)
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    return len(todo)


async def drain() -> None:
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)
