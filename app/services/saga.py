"""The endless saga (owner request, like NoClip): one long story per character, told in steps.

- A step is an ordinary forced-only event whose id starts with `saga_` (written: `content/events_saga.yaml`) or `sagx_`
  (written by Claude, kept in the table `saga_events`). It opens by marks: `conditions.flags_all` / `flags_none` on the
  character flags `s_<word>`, which the choices of earlier steps set with `flag_set:s_<word>`.
- After a lived week, with `players.saga.chance`, the deck of the steps the character can open now is shuffled and the
  first one joins the week's queue. A step is played once per life (`saga_played`).
- When the deck is empty and the narrator is live, Claude is asked for the next step: it continues the character's story
  from its marks and the titles of the last steps, requires the newest mark and gives every choice a new mark. The step
  is checked like a unique event (effects on the menu and clamped, no digits in texts) and is stored for everybody:
  another character with the same mark can draw it later. The library grows without an end.
"""

from __future__ import annotations

import logging
import random
import re
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select

from app.content.loader import Content
from app.content.schemas import EVENT_CATEGORIES, SKILLS, STATS, Event
from app.db import session as db
from app.db.models import SagaEvent
from app.engine import events as engine_events
from app.engine import jail as jail_engine
from app.narrator import narrator
from app.services import game, narration, uniqueevent
from app.services.game import save_active
from app.services.week import pending_events, set_pending

log = logging.getLogger(__name__)

WRITTEN, GENERATED = "saga_", "sagx_"
FLAG_PLAYED = "saga_played"   # ids of the saga steps this character has had
FLAG_LOG = "saga_log"         # titles of the last steps, for Claude
MARK_RE = re.compile(r"^s_[a-z][a-z0-9_]{1,30}$")


def is_saga(event_id: str) -> bool:
    return event_id.startswith(WRITTEN) or event_id.startswith(GENERATED)


def marks(st: Any) -> list[str]:
    return sorted(k for k, v in st.flags.items() if k.startswith("s_") and v)


def deck(content: Content, st: Any, rng: random.Random) -> list[str]:
    """The saga steps the character can open now, shuffled."""
    played = set(st.flags.get(FLAG_PLAYED) or [])
    flags = None
    out = []
    for eid, ev in content.events.items():
        if not is_saga(eid) or eid in played:
            continue
        if flags is None:
            from app.engine import derived

            flags = derived.event_flags(st, content)
        if engine_events.matches(ev, st, content, flags):
            out.append(eid)
    rng.shuffle(out)
    return out


# -- the library of generated steps ----------------------------------------------------------------------------------


async def restore(content: Content) -> int:
    """At start: every step Claude ever wrote joins `content.events`."""
    async with db.session_scope() as s:
        rows = list((await s.execute(select(SagaEvent))).scalars())
    n = 0
    for row in rows:
        try:
            content.events[row.id] = Event.model_validate(row.data)
            n += 1
        except ValidationError:
            log.warning("saga step %s is broken and is skipped", row.id)
    return n


def _marks_of(raw: Any, limit: int) -> list[str]:
    if not isinstance(raw, list):
        return []
    out = []
    for m in raw[:limit]:
        if isinstance(m, str):
            m = m.strip().lower()
            if not m.startswith("s_"):
                m = "s_" + m
            if MARK_RE.match(m):
                out.append(m)
    return out


def _with_marks(event: Event, need: list[str], choice_marks: list[list[str]]) -> Event:
    """The checked event with its saga conditions and the marks every choice sets."""
    data = event.model_dump(mode="json")
    data["conditions"] = {**data.get("conditions", {}), "flags_all": need}
    for c, ms in zip(data["choices"], choice_marks):
        add = {f"flag_set:{m}": True for m in ms}
        if c.get("check"):
            for side in ("success", "fail"):
                c[side]["effects"] = {**(c[side].get("effects") or {}), **add}
        else:
            c["effects"] = {**(c.get("effects") or {}), **add}
    return Event.model_validate(data)


async def _write(content: Content, user_id: int, tz: str, rng: random.Random) -> str | None:
    """Ask Claude for the next step of this character's saga; store it for everybody. Returns its id."""
    cfg = content.balance.players.saga
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, user_id)
        if ch is None:
            return None
        st = ch.state
        have = marks(st)
        if not have:
            return None
        newest = str(st.flags.get("saga_last_mark") or have[-1])
        payload = uniqueevent.build_payload(content, st, await narration.legacy_note(s, content, ch))
        payload["kind"] = "saga_step"
        payload["saga"] = {"marks": have, "newest_mark": newest,
                           "last_steps": [x.get("title") for x in (st.flags.get(FLAG_LOG) or [])][-cfg.log_steps:]}
        weeks, year = st.weeks_lived, st.current_year
    nb = content.balance.narrator
    uc = content.balance.events.unique
    data = await narrator.generate_json(
        "saga_step", payload, year=year, title_max=nb.unique_title_max_chars, categories="|".join(EVENT_CATEGORIES),
        text_max=int(nb.message_max_chars * nb.target_ratio), label_max=nb.unique_label_max_chars,
        stats="|".join(STATS), skills="|".join(SKILLS), dv_min=uc.dv_min, dv_max=uc.dv_max,
        out_max=nb.unique_outcome_max_chars, xp_max=uc.skill_xp_max, max_marks=cfg.max_new_marks,
    )
    if data is None:
        return None
    event_id = f"{GENERATED}{user_id % 100000}_{weeks}_{rng.randint(100, 999)}"
    async with db.session_scope() as s:
        ch = await game.get_active_character(s, user_id)
        if ch is None or ch.state.weeks_lived != weeks:
            return None
        try:
            base = uniqueevent.validate(content, ch.state, data, event_id)
            choice_marks = [_marks_of(c.get("marks"), cfg.max_new_marks) if isinstance(c, dict) else []
                            for c in data.get("choices", [])]
            if not all(choice_marks):
                raise uniqueevent._Invalid("every choice needs a new mark")
            event = _with_marks(base, [newest], choice_marks)
        except (uniqueevent._Invalid, ValidationError) as err:
            log.warning("saga step dropped: %s", str(err).splitlines()[0][:200])
            return None
        s.add(SagaEvent(id=event.id, data=event.model_dump(mode="json"), author_tg=user_id))
        content.events[event.id] = event
        _queue(ch, event)
        await save_active(s, ch, tz)
    log.info("saga step %s written for %s: %s", event.id, user_id, event.title)
    return event.id


def _queue(ch: Any, event: Event) -> None:
    st = ch.state
    st.flags[FLAG_PLAYED] = list(st.flags.get(FLAG_PLAYED) or []) + [event.id]
    st.flags[FLAG_LOG] = (list(st.flags.get(FLAG_LOG) or []) + [{"title": event.title}])[-20:]
    from app.services.week import append_capped

    append_capped(ch, event.id)


async def after_week(content: Content, user_id: int, tz: str, rng: random.Random) -> str | None:
    """After a lived week: maybe the next step of the saga joins the queue (written, or new from Claude)."""
    cfg = content.balance.players.saga
    try:
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, user_id)
            if ch is None or jail_engine.in_jail(ch.state) or rng.random() >= cfg.chance:
                return None
            cards = deck(content, ch.state, rng)
            if cards:
                event = content.events[cards[0]]
                _queue(ch, event)
                await save_active(s, ch, tz)
                return event.id
        if narrator.is_live() and rng.random() < cfg.ai_chance:
            return await _write(content, user_id, tz, rng)
        return None
    except Exception:  # the saga must never break the week
        log.exception("saga step failed")
        return None
