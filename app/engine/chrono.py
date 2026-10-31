"""The «In Time» crossover (owner request): Aeon's clock of the eternal life. Pure functions.

A character with the clock does not die of old age; instead every week of life costs a week of time, and the clock
at zero kills. Time is earned in Aeon (jobs, the exchange, poker, a robbery) and can be given to another player.

flag `chrono` = {weeks, since}
"""

from __future__ import annotations

from app.content.loader import Content
from app.engine.state import CharacterState

FLAG = "chrono"
DEATH = "time_out"


def cfg(content: Content):  # noqa: ANN201
    return content.balance.chrono


def has(state: CharacterState) -> bool:
    return isinstance(state.flags.get(FLAG), dict)


def weeks(state: CharacterState) -> int:
    r = state.flags.get(FLAG)
    return int(r.get("weeks", 0) or 0) if isinstance(r, dict) else 0


def install(state: CharacterState, content: Content) -> None:
    state.flags[FLAG] = {"weeks": cfg(content).implant_weeks, "since": state.weeks_lived}


def add(state: CharacterState, n: int) -> int:
    """Add (or take, n < 0) weeks of time; returns the new balance (never below zero)."""
    r = dict(state.flags.get(FLAG) or {})
    r["weeks"] = max(0, int(r.get("weeks", 0) or 0) + n)
    state.flags[FLAG] = r
    return int(r["weeks"])


def tick(state: CharacterState) -> bool:
    """One week of life. True when the clock ran out now."""
    if not has(state):
        return False
    return add(state, -1) <= 0


def label(state: CharacterState) -> str | None:
    if not has(state):
        return None
    n = weeks(state)
    y, w = divmod(n, 52)
    return (f"{y} г. {w} нед." if y else f"{w} нед.")
