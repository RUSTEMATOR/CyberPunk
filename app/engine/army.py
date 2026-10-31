"""The character's own fighters (owner request 25.10: «собирать отряд, а при нужном размере они перерастают в банду и
так бесконечно можно копить солдат и сражаться»). Pure helpers over the flag.

flag `army` = {"name": str, "next": int, "soldiers": [{id, name, role, danger, xp, npc?}]}
The size gives the tier (balance.army.tiers): how many fight at the character's side and the racket income.
"""

from __future__ import annotations

import random
from typing import Any

from app.content.loader import Content
from app.engine.state import CharacterState

FLAG = "army"
ROLES = {1: "новичок", 2: "боец", 3: "ветеран", 4: "головорез", 5: "легенда улиц"}


def cfg(content: Content):  # noqa: ANN201
    return content.balance.army


def rec(state: CharacterState) -> dict[str, Any]:
    r = state.flags.get(FLAG)
    return dict(r) if isinstance(r, dict) else {"name": "", "next": 1, "soldiers": []}


def soldiers(state: CharacterState) -> list[dict]:
    return [dict(s) for s in (rec(state).get("soldiers") or []) if isinstance(s, dict)]


def save(state: CharacterState, r: dict[str, Any]) -> None:
    if r.get("soldiers") or r.get("name"):
        state.flags[FLAG] = r
    else:
        state.flags.pop(FLAG, None)


def tier(content: Content, n: int):  # noqa: ANN201
    """The highest tier the size reaches, or None for nobody."""
    got = None
    for t in cfg(content).tiers:
        if n >= t.size:
            got = t
    return got


def next_tier(content: Content, n: int):  # noqa: ANN201
    return next((t for t in cfg(content).tiers if n < t.size), None)


def needs_name(content: Content, state: CharacterState) -> bool:
    t = tier(content, len(soldiers(state)))
    return t is not None and t.named and not rec(state).get("name")


def title(content: Content, state: CharacterState) -> str:
    r = rec(state)
    t = tier(content, len(r.get("soldiers") or []))
    if t is None:
        return ""
    return f"{t.name} «{r['name']}»" if r.get("name") and t.named else t.name


def make_soldier(content: Content, r: dict[str, Any], rng: random.Random, danger: int, name: str = "",
                 npc: int = 0) -> dict:
    c = cfg(content)
    sid = int(r.get("next", 1) or 1)
    r["next"] = sid + 1
    if not name:
        first = rng.choice(content.first_names) if content.first_names else "Боец"
        name = f"{first} «{rng.choice(c.nicks)}»" if c.nicks else first
    s = {"id": sid, "name": name, "danger": max(1, min(5, danger)), "xp": 0}
    if npc:
        s["npc"] = npc
    return s


def add(content: Content, state: CharacterState, new: list[dict]) -> None:
    r = rec(state)
    r["soldiers"] = list(r.get("soldiers") or []) + new
    save(state, r)


def strongest(state: CharacterState, n: int) -> list[dict]:
    return sorted(soldiers(state), key=lambda s: (-int(s.get("danger", 1)), -int(s.get("xp", 0))))[:max(0, n)]


def wage(content: Content, state: CharacterState) -> int:
    c = cfg(content)
    return sum(c.wage_per_danger * int(s.get("danger", 1)) for s in soldiers(state))


def racket(content: Content, state: CharacterState) -> int:
    n = len(soldiers(state))
    t = tier(content, n)
    return n * (t.racket if t is not None else 0)


def power(state: CharacterState) -> int:
    return sum(int(s.get("danger", 1)) for s in soldiers(state))


def after_fight(content: Content, state: CharacterState, fought: list[int], fallen: list[int], won: bool) -> list[str]:
    """The dead leave the roster; the living gain experience on a win and grow in strength."""
    c = cfg(content)
    r = rec(state)
    out: list[str] = []
    keep: list[dict] = []
    for s in r.get("soldiers") or []:
        sid = int(s.get("id", 0))
        if sid in fallen:
            out.append(f"✝️ {s.get('name')} погибает в бою.")
            continue
        if won and sid in fought:
            s["xp"] = int(s.get("xp", 0)) + 1
            lv = int(s.get("danger", 1))
            if lv < 5 and s["xp"] >= c.xp_per_level * lv:
                s["danger"], s["xp"] = lv + 1, 0
                out.append(f"⬆️ {s.get('name')} теперь {ROLES.get(lv + 1, 'боец')}.")
        keep.append(s)
    r["soldiers"] = keep
    save(state, r)
    return out


def desert(state: CharacterState, share: float, rng: random.Random) -> int:
    """Unpaid: a share of the fighters walks away (the weakest first). Returns how many."""
    r = rec(state)
    roster = list(r.get("soldiers") or [])
    n = max(1, int(len(roster) * share)) if roster else 0
    roster.sort(key=lambda s: (int(s.get("danger", 1)), int(s.get("xp", 0))))
    r["soldiers"] = roster[n:]
    save(state, r)
    return n
