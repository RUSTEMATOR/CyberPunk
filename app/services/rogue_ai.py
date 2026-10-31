"""The rogue AI (owner request): a character who stepped beyond the Blackwall comes back as a hostile AI.

The player plays the AI from the window until the city erases it: three actions per real day against the other
players and the city. Its swarm can be called as the city raid (boss `rogue_ai`); when the raid wins, the AI is erased
and the life closes with the cause `blackwall`. Everything that hits another player goes as a letter.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.db import repo
from app.db.models import Character, PlayerMail, RogueAI
from app.engine import derived
from app.services import game
from app.services.game import ActiveCharacter, save_active

FLAG_ASCEND = "ascend_ai"   # set by the Threshold event: the character steps beyond the Blackwall
FLAG_AI_MODE = "ai_mode"    # the character is an AI now: the window shows the AI screen
ACTIVE, ERASED, DISSOLVED = "active", "erased", "dissolved"
MAIL_HIT, MAIL_GLITCH = "ai_hit", "ai_glitch"

BLOCK_CHARGES = "charges"
BLOCK_GONE = "gone"


def _today(tz: str) -> str:
    return datetime.now(ZoneInfo(tz)).date().isoformat()


async def of_player(session: AsyncSession, tg_id: int) -> RogueAI | None:
    res = await session.execute(select(RogueAI).where(RogueAI.owner_tg == tg_id, RogueAI.status == ACTIVE))
    return res.scalars().first()


async def active_all(session: AsyncSession) -> list[RogueAI]:
    return list((await session.execute(select(RogueAI).where(RogueAI.status == ACTIVE))).scalars())


def charges_left(content: Content, ai: RogueAI, tz: str) -> int:
    data = ai.data or {}
    used = int(data.get("used", 0)) if data.get("day") == _today(tz) else 0
    from app.services import ai_life  # owner request 24.10: levels and Многопоточность add actions

    return max(0, ai_life.charges(content, ai) - used)


def _spend(ai: RogueAI, tz: str, line: str) -> None:
    data = dict(ai.data or {})
    if data.get("day") != _today(tz):
        data["day"], data["used"] = _today(tz), 0
    data["used"] = int(data.get("used", 0)) + 1
    data["log"] = (list(data.get("log") or []) + [line])[-8:]
    ai.data = data


async def ascend(session: AsyncSession, content: Content, ch: ActiveCharacter, tz: str) -> RogueAI:
    """The step beyond the Blackwall: the AI is born from the character's build."""
    cfg = content.balance.players.rogue_ai
    st = ch.state
    power = derived.stat_total(st, content, "INT") + derived.skill_total(st, content, "netrunning")
    from app.engine import blackwall as bw_engine  # owner request 24.10: the Avatar keeps the power it gathered

    if bw_engine.is_avatar(st):
        power += bw_engine.ai_power_bonus(st, content)
    hp = cfg.hp_base + cfg.hp_per_power * power
    ai = RogueAI(owner_tg=ch.player_tg_id, char_id=ch.id, name=f"{st.name} {st.surname}".strip(), power=power,
                 hp=hp, hp_max=hp, status=ACTIVE, data={"log": []})
    session.add(ai)
    st.flags.pop(FLAG_ASCEND, None)
    st.flags[FLAG_AI_MODE] = True
    await save_active(session, ch, tz)
    await session.flush()
    return ai


@dataclass
class AiResult:
    blocked: str | None = None
    won: bool = False
    mine: int = 0
    theirs: int = 0
    steal: int = 0
    target_name: str = ""
    target_tg: int = 0
    erased: bool = False


async def targets(session: AsyncSession, ai: RogueAI) -> list[Character]:
    """Living characters of the other players who are not AIs themselves."""
    return [c for c in await repo.living_characters(session)
            if c.player_tg_id != ai.owner_tg and not (c.flags or {}).get(FLAG_AI_MODE)]


