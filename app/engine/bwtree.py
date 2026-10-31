"""The Blackwall branch of the character (owner request 25.10). Pure.

«Прокачка» → «ЗАСЛОН»: for a character on the side of the Blackwall (the cult, the Avatar chain started, an Avatar).
The branch mixes the Blackwall with the character: perks need the character's own attributes and feed them
(content/bw_perks.yaml). Blackwall points come from the path itself, never from the usual talent points:
the base on joining, each chain step, each cult rank, each taint tier, the Avatar, the favor of the cult.

flag `bw_tree` = {key: rank}.
"""

from __future__ import annotations

import functools
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import yaml

from app.content.loader import Content
from app.engine import blackwall as bw
from app.engine.state import CharacterState

FLAG = "bw_tree"
FILE = Path(__file__).resolve().parent.parent.parent / "content" / "bw_perks.yaml"
ATTRS = ("BODY", "REF", "TECH", "INT", "COOL", "EMP", "LUCK")


@functools.cache
def tree() -> dict[str, Any]:
    with FILE.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def perks() -> dict[str, dict[str, Any]]:
    return dict(tree().get("perks") or {})


def cfg(content: Content):  # noqa: ANN201
    return content.balance.bw_tree


def on_side(state: CharacterState) -> bool:
    return bool(bw.in_cult(state) or bw.stage(state) >= 1 or bw.is_avatar(state))


def ranks(state: CharacterState) -> dict[str, int]:
    r = state.flags.get(FLAG)
    return {str(k): int(v) for k, v in r.items()} if isinstance(r, dict) else {}


def earned(state: CharacterState, content: Content) -> int:
    if not on_side(state):
        return 0
    c = cfg(content)
    return (c.base + c.per_step * bw.stage(state) + c.per_rank * bw.cult_rank(state, content)
            + c.per_tier * bw.tier(state, content) + (c.avatar if bw.is_avatar(state) else 0)
            + min(c.favor_cap, bw.favor(state) // max(1, c.favor_per)))


def spent(state: CharacterState) -> int:
    return sum(ranks(state).values())


def points(state: CharacterState, content: Content) -> int:
    return max(0, earned(state, content) - spent(state))


def top_stat(state: CharacterState) -> str:
    return max(ATTRS, key=lambda k: (int(state.stats.get(k, 0)), -ATTRS.index(k)))


def block(state: CharacterState, content: Content, key: str) -> str | None:
    p = perks().get(key)
    if p is None:
        return "none"
    if not on_side(state):
        return "side"
    have = ranks(state).get(key, 0)
    if have >= int(p.get("ranks", 1)):
        return "max"
    if points(state, content) <= 0:
        return "points"
    for stat, need in (p.get("stat") or {}).items():
        if int(state.stats.get(stat, 0)) < int(need) + have:
            return f"need:{stat}:{int(need) + have}"
    if p.get("tier") and bw.tier(state, content) < int(p["tier"]):
        return f"tier:{p['tier']}"
    if p.get("step") and bw.stage(state) < int(p["step"]):
        return f"step:{p['step']}"
    if p.get("avatar") and not bw.is_avatar(state):
        return "avatar"
    return None


def buy(state: CharacterState, content: Content, key: str) -> str | None:
    why = block(state, content, key)
    if why:
        return why
    r = ranks(state)
    r[key] = r.get(key, 0) + 1
    state.flags[FLAG] = r
    return None


def mod_sources(state: CharacterState, content: Content) -> Iterator[tuple[str, Mapping[str, int]]]:  # noqa: ARG001
    r = ranks(state)
    if not r or not on_side(state):
        return
    total: dict[str, int] = {}
    for key, rk in r.items():
        p = perks().get(key)
        if p is None or rk <= 0:
            continue
        for m, v in (p.get("mods") or {}).items():
            total[m] = total.get(m, 0) + int(v) * rk
        if rk >= 2:
            for m, v in (p.get("rank2") or {}).items():
                total[m] = total.get(m, 0) + int(v)
        if p.get("top_stat"):
            k = f"stat.{top_stat(state)}"
            total[k] = total.get(k, 0) + int(p["top_stat"]) * rk
    if total:
        yield "bw_tree", total


def specials(state: CharacterState) -> dict[str, int]:
    out: dict[str, int] = {}
    if not on_side(state):
        return out
    for key, rk in ranks(state).items():
        p = perks().get(key)
        if p is None or rk <= 0:
            continue
        if p.get("special"):
            out[str(p["special"])] = out.get(str(p["special"]), 0) + rk
        for s, n in (p.get("special_many") or {}).items():
            out[str(s)] = out.get(str(s), 0) + int(n)
    return out


def quiet(state: CharacterState) -> bool:
    """«Тишина за Заслоном»: the taint no longer eats humanity and mood."""
    return on_side(state) and ranks(state).get("silence", 0) > 0
