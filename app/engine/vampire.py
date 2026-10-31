"""Night-taxi vampires (owner request 24.10). Pure.

A character with the flag `vampire` carries the Дренажный контур (cyberware vamp_drain) and works for the night taxi
(careers.yaml night_taxi: requires and binds the flag). Drained blood (flag `vamp_blood`, 0..blood_max) inflates the
body: hp max +1 per point, regen +1 per `regen_per`. The body burns `decay` a week; with none left the hunger hurts.
"""

from __future__ import annotations

import random
from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from app.content.loader import Content
from app.engine import checks
from app.engine.checks import CheckResult
from app.engine.state import CharacterState

FLAG = "vampire"
FLAG_BLOOD = "vamp_blood"


def cfg(content: Content):  # noqa: ANN201
    return content.balance.vampire


def is_vampire(state: CharacterState) -> bool:
    return bool(state.flags.get(FLAG))


def blood(state: CharacterState) -> int:
    return int(state.flags.get(FLAG_BLOOD, 0) or 0)


def add_blood(state: CharacterState, content: Content, n: int) -> int:
    state.flags[FLAG_BLOOD] = max(0, min(cfg(content).blood_max, blood(state) + n))
    return blood(state)


def mod_sources(state: CharacterState, content: Content) -> Iterator[tuple[str, Mapping[str, int]]]:
    if not is_vampire(state):
        return
    b = blood(state)
    if b > 0:
        yield "vamp_blood", {"hp_max": b, "hp_regen": b // max(1, cfg(content).regen_per)}
    elif cfg(content).hunger_mods:
        yield "vamp_hunger", dict(cfg(content).hunger_mods)


def weekly(state: CharacterState, content: Content) -> int:
    """The body burns the blood. Returns how much went."""
    if not is_vampire(state):
        return 0
    before = blood(state)
    add_blood(state, content, -cfg(content).decay)
    return before - blood(state)


@dataclass
class FeedPlan:
    key: str
    ok: bool
    check: CheckResult | None
    effects: dict[str, int]
    text: str


def plan_feed(state: CharacterState, content: Content, rng: random.Random, key: str) -> FeedPlan | None:
    f = cfg(content).feeds.get(key)
    if f is None:
        return None
    chk = None
    ok = True
    if f.stat:
        chk = checks.roll_check(state, content, rng, f.stat, f.skill, f.dv)
        ok = chk.ok
    fx: dict[str, int] = {}
    if ok:
        fx["vamp_blood"] = f.blood
        fx["hp"] = f.blood
        if f.eddies:
            fx["eddies"] = f.eddies
        if f.humanity:
            fx["humanity"] = f.humanity
        if f.heat:
            fx["heat"] = f.heat
        return FeedPlan(key, True, chk, fx, f.text)
    if f.fail_heat:
        fx["heat"] = f.fail_heat
    if f.fail_hp:
        fx["hp"] = -f.fail_hp
    return FeedPlan(key, False, chk, fx, f.fail_text)