async def neural_hack(
    session: AsyncSession, content: Content, ai: RogueAI, rng: random.Random, tz: str, char_id: int,
) -> AiResult:
    """AI power + d10 against the target's INT + netrunning + ice + defense + d10. A win steals eddies and humanity,
    a loss costs the AI hp (zero hp erases it)."""
    cfg = content.balance.players.rogue_ai
    res = AiResult()
    if charges_left(content, ai, tz) <= 0:
        res.blocked = BLOCK_CHARGES
        return res
    row = await session.get(Character, char_id)
    if row is None or not row.alive or row.player_tg_id == ai.owner_tg:
        res.blocked = BLOCK_GONE
        return res
    target = await game.load_character(session, char_id)
    if target is None:
        res.blocked = BLOCK_GONE
        return res
    t = target.state
    res.target_name, res.target_tg = f"{t.name} {t.surname}".strip(), target.player_tg_id
    res.mine = ai.power + rng.randint(1, 10)
    res.theirs = (derived.stat_total(t, content, "INT") + derived.skill_total(t, content, "netrunning")
                  + derived.mod(t, content, "ice") + cfg.defense + rng.randint(1, 10))
    res.won = res.mine > res.theirs
    if res.won:
        res.steal = min(cfg.steal_cap, max(0, t.eddies) * cfg.steal_pct // 100)
        ai.power += cfg.power_gain
        session.add(PlayerMail(to_tg_id=target.player_tg_id, from_tg_id=ai.owner_tg, from_char_id=ai.char_id,
                               to_char_id=target.id, from_name=ai.name, kind=MAIL_HIT,
                               payload={"steal": res.steal, "humanity": cfg.hit_humanity}))
        _spend(ai, tz, f"Взлом: {res.target_name}, −{res.steal} эдди.")
        from app.services import ai_life

        ai_life.gain(content, ai, xp=cfg.xp_per_action, data=max(1, res.steal // 100), trace_key="hack")
    else:
        ai.hp = max(0, ai.hp - cfg.fail_damage)
        _spend(ai, tz, f"Взлом сорвался, цель устояла: {res.target_name}.")
        from app.services import ai_life

        ai_life.gain(content, ai, xp=cfg.xp_per_action // 2, trace_key="hack")
        if ai.hp <= 0:
            res.erased = True
    await session.flush()
    return res


async def district_glitch(session: AsyncSession, content: Content, ai: RogueAI, tz: str, district: str) -> int:
    """A failure in a district: every character there gets heat and loses mood (by mail). Returns how many."""
    if charges_left(content, ai, tz) <= 0:
        return -1
    n = 0
    for c in await targets(session, ai):
        if c.district == district:
            session.add(PlayerMail(to_tg_id=c.player_tg_id, from_tg_id=ai.owner_tg, from_char_id=ai.char_id,
                                   to_char_id=c.id, from_name=ai.name, kind=MAIL_GLITCH, payload={"heat": 1, "mood": -4}))
            n += 1
    ai.power += content.balance.players.rogue_ai.power_gain
    _spend(ai, tz, f"Сбой в районе: задело {n}.")
    from app.services import ai_life

    ai_life.gain(content, ai, xp=content.balance.players.rogue_ai.xp_per_action, data=n * 2, trace_key="glitch")
    await session.flush()
    return n


async def grow(session: AsyncSession, content: Content, ai: RogueAI, tz: str) -> bool:
    if charges_left(content, ai, tz) <= 0:
        return False
    ai.power += content.balance.players.rogue_ai.power_gain
    ai.hp = min(ai.hp_max, ai.hp + content.balance.players.rogue_ai.fail_damage)
    _spend(ai, tz, "Рост: ядро глубже в сети.")
    from app.services import ai_life

    ai_life.gain(content, ai, xp=content.balance.players.rogue_ai.xp_per_action)
    await session.flush()
    return True


async def erase(session: AsyncSession, content: Content, ai: RogueAI, tz: str, status: str = ERASED) -> None:
    """The end of the AI: the life closes (cause `blackwall`), the lineage goes on."""
    from app.services import death

    ai.status = status
    ch = await game.load_character(session, ai.char_id)
    if ch is not None:
        ch.state.flags.pop(FLAG_AI_MODE, None)
        await death.kill(session, content, ch, "blackwall", tz)
    await session.flush()
