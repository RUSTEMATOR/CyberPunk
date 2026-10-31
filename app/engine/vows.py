"""Vows (PLAN 4.8, DESIGN.md 7a): what breaks a vow, who may take one, the streak bonuses.

Pure functions. A vow lists what it `forbids`; this module maps those words to the bus events that
break it. The break itself (streak lost, the break effects, the followup line) is `apply_effects`
with the key `vow_break:<vow>`, so the service only decides WHEN to call it.
"""

from __future__ import annotations

from typing import Any

from app.content.loader import Content
from app.content.schemas import EventChoice, Vow, VowBonus
from app.engine.state import CharacterState

# forbid word -> predicate over (bus event name, its params). `cyberware` is also broken straight
# from apply_effects when an implant goes in; the bus event backs it up for other sources.
FORBID_EVENTS: dict[str, tuple[str, ...]] = {
    "cyberware": ("cyberware_installed",),
    "violence": ("fight_won", "takedown"),
    "braindance": ("braindance",),
    "crime": ("crime_done",),
    "lies": ("lie",),
}

TAKE_HELD = "held"
TAKE_CHROME = "chrome"  # the vow forbids chrome and the body already has implants
TAKE_UNKNOWN = "unknown"


def forbids_hit(event: str, params: dict[str, Any]) -> set[str]:
    """The forbid words an event breaks. A choice with a violent deed arrives as `event_resolved`
    with `nonviolent=False`."""
    hit = {word for word, events in FORBID_EVENTS.items() if event in events}
    if event == "event_resolved" and params.get("nonviolent") is False:
        hit.add("violence")
    return hit


def broken_vows(state: CharacterState, content: Content, event: str, params: dict[str, Any]) -> list[str]:
    """Held vows that this event breaks."""
    hit = forbids_hit(event, params)
    if not hit:
        return []
    out: list[str] = []
    for key in state.vows:
        vow = content.vows.get(key)
        if vow is not None and hit & set(vow.forbids):
            out.append(key)
    return out


def choice_breaks(state: CharacterState, content: Content, choice: EventChoice) -> list[str]:
    """Held vows an event choice would break (for the warning on its button)."""
    params: dict[str, Any] = {"nonviolent": not choice.is_violent}
    out = broken_vows(state, content, "event_resolved", params)
    if any(k.split(":")[0] == "lie" for k in choice.bus):
        out += [k for k in broken_vows(state, content, "lie", {}) if k not in out]
    return out


def can_take(state: CharacterState, content: Content, key: str) -> str | None:
    """Why the vow cannot be taken now, or None."""
    vow = content.vows.get(key)
    if vow is None:
        return TAKE_UNKNOWN
    if key in state.vows:
        return TAKE_HELD
    if "cyberware" in vow.forbids and state.cyberware:
        return TAKE_CHROME
    return None


def take(state: CharacterState, content: Content, key: str) -> str | None:
    """Take the vow with a fresh streak. Returns the refusal reason or None."""
    why = can_take(state, content, key)
    if why is None:
        state.vows[key] = 0
    return why


def reached_bonuses(vow: Vow, weeks: int) -> list[VowBonus]:
    return [b for b in vow.streak_bonuses if weeks >= b.weeks]


def next_bonus(vow: Vow, weeks: int) -> VowBonus | None:
    for b in vow.streak_bonuses:
        if weeks < b.weeks:
            return b
    return None


def bonus_at(vow: Vow, weeks: int) -> VowBonus | None:
    """The bonus that is reached exactly in this week of the streak."""
    for b in vow.streak_bonuses:
        if b.weeks == weeks:
            return b
    return None
