"""Season and weather (owner request). Pure functions.

The season comes from the character's own week of the year (age_weeks). The weather is rolled every lived week by the
season's weights (some kinds only in their districts) and kept in the flag `weather`; its mods count in derived.py
and its weekly effects are added to the week (engine/week.py). Events may ask for seasons and weather.
"""

from __future__ import annotations

import random
from collections.abc import Iterator, Mapping

from app.content.loader import Content
from app.engine.state import CharacterState

FLAG = "weather"


def season(state: CharacterState, content: Content):  # noqa: ANN201
    w = state.age_weeks % content.balance.time.weeks_per_year
    for s in content.balance.weather.seasons:
        a, b = s.weeks
        if (a <= w <= b) if a <= b else (w >= a or w <= b):
            return s
    return None


def kind(content: Content, key: str | None):  # noqa: ANN201
    return next((k for k in content.balance.weather.kinds if k.key == key), None)


def current(state: CharacterState, content: Content):  # noqa: ANN201
    return kind(content, state.flags.get(FLAG))


def roll(state: CharacterState, content: Content, rng: random.Random):  # noqa: ANN201
    """The weather of the coming week (stored on the character)."""
    s = season(state, content)
    pool = [(k, k.weights.get(s.key, 0) if s else 1) for k in content.balance.weather.kinds
            if not k.districts or state.district in k.districts]
    pool = [(k, w) for k, w in pool if w > 0]
    if not pool:
        return None
    total = sum(w for _, w in pool)
    x = rng.uniform(0, total)
    for k, w in pool:
        x -= w
        if x <= 0:
            state.flags[FLAG] = k.key
            return k
    state.flags[FLAG] = pool[-1][0].key
    return pool[-1][0]


def mod_sources(state: CharacterState, content: Content) -> Iterator[tuple[str, Mapping[str, int]]]:
    s = season(state, content)
    if s is not None and s.mods:
        yield f"season:{s.key}", s.mods
    k = current(state, content)
    if k is not None and k.mods:
        yield f"weather:{k.key}", k.mods
