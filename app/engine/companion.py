"""Hired city NPCs (owner request: the living city, docs/PLAN_FREEDOM.md stage 5). Pure functions.

Owner request 24.10: a squad. flag `squad` = [{npc, name, role, danger, until}, ...] up to `life.squad_max`; each one
works until weeks_lived reaches `until` and fights at the character's side as an ally in the combat window.
The old single flag `companion` = {npc, name, role, until} is read as a squad of one and moved over on the next write.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping

from app.content.loader import Content
from app.engine.state import CharacterState

FLAG = "companion"  # legacy: one companion
SQUAD = "squad"


def members(state: CharacterState) -> list[dict]:
    """Every contract on record, the expired ones too (the press reports their end)."""
    out = [dict(x) for x in (state.flags.get(SQUAD) or []) if isinstance(x, dict)]
    old = state.flags.get(FLAG)
    if isinstance(old, dict) and old and not any(int(m.get("npc", 0) or 0) == int(old.get("npc", 0) or 0) for m in out):
        out.insert(0, dict(old))
    return out


def active_members(state: CharacterState) -> list[dict]:
    return [m for m in members(state) if state.weeks_lived < int(m.get("until", 0) or 0)]


def save(state: CharacterState, squad: list[dict]) -> None:
    state.flags.pop(FLAG, None)
    if squad:
        state.flags[SQUAD] = squad
    else:
        state.flags.pop(SQUAD, None)


def has(state: CharacterState, npc_id: int) -> bool:
    return any(int(m.get("npc", 0) or 0) == npc_id for m in active_members(state))


def rec(state: CharacterState) -> dict:
    """The first active member (old callers)."""
    act = active_members(state)
    return act[0] if act else {}


def active(state: CharacterState) -> bool:
    return bool(active_members(state))


def label(state: CharacterState) -> str | None:
    act = active_members(state)
    return ", ".join(f"{m.get('name', '?')} ({m.get('role', '')})" for m in act) if act else None


def price(content: Content, danger: int) -> int:
    table = content.balance.freedom.life.hire_price
    return table[min(max(danger, 0), len(table) - 1)]


def mod_sources(state: CharacterState, content: Content) -> Iterator[tuple[str, Mapping[str, int]]]:
    mods = content.balance.freedom.life.hire_mods
    if mods and active(state):
        yield "companion", mods
