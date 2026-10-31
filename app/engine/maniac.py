"""The maniac mode (owner request). Pure functions.

Civilians killed in the city (not on the purge night) count in the flag `civ_kills`. From the first level's count the
character is a maniac: a nickname the city knows, mods by level, a thirst after weeks without a kill, clues the NCPD
and other players gather. At `reveal_clues` the name is out. A maniac may quit: `quit_weeks` without a kill.

flag `maniac` = {nick, kills, last_kill, clues, revealed, quit}
"""

from __future__ import annotations

import random
from collections.abc import Iterator, Mapping

from app.content.loader import Content
from app.engine.state import CharacterState

FLAG = "maniac"


def cfg(content: Content):  # noqa: ANN201
    return content.balance.freedom.maniac


def rec(state: CharacterState) -> dict:
    r = state.flags.get(FLAG)
    return dict(r) if isinstance(r, dict) else {}


def level(state: CharacterState, content: Content):  # noqa: ANN201
    r = rec(state)
    if not r:
        return None
    best = None
    for lv in cfg(content).levels:
        if int(r.get("kills", 0)) >= lv.kills:
            best = lv
    return best


def nickname(content: Content, rng: random.Random) -> str:
    c = cfg(content)
    return f"{rng.choice(c.first)} {rng.choice(c.second)}" if c.first and c.second else "Безликий"


def on_kill(state: CharacterState, content: Content, rng: random.Random) -> tuple[bool, str | None]:
    """A civilian killed. Returns (became a maniac now, the new level name if it grew)."""
    kills = int(state.flags.get("civ_kills", 0) or 0)
    r = rec(state)
    before = level(state, content)
    if not r:
        first = cfg(content).levels[0].kills if cfg(content).levels else 3
        if kills < first:
            return False, None
        r = {"nick": nickname(content, rng), "kills": kills, "clues": 0, "revealed": False, "last_kill": state.weeks_lived}
        state.flags[FLAG] = r
        return True, None
    r["kills"] = kills
    r["last_kill"] = state.weeks_lived
    r.pop("quit", None)
    state.flags[FLAG] = r
    after = level(state, content)
    return False, (after.name if after is not None and (before is None or after.kills != before.kills) else None)


def mod_sources(state: CharacterState, content: Content) -> Iterator[tuple[str, Mapping[str, int]]]:
    lv = level(state, content)
    if lv is not None and lv.mods:
        yield "maniac", lv.mods


def thirsty(state: CharacterState, content: Content) -> bool:
    r = rec(state)
    return bool(r) and state.weeks_lived - int(r.get("last_kill", state.weeks_lived)) > cfg(content).thirst_weeks


def quit_done(state: CharacterState, content: Content) -> bool:
    r = rec(state)
    return bool(r.get("quit")) and state.weeks_lived - int(r.get("last_kill", 0)) >= cfg(content).quit_weeks
