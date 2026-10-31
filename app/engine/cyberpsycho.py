"""The cyberpsychopath mode (owner request): the life of a fugitive psycho goes on in the city. Pure functions.

A fugitive (humanity hit zero, PLAN 6.6a) may walk the city. The power grows with every rampage, the mind goes: the
voices choose some deeds instead of the player, the text of the window glitches, the group sees each rampage.

flag `cyberpsycho` = {rampages}
"""

from __future__ import annotations

import random
from collections.abc import Iterator, Mapping

from app.content.loader import Content
from app.engine import psychosis
from app.engine.state import CharacterState

FLAG = "cyberpsycho"
GLITCH = "█▓▒░#%"


def cfg(content: Content):  # noqa: ANN201
    return content.balance.freedom.cyberpsycho


def active(state: CharacterState) -> bool:
    return psychosis.is_fugitive(state)


def rampages(state: CharacterState) -> int:
    r = state.flags.get(FLAG)
    return int(r.get("rampages", 0) or 0) if isinstance(r, dict) else 0


def add_rampage(state: CharacterState) -> int:
    n = rampages(state) + 1
    state.flags[FLAG] = {"rampages": n}
    return n


def level(state: CharacterState, content: Content):  # noqa: ANN201
    if not active(state):
        return None
    best = None
    for lv in cfg(content).levels:
        if rampages(state) >= lv.rampages:
            best = lv
    return best


def mod_sources(state: CharacterState, content: Content) -> Iterator[tuple[str, Mapping[str, int]]]:
    lv = level(state, content)
    if lv is not None and lv.mods:
        yield "cyberpsycho", lv.mods


def glitch_rate(state: CharacterState, content: Content) -> float:
    lv = level(state, content)
    return lv.glitch if lv is not None else 0.0


def voices_take(state: CharacterState, content: Content, rng: random.Random) -> bool:
    """The voices pick the deed instead of the player."""
    lv = level(state, content)
    return lv is not None and rng.random() < lv.voices


def voice(content: Content, rng: random.Random) -> str:
    pool = cfg(content).voices
    return rng.choice(pool) if pool else ""


def glitch(text: str, rng: random.Random, rate: float) -> str:
    """Plain text only (no HTML): some letters break into noise, some words stutter."""
    if rate <= 0:
        return text
    out: list[str] = []
    for word in text.split(" "):
        if len(word) > 3 and rng.random() < rate:
            if rng.random() < 0.5:
                i = rng.randrange(len(word))
                word = word[:i] + rng.choice(GLITCH) + word[i + 1:]
            else:
                word = f"{word[:2]}-{word[:2]}-{word}"
        out.append(word)
    return " ".join(out)
