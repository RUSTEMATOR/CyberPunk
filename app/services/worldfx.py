"""The chronicle of the world and the effects a character feels at his own date (PLAN 10.6, DESIGN.md 9a).

There is no shared world clock, so there is no "current" world effect either. The chronicle is a list of dated beats:
the canon events of `timeline.yaml` and the Claude-written future (`world_history` rows of kind `generated`, written by
`services/futurebeats.py`; a row is dated at its week). The effects of a character are the fold of every beat dated
not later than his own date (`engine/world.py::fold_dated`), applied inside the corridors of `balance.world_effects`.
Nothing is stored for it, so cancelling a generated beat (`/admin_veto`) is just a row that no longer counts. The two
multipliers that live on the character's state (heat, housing rent) are cached in his flags when his date moves.

A beat is shown to a character when his date reaches it (`beats_between`). The first character who reaches a beat makes
it real for the group: a canon event gets its `world_history` row, and a `coin_shock` of a beat hits the Eddy-crypto once
(`reach`). Real-time things (gang wars rolled by the tick, district heat) are global and stay in `world_state`.
Everything here works inside the caller's unit of work.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import DEFAULT_TZ, kyiv_date, utcnow
from app.content.loader import Content
from app.db import repo
from app.db.models import WorldHistory
from app.engine import world as world_engine
from app.engine.state import FLAG_WORLD_MULT, CharacterState
from app.engine.world import WorldFx

log = logging.getLogger(__name__)

WARS_KEY = "gang_wars"  # the random wars of the real-time tick: gang -> days left
HEAT_KEY = "district_heat"

KIND_CANON, KIND_GENERATED = "canon", "generated"


@dataclass
class Beat:
    """One event of the chronicle, placed on the calendar."""

    week: int  # the date in weeks since the start of the calendar
    year: int
    kind: str  # canon | generated
    ref_id: str | None
    title: str
    text: str  # the announcement shown to the player and posted in the feed
    effects: dict[str, Any] = field(default_factory=dict)
    row_id: int | None = None  # the world_history row, None for a canon event nobody has reached yet

    @property
    def is_canon(self) -> bool:
        return self.kind == KIND_CANON


def canon_beats(content: Content) -> list[Beat]:
    return [
        Beat(world_engine.event_week(content, e), e.year, KIND_CANON, e.id, e.title, e.announcement, dict(e.effects))
        for e in content.timeline.values()
    ]


def _row_beat(content: Content, row: WorldHistory) -> Beat:
    return Beat(int(row.world_week), int(row.year), row.kind, row.ref_id, row.title, row.text or "", dict(row.effects or {}), row.id)


async def generated_beats(session: AsyncSession, content: Content) -> list[Beat]:
    """The active Claude-written beats (a vetoed one no longer counts)."""
    res = await session.execute(
        select(WorldHistory).where(WorldHistory.kind == KIND_GENERATED, WorldHistory.status == "active")
        .order_by(WorldHistory.world_week, WorldHistory.id)
    )
    return [_row_beat(content, r) for r in res.scalars()]


async def chronicle(session: AsyncSession, content: Content) -> list[Beat]:
    """Every beat that counts, in calendar order (canon first on the same week). Canon rows already in the table give
    their row id to the canon beat."""
    rows = await session.execute(select(WorldHistory.ref_id, WorldHistory.id).where(WorldHistory.kind == KIND_CANON))
    ids = {str(ref): rid for ref, rid in rows.all() if ref}
    out = canon_beats(content)
    for b in out:
        b.row_id = ids.get(b.ref_id or "")
    out += await generated_beats(session, content)
    out.sort(key=lambda b: (b.week, 0 if b.is_canon else 1, b.row_id or 0, b.ref_id or ""))
    return out


async def beats_between(session: AsyncSession, content: Content, week0: int, week1: int) -> list[Beat]:
    """The beats a character walks into when his date goes from `week0` to `week1`: dated after the first, up to and
    including the second."""
    return [b for b in await chronicle(session, content) if week0 < b.week <= week1]


# -- the effects of a date -------------------------------------------------------------------------------------------------


async def fx_at(session: AsyncSession, content: Content, week: int) -> WorldFx:
    """The world effects at the date `week`: every beat dated not later, folded in order."""
    beats = await chronicle(session, content)
    return world_engine.fold_dated([(b.week, b.effects) for b in beats if b.week <= week], content, week)


async def load_fx(session: AsyncSession, content: Content, tg_id: int | None = None) -> WorldFx:
    """The effects at the own date of the player `tg_id` (default: the one whose update is being handled)."""
    from app.services import world  # local import: it does not import this module, but keeps the layers apart

    return await fx_at(session, content, await world.personal_week(session, content, tg_id))


def mult_flag(fx: WorldFx) -> dict[str, float] | None:
    """The `FLAG_WORLD_MULT` value of an effect set; None when both multipliers are neutral."""
    mult = {"heat": fx.heat_mult, "housing": fx.price("housing")}
    if abs(mult["heat"] - 1.0) < 1e-9 and abs(mult["housing"] - 1.0) < 1e-9:
        return None
    return mult


def refresh_state(state: CharacterState, fx: WorldFx) -> None:
    """Cache the multipliers the engine reads from the state (heat, rent) in the character's flags, so a loaded
    character feels the effects of his own date without the loader folding the chronicle."""
    mult = mult_flag(fx)
    if mult is None:
        state.flags.pop(FLAG_WORLD_MULT, None)
    else:
        state.flags[FLAG_WORLD_MULT] = mult
    state.world_heat_mult = fx.heat_mult
    state.world_housing_mult = fx.price("housing")


async def gang_wars(session: AsyncSession) -> dict[str, int]:
    """The random gang wars of the tick (gang -> days left); they belong to the real-time city."""
    return {str(k): int(v) for k, v in (await repo.ws_get(session, WARS_KEY, {}) or {}).items()}


async def active_wars(session: AsyncSession, content: Content, fx: WorldFx, year: int) -> dict[str, int]:
    """The gangs at war for a character of `year`: the random wars of the tick (only of gangs that exist in his year)
    and the canon or generated wars of his own date (`fx.wars`)."""
    gangs = content.available("gangs", year)
    out = {g: n for g, n in (await gang_wars(session)).items() if g in gangs}
    for g, n in fx.wars.items():
        out[g] = max(out.get(g, 0), n)
    return out


# -- reaching a beat -------------------------------------------------------------------------------------------------------


async def reach(session: AsyncSession, content: Content, beat: Beat, tz: str = DEFAULT_TZ) -> Beat:
    """A character's date reached `beat`. The first one to arrive makes it real for the group: a canon event gets its
    history row, and a `coin_shock` among its effects hits the Eddy-crypto once. Later arrivals change nothing."""
    row: WorldHistory | None = None
    if beat.row_id is not None:
        row = await repo.get_history(session, beat.row_id)
    elif beat.is_canon and beat.ref_id:
        found = await session.execute(
            select(WorldHistory).where(WorldHistory.kind == KIND_CANON, WorldHistory.ref_id == beat.ref_id).limit(1)
        )
        row = found.scalar_one_or_none()
    if row is not None and row.reached:
        beat.row_id = row.id
        return beat
    if row is None:
        row = await repo.add_history(
            session, world_week=beat.week, year=beat.year, kind=beat.kind, ref_id=beat.ref_id, title=beat.title,
            text=beat.text, effects=dict(beat.effects),
        )
    row.reached = True
    beat.row_id = row.id
    await session.flush()
    res = world_engine.apply_world_effects(WorldFx(), beat.effects, content)
    if res.rejected:
        log.warning("world beat %s: rejected effect keys %s", beat.ref_id or beat.title, res.rejected)
    if res.coin_shock:
        from app.services import minigames  # local import: minigames pulls in the game services

        await minigames.apply_coin_shock(session, content, res.coin_shock, kyiv_date(tz), row.id)
    return beat


async def ensure_future_beats(session: AsyncSession, content: Content, year: int, week: int | None = None) -> int:
    """The hook of the generated future: a character advanced to `year`/`week` (past the end of the canon). Make sure
    the beats up to that date exist; the first character to come writes them, later ones read the same rows. Delegates
    to `services/futurebeats.py` (the narrator's writer), which reads through the caller's session and writes in the
    background, so the character never waits for Claude. Returns how many dates still have no beat; 0 when the writer
    is absent or off."""
    try:
        from app.services import futurebeats
    except ImportError:  # the writer is not installed: the future after the canon stays empty
        return 0
    return await futurebeats.ensure_future_beats(session, content, year, week)


# -- the veto ----------------------------------------------------------------------------------------------------------------


@dataclass
class VetoResult:
    ok: bool
    reason: str = ""  # not_found | not_generated | already | late
    title: str = ""
    kind: str = ""


async def veto(session: AsyncSession, content: Content, row_id: int) -> VetoResult:
    """Cancel a generated beat within `world.history_veto_hours`. The row stops counting for every character's fold at
    once (the cached heat and rent multipliers of a character follow when his date next moves), and the Eddy-crypto
    shock it caused is taken back. A canon event cannot be cancelled."""
    row = await repo.get_history(session, row_id)
    if row is None:
        return VetoResult(False, "not_found")
    if row.kind != KIND_GENERATED:
        return VetoResult(False, "not_generated", row.title, row.kind)
    if row.status == "vetoed":
        return VetoResult(False, "already", row.title, row.kind)
    if utcnow() - row.created_at > timedelta(hours=content.balance.world.history_veto_hours):
        return VetoResult(False, "late", row.title, row.kind)
    row.status = "vetoed"
    await session.flush()
    if "coin_shock" in (row.effects or {}):
        from app.services import minigames  # local import: minigames pulls in the game services

        await minigames.undo_coin_shock(session, content, row.id)
    return VetoResult(True, title=row.title, kind=row.kind)
