"""Local legends in the world history (PLAN 8.9, DESIGN.md 9a): the deeds of players that the city remembers.

A legend is a `world_history` row of kind `local`, dated at the character's own date (there is no shared clock), so it
is part of the past only for those who come to that date later. The thresholds are in `balance.world`:

- a death at `legend_death_min_street_cred` or more;
- a finished gig of tier `legend_gig_tier` or higher;
- a lineage that has lived `legend_dynasty_lives` lives.

The text is plain facts from the game's data (no Claude, no cost); the history digest later folds the legends into the
story Claude is told. Each legend has a `ref_id`, so a repeated call writes nothing twice. Works in the caller's unit
of work.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts, views
from app.bot import texts_narrator as T
from app.content.loader import Content
from app.db import repo
from app.db.models import WorldHistory
from app.engine import world as world_engine
from app.engine.state import CharacterState
from app.narrator import digest

log = logging.getLogger(__name__)

GIG_SUCCESS = ("clean", "complication")


def _week_of(content: Content, st: CharacterState) -> int:
    wpy = content.balance.time.weeks_per_year
    return world_engine.week_of(content, st.current_year, max(1, min(wpy, st.age_weeks + 1)))


async def _add(
    session: AsyncSession, content: Content, st: CharacterState, ref: str, title: str, text: str,
) -> bool:
    """Write the legend once. False when it is already there."""
    found = await session.execute(select(WorldHistory.id).where(WorldHistory.kind == "local", WorldHistory.ref_id == ref))
    if found.first() is not None:
        return False
    await repo.add_history(
        session, world_week=_week_of(content, st), year=st.current_year, kind="local", ref_id=ref, title=title[:200],
        text=text, effects={},
    )
    digest.invalidate(st.current_year)
    log.info("legend: %s", title)
    return True


def _district(content: Content, st: CharacterState) -> str:
    d = content.districts.get(st.district)
    return d.name if d else st.district


async def death_legend(
    session: AsyncSession, content: Content, character_id: int, st: CharacterState, role: str, cause: str,
) -> bool:
    if st.street_cred < content.balance.world.legend_death_min_street_cred:
        return False
    name = f"{st.name} {st.surname}".strip()
    return await _add(
        session, content, st, f"death_{character_id}", T.LEGEND_DEATH_TITLE.format(name=name),
        T.LEGEND_DEATH_TEXT.format(
            name=name, role=role, age=views.years(st.age_years), cause=texts.DEATH_CAUSE_NAMES.get(cause, cause),
            cred=st.street_cred, district=_district(content, st),
        ),
    )


async def gig_legend(
    session: AsyncSession, content: Content, character_id: int, st: CharacterState, gig_id: int, title: str,
    tier: int, outcome: str,
) -> bool:
    if tier < content.balance.world.legend_gig_tier or outcome not in GIG_SUCCESS:
        return False
    name = f"{st.name} {st.surname}".strip()
    return await _add(
        session, content, st, f"gig_{gig_id}", T.LEGEND_GIG_TITLE.format(name=name, title=title),
        T.LEGEND_GIG_TEXT.format(tier=tier, district=_district(content, st), title=title),
    )


async def dynasty_legend(
    session: AsyncSession, content: Content, st: CharacterState, lineage_id: int, family: str, lives: int,
) -> bool:
    if lives != content.balance.world.legend_dynasty_lives:
        return False
    return await _add(
        session, content, st, f"dynasty_{lineage_id}", T.LEGEND_DYNASTY_TITLE.format(family=family),
        T.LEGEND_DYNASTY_TEXT.format(family=family, n=lives),
    )
