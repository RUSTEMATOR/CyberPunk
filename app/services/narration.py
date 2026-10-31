"""What the narrator is told about a character (PLAN 8.7, DESIGN.md sections 13 and 14).

`legacy_note` is the short summary of the lineage: 1-2 lines about the past lives, handed to `gig_brief`, `npc_chat`,
`obituary` and the unique events so that a fixer can say "your father started with deliveries too". It is made from
the rows of the lineage, so it costs nothing and cannot invent a past. `character_card` is the compact description of
the living character that goes into payloads.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts, views
from app.bot import texts_narrator as T
from app.content.loader import Content
from app.db import repo
from app.db.models import Character
from app.engine.state import FLAG_HEIR_OF, CharacterState
from app.services import legends
from app.services.game import ActiveCharacter

NOTE_MAX_CHARS = 260
PAST_LIVES = 2  # the note names the last two lives


def _life_row(content: Content, row: Character) -> str:
    return T.LEGACY_PAST_ROW.format(
        name=" ".join(x for x in (row.name, row.surname) if x) or "безымянный",
        role=legends.roles_label(content, row.role_affinity or {}), age=views.years(int(row.age_years)),
        cause=texts.DEATH_CAUSE_NAMES.get(row.death_cause or "", row.death_cause or "неизвестна"),
    )


async def legacy_note(session: AsyncSession, content: Content, ch: ActiveCharacter) -> str:
    """The lineage summary of a living character, empty for the first life of a lineage. Never raises."""
    try:
        row = await repo.get_character_row(session, ch.id)
        if row is None or row.lineage_id is None:
            return ""
        lineage = await repo.get_lineage(session, row.lineage_id)
        res = await session.execute(
            select(Character).where(
                Character.lineage_id == row.lineage_id, Character.alive.is_(False), Character.id != ch.id,
            ).order_by(Character.id.desc()).limit(PAST_LIVES)
        )
        past = list(reversed(list(res.scalars())))
        heir = ch.state.flags.get(FLAG_HEIR_OF) or {}
        if not past and not heir:
            return ""
        parts = []
        if lineage is not None:
            number = int((await session.execute(
                select(func.count()).select_from(Character).where(Character.lineage_id == row.lineage_id, Character.id <= ch.id)
            )).scalar_one())
            parts.append(T.LEGACY_HEAD.format(family=lineage.family_name, n=number))
        how = T.LEGACY_HOW.get(str(heir.get("kind")))
        if how and heir.get("from"):
            parts.append(how.format(who=heir["from"]))
        if len(past) == 1:
            parts.append(T.LEGACY_PAST_ONE.format(**_split(content, past[0])))
        elif past:
            parts.append(T.LEGACY_PAST_MANY.format(rows="; ".join(_life_row(content, r) for r in past)))
        note = " ".join(parts)
        return note if len(note) <= NOTE_MAX_CHARS else note[: NOTE_MAX_CHARS - 1].rstrip() + "…"
    except Exception:  # the note is a bonus for the prompt
        return ""


def _split(content: Content, row: Character) -> dict[str, str]:
    return {
        "name": " ".join(x for x in (row.name, row.surname) if x) or "безымянный",
        "role": legends.roles_label(content, row.role_affinity or {}), "age": views.years(int(row.age_years)),
        "cause": texts.DEATH_CAUSE_NAMES.get(row.death_cause or "", row.death_cause or "неизвестна"),
    }


def character_card(content: Content, st: CharacterState, *, with_name: bool = True) -> dict[str, object]:
    """Who the character is, compactly, for a payload. Figures are the character's own and may be repeated by the text."""
    district = content.districts.get(st.district)
    card: dict[str, object] = {
        "role": legends.roles_label(content, st.role_affinity), "district": district.name if district else st.district,
        "age": st.age_years, "mood": st.mood, "humanity": st.humanity, "street_cred": st.street_cred, "heat": st.heat,
    }
    if with_name:
        card["name"] = f"{st.name} {st.surname}".strip()
    traits = [content.traits[k].name for k in st.traits if k in content.traits]
    if traits:
        card["traits"] = traits[:3]
    chrome = [content.cyberware[c.key].name for c in st.cyberware if c.key in content.cyberware]
    if chrome:
        card["chrome"] = chrome[:4]
    return card


ATTITUDES = ("враждебно", "холодно", "ровно", "тепло", "близко")


def attitude(relation: int) -> str:
    """The relation 0..100 as a word for the narrator (step of 20)."""
    return ATTITUDES[max(0, min(len(ATTITUDES) - 1, relation // 20))]


def npc_card(
    content: Content, st: CharacterState, *, name: str, who: str, relation: int, age: int | None, enemy: bool,
    lineage_note: str,
) -> dict[str, object]:
    """The payload of an `npc_chat` call: the NPC, the character summary, the lineage note."""
    npc: dict[str, object] = {"name": name, "who": who, "relation": relation, "attitude": attitude(relation)}
    if age is not None:
        npc["age"] = age
    if enemy:
        npc["enemy"] = True
    card: dict[str, object] = {"kind": "npc_chat", "npc": npc, "character": character_card(content, st)}
    if lineage_note:
        card["lineage_note"] = lineage_note
    return card
