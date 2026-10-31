"""Project «Каприкорн» (owner request 26.10, docs/PLAN_CAPRI.md): the power of Capri in her host. Pure.

The host's own flag `capri_host` = {"part": "full" | "half", "sync": 0..100, "reject": unix time or 0} is a copy of
world_state `capri` made by the host's own sessions (services/capri.sync); the modifiers come from it.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping

from app.content.loader import Content
from app.engine.state import CharacterState

FLAG = "capri_host"


def cfg(content: Content):  # noqa: ANN201
    return content.balance.capri


def host(state: CharacterState) -> dict | None:
    rec = state.flags.get(FLAG)
    return rec if isinstance(rec, dict) else None


def mods(state: CharacterState, content: Content) -> dict[str, int]:
    rec = host(state)
    if rec is None or float(rec.get("reject", 0) or 0):
        return {}
    full = rec.get("part") == "full"
    out: dict[str, int] = {}
    for k, v in cfg(content).power.items():
        out[k] = int(v) if full else int(v) // 2
    return out


def mod_sources(state: CharacterState, content: Content) -> Iterator[tuple[str, Mapping[str, int]]]:
    m = mods(state, content)
    if m:
        yield "capri", m


def tolerance(state: CharacterState, content: Content) -> int:
    """How well the body takes a construct: BODY + COOL + humanity / 10."""
    from app.engine import derived

    return (derived.stat_total(state, content, "BODY") + derived.stat_total(state, content, "COOL")
            + max(0, state.humanity) // 10)
