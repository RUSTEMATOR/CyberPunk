"""The «Equilibrium» crossover (owner request 23.10): Libria, the city where feeling is a crime. Pure functions.

flag `libria` = {dose_until, dosed, cleric, art}: a Prozium course lasts until `dose_until` (weeks_lived); `dosed` marks
that a course ran and its end has not been felt yet; `cleric` is a Grammaton Cleric; `art` is how many confiscated
works of art the character hides at home.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping

from app.content.loader import Content
from app.engine.state import CharacterState

FLAG = "libria"


def cfg(content: Content):  # noqa: ANN201
    return content.balance.libria


def rec(state: CharacterState) -> dict:
    r = state.flags.get(FLAG)
    return dict(r) if isinstance(r, dict) else {}


def set_rec(state: CharacterState, **kw: object) -> None:
    r = rec(state)
    r.update(kw)
    state.flags[FLAG] = r


def on_dose(state: CharacterState) -> bool:
    return int(rec(state).get("dose_until", -1)) >= state.weeks_lived


def dose_left(state: CharacterState) -> int:
    return max(0, int(rec(state).get("dose_until", -1)) - state.weeks_lived + 1)


def cleric(state: CharacterState) -> bool:
    return bool(rec(state).get("cleric"))


def art(state: CharacterState) -> int:
    return int(rec(state).get("art", 0) or 0)


def mod_sources(state: CharacterState, content: Content) -> Iterator[tuple[str, Mapping[str, int]]]:
    c = cfg(content)
    if on_dose(state) and c.dose_mods:
        yield "prozium", c.dose_mods
    if cleric(state) and c.cleric_mods:
        yield "cleric", c.cleric_mods
