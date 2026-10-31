"""Character levels and perk trees (owner request 24.10, after Cyberpunk 2077). Pure.

Flag `lvl` = {xp, level, attr, perk, perks {key: rank}}. Experience comes with every skill experience the character
gains (effects.skill_xp). Each level gives one attribute point (+1 to a stat, up to perks.stat_cap) and one perk point (owner report 26.10: above stat_cap up to
stat_legend_cap for more points each, then attribute points turn into perk points).
Perks (content/perks.yaml) give modifiers per rank (derived.collect_mods) and combat specials (engine/combat.py reads
`specials`).
"""

from __future__ import annotations

import functools
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import yaml

from app.content.loader import Content
from app.engine.state import CharacterState

FLAG = "lvl"
FILE = Path(__file__).resolve().parent.parent.parent / "content" / "perks.yaml"
TREES = ("BODY", "REF", "TECH", "INT", "COOL", "EMP", "LUCK")


@functools.cache
def trees() -> dict[str, dict[str, Any]]:
    with FILE.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def perk(key: str) -> tuple[str, dict[str, Any]] | None:
    for tree, t in trees().items():
        if key in (t.get("perks") or {}):
            return tree, t["perks"][key]
    return None


def cfg(content: Content):  # noqa: ANN201
    return content.balance.perks


