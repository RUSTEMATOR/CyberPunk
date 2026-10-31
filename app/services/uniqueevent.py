"""The unique event of the week, written by Claude (PLAN 8.5, DESIGN.md section 12).

After a lived week, with the chance `events.unique.chance` and at most `max_per_week` per week of life, the narrator is
asked for one scene with 2-3 choices as a JSON object in the shape of `events.yaml`. Everything it sends is checked here:

- the JSON becomes a real `Event` (the pydantic model of the content), so it has the same shape as an authored one;
- every effect must be on the menu (`eddies hp mood humanity street_cred heat skill_xp:<skill>`) and is clamped into the
  corridors of `balance.events.unique` (eddies +-20% of the current money with a floor, hp +-15, ...). An effect that is
  not on the menu (death, a fight, jail, implants, debts, new people) makes the whole event invalid;
- checks stay inside `dv_min..dv_max`; titles, buttons and texts keep their limits and carry no digits (the screen shows
  the real figures itself).

An invalid event is dropped: the week keeps its ordinary events from the YAML. The generated event is stored on the
character (`flags.unique_events`), queued like any other event and registered in `content.events` for as long as it lives,
so the event screens, checks and `finalize` need no special case. It is gone after it is played.
"""

from __future__ import annotations

import logging
import random
from typing import Any

from pydantic import ValidationError

from app.content.loader import Content
from app.content.schemas import EVENT_CATEGORIES, SKILLS, STATS, Event
from app.db import session as db
from app.engine import jail as jail_engine
from app.engine.state import FLAG_RECENT_EVENTS
from app.narrator import guard, narrator
from app.services import game, narration
from app.services.game import ActiveCharacter, save_active
from app.services.week import pending_events, set_pending

log = logging.getLogger(__name__)

FLAG_UNIQUE_EVENTS = "unique_events"  # {event id: Event as a dict} generated events not played yet
FLAG_UNIQUE_WEEK = "unique_week"  # [weeks_lived, count]: how many were made in this week of life
PREFIX = "uniq_"
PLAIN_EFFECTS = ("eddies", "hp", "mood", "humanity", "street_cred", "heat")


def is_unique(event_id: str) -> bool:
    return event_id.startswith(PREFIX)


# -- registry: a generated event lives in content.events while it waits ---------------------------------------------------


def register(content: Content, event: Event) -> None:
    content.events[event.id] = event


async def restore_all(content: Content) -> int:
    """At start: put the waiting generated events of all living characters back into `content.events`."""
    from sqlalchemy import select

    from app.db.models import Character

    count = 0
    async with db.session_scope() as s:
        rows = (await s.execute(select(Character.id, Character.flags).where(Character.alive.is_(True)))).all()
    for cid, flags in rows:
        for eid, raw in ((flags or {}).get(FLAG_UNIQUE_EVENTS) or {}).items():
            try:
                content.events[eid] = Event.model_validate(raw)
                count += 1
            except ValidationError:
                log.warning("unique event %s of character %s is broken and is dropped", eid, cid)
    return count


def forget(content: Content, ch: ActiveCharacter, event_id: str) -> None:
    """The event was played: it leaves the character and the registry."""
    stored = ch.state.flags.get(FLAG_UNIQUE_EVENTS)
    if stored:
        stored.pop(event_id, None)
        if not stored:
            ch.state.flags.pop(FLAG_UNIQUE_EVENTS, None)
    content.events.pop(event_id, None)


# -- the corridors -----------------------------------------------------------------------------------------------------


def corridor(content: Content, eddies: int) -> dict[str, tuple[int, int]]:
    """The allowed range of every effect for a character with this money (balance.events.unique.corridor)."""
    c = content.balance.events.unique.corridor
    money = max(c.eddies_floor, int(max(0, eddies) * c.eddies_pct))
    return {
        "eddies": (-money, money), "hp": (-c.hp_abs, c.hp_abs), "mood": (-c.mood_abs, c.mood_abs),
        "humanity": (-c.humanity_abs, c.humanity_abs), "street_cred": (-c.street_cred_abs, c.street_cred_abs),
        "heat": (-c.heat_abs, c.heat_abs),
    }


