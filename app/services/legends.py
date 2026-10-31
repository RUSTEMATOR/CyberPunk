"""Rankings of the group: the weekly "Легенды недели" post and (PLAN 4.9) the /legends tops.

Reputations belong to the living characters; the weekly post compares each player's three scales
with the snapshot taken at the previous post (world_state `legends_baseline`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import kyiv_date
from app.content.loader import Content
from app.db import repo
from app.db.models import Character, GraveyardEntry, Player
from app.engine.daily import days_between
from app.engine.minigames import caliber_title

BASELINE_KEY = "legends_baseline"
SCALES: tuple[str, ...] = ("street", "status", "fame")
_COLUMN = {"street": "street_cred", "status": "status", "fame": "fame"}


def scale_value(row: Character, scale: str) -> int:
    return int(getattr(row, _COLUMN[scale]))


@dataclass
class WeeklyGain:
    scale: str
    tg_id: int
    name: str
    gain: int


@dataclass
class WeeklySummary:
    gains: list[WeeklyGain] = field(default_factory=list)  # best of each scale, only positive
    caliber: tuple[int, str, int, str] | None = None  # (tg_id, name, mm, title)
    dump: tuple[int, str, int, str] | None = None  # (from id, from name, to id, to name)

    @property
    def quiet(self) -> bool:
        return not (self.gains or self.caliber or self.dump)


async def _label(session: AsyncSession, tg_id: int) -> str:
    return await repo.display_name(session, tg_id, "игрок")


async def weekly_summary(session: AsyncSession, content: Content, today: str, *, save_baseline: bool = True) -> WeeklySummary:
    """Who grew most on each scale since the last post, the biggest caliber, the loudest dump of the week."""
    baseline: dict[str, Any] = dict(await repo.ws_get(session, BASELINE_KEY, {}) or {})
    chars = await repo.living_characters(session)
    out = WeeklySummary()
    now_snapshot: dict[str, dict[str, int]] = {}
    for scale in SCALES:
        best: tuple[int, int] | None = None  # (gain, tg_id)
        for row in chars:
            before = int((baseline.get(str(row.player_tg_id)) or {}).get(scale, 0))
            gain = scale_value(row, scale) - before
            if gain > 0 and (best is None or (gain, -row.player_tg_id) > (best[0], -best[1])):
                best = (gain, row.player_tg_id)
        if best is not None:
            out.gains.append(WeeklyGain(scale, best[1], await _label(session, best[1]), best[0]))
    for row in chars:
        now_snapshot[str(row.player_tg_id)] = {s: scale_value(row, s) for s in SCALES}
    if save_baseline:
        await repo.ws_set(session, BASELINE_KEY, now_snapshot)

    res = await session.execute(select(Player))
    rolled = [p for p in res.scalars() if (p.mini_last or {}).get("caliber")]
    if rolled:
        top = max(rolled, key=lambda p: (int(p.caliber_mm or 0), -p.tg_id))
        out.caliber = (top.tg_id, await _label(session, top.tg_id), int(top.caliber_mm or 0),
                       caliber_title(content, int(top.caliber_mm or 0)))
    log = [e for e in (await repo.ws_get(session, "dump_log", []) or [])
           if e.get("ok") and days_between(e["day"], today) < 7]
    if log:
        e = log[-1]
        out.dump = (e["from"], await _label(session, e["from"]), e["to"], await _label(session, e["to"]))
    return out


@dataclass
class TopRow:
    tg_id: int
    name: str
    value: int
    dead: bool = False


async def tops(session: AsyncSession, scale: str, limit: int, *, include_dead: bool = False) -> list[TopRow]:
    """Characters with a positive value on the scale, best first (ties: older life first).

    The living only by default; `include_dead` adds the dead ones, whose last values stay in their rows
    (PLAN 10.1: legends outlive the people). A dead one is named by his own name, not by the player's current life.
    """
    chars = [c for c in await repo.living_characters(session) if scale_value(c, scale) > 0]
    if include_dead:
        res = await session.execute(select(Character).where(Character.alive.is_(False)))
        chars += [c for c in res.scalars() if scale_value(c, scale) > 0]
    chars.sort(key=lambda c: (-scale_value(c, scale), -int(c.weeks_lived or 0), c.id))
    rows: list[TopRow] = []
    for c in chars[:limit]:
        if c.alive:
            rows.append(TopRow(c.player_tg_id, await _label(session, c.player_tg_id), scale_value(c, scale)))
        else:
            name = " ".join(x for x in (c.name, c.surname) if x)
            rows.append(TopRow(c.player_tg_id, name, scale_value(c, scale), dead=True))
    return rows


async def graveyard(session: AsyncSession, limit: int) -> list[GraveyardEntry]:
    """The latest dead first (empty until the death milestone writes the graveyard rows)."""
    res = await session.execute(select(GraveyardEntry).order_by(GraveyardEntry.died_at.desc(), GraveyardEntry.id.desc()).limit(limit))
    return list(res.scalars())


def roles_label(content: Content, role_affinity: dict[str, float]) -> str:
    """'Нетраннер 3, Техник 1' from an affinity dict; the roles at rank 0 are left out ('без роли' when none)."""
    from app.bot import texts
    from app.engine import derived
    from app.engine.state import CharacterState

    st = CharacterState()
    st.role_affinity = {k: float(v) for k, v in role_affinity.items()}
    rows = [(k, r) for k, _a, r in derived.ranked_roles(st, content) if r >= 1]
    if not rows:
        return texts.WHO_NO_ROLE
    return ", ".join(f"{content.roles[k].name} {r}" for k, r in rows[:2])
