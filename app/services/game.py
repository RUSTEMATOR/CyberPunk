"""Game glue: world clock, the player's active character, character creation (PLAN 3.0).

Services sit between the DB (`app/db/repo.py`), the engine and the UI. They own the unit of work:
callers pass an open `AsyncSession` and commit it (usually through `db.session_scope()`).
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import DEFAULT_TZ, kyiv_date
from app.content.loader import Content
from app.db import repo
from app.content.schemas import Fixer
from app.engine import character_gen
from app.engine.character_gen import (
    FLAG_GENDER,
    CharacterDraft,
    LineageInfo,
    gender_of_name,
    roll_first_name,
    surname_for,
)
from app.engine.state import CharacterState
from app.narrator import narrator

log = logging.getLogger(__name__)

@dataclass
class ActiveCharacter:
    """A loaded living character. Mutate `state` (through apply_effects) and call `save_active`."""

    id: int
    player_tg_id: int
    state: CharacterState


# -- personal time ----------------------------------------------------------------


def year_for_week(content: Content, week: int) -> int:
    """Calendar year at a date given in weeks since the start of the calendar (DESIGN.md 9a)."""
    return content.balance.world.start_year + max(0, week) // content.balance.time.weeks_per_year


WORLD_WEEK_KEY = "world_week"  # the retired shared clock; the tick reads it until T.5 removes the tick's clock


async def world_week(session: AsyncSession) -> int:
    return int(await repo.ws_get(session, WORLD_WEEK_KEY, 0) or 0)


async def current_year(session: AsyncSession, content: Content, tg_id: int | None = None) -> int:
    """The year of a player's own date: the player `tg_id`, else the one whose update is being handled
    (`services.world.ACTOR`), else the start year. There is no shared world year (DESIGN.md 9a)."""
    from app.services import world  # local import: it imports repo, as this module does

    year = year_for_week(content, await world.personal_week(session, content, tg_id))
    narrator.note_year(year)  # the narrator picks the era of its lore block from the year it was last told
    return year


# -- characters ------------------------------------------------------------------


async def get_active_character(session: AsyncSession, tg_id: int) -> ActiveCharacter | None:
    """The player's living character, or None when they have none (new player or dead)."""
    cid = await repo.active_character_id(session, tg_id)
    if cid is None:
        return None
    state = await repo.load_character(session, cid)
    if state is None:
        return None
    return ActiveCharacter(cid, tg_id, state)


async def load_character(session: AsyncSession, character_id: int) -> ActiveCharacter | None:
    row = await repo.get_character_row(session, character_id)
    if row is None:
        return None
    state = await repo.load_character(session, character_id)
    return None if state is None else ActiveCharacter(character_id, row.player_tg_id, state)


async def save_active(session: AsyncSession, ch: ActiveCharacter, tz: str = DEFAULT_TZ) -> None:
    await repo.save_character(session, ch.id, ch.state, tz)


async def lineage_info(session: AsyncSession, tg_id: int) -> LineageInfo:
    """What the generator needs to know about the player's lineage (empty for a new player)."""
    player = await repo.get_player(session, tg_id)
    lineage = await repo.get_lineage(session, player.lineage_id) if player else None
    if lineage is None:
        return LineageInfo()
    return LineageInfo(lineage.family_name, lineage.lives_count, lineage.glitch_bonus)


async def new_draft(
    session: AsyncSession, content: Content, tg_id: int, preset_key: str,
    rng: random.Random | None = None,
) -> CharacterDraft:
    """Roll a draft of a preset for the year of the player's lineage (his own date). Saves nothing."""
    rng = rng or random.Random()
    year = await current_year(session, content, tg_id)
    lineage = await lineage_info(session, tg_id)
    from app.services import legacy as legacy_service
    from app.services import worldfx  # local import: it imports repo, as this module does

    plan = await legacy_service.load_plan(session, tg_id)  # an heir of a dead character (PLAN 7.3), else None
    if plan is not None and plan.option == "scratch":  # a stranger starts from scratch: not the family's surname
        import dataclasses

        lineage = dataclasses.replace(lineage, family_name=None)
    ident = legacy_service.identity_for(plan, content)
    draft = character_gen.generate(
        rng, content, preset_key, year=year, lineage=lineage, fx=await worldfx.load_fx(session, content, tg_id),
        name=ident.name if ident else None, surname=ident.surname if ident else None,
    )
    legacy_service.apply_to_draft(plan, content, draft, rng, year, lineage.lives_count)
    from app.engine import creator  # owner request 23.10: every new character has a face

    creator.random_look(rng, draft)
    return draft


