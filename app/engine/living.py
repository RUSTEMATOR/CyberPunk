"""Living conditions (owner request 23.10): the home and the food shape the body and the mood. Pure functions.

- the housing tier (0 = no home) gives weekly mods: a capsule grinds you down, a room is neutral, an apartment and a
  loft give an edge;
- a bad home and bad food make you ill now and then: flag `sick` = weeks left, mods and hp while ill; a good home
  cures twice as fast, a medic cures at once;
- a cheap implant lags now and then.
"""

from __future__ import annotations

import random
from collections.abc import Iterator, Mapping

from app.content.loader import Content
from app.engine.state import CharacterState

FLAG_SICK = "sick"


def cfg(content: Content):  # noqa: ANN201
    return content.balance.living


def tier(state: CharacterState, content: Content) -> int:
    home = content.housing.get(state.housing or "")
    return home.tier if home is not None else 0


def sick(state: CharacterState) -> int:
    return int(state.flags.get(FLAG_SICK, 0) or 0)


def mod_sources(state: CharacterState, content: Content) -> Iterator[tuple[str, Mapping[str, int]]]:
    c = cfg(content)
    mods = c.tier_mods.get(tier(state, content))
    if mods:
        yield "living", mods
    if sick(state) and c.sick_mods:
        yield "sick", c.sick_mods


def week(state: CharacterState, content: Content, rng: random.Random) -> tuple[dict, list[str]]:
    """One week: illness (comes, hurts, passes) and cheap chrome lag. Returns (effects, lines)."""
    c = cfg(content)
    fx: dict = {}
    lines: list[str] = []
    t = tier(state, content)
    n = sick(state)
    if n:
        fx["hp"] = -c.sick_hp
        n -= 2 if t >= c.fast_cure_tier else 1
        if n <= 0:
            state.flags.pop(FLAG_SICK, None)
            lines.append("Болезнь отступила.")
        else:
            state.flags[FLAG_SICK] = n
    else:
        chance = c.sick_chance.get(t, 0.0)
        life = str(state.flags.get("lifestyle") or "")
        if life == "starving":
            chance += c.starving_sick
        chance = max(0.0, chance - c.lifestyle_sick_cut.get(life, 0.0))
        if rng.random() < chance:
            state.flags[FLAG_SICK] = c.sick_weeks
            lines.append("Сырость, плохая еда, чужой кашель за стенкой: пришла болезнь. "
                         "Хороший дом лечит вдвое быстрее, медик в «Городе» — сразу.")
    cheap = [cw for cw in state.cyberware if (content.cyberware.get(cw.key) is not None
                                              and content.cyberware[cw.key].tier == c.cheap_tier)]
    for cw in cheap:
        if rng.random() < c.cheap_lag_chance:
            fx["hp"] = int(fx.get("hp", 0)) - c.cheap_lag_hp
            fx["mood"] = int(fx.get("mood", 0)) - c.cheap_lag_mood
            lines.append(f"Дешёвый имплант «{content.cyberware[cw.key].name}» лагает: дёргается, греется, бьёт током.")
            break
    return fx, lines
