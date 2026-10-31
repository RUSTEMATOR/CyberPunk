"""The city raid as a service: spawn, attacks, the end (a kill or an escape) and the rewards by mail.

One raid at a time. The boss and the attacks live in real time (Kyiv days), like the dailies: personal years do not
matter here. Rewards go out as letters (`player_mail`, kind `raid`), so every player gets them in his own session.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.content.schemas import RaidBoss
from app.db import repo
from app.db.models import PlayerMail, Raid
from app.engine import raid as engine
from app.engine.effects import AppliedEffects, apply_effects
from app.services import ap as ap_services
from app.services.game import ActiveCharacter, save_active

ACTIVE, WON, ESCAPED = "active", "won", "escaped"
MAIL_KIND = "raid"

BLOCK_NONE = "none"
BLOCK_TODAY = "today"
BLOCK_JAIL = "jail"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def today(tz: str) -> str:
    return datetime.now(ZoneInfo(tz)).date().isoformat()


def boss_of(content: Content, raid: Raid) -> RaidBoss | None:
    boss = next((b for b in content.balance.players.raid.bosses if b.key == raid.boss), None)
    name = (raid.data or {}).get("name")
    if boss is not None and name:  # the swarm of a rogue AI carries its name
        boss = boss.model_copy(update={"name": f"{boss.name}: «{name}»"})
    return boss


async def active(session: AsyncSession) -> Raid | None:
    res = await session.execute(select(Raid).where(Raid.status == ACTIVE).order_by(Raid.id.desc()).limit(1))
    return res.scalars().first()


async def last(session: AsyncSession) -> Raid | None:
    res = await session.execute(select(Raid).order_by(Raid.id.desc()).limit(1))
    return res.scalars().first()


async def spawn(session: AsyncSession, content: Content, rng: random.Random, boss_key: str | None = None) -> Raid | None:
    """A new boss (not when one is active). The hp scales with the living characters."""
    if await active(session) is not None:
        return None
    cfg = content.balance.players.raid
    if not cfg.bosses:
        return None
    prev = await last(session)
    pool = [b for b in cfg.bosses if (prev is None or b.key != prev.boss) and b.key != "rogue_ai"] or list(cfg.bosses)
    boss = next((b for b in cfg.bosses if b.key == boss_key), None) or rng.choices(pool, [b.weight for b in pool])[0]
    players = len(await repo.living_characters(session))
    hp = engine.boss_hp(content, players)
    row = Raid(boss=boss.key, hp_max=hp, hp=hp, status=ACTIVE,
               ends_at=_utcnow() + timedelta(hours=cfg.duration_hours), data={"hits": {}, "support": 0})
    session.add(row)
    await session.flush()
    return row


async def spawn_ai(session: AsyncSession, content: Content, ai: Any) -> Raid | None:
    """The swarm of a rogue AI as the city raid: the boss is the AI, its hp is the AI's core."""
    if await active(session) is not None:
        return None
    cfg = content.balance.players.raid
    row = Raid(boss="rogue_ai", hp_max=max(1, ai.hp), hp=max(1, ai.hp), status=ACTIVE,
               ends_at=_utcnow() + timedelta(hours=cfg.duration_hours),
               data={"hits": {}, "support": 0, "ai_id": ai.id, "name": ai.name})
    session.add(row)
    await session.flush()
    return row


async def due_to_spawn(session: AsyncSession, content: Content, tz: str) -> bool:
    """No active raid, the last one ended `every_days` ago or more (or none yet), and it is past `spawn_hour`."""
    cfg = content.balance.players.raid
    if datetime.now(ZoneInfo(tz)).hour < cfg.spawn_hour or await active(session) is not None:
        return False
    prev = await last(session)
    if prev is None:
        return True
    ended = prev.closed_at or prev.ends_at
    return _utcnow() - ended >= timedelta(days=cfg.every_days) - timedelta(hours=2)


@dataclass
class HitResult:
    blocked: str | None = None
    no_ap: bool = False
    hit: engine.Hit | None = None
    applied: AppliedEffects | None = None
    raid: Raid | None = None
    closed: list[engine.Share] = field(default_factory=list)  # the boss fell with this hit


