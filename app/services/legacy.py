"""Legacy as a service (PLAN 7.3, 7.4, DESIGN.md 13): who continues the line, the heir's draft, Secure Your Soul, the lineage.

After a death the player's newest character is dead with the flag `legacy_pending`. The player picks an option on the
legacy screen (`choose`), which is written on the dead character as `legacy_plan`; the usual creation flow (preset,
sheet) then rolls the heir (`apply_to_draft`, called by `game.new_draft`) and the save clears the pending state
(`finalize`, called by `game.create_character_from_draft`).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts_death as T
from app.content.loader import Content
from app.db import repo
from app.db.models import Character
from app.engine import legacy as eng
from app.engine.character_gen import CharacterDraft, surname_for
from app.engine.effects import apply_effects
from app.engine.state import (
    FLAG_HEIR_OF,
    FLAG_JAIL_WEEKS,
    FLAG_LEGACY_PENDING,
    FLAG_LEGACY_PLAN,
    FLAG_SECURE_SOUL,
)
from app.services import death as death_service
from app.services import legends
from app.services.game import ActiveCharacter, load_character, save_active

ARASAKA = "arasaka"


@dataclass
class Plan:
    option: str
    index: int | None
    dead: ActiveCharacter
    boon: str | None = None


@dataclass
class OptionsView:
    """What the legacy screen offers."""

    dead: ActiveCharacter
    family_name: str
    relatives: list[eng.Candidate] = field(default_factory=list)
    protege: tuple[int, Any] | None = None
    engram: bool = False
    scratch_bonus: int = 0


# -- the pending legacy ----------------------------------------------------------------------------------------------------


async def pending_dead(session: AsyncSession, tg_id: int) -> ActiveCharacter | None:
    """The player's dead character whose heir is not yet chosen, loaded with its last state."""
    row = await death_service.latest_dead_pending(session, tg_id)
    return None if row is None else await load_character(session, row.id)


async def _lineage_of(session: AsyncSession, tg_id: int):  # noqa: ANN202
    player = await repo.get_player(session, tg_id)
    return await repo.get_lineage(session, player.lineage_id) if player else None


async def options(session: AsyncSession, content: Content, tg_id: int) -> OptionsView | None:
    dead = await pending_dead(session, tg_id)
    if dead is None:
        return None
    lineage = await _lineage_of(session, tg_id)
    return OptionsView(
        dead=dead,
        family_name=lineage.family_name if lineage else dead.state.surname,
        relatives=eng.relative_candidates(dead.state, content),
        protege=eng.protege_candidate(dead.state),
        engram=eng.engram_paid(dead.state),
        scratch_bonus=eng.scratch_bonus(content, lineage.lives_count if lineage else 0),
    )


async def choose(
    session: AsyncSession, content: Content, tg_id: int, option: str, index: int | None = None,
) -> bool:
    """Write the chosen way on the dead character. False when the option does not hold (nobody to continue, not paid)."""
    view = await options(session, content, tg_id)
    if view is None or option not in eng.OPTIONS:
        return False
    if option == eng.OPT_RELATIVE and not any(c.index == index for c in view.relatives):
        return False
    if option == eng.OPT_PROTEGE and view.protege is None:
        return False
    if option == eng.OPT_ENGRAM and not view.engram:
        return False
    row = await repo.get_character_row(session, view.dead.id)
    assert row is not None
    row.flags = {**(row.flags or {}), FLAG_LEGACY_PLAN: {"option": option, "index": index}}
    lineage = await _lineage_of(session, tg_id)
    if lineage is not None:  # the glitch bonus of the lineage belongs to the "from scratch" way only
        lineage.glitch_bonus = view.scratch_bonus if option == eng.OPT_SCRATCH else 0
    await session.flush()
    return True


async def boon_offer(session: AsyncSession, content: Content, tg_id: int) -> list[str]:
    """Owner request 24.10: the gifts this death offers (stable for the same death: seeded by the character)."""
    dead = await pending_dead(session, tg_id)
    if dead is None:
        return []
    row = await repo.get_character_row(session, dead.id)
    facts = await death_service._facts(session, content, dead)
    achievement, _ = eng.pick_achievement(content, facts)
    lineage = await _lineage_of(session, tg_id)
    lives = lineage.lives_count if lineage else 0
    return eng.boon_offer(content, random.Random(dead.id * 7919), achievement, str(row.death_cause or "") if row else "",
                          lives)


async def choose_boon(session: AsyncSession, content: Content, tg_id: int, key: str) -> bool:
    if key not in await boon_offer(session, content, tg_id):
        return False
    dead = await pending_dead(session, tg_id)
    row = await repo.get_character_row(session, dead.id) if dead else None
    plan = dict((row.flags or {}).get(FLAG_LEGACY_PLAN) or {}) if row else {}
    if row is None or not plan:
        return False
    row.flags = {**(row.flags or {}), FLAG_LEGACY_PLAN: {**plan, "boon": key}}
    await session.flush()
    return True


async def needs_choice(session: AsyncSession, tg_id: int) -> bool:
    """A dead character waits for its heir to be chosen: character creation must go through the legacy screen first."""
    return await pending_dead(session, tg_id) is not None and await load_plan(session, tg_id) is None


