"""Daily and weekly quests (PLAN 4.7, DESIGN.md 12a): issue by path, count bus events, claim.

- A new period is issued lazily: the first look at the quests (or the first bus event) of a Kyiv day
  or of a week (Monday) drops the old rows and draws the new ones. The draw is seeded by player and
  period, so a restart does not reroll it.
- The quests listener counts every bus event for the player (not for the character: the daily loop
  outlives deaths) and counts the way they play (combat / non-combat) for the next draw.
- A claim pays tokens to the player and, with a living character, eddies / xp / reputations through
  `apply_effects`. Without a character only the tokens are paid and the rest is lost: say so.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import kyiv_date
from app.content.loader import Content
from app.content.schemas import Quest
from app.db import repo
from app.db.models import QuestProgress
from app.engine import daily as engine_daily
from app.engine.effects import AppliedEffects, apply_effects
from app.engine.state import CharacterState
from app.services import game_events as bus
from app.services import notices, playerdata
from app.services.game import ActiveCharacter, save_active

log = logging.getLogger(__name__)

PERIODS = ("daily", "weekly")


async def _paths(session: AsyncSession, content: Content, tg_id: int, chrome_free: bool) -> set[str]:
    counters = await playerdata.get(session, tg_id, playerdata.KEY_PATHS, {})
    return engine_daily.player_paths(content, counters, chrome_free)


async def ensure(
    session: AsyncSession, content: Content, tg_id: int, chrome_free: bool, today: str,
) -> list[QuestProgress]:
    """The quests of the current day and week, issued if the period is new. Daily first."""
    out: list[QuestProgress] = []
    await repo.ensure_player(session, tg_id)
    for period in PERIODS:
        start = engine_daily.period_start(period, today)
        res = await session.execute(
            select(QuestProgress).where(QuestProgress.player_tg_id == tg_id, QuestProgress.period == period)
            .order_by(QuestProgress.id)
        )
        rows = list(res.scalars())
        stale = [r for r in rows if r.period_start != start]
        if stale:
            await session.execute(delete(QuestProgress).where(QuestProgress.id.in_([r.id for r in stale])))
            rows = [r for r in rows if r.period_start == start]
        if not rows:
            paths = await _paths(session, content, tg_id, chrome_free)
            rng = random.Random(f"{tg_id}:{period}:{start}")
            for q in engine_daily.pick_quests(content, rng, period, paths):
                row = QuestProgress(
                    player_tg_id=tg_id, quest_key=q.key, period=period, period_start=start,
                    progress=0, target=q.target, done=False, claimed=False,
                )
                session.add(row)
                rows.append(row)
            await session.flush()
        out.extend(rows)
    return out


def quest_of(content: Content, row: QuestProgress) -> Quest | None:
    return content.quests.get(row.quest_key)


async def count_event(
    session: AsyncSession, content: Content, tg_id: int, chrome_free: bool, today: str, event: str,
    params: dict,
) -> list[Quest]:
    """Count one bus event for the player. Returns the quests that this event finished."""
    finished: list[Quest] = []
    for row in await ensure(session, content, tg_id, chrome_free, today):
        if row.done:
            continue
        q = quest_of(content, row)
        if q is None or not engine_daily.goal_matches(q.goal, event, params):
            continue
        row.progress = min(row.target, row.progress + 1)
        if row.progress >= row.target:
            row.done = True
            finished.append(q)
    await session.flush()
    return finished


class QuestListener:
    """Bus subscriber: counts the way of playing and the quest goals."""

    def __init__(self, content: Content, tz: str) -> None:
        self.content = content
        self.tz = tz

    async def __call__(self, ev: bus.GameEvent) -> None:
        s = ev.session
        if s is None:
            return
        path = engine_daily.action_path(ev.name, ev.params)
        if path is not None:
            await playerdata.bump(s, ev.player_tg_id, playerdata.KEY_PATHS, path)
        chrome_free = bool(ev.ch.state.chrome_free) if ev.ch is not None else False
        finished = await count_event(s, self.content, ev.player_tg_id, chrome_free, kyiv_date(self.tz), ev.name, ev.params)
        if finished and ev.ch is not None:
            for q in finished:
                notices.push(ev.ch, f"Задание выполнено: {q.text}.")
            await save_active(s, ev.ch, self.tz)


# -- claiming ------------------------------------------------------------------------------------------------


@dataclass
class ClaimResult:
    ok: bool
    reason: str | None = None  # "gone" / "not_done" / "claimed"
    quest: Quest | None = None
    tokens: int = 0
    applied: AppliedEffects | None = None
    lost: bool = False  # the character part of the reward was lost: no living character
    lines: list[str] = field(default_factory=list)


def xp_skill(st: CharacterState, content: Content) -> str | None:
    """The skill that gets a quest's xp: the character's best one that can still grow."""
    cap = content.balance.character.skill_cap
    open_skills = [(s.level, s.xp, k) for k, s in st.skills.items() if s.level < cap]
    return max(open_skills)[2] if open_skills else None


async def claim(
    session: AsyncSession, content: Content, tg_id: int, row_id: int, ch: ActiveCharacter | None,
    rng: random.Random, year: int, tz: str,
) -> ClaimResult:
    row = await session.get(QuestProgress, row_id)
    if row is None or row.player_tg_id != tg_id:
        return ClaimResult(False, "gone")
    q = quest_of(content, row)
    if q is None:
        return ClaimResult(False, "gone")
    if row.claimed:
        return ClaimResult(False, "claimed", q)
    if not row.done:
        return ClaimResult(False, "not_done", q)
    row.claimed = True
    tokens = content.balance.daily.quest_tokens[q.period] + q.rewards.tokens
    player = await repo.ensure_player(session, tg_id)
    player.tokens = int(player.tokens or 0) + tokens
    res = ClaimResult(True, None, q, tokens)
    fx = engine_daily.quest_effects(q.rewards, xp_skill(ch.state, content) if ch is not None else None)
    if ch is not None and fx:
        res.applied = apply_effects(ch.state, fx, content, rng, year)
        await save_active(session, ch, tz)
    elif fx:
        res.lost = True
    if ch is not None and q.world is not None:  # owner request 26.10: the deed moves the district
        from app.services import control

        d = content.districts.get(ch.state.district)
        if d is not None and d.city == "nc":
            await control.adjust(session, content, ch.state.district, gang=q.world.gang, corp=q.world.corp)
            res.lines.append(q.world.text.format(district=d.name))
    await session.flush()
    return res