async def hit(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    approach: str,
) -> HitResult:
    from app.engine import jail as jail_engine

    cfg = content.balance.players.raid
    res = HitResult()
    raid = await active(session)
    res.raid = raid
    if raid is None or approach not in engine.APPROACHES:
        res.blocked = BLOCK_NONE
        return res
    if jail_engine.in_jail(ch.state):
        res.blocked = BLOCK_JAIL
        return res
    if ch.state.flags.get("ai_mode"):  # an AI does not raid; its swarm is the raid
        res.blocked = BLOCK_NONE
        return res
    data = dict(raid.data or {})
    hits = {k: dict(v) for k, v in (data.get("hits") or {}).items()}
    mine = hits.get(str(ch.player_tg_id), {})
    if mine.get("day") == today(tz):
        res.blocked = BLOCK_TODAY
        return res
    if not ap_services.can_spend(ch, cfg.ap):
        res.no_ap = True
        return res
    ap_services.spend(ch, content, cfg.ap)
    support = 0
    if approach != engine.SUPPORT and int(data.get("support", 0)) > 0:
        support = cfg.support_bonus
        data["support"] = int(data["support"]) - 1
    from app.services import crews

    crew = await crews.crew_of(session, ch.player_tg_id)
    if crew is not None:  # a crewmate already hit today: the crew backs you up
        mates = set(await crews.members(session, crew.id)) - {ch.player_tg_id}
        if any(hits.get(str(t), {}).get("day") == today(tz) for t in mates):
            support += content.balance.players.crew.raid_bonus
    boss = boss_of(content, raid)
    h = engine.attack(ch.state, content, rng, approach, boss.weak if boss else "", support)
    if approach == engine.SUPPORT and h.check.ok:
        data["support"] = int(data.get("support", 0)) + cfg.support_hits
    res.hit = h
    _, skill = engine.CHECKS[approach]
    fx: dict[str, int | bool] = {f"skill_xp:{skill}": cfg.xp}
    if h.hp_loss:
        fx["hp"] = -h.hp_loss
    res.applied = apply_effects(ch.state, fx, content, rng, year)
    await save_active(session, ch, tz)

    mine = {
        "name": f"{ch.state.name} {ch.state.surname}".strip(), "char": ch.id, "crew": crew.name if crew else None,
        "dmg": int(mine.get("dmg", 0)) + h.damage, "n": int(mine.get("n", 0)) + 1, "day": today(tz),
        "pts": int(mine.get("pts", mine.get("dmg", 0))) + h.damage
        + (cfg.support_points if approach == engine.SUPPORT and h.check.ok else 0),
    }
    hits[str(ch.player_tg_id)] = mine
    data["hits"] = hits
    raid.data = data
    raid.hp = max(0, raid.hp - h.damage)
    if raid.hp <= 0:
        res.closed = await close(session, content, raid, won=True)
    await session.flush()
    return res


async def close(session: AsyncSession, content: Content, raid: Raid, *, won: bool) -> list[engine.Share]:
    """The end of a raid: rewards by the share of damage go out as letters."""
    cfg = content.balance.players.raid
    raid.status = WON if won else ESCAPED
    raid.closed_at = _utcnow()
    boss = boss_of(content, raid)
    out = engine.shares(content, (raid.data or {}).get("hits") or {}, won)
    if won:
        from app.services import crews

        await crews.note_raid_win(session, [s.tg for s in out])
    ai_id = (raid.data or {}).get("ai_id")
    if ai_id:  # the swarm of a rogue AI: a win erases it, an escape makes it stronger
        from app.db.models import RogueAI
        from app.services import rogue_ai

        ai = await session.get(RogueAI, int(ai_id))
        if ai is not None and ai.status == rogue_ai.ACTIVE:
            if won:
                await rogue_ai.erase(session, content, ai, "Europe/Kyiv")
            else:
                ai.power += content.balance.players.rogue_ai.escape_power
    for place, s in enumerate(out, 1):
        session.add(PlayerMail(
            to_tg_id=s.tg, from_tg_id=0, from_char_id=None, to_char_id=s.char, from_name=boss.name if boss else "",
            from_year=0, kind=MAIL_KIND,
            payload={"won": won, "eddies": s.eddies, "cred": s.cred, "place": place, "dmg": s.dmg,
                     "item": cfg.top_item if s.top else None, "boss": raid.boss},
        ))
    await session.flush()
    return out


async def expire(session: AsyncSession, content: Content) -> tuple[Raid, list[engine.Share]] | None:
    """Close an active raid whose time ran out (the boss escapes)."""
    raid = await active(session)
    if raid is None or raid.ends_at > _utcnow():
        return None
    return raid, await close(session, content, raid, won=False)
