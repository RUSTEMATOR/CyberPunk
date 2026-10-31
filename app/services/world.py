"""Personal time (DESIGN.md 9a "Личное время", PLAN "Личное время").

There is no shared world clock. Every lineage lives in its own time, like BitLife: a character's own calendar
(`birth_year + age_years` is the year, `age_weeks` the week of the year) is the date of his lineage. A new player
starts at `world.start_year`; an heir (or a fresh start) continues from the date of the predecessor, which stays
on the dead character's row. The date of a player is therefore the calendar of his latest character, alive or dead.

Everything that depends on time reads the personal date: `content.available(kind, year)`, the eras, the canon
timeline, the world effects, the gig boards, the shops. Real-time things stay global (dailies, mini-games, the coin,
the night news, reminders, backups) and live in the global tick (scheduler/tick.py).

`ACTOR` is the player whose update is being handled (set by the middleware); `game.current_year(session, content)`
without an explicit player reads it, so the handlers did not have to learn about personal time one by one. Code that
runs outside an update (the tick, jobs) passes the player explicitly or has no date at all.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar, Token

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import DEFAULT_TZ, world_day
from app.content.loader import Content
from app.db import repo
from app.db.models import Character
from app.engine.state import CharacterState

log = logging.getLogger(__name__)

WORLD_WEEK_KEY = "world_week"  # the retired shared clock; kept in the DB as the record the migration started from
LAST_WORLD_DAY_KEY = "last_world_day"  # the real-time day (rolls at the tick hour) of the last global tick
LAST_TICK_KEY = "last_tick"
MIGRATION_KEY = "personal_time_v1"

ACTOR: ContextVar[int | None] = ContextVar("actor_tg_id", default=None)


def set_actor(tg_id: int | None) -> Token:
    return ACTOR.set(tg_id)


def reset_actor(token: Token) -> None:
    ACTOR.reset(token)


def today_world_day(content: Content, tz: str = DEFAULT_TZ) -> str:
    """The real-time day of the global tick: it turns over at the tick hour (03:00 Kyiv), not at midnight."""
    return world_day(tz, content.balance.time.global_tick_hour)


# -- the calendar of a character ------------------------------------------------------------------------------------


def week_index(content: Content, year: int, week_of_year: int) -> int:
    """Weeks since `world.start_year` week 0 for a calendar position (`week_of_year` counts from 0)."""
    per = content.balance.time.weeks_per_year
    return (year - content.balance.world.start_year) * per + week_of_year


def char_week(state: CharacterState, content: Content) -> int:
    """The personal date of the character in weeks since the start of the calendar."""
    return week_index(content, state.birth_year + state.age_years, state.age_weeks)


def row_week(row: Character, content: Content) -> int:
    return week_index(content, int(row.birth_year) + int(row.age_years), int(row.age_weeks))


def year_of_week(content: Content, week: int) -> int:
    return content.balance.world.start_year + max(0, week) // content.balance.time.weeks_per_year


def set_date(state: CharacterState, content: Content, week: int) -> None:
    """Put the character's calendar at `week`. The age (years) is kept; the birth year and the week of the year move,
    so `birth_year + age_years` is the year of the date and `char_week` is exact."""
    per = content.balance.time.weeks_per_year
    week = max(0, int(week))
    state.birth_year = year_of_week(content, week) - state.age_years
    state.age_weeks = week % per


# -- the date of a player --------------------------------------------------------------------------------------------


async def latest_character(session: AsyncSession, tg_id: int) -> Character | None:
    """The player's newest character, alive or dead: the holder of the lineage's date."""
    res = await session.execute(
        select(Character).where(Character.player_tg_id == tg_id).order_by(Character.id.desc()).limit(1)
    )
    return res.scalar_one_or_none()


async def lineage_week(session: AsyncSession, content: Content, tg_id: int) -> int:
    """The date a new life of this player starts at: the date of the predecessor, else the start of the calendar."""
    row = await latest_character(session, tg_id)
    return 0 if row is None else row_week(row, content)


async def personal_week(session: AsyncSession, content: Content, tg_id: int | None = None) -> int:
    """The personal date (weeks) of `tg_id`, of the acting player, else the start of the calendar."""
    who = tg_id if tg_id is not None else ACTOR.get()
    if who is None:
        return 0
    return await lineage_week(session, content, who)


# -- the one-time migration from the shared clock -----------------------------------------------------------------------


async def migrate_personal_time(session: AsyncSession, content: Content) -> int:
    """Convert a database of the shared-clock era (run at startup, idempotent).

    The date of each player's latest character (alive or dead: an heir continues from it) becomes the world date the
    shared clock showed (`world_state.world_week`), so nobody jumps in time. The age in years is kept, the birth year is
    recomputed to match and the week of the year follows the date. Open gigs of the shared boards are closed: boards are
    personal now. Returns the number of characters moved (0 when the migration is done already)."""
    if await repo.ws_get(session, MIGRATION_KEY) is not None:
        return 0
    wk = max(0, int(await repo.ws_get(session, WORLD_WEEK_KEY, 0) or 0))
    res = await session.execute(select(Character).order_by(Character.id))
    latest: dict[int, Character] = {}
    for row in res.scalars():
        latest[int(row.player_tg_id)] = row  # ids ascend: the last one of a player is the newest
    per = content.balance.time.weeks_per_year
    from app.engine.state import FLAG_WORLD_MULT
    from app.services import worldfx

    mult = worldfx.mult_flag(await worldfx.fx_at(session, content, wk))  # heat and rent multipliers of that date
    for row in latest.values():
        row.birth_year = year_of_week(content, wk) - int(row.age_years)
        row.age_weeks = wk % per
        flags = dict(row.flags or {})
        flags.pop(FLAG_WORLD_MULT, None)
        if mult is not None:
            flags[FLAG_WORLD_MULT] = mult
        row.flags = flags
    from app.db.models import Gig

    gigs = await session.execute(select(Gig).where(Gig.status == "open"))
    closed = 0
    for g in gigs.scalars():
        g.status = "expired"
        closed += 1
    await session.flush()
    await repo.ws_set(session, MIGRATION_KEY, {"world_week": wk, "characters": len(latest), "gigs_closed": closed})
    log.warning("personal time: %d player(s) moved to week %d (year %d), %d open gig(s) closed",
                len(latest), wk, year_of_week(content, wk), closed)
    return len(latest)