async def create_character_from_draft(
    session: AsyncSession, content: Content, tg_id: int, username: str | None,
    draft: CharacterDraft, tz: str = DEFAULT_TZ,
) -> ActiveCharacter:
    """Save a draft as the player's living character, at the date of his lineage (the start of the calendar for a
    new player, the predecessor's date otherwise).

    Creates the player and the lineage on the first life and writes
    `character_created` to event_log (the lives that ended are counted by `services/death.py::kill`). `fixer_rel` stays empty: a missing fixer defaults to
    balance.gigs.fixer_rel_start in apply_effects. Raises ValueError when the player already
    has a living character (a double click).
    """
    from app.services import daily, world  # local import: both import this module

    player = await repo.ensure_player(session, tg_id, username)
    if await repo.active_character_id(session, tg_id) is not None:
        raise ValueError("player already has a living character")
    st = draft.state
    week = await world.lineage_week(session, content, tg_id)  # an heir or a fresh start continues the predecessor's date
    world.set_date(st, content, week)
    from app.services import worldfx  # local import: it imports repo, as this module does

    worldfx.refresh_state(st, await worldfx.fx_at(session, content, week))
    await daily.attach_pending(session, content, tg_id, st, random.Random(), year_for_week(content, week), kyiv_date(tz))
    lineage = await repo.ensure_lineage(session, player, st.surname)
    from app.services import legacy as legacy_service

    await legacy_service.finalize(session, tg_id, draft, content)
    cid = await repo.create_character(session, tg_id, st, lineage.id, tz)
    await repo.log_event(
        session, "character_created",
        {"preset": draft.preset_key, "origin": draft.origin_key, "name": st.name, "surname": st.surname},
        character_id=cid,
    )
    return ActiveCharacter(cid, tg_id, st)


# -- drafts in the window ----------------------------------------------------------------------


def draft_to_dict(draft: CharacterDraft) -> dict:
    """JSON-safe form of a draft, kept in `windows.screen_state` while the player creates a character."""
    li = draft.lineage
    return {
        "state": draft.state.to_dict(), "preset": draft.preset_key, "origin": draft.origin_key,
        "lineage": {"family_name": li.family_name, "lives_count": li.lives_count, "glitch_bonus": li.glitch_bonus},
        "heir": draft.heir,
    }


def draft_from_dict(d: dict) -> CharacterDraft:
    li = d.get("lineage") or {}
    return CharacterDraft(
        state=CharacterState.from_dict(d["state"]),
        preset_key=d["preset"],
        origin_key=d["origin"],
        lineage=LineageInfo(li.get("family_name"), int(li.get("lives_count", 0)), int(li.get("glitch_bonus", 0))),
        heir=d.get("heir"),
    )


def reroll_surname(rng: random.Random, content: Content, draft: CharacterDraft) -> None:
    """A new random surname for the draft (owner request); the family of the draft takes it too."""
    from app.engine.character_gen import surname_forms

    st = draft.state
    old = surname_forms(content, st.surname)
    pick = rng.choice([sn for sn in content.surnames if sn.male != old.male] or list(content.surnames))
    gender = str(st.flags.get(FLAG_GENDER) or "m")
    st.surname = pick.form(gender)
    for npc in st.npcs:
        for form in {old.male, old.female}:
            if form and npc.name.endswith(" " + form):
                g = str(npc.data.get("gender") or "m")
                npc.name = npc.name[: -len(form)] + pick.form(g)
                break


def rename_draft(
    rng: random.Random, content: Content, draft: CharacterDraft, name: str | None = None, gender: str | None = None,
) -> None:
    """Give the draft a typed first name, or a new random one when `name` is None.

    The gender follows the name and the surname takes the matching form (M2.7 name rules).
    """
    st = draft.state
    if name is None:
        taken = {n.name.split()[0].casefold() for n in draft.family if n.name}
        taken.add(st.name.casefold())
        new, gender = roll_first_name(rng, content, gender, taken=taken)
    else:
        new, gender = name.strip(), gender_of_name(rng, content, name)
    st.name = new
    st.flags[FLAG_GENDER] = gender
    st.surname = surname_for(content, st.surname, gender)


def set_draft_gender(rng: random.Random, content: Content, draft: CharacterDraft, gender: str) -> None:
    """Switch the draft's gender. A name that clearly belongs to the other gender is rerolled; a unisex or typed
    name stays, and the surname takes the matching form."""
    st = draft.state
    known = next((fn for fn in content.given_names if fn.name.casefold() == st.name.casefold()), None)
    if known is not None and known.gender not in (gender, "n"):
        rename_draft(rng, content, draft, None, gender=gender)
        return
    st.flags[FLAG_GENDER] = gender
    st.surname = surname_for(content, st.surname, gender)


def district_fixer(content: Content, district_key: str, year: int) -> Fixer | None:
    """The fixer who works a district in `year`: the district's main one, else any available there."""
    fixers = content.available("fixers", year)
    district = content.districts.get(district_key)
    if district is not None and district.fixer in fixers:
        return fixers[district.fixer]
    for fixer in fixers.values():
        if district_key in fixer.districts:
            return fixer
    return None
