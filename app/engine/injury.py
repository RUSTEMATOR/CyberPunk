"""Injuries (owner request 26.10, docs/PLAN_EQUILIBRIUM.md part 2): a lost arm, leg or eye, a damaged implant.

The flag `injuries` = {part: "lost"} | {"dmg:<implant key>": 1}. A part with an implant in its slot takes the hit on
the implant: a critical one tears it off (the part is lost too), else the implant is damaged until a ripper repairs
it. A part of flesh is lost at once. Any implant installed into the slot later gives the part back (on_install); the
clone corp can grow a part of flesh (services/clones.py). Pure functions; implant removal goes through apply_effects.
"""

from __future__ import annotations

import random
from collections.abc import Iterator, Mapping

from app.content.loader import Content
from app.engine.state import CharacterState

FLAG = "injuries"
PARTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "arm_l": ("левая рука", ("forearms", "hands")),
    "arm_r": ("правая рука", ("forearms", "hands")),
    "leg_l": ("левая нога", ("legs",)),
    "leg_r": ("правая нога", ("legs",)),
    "eye_l": ("левый глаз", ("eyes",)),
    "eye_r": ("правый глаз", ("eyes",)),
}
KIND_MODS: dict[str, dict[str, int]] = {
    "arm": {"check.combat": -1, "melee_damage": -2},
    "leg": {"flee": -3, "ap_week": -1},
    "eye": {"ranged_damage": -1, "check.combat": -1},
}


def cfg(content: Content):  # noqa: ANN201
    return content.balance.injury


def injuries(state: CharacterState) -> dict[str, object]:
    return dict(state.flags.get(FLAG) or {})


def lost(state: CharacterState) -> list[str]:
    return [p for p, v in injuries(state).items() if p in PARTS and v == "lost"]


def damaged(state: CharacterState) -> list[str]:
    return [k[4:] for k in injuries(state) if k.startswith("dmg:")]


def implants_in(state: CharacterState, content: Content, part: str) -> list[str]:
    slots = PARTS[part][1]
    return [i.key for i in state.cyberware if i.key in content.cyberware and content.cyberware[i.key].slot in slots]


def part_name(part: str) -> str:
    return PARTS[part][0]


def lost_word(part: str) -> str:
    """«утрачена» / «утрачен» by the part's gender."""
    return "утрачен" if part.startswith("eye") else "утрачена"


def roll(state: CharacterState, content: Content, rng: random.Random, *, downed: bool, heavy: bool,
         crit_bonus: float = 0.0) -> tuple[str | None, str, dict[str, bool]]:
    """Whether the fight left an injury: (part, what happened, effects to apply). Nothing when the dice say no."""
    c = cfg(content)
    if not c.enabled:
        return None, "", {}
    chance = c.downed_chance if downed else c.heavy_chance if heavy else 0.0
    if chance <= 0 or rng.random() >= chance:
        return None, "", {}
    return hit_part(state, content, rng, crit_bonus=crit_bonus)


def hit_part(state: CharacterState, content: Content, rng: random.Random, *, crit_bonus: float = 0.0,
             part: str | None = None) -> tuple[str | None, str, dict[str, bool]]:
    """One part takes the blow (the flag is changed here; implant removal is returned as effects)."""
    c = cfg(content)
    inj = injuries(state)
    pool = [p for p in PARTS if inj.get(p) != "lost"]
    if part is None:
        if not pool:
            return None, "", {}
        part = rng.choice(pool)
    chrome = implants_in(state, content, part)
    fx: dict[str, bool] = {}
    if chrome:
        key = rng.choice(chrome)
        name = content.cyberware[key].name
        if rng.random() < c.crit_chance + crit_bonus:
            fx[f"cyberware_lose:{key}"] = True
            inj[part] = "lost"
            state.flags[FLAG] = inj
            return part, (f"{name} разлетается на куски, {part_name(part)} {lost_word(part)} вместе с ним. Нужен новый "
                          f"имплант."), fx
        inj[f"dmg:{key}"] = 1
        state.flags[FLAG] = inj
        return part, f"{name} повреждён: искрит и слушается через раз. Риппер починит.", fx
    inj[part] = "lost"
    state.flags[FLAG] = inj
    return part, (f"Увечье: {part_name(part)} {lost_word(part)}. Вырастить плоть заново очень дорого; имплант в этот "
                  f"слот у любого риппера вернёт часть тела."), fx


def on_install(state: CharacterState, content: Content, key: str) -> None:
    """A new implant in a slot gives back every lost part of that slot."""
    cw = content.cyberware.get(key)
    inj = injuries(state)
    if cw is None or not inj:
        return
    for part, (_, slots) in PARTS.items():
        if inj.get(part) == "lost" and cw.slot in slots:
            inj.pop(part, None)
    state.flags[FLAG] = inj


def repair(state: CharacterState, key: str) -> None:
    inj = injuries(state)
    inj.pop(f"dmg:{key}", None)
    state.flags[FLAG] = inj


def regrow(state: CharacterState, part: str) -> None:
    inj = injuries(state)
    inj.pop(part, None)
    state.flags[FLAG] = inj


def repair_price(content: Content, key: str) -> int:
    cw = content.cyberware.get(key)
    return max(cfg(content).repair_min, int((cw.price if cw else 0) * cfg(content).repair_pct / 100))


def mods(state: CharacterState) -> dict[str, int]:
    out: dict[str, int] = {}
    for part in lost(state):
        for k, v in KIND_MODS[part[:3]].items():
            out[k] = out.get(k, 0) + v
    if damaged(state):
        out["check.combat"] = out.get("check.combat", 0) - min(2, len(damaged(state)))
    return out


def mod_sources(state: CharacterState) -> Iterator[tuple[str, Mapping[str, int]]]:
    m = mods(state)
    if m:
        yield "injury", m


def describe(state: CharacterState, content: Content) -> list[str]:
    out = [f"• {part_name(p)}: {lost_word(p)}" for p in lost(state)]
    out += [f"• {content.cyberware[k].name if k in content.cyberware else k}: повреждён" for k in damaged(state)]
    return out
