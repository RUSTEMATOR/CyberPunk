"""City crises (owner request 24.10): real-time shared events that hit every living character at once.

The definitions live in content/crises.yaml. The live state (which crisis, scores, contributors) is kept in
world_state by services/crisis.py, which also pushes the active modifiers here with `set_live`, so the pure modifier
fold (engine/derived.py) can read them without the database. Character flags: `cr_side` = "a" | "b" (the side in the
current war), `cr_id` = the crisis it belongs to, `crisis_pts` = contribution not yet counted by the service.
"""

from __future__ import annotations

import functools
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import yaml

from app.engine.state import CharacterState

FILE = Path(__file__).resolve().parent.parent.parent / "content" / "crises.yaml"
FLAG_SIDE = "cr_side"
FLAG_ID = "cr_id"
FLAG_PTS = "crisis_pts"
WAR, COMMON = "war", "common"

_live: dict[str, Any] = {"key": None, "mods": {}, "after": {}, "version": 0}


@functools.cache
def data() -> dict[str, Any]:
    with FILE.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def settings() -> dict[str, Any]:
    return dict(data().get("settings") or {})


def crises() -> dict[str, dict[str, Any]]:
    return dict(data().get("crises") or {})


def get(key: str | None) -> dict[str, Any] | None:
    return crises().get(key) if key else None


def set_live(key: str | None, mods: Mapping[str, int] | None, after: Mapping[str, int] | None) -> None:
    """The service tells the engine what holds now; derived caches see the change through `version`."""
    new = {"key": key, "mods": dict(mods or {}), "after": dict(after or {})}
    if any(_live.get(k) != v for k, v in new.items()):
        _live.update(new)
        _live["version"] = int(_live.get("version", 0)) + 1


def version() -> int:
    return int(_live.get("version", 0))


def live_key() -> str | None:
    return _live.get("key")


def mod_sources(state: CharacterState) -> Iterator[tuple[str, Mapping[str, int]]]:
    if _live.get("mods"):
        yield "crisis", dict(_live["mods"])
    if _live.get("after"):
        yield "crisis_after", dict(_live["after"])


def side_name(c: Mapping[str, Any], side: str | None) -> str:
    return str(((c.get("sides") or {}).get(side) or {}).get("name", "")) if side else ""


def sync_side(state: CharacterState, crisis_id: str | None) -> str | None:
    """The character's side in the current war: a flag from another crisis is dropped, an event's pick becomes it."""
    f = state.flags
    if f.get(FLAG_ID) != crisis_id:
        f.pop(FLAG_SIDE, None)
        f.pop(FLAG_ID, None)
    for side in ("a", "b"):
        if f.pop(f"cr_side_{side}", None) and crisis_id and not f.get(FLAG_SIDE):
            f[FLAG_SIDE] = side
            f[FLAG_ID] = crisis_id
    return f.get(FLAG_SIDE) if f.get(FLAG_ID) == crisis_id else None


def take_points(state: CharacterState) -> int:
    return int(state.flags.pop(FLAG_PTS, 0) or 0)