def _clean_effects(content: Content, raw: Any, ranges: dict[str, tuple[int, int]]) -> dict[str, int] | None:
    """Effects from the menu, clamped. None = something off the menu was asked for."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        return None
    xp_max = content.balance.events.unique.skill_xp_max
    out: dict[str, int] = {}
    for key, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        v = int(round(value))
        if key in PLAIN_EFFECTS:
            lo, hi = ranges[key]
            v = max(lo, min(hi, v))
        elif key.startswith("skill_xp:") and key.split(":", 1)[1] in SKILLS:
            v = max(0, min(xp_max, v))
        else:
            return None
        if v != 0:
            out[key] = v
    return out


# -- validation ------------------------------------------------------------------------------------------------------


class _Invalid(Exception):
    pass


def _text(content: Content, value: Any, limit: int, what: str, allowed: set[str]) -> str:
    nb = content.balance.narrator
    if not isinstance(value, str):
        raise _Invalid(f"{what}: not a string")
    text = guard.clean(value)
    if not text:
        raise _Invalid(f"{what}: empty")
    fitted = guard.fit(text, limit, nb.min_keep_ratio)
    if fitted is None:
        raise _Invalid(f"{what}: too long")
    if guard.numbers_in(fitted):  # the figures on screen are the real ones; a scene has none of its own
        raise _Invalid(f"{what}: has digits")
    return fitted


def validate(content: Content, st: Any, data: dict[str, Any], event_id: str) -> Event:
    """The generated JSON as an `Event`, or `_Invalid`/ValidationError. Never trusts a number."""
    nb = content.balance.narrator
    uc = content.balance.events.unique
    ranges = corridor(content, st.eddies)
    allowed: set[str] = set()
    title = _text(content, data.get("title"), nb.unique_title_max_chars, "title", allowed)
    body = _text(content, data.get("text"), nb.message_max_chars, "text", allowed)
    category = data.get("category") if data.get("category") in EVENT_CATEGORIES else "street"
    raw_choices = data.get("choices")
    if not isinstance(raw_choices, list) or not 2 <= len(raw_choices) <= 3:
        raise _Invalid("choices: need 2 or 3")
    choices: list[dict[str, Any]] = []
    for i, c in enumerate(raw_choices):
        if not isinstance(c, dict):
            raise _Invalid(f"choice {i}: not an object")
        label = _text(content, c.get("label"), nb.unique_label_max_chars, f"choice {i} label", allowed)
        if isinstance(c.get("check"), dict):
            chk = c["check"]
            stat = chk.get("stat")
            if stat not in STATS:
                raise _Invalid(f"choice {i}: unknown stat {stat!r}")
            skill = chk.get("skill")
            try:
                dv = int(chk.get("dv"))
            except (TypeError, ValueError) as exc:
                raise _Invalid(f"choice {i}: dv is not a number") from exc
            dv = max(uc.dv_min, min(uc.dv_max, dv))
            outcomes: dict[str, Any] = {}
            for side in ("success", "fail"):
                o = c.get(side)
                if not isinstance(o, dict):
                    raise _Invalid(f"choice {i}: no {side}")
                fx = _clean_effects(content, o.get("effects"), ranges)
                if fx is None:
                    raise _Invalid(f"choice {i} {side}: effect off the menu")
                outcomes[side] = {"text": _text(content, o.get("text"), nb.unique_outcome_max_chars, f"choice {i} {side}", allowed), "effects": fx}
            choices.append({"label": label, "check": {"stat": stat, "skill": skill if skill in SKILLS else None, "dv": dv}, **outcomes})
        else:
            fx = _clean_effects(content, c.get("effects"), ranges)
            if fx is None:
                raise _Invalid(f"choice {i}: effect off the menu")
            choices.append({"label": label, "text": _text(content, c.get("text"), nb.unique_outcome_max_chars, f"choice {i}", allowed), "effects": fx})
    return Event.model_validate({
        "id": event_id, "title": title, "category": category, "weight": 1, "text": body, "choices": choices,
        "forced_only": True,
    })


# -- the payload and the whole step --------------------------------------------------------------------------------------


def build_payload(content: Content, st: Any, lineage_note: str) -> dict[str, Any]:
    ranges = corridor(content, st.eddies)
    recent = [content.events[e].title for e in (st.flags.get(FLAG_RECENT_EVENTS) or [])[-4:] if e in content.events and not is_unique(e)]
    district = content.districts.get(st.district)
    card = narration.character_card(content, st)
    if lineage_note:
        card["lineage_note"] = lineage_note
    return {
        "kind": "unique_event", "character": card, "district_description": district.description if district else "",
        "recent_events": recent,
        "corridor": {k: {"min": lo, "max": hi} for k, (lo, hi) in ranges.items()},
    }


async def add_from_lead(content: Content, user_id: int, tz: str, hook: dict[str, str]) -> str | None:
    """Owner report 27.10: a lead an NPC gave in a talk becomes a scene at once, first in the queue of events.
    Runs after the talk, outside any unit of work. None when the narrator is off or the scene did not pass."""
    import time

    from app.services import notices

    if not narrator.is_live():
        return None
    try:
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, user_id)
            if ch is None or jail_engine.in_jail(ch.state):
                return None
            st = ch.state
            cid, year = ch.id, st.current_year
            payload = build_payload(content, st, await narration.legacy_note(s, content, ch))
            payload["hook"] = {k: str(v)[:400] for k, v in hook.items()}
        nb = content.balance.narrator
        uc = content.balance.events.unique
        data = await narrator.generate_json(
            "unique_event", payload, year=year, title_max=nb.unique_title_max_chars, categories="|".join(EVENT_CATEGORIES),
            text_max=int(nb.message_max_chars * nb.target_ratio), label_max=nb.unique_label_max_chars,
            stats="|".join(STATS), skills="|".join(SKILLS), dv_min=uc.dv_min, dv_max=uc.dv_max,
            out_max=nb.unique_outcome_max_chars, xp_max=uc.skill_xp_max,
        )
        if data is None:
            return None
        event_id = f"{PREFIX}{cid}_lead{int(time.time())}"
        try:
            event = validate(content, st, data, event_id)
        except (_Invalid, ValidationError) as err:
            log.warning("lead event dropped: %s", str(err).splitlines()[0][:200])
            return None
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, user_id)
            if ch is None or ch.id != cid:
                return None
            stored = dict(ch.state.flags.get(FLAG_UNIQUE_EVENTS) or {})
            stored[event.id] = event.model_dump(mode="json")
            ch.state.flags[FLAG_UNIQUE_EVENTS] = stored
            set_pending(ch, [event.id] + [e for e in pending_events(ch) if e != event.id])
            notices.push(ch, f"📍 Наводка от {hook.get('who', 'знакомого')}: «{event.title}». Дело первое в «Событиях».")
            await save_active(s, ch, tz)
        register(content, event)
        log.info("lead event %s for character %s: %s", event.id, cid, event.title)
        return event.id
    except Exception:  # a bonus scene never breaks the talk
        log.exception("lead event failed")
        return None


def due(content: Content, st: Any, rng: random.Random) -> bool:
    """The chance and the weekly cap. Nothing is rolled while the narrator is off."""
    uc = content.balance.events.unique
    mark = st.flags.get(FLAG_UNIQUE_WEEK) or [None, 0]
    if mark[0] == st.weeks_lived and int(mark[1]) >= uc.max_per_week:
        return False
    return rng.random() < uc.chance


async def maybe_add(content: Content, user_id: int, tz: str, rng: random.Random) -> str | None:
    """After a lived week: maybe write a unique event and put it at the end of the player's queue. Returns its id.

    Runs outside any unit of work (the call to Claude takes seconds), reads the character, asks, validates, and only
    then writes. A failure of any kind returns None and costs nothing in the game."""
    if not narrator.is_live():
        return None
    try:
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, user_id)
            if ch is None:
                return None
            st = ch.state
            if jail_engine.in_jail(st) or not due(content, st, rng):
                return None
            note = await narration.legacy_note(s, content, ch)
            weeks_lived = st.weeks_lived
            year = st.current_year
            payload = build_payload(content, st, note)
        nb = content.balance.narrator
        uc = content.balance.events.unique
        data = await narrator.generate_json(
            "unique_event", payload, year=year, title_max=nb.unique_title_max_chars, categories="|".join(EVENT_CATEGORIES),
            text_max=int(nb.message_max_chars * nb.target_ratio), label_max=nb.unique_label_max_chars,
            stats="|".join(STATS), skills="|".join(SKILLS), dv_min=uc.dv_min, dv_max=uc.dv_max,
            out_max=nb.unique_outcome_max_chars, xp_max=uc.skill_xp_max,
        )
        if data is None:
            return None
        event_id = f"{PREFIX}{ch.id}_{weeks_lived}"
        try:
            event = validate(content, st, data, event_id)
        except (_Invalid, ValidationError) as err:
            log.warning("unique event dropped: %s", str(err).splitlines()[0][:200])
            return None
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, user_id)
            if ch is None or ch.id != int(event_id[len(PREFIX):].split("_")[0]) or ch.state.weeks_lived != weeks_lived:
                return None  # the player moved on while Claude was writing
            stored = dict(ch.state.flags.get(FLAG_UNIQUE_EVENTS) or {})
            stored[event.id] = event.model_dump(mode="json")
            ch.state.flags[FLAG_UNIQUE_EVENTS] = stored
            mark = ch.state.flags.get(FLAG_UNIQUE_WEEK) or [None, 0]
            ch.state.flags[FLAG_UNIQUE_WEEK] = [weeks_lived, (int(mark[1]) + 1) if mark[0] == weeks_lived else 1]
            from app.services.week import append_capped

            append_capped(ch, event.id, content.balance.events.max_per_press)
            await save_active(s, ch, tz)
        register(content, event)
        log.info("unique event %s for character %s: %s", event.id, ch.id, event.title)
        return event.id
    except Exception:  # a bonus event must never break the week
        log.exception("unique event failed")
        return None