async def load_plan(session: AsyncSession, tg_id: int) -> Plan | None:
    dead = await pending_dead(session, tg_id)
    if dead is None:
        return None
    raw = dead.state.flags.get(FLAG_LEGACY_PLAN)
    if not raw or raw.get("option") not in eng.OPTIONS:
        return None
    return Plan(str(raw["option"]), raw.get("index"), dead, raw.get("boon"))


def identity_for(plan: Plan | None, content: Content) -> eng.Identity | None:
    return None if plan is None else eng.identity(plan.dead.state, content, plan.option, plan.index)


def apply_to_draft(
    plan: Plan | None, content: Content, draft: CharacterDraft, rng: random.Random, year: int, lives_ended: int,
) -> None:
    """Put the chosen option on a rolled draft (no-op without a plan)."""
    if plan is None:
        return
    bonus = eng.scratch_bonus(content, lives_ended)
    summary = eng.apply_option(draft, plan.dead.state, content, rng, year, plan.option, plan.index, bonus)
    if summary is None:
        return
    draft.heir = summary.to_dict()
    if plan.boon:  # owner request 24.10: the gift of the dead life
        name = eng.apply_boon(draft, plan.dead.state, content, rng, year, plan.boon)
        if name:
            draft.heir["boon"] = plan.boon
    ident = identity_for(plan, content)
    if plan.option == eng.OPT_ENGRAM and ident is not None and ident.gender and draft.lineage.family_name:
        from app.engine.character_gen import FLAG_GENDER

        draft.state.flags[FLAG_GENDER] = ident.gender
        draft.state.surname = surname_for(content, draft.lineage.family_name, ident.gender)


async def finalize(session: AsyncSession, tg_id: int, draft: CharacterDraft, content: Content | None = None) -> None:
    """The heir is saved: the dead character is done with, the glitch bonus is spent."""
    dead = await death_service.latest_dead_pending(session, tg_id)
    lineage = await _lineage_of(session, tg_id)
    if lineage is not None:
        lineage.glitch_bonus = 0
    from app.services import family  # owner request 23.10: the family fund goes to the next life whole

    fund = await family.take_for_heir(session, tg_id)
    if fund:
        draft.state.eddies += fund
        draft.heir = {**(draft.heir or {}), "fund": fund}
    from app.services import legend  # owner request 25.10: the dynasty of legends gives every heir a start

    if content is not None and await legend.apply_heir(session, content, tg_id, draft.state):
        draft.heir = {**(draft.heir or {}), "dynasty": True}
    if dead is None:
        return
    dead.flags = {k: v for k, v in (dead.flags or {}).items() if k not in (FLAG_LEGACY_PENDING, FLAG_LEGACY_PLAN)}
    await repo.log_event(
        session, "heir",
        {"kind": (draft.heir or {}).get("kind", "scratch"), "from": f"{dead.name} {dead.surname}".strip(),
         "name": f"{draft.state.name} {draft.state.surname}".strip()},
        character_id=dead.id,
    )


# -- Secure Your Soul ----------------------------------------------------------------------------------------------------------


def engram_offered(content: Content, year: int) -> bool:
    """Arasaka sells the service from `legacy.engram.available_from_year`, while the corporation exists."""
    return year >= content.balance.legacy.engram.available_from_year and ARASAKA in content.available("corps", year)


@dataclass
class EngramResult:
    ok: bool
    reason: str | None = None  # have | unavailable | jail | eddies


async def buy_engram(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
) -> EngramResult:
    st = ch.state
    price = content.balance.legacy.engram.price
    if st.flags.get(FLAG_SECURE_SOUL):
        return EngramResult(False, "have")
    if not engram_offered(content, year):
        return EngramResult(False, "unavailable")
    if int(st.flags.get(FLAG_JAIL_WEEKS, 0) or 0) > 0:
        return EngramResult(False, "jail")
    if st.eddies < price:  # cash only: a debt for a soul is not on offer
        return EngramResult(False, "eddies")
    apply_effects(st, {"eddies": -price}, content, rng, year)
    st.flags[FLAG_SECURE_SOUL] = True
    await save_active(session, ch, tz)
    await repo.log_event(session, "secure_soul", {"price": price}, character_id=ch.id)
    return EngramResult(True)


# -- the lineage -------------------------------------------------------------------------------------------------------------------


@dataclass
class LifeRow:
    name: str
    role: str
    age: int
    how: str
    alive: bool
    cause: str = ""
    street_cred: int = 0


@dataclass
class LineageView:
    family: str
    lives_ended: int
    glitch_bonus: int
    lives: list[LifeRow]  # oldest first


async def lineage_view(session: AsyncSession, content: Content, tg_id: int) -> LineageView | None:
    lineage = await _lineage_of(session, tg_id)
    if lineage is None:
        return None
    res = await session.execute(
        select(Character).where(Character.lineage_id == lineage.id).order_by(Character.id)
    )
    lives: list[LifeRow] = []
    for c in res.scalars():
        heir = (c.flags or {}).get(FLAG_HEIR_OF) or {}
        kind = str(heir.get("kind") or "founder")
        lives.append(LifeRow(
            name=" ".join(x for x in (c.name, c.surname) if x), role=legends.roles_label(content, c.role_affinity or {}),
            age=int(c.age_years), how=T.HOW.get(kind, T.HOW["founder"]), alive=bool(c.alive),
            cause=c.death_cause or "", street_cred=int(c.street_cred),
        ))
    return LineageView(
        lineage.family_name, lineage.lives_count,
        eng.scratch_bonus(content, lineage.lives_count), lives,
    )