def rec(state: CharacterState, content: Content) -> dict[str, Any]:
    """The level record; an existing character gets a start level from the skills it already has.
    Owner request 24.10: skill points came later; a record without them gets them for the levels it already has."""
    c = cfg(content)
    r = state.flags.get(FLAG)
    if isinstance(r, dict):
        out = dict(r)
        out["skill"] = 0  # owner request 24.10 (later): skills grow only by training and use, no skill points
        m = float(c.xp_curve_mult)
        was = float(out.get("xpm", 1.0) or 1.0)
        if was != m:  # owner request 26.10: the curve got steeper; the same share of the way to the next level
            out["xp"] = int(round(int(out.get("xp", 0)) * m / was))
            out["xpm"] = m
            state.flags[FLAG] = dict(out)
        return out
    total = sum(sk.level for sk in state.skills.values())
    lv = max(1, min(c.start_cap, 1 + total // max(1, c.start_per_skill_levels) - 2))
    xp = int(c.level_xp[min(lv, len(c.level_xp)) - 1] * c.xp_curve_mult)
    return {"xp": xp, "level": lv, "attr": lv - 1, "perk": lv - 1, "skill": 0,
            "perks": {}, "xpm": float(c.xp_curve_mult)}


def save(state: CharacterState, r: dict[str, Any]) -> None:
    state.flags[FLAG] = r


def level_for(content: Content, xp: int) -> int:
    lv = 1
    m = cfg(content).xp_curve_mult
    for i, need in enumerate(cfg(content).level_xp):
        if xp >= need * m:
            lv = i + 1
    return lv


def next_xp(content: Content, level: int) -> int | None:
    lx = cfg(content).level_xp
    return int(lx[level] * cfg(content).xp_curve_mult) if level < len(lx) else None


def add_xp(state: CharacterState, content: Content, n: int) -> int:
    """Experience for the character; returns the levels gained (each with an attribute and a perk point)."""
    if n <= 0:
        return 0
    r = rec(state, content)
    r["xp"] = int(r.get("xp", 0)) + n
    new = level_for(content, r["xp"])
    gained = max(0, new - int(r.get("level", 1)))
    if gained:
        r["level"] = new
        r["attr"] = int(r.get("attr", 0)) + gained
        r["perk"] = int(r.get("perk", 0)) + gained
    save(state, r)
    return gained


def on_skill_level(state: CharacterState, content: Content) -> int:
    """Owner request 24.10: a skill level-up is worth extra experience, and every few of them a perk point."""
    c = cfg(content)
    r = rec(state, content)
    n = int(r.get("slv", 0)) + 1
    if n >= max(1, c.skill_levels_per_talent):
        n = 0
        r["perk"] = int(r.get("perk", 0)) + 1
    r["slv"] = n
    save(state, r)
    return add_xp(state, content, c.skill_level_xp)


def raise_skill(state: CharacterState, content: Content, skill: str) -> str | None:  # noqa: ARG001
    """Owner request 24.10 (later): skill points are gone, skills grow only by training and use."""
    return "points"


def rank(state: CharacterState, key: str) -> int:
    r = state.flags.get(FLAG)
    return int(((r or {}).get("perks") or {}).get(key, 0) or 0) if isinstance(r, dict) else 0


def specials(state: CharacterState) -> dict[str, int]:
    """Combat specials and their ranks."""
    out: dict[str, int] = {}
    r = state.flags.get(FLAG)
    if isinstance(r, dict):
        for key, rk in (r.get("perks") or {}).items():
            found = perk(key)
            if found and found[1].get("special") and int(rk) > 0:
                out[str(found[1]["special"])] = int(rk)
    from app.engine import bwtree  # owner request 25.10: the Blackwall branch adds its own

    for k, v in bwtree.specials(state).items():
        out[k] = out.get(k, 0) + v
    return out


def mod_sources(state: CharacterState) -> Iterator[tuple[str, Mapping[str, int]]]:
    r = state.flags.get(FLAG)
    if not isinstance(r, dict):
        return
    total: dict[str, int] = {}
    implants = len(state.cyberware)
    high = sum(1 for k in ("BODY", "REF", "TECH", "INT", "COOL", "EMP", "LUCK") if int(state.stats.get(k, 0)) >= 9)
    for key, rk in (r.get("perks") or {}).items():
        found = perk(key)
        if not found:
            continue
        p = found[1]
        if implants < int(p.get("min_implants", 0) or 0):
            continue
        for m, v in (p.get("mods") or {}).items():
            total[m] = total.get(m, 0) + int(v) * int(rk)
        for m, v in (p.get("per_attr9") or {}).items():
            total[m] = total.get(m, 0) + int(v) * high
    if total:
        yield "perks", total


def buy_block(state: CharacterState, content: Content, key: str) -> str | None:
    found = perk(key)
    if found is None:
        return "none"
    tree, p = found
    r = rec(state, content)
    have = int((r.get("perks") or {}).get(key, 0) or 0)
    if have >= int(p.get("ranks", 1)):
        return "max"
    if int(r.get("perk", 0)) <= 0:
        return "points"
    need = int(p.get("need", 0)) + have
    if int(state.stats.get(tree, 0)) < need:
        return f"need:{tree}:{need}"
    return None


def buy(state: CharacterState, content: Content, key: str) -> str | None:
    why = buy_block(state, content, key)
    if why:
        return why
    r = rec(state, content)
    perks = dict(r.get("perks") or {})
    perks[key] = int(perks.get(key, 0) or 0) + 1
    r["perks"] = perks
    r["perk"] = int(r.get("perk", 0)) - 1
    save(state, r)
    return None


def stat_cost(content: Content, current: int) -> int:
    """Attribute points for +1 from `current`: one up to stat_cap, then over_cost_start and one more each step."""
    c = cfg(content)
    return 1 if current < c.stat_cap else current - c.stat_cap + c.over_cost_start


def raise_stat(state: CharacterState, content: Content, stat: str) -> str | None:
    if stat not in TREES and stat not in ("EMP", "LUCK"):
        return "none"
    r = rec(state, content)
    if int(r.get("attr", 0)) <= 0:
        return "points"
    cur = int(state.stats.get(stat, 0))
    if cur >= cfg(content).stat_legend_cap:
        return "cap"
    cost = stat_cost(content, cur)
    if int(r.get("attr", 0)) < cost:
        return "cost"
    state.stats[stat] = cur + 1
    r["attr"] = int(r.get("attr", 0)) - cost
    save(state, r)
    return None


def all_legend(state: CharacterState, content: Content) -> bool:
    cap = cfg(content).stat_legend_cap
    return all(int(state.stats.get(k, 0)) >= cap for k in TREES)


def attr_to_perk(state: CharacterState, content: Content) -> str | None:
    """Owner report 26.10: with every attribute at the legend cap an attribute point becomes a talent point."""
    r = rec(state, content)
    if int(r.get("attr", 0)) <= 0:
        return "points"
    if not all_legend(state, content):
        return "notyet"
    r["attr"] = int(r.get("attr", 0)) - 1
    r["perk"] = int(r.get("perk", 0)) + 1
    save(state, r)
    return None
