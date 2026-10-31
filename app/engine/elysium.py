"""The «Elysium» crossover (owner request): a station in orbit for the rich. Pure functions.

flag `elysium` = {citizen, pass_until, pass_from, medbay_at}: a citizen lives with the station's mods and a free
Med-Bay every few weeks; a smuggled visitor is on board until the next press and gets one Med-Bay.
flag `radiation` = weeks left after a lethal dose at the plant; only a Med-Bay saves.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping

from app.content.loader import Content
from app.engine.state import CharacterState

FLAG, RAD = "elysium", "radiation"
STATION, GROUND = "ely_station", "ely_la"  # owner request 30.10: the districts of the Elysium city (world_crossover.yaml)
DEATH = "radiation"


def cfg(content: Content):  # noqa: ANN201
    return content.balance.elysium


def rec(state: CharacterState) -> dict:
    r = state.flags.get(FLAG)
    return dict(r) if isinstance(r, dict) else {}


def citizen(state: CharacterState) -> bool:
    return bool(rec(state).get("citizen"))


def on_board(state: CharacterState) -> bool:
    r = rec(state)
    return bool(r.get("citizen")) or int(r.get("pass_until", -1)) >= state.weeks_lived


def medbay_ready(state: CharacterState, content: Content) -> bool:
    r = rec(state)
    used = int(r.get("medbay_at", -10**6))
    if r.get("citizen"):
        return state.weeks_lived - used >= cfg(content).medbay_cooldown_weeks
    return on_board(state) and used < int(r.get("pass_from", 0))


def set_rec(state: CharacterState, **kw: object) -> None:
    r = rec(state)
    r.update(kw)
    state.flags[FLAG] = r


def radiation(state: CharacterState) -> int:
    return int(state.flags.get(RAD, 0) or 0)


def tick_radiation(state: CharacterState) -> bool:
    """One week with a lethal dose. True when it kills now."""
    n = radiation(state)
    if n <= 0:
        return False
    n -= 1
    state.flags[RAD] = n
    if n <= 0:
        state.flags.pop(RAD, None)
        return True
    return False


def mod_sources(state: CharacterState, content: Content) -> Iterator[tuple[str, Mapping[str, int]]]:
    mods = cfg(content).citizen_mods
    if mods and citizen(state):
        yield "elysium", mods
