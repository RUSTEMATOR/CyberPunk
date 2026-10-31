"""Per-player records that outlive characters (table `player_data`): counters, the card of the day, marks.

Keys in use:
- `paths`    {"combat": int, "noncombat": int}: counted actions, they choose the daily quests (DESIGN.md 12a);
- `tarot`    {"date", "card", "rerolled", "pending_eddies", "applied"}: the Misty card of the day;
- `race_log` is not here but in world_state (pairs, not players).
"""

from __future__ import annotations

import copy
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PlayerData

KEY_PATHS = "paths"
KEY_TAROT = "tarot"


async def get(session: AsyncSession, tg_id: int, key: str, default: Any = None) -> Any:
    """The stored value (a copy: edit it and `put` it back), or `default`."""
    row = await session.get(PlayerData, (tg_id, key))
    return copy.deepcopy(default) if row is None else copy.deepcopy(row.value)


async def put(session: AsyncSession, tg_id: int, key: str, value: Any) -> None:
    row = await session.get(PlayerData, (tg_id, key))
    if row is None:
        session.add(PlayerData(player_tg_id=tg_id, key=key, value=value))
    else:
        row.value = value
    await session.flush()


async def bump(session: AsyncSession, tg_id: int, key: str, field: str, n: int = 1) -> dict[str, int]:
    """Add `n` to one counter of a dict record and return the record."""
    rec = await get(session, tg_id, key, {})
    rec[field] = int(rec.get(field, 0)) + n
    await put(session, tg_id, key, rec)
    return rec


async def apply_deltas(session: AsyncSession, tg_id: int, deltas: dict[str, int]) -> None:
    """Dirt and Afterlife tokens that `apply_effects` could not apply (they live on the player).

    Neither goes below zero. Called after every `apply_effects` whose effects may carry them
    (events). A key it does not know is ignored.
    """
    from app.db import repo  # local: repo does not import services

    if not deltas:
        return
    player = await repo.ensure_player(session, tg_id)
    if deltas.get("tokens"):
        player.tokens = max(0, int(player.tokens or 0) + int(deltas["tokens"]))
    if deltas.get("dirt"):
        player.dirt = max(0, int(player.dirt or 0) + int(deltas["dirt"]))
    await session.flush()
