"""The price of power (owner request 25.10, docs/PLAN_ENDGAME2.md part 5): the body of a character who outgrew the city
wears every lived month (services/wear.py). The wear cuts the fight and the health; a ripper's maintenance takes it off.

flag `wear` = int level.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping

from app.content.loader import Content
from app.engine.state import CharacterState

FLAG = "wear"


def cfg(content: Content):  # noqa: ANN201
    return content.balance.wear


def level(state: CharacterState, content: Content | None = None) -> int:
    if content is not None and not cfg(content).enabled:
        return 0
    return int(state.flags.get(FLAG, 0) or 0)


def mods(content: Content, lvl: int) -> dict[str, int]:
    c = cfg(content)
    out: dict[str, int] = {}
    if lvl <= 0:
        return out
    if lvl // c.check_per:
        out["check.combat"] = -(lvl // c.check_per)
    out["hp_max"] = -lvl * c.hp_per_level
    ref = sum(1 for x in c.ref_at if lvl >= x)
    if ref:
        out["stat.REF"] = -ref
    return out


def mod_sources(state: CharacterState, content: Content) -> Iterator[tuple[str, Mapping[str, int]]]:
    m = mods(content, level(state, content))
    if m:
        yield "wear", m


def price(content: Content, lvl: int) -> int:
    return cfg(content).price_per_level * max(0, lvl)
