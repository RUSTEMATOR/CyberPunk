"""Crews of players (docs/EPIC_PLAYERS.md, MMO part): found, invite (an offer the other accepts), leave, the bank.

A crew belongs to players, not characters: it outlives deaths. Money between a character and the bank moves only
in the acting player's own session (deposit), or as a letter (the leader pays a member out).
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.db.models import Crew, CrewMember, PlayerMail
from app.engine.effects import apply_effects
from app.services.game import ActiveCharacter, save_active

BLOCK_IN_CREW = "in_crew"
BLOCK_NO_CREW = "no_crew"
BLOCK_EDDIES = "eddies"
BLOCK_FULL = "full"
BLOCK_NAME = "name"
BLOCK_LEADER = "leader"
BLOCK_BANK = "bank"
BLOCK_GONE = "gone"

MAIL_PAYOUT = "crewpay"   # the leader pays a member from the bank
MAIL_INFO = "crewinfo"    # a plain line: kicked, the crew dissolved, a new leader


async def crew_of(session: AsyncSession, tg_id: int) -> Crew | None:
    m = await session.get(CrewMember, tg_id)
    return await session.get(Crew, m.crew_id) if m is not None else None


async def members(session: AsyncSession, crew_id: int) -> list[int]:
    res = await session.execute(select(CrewMember.tg_id).where(CrewMember.crew_id == crew_id).order_by(CrewMember.joined_at))
    return [int(x) for x in res.scalars()]


async def all_crews(session: AsyncSession) -> list[Crew]:
    res = await session.execute(select(Crew).order_by(Crew.id))
    return list(res.scalars())


async def same_crew(session: AsyncSession, a_tg: int, b_tg: int) -> bool:
    a, b = await session.get(CrewMember, a_tg), await session.get(CrewMember, b_tg)
    return a is not None and b is not None and a.crew_id == b.crew_id


def _log(crew: Crew, line: str) -> None:
    data = dict(crew.data or {})
    data["log"] = (list(data.get("log") or []) + [line])[-8:]
    crew.data = data


@dataclass
class CrewResult:
    blocked: str | None = None
    crew: Crew | None = None


async def found(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str, name: str,
) -> CrewResult:
    cfg = content.balance.players.crew
    if await crew_of(session, ch.player_tg_id) is not None:
        return CrewResult(BLOCK_IN_CREW)
    if not cfg.name_min <= len(name) <= cfg.name_max:
        return CrewResult(BLOCK_NAME)
    if ch.state.eddies < cfg.found_cost:
        return CrewResult(BLOCK_EDDIES)
    apply_effects(ch.state, {"eddies": -cfg.found_cost}, content, rng, year)
    await save_active(session, ch, tz)
    crew = Crew(name=name, leader_tg=ch.player_tg_id, bank=0, data={"raids_won": 0, "log": []})
    session.add(crew)
    await session.flush()
    session.add(CrewMember(tg_id=ch.player_tg_id, crew_id=crew.id))
    _log(crew, f"{ch.state.name}: основание банды.")
    await session.flush()
    return CrewResult(None, crew)


async def join(session: AsyncSession, content: Content, tg_id: int, crew_id: int, name: str) -> CrewResult:
    """Accepting an invite."""
    crew = await session.get(Crew, crew_id)
    if crew is None:
        return CrewResult(BLOCK_GONE)
    if await crew_of(session, tg_id) is not None:
        return CrewResult(BLOCK_IN_CREW, crew)
    if len(await members(session, crew_id)) >= content.balance.players.crew.max_members:
        return CrewResult(BLOCK_FULL, crew)
    session.add(CrewMember(tg_id=tg_id, crew_id=crew_id))
    _log(crew, f"{name}: вступление в банду.")
    await session.flush()
    return CrewResult(None, crew)


async def leave(session: AsyncSession, tg_id: int, name: str) -> CrewResult:
    """Leave the crew. A leaving leader hands it to the oldest member; the last one out dissolves it (the bank goes
    to him by mail)."""
    m = await session.get(CrewMember, tg_id)
    if m is None:
        return CrewResult(BLOCK_NO_CREW)
    crew = await session.get(Crew, m.crew_id)
    await session.delete(m)
    await session.flush()
    if crew is None:
        return CrewResult(None)
    rest = await members(session, crew.id)
    if not rest:
        if crew.bank:
            session.add(PlayerMail(to_tg_id=tg_id, from_tg_id=0, from_name=crew.name, kind=MAIL_PAYOUT,
                                   payload={"eddies": crew.bank, "crew": crew.name, "dissolved": True}))
        await session.delete(crew)
        await session.flush()
        return CrewResult(None)
    if crew.leader_tg == tg_id:
        crew.leader_tg = rest[0]
        session.add(PlayerMail(to_tg_id=rest[0], from_tg_id=0, from_name=crew.name, kind=MAIL_INFO,
                               payload={"line": "leader", "crew": crew.name}))
    _log(crew, f"{name}: уход из банды.")
    await session.flush()
    return CrewResult(None, crew)


async def kick(session: AsyncSession, leader_tg: int, target_tg: int) -> CrewResult:
    crew = await crew_of(session, leader_tg)
    if crew is None:
        return CrewResult(BLOCK_NO_CREW)
    if crew.leader_tg != leader_tg or target_tg == leader_tg:
        return CrewResult(BLOCK_LEADER, crew)
    m = await session.get(CrewMember, target_tg)
    if m is None or m.crew_id != crew.id:
        return CrewResult(BLOCK_GONE, crew)
    await session.delete(m)
    session.add(PlayerMail(to_tg_id=target_tg, from_tg_id=leader_tg, from_name=crew.name, kind=MAIL_INFO,
                           payload={"line": "kicked", "crew": crew.name}))
    await session.flush()
    return CrewResult(None, crew)


async def deposit(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str, amount: int,
) -> CrewResult:
    crew = await crew_of(session, ch.player_tg_id)
    if crew is None:
        return CrewResult(BLOCK_NO_CREW)
    if amount <= 0 or ch.state.eddies < amount:  # owner request 24.10: any amount
        return CrewResult(BLOCK_EDDIES, crew)
    apply_effects(ch.state, {"eddies": -amount}, content, rng, year)
    await save_active(session, ch, tz)
    crew.bank += amount
    _log(crew, f"{ch.state.name}: +{amount} в общак.")
    await session.flush()
    return CrewResult(None, crew)


async def pay_out(session: AsyncSession, leader_tg: int, target_tg: int, amount: int) -> CrewResult:
    """The leader pays a member from the bank: a letter, the member gets it with his menu."""
    crew = await crew_of(session, leader_tg)
    if crew is None:
        return CrewResult(BLOCK_NO_CREW)
    if crew.leader_tg != leader_tg:
        return CrewResult(BLOCK_LEADER, crew)
    if not await same_crew(session, leader_tg, target_tg):
        return CrewResult(BLOCK_GONE, crew)
    if amount <= 0 or crew.bank < amount:
        return CrewResult(BLOCK_BANK, crew)
    crew.bank -= amount
    session.add(PlayerMail(to_tg_id=target_tg, from_tg_id=leader_tg, from_name=crew.name, kind=MAIL_PAYOUT,
                           payload={"eddies": amount, "crew": crew.name}))
    _log(crew, f"Выплата из общака: {amount}.")
    await session.flush()
    return CrewResult(None, crew)


async def note_raid_win(session: AsyncSession, tg_ids: list[int]) -> None:
    """Every crew with a member among the raid winners counts a won raid."""
    seen: set[int] = set()
    for tg in tg_ids:
        crew = await crew_of(session, tg)
        if crew is not None and crew.id not in seen:
            seen.add(crew.id)
            data = dict(crew.data or {})
            data["raids_won"] = int(data.get("raids_won", 0)) + 1
            crew.data = data
    await session.flush()


# -- the crew's own business (owner request: a shared business for two or three players) -------------------------------


def crew_biz(crew: Crew) -> dict | None:
    b = (crew.data or {}).get("biz")
    return dict(b) if b else None


async def biz_buy(session: AsyncSession, content: Content, leader_tg: int, kind_key: str) -> str | None:
    """The leader buys a business with the crew bank. Returns a block reason or None."""
    from app.services import business

    crew = await crew_of(session, leader_tg)
    if crew is None or crew.leader_tg != leader_tg:
        return BLOCK_LEADER
    kind = business.kind_of(content, kind_key)
    if kind is None:
        return "gone"
    b = crew_biz(crew)
    if b is not None and b.get("kind") == kind_key:  # the same kind again: an upgrade
        if int(b["level"]) >= content.balance.players.business.max_level:
            return "max"
        cost = business.upgrade_cost(content, kind, int(b["level"]))
        if crew.bank < cost:
            return "bank"
        crew.bank -= cost
        b["level"] = int(b["level"]) + 1
        _log(crew, f"Бизнес «{kind.name}» расширен до уровня {b['level']} за {cost}.")
    else:
        if b is not None:
            return "have"
        if crew.bank < kind.price:
            return "bank"
        crew.bank -= kind.price
        b = {"kind": kind_key, "level": 1}
        _log(crew, f"Банда купила бизнес «{kind.name}» за {kind.price}.")
    data = dict(crew.data or {})
    data["biz"] = b
    crew.data = data
    await session.flush()
    return None


def biz_hourly(content: Content, b: dict) -> int:
    """The average net of a crew business an hour (owner report 23.10: it paid once a night, nobody saw it)."""
    from app.services import business

    kind = business.kind_of(content, str(b.get("kind", "")))
    if kind is None:
        return 0
    lvl = int(b.get("level", 1))
    week = kind.income * lvl - kind.upkeep * lvl
    return int(week * content.balance.players.business.crew_weeks_per_day / 24)


async def biz_tick(session: AsyncSession, content: Content, rng) -> int:  # noqa: ANN001
    """Every hour: each crew business pays its hourly share (with the swing) into the bank, catching up missed hours
    (up to two days). Returns crews paid."""
    from datetime import datetime, timedelta

    from app.clock import utcnow
    from app.services import business

    cfg = content.balance.players.business
    now = utcnow()
    paid = 0
    for crew in await all_crews(session):
        b = crew_biz(crew)
        kind = business.kind_of(content, b["kind"]) if b else None
        if kind is None:
            continue
        data = dict(crew.data or {})
        last = datetime.fromisoformat(data["biz_at"]) if data.get("biz_at") else now - timedelta(hours=1)
        hours = min(48, int((now - last).total_seconds() // 3600))
        if hours <= 0:
            continue
        lvl = int(b["level"])
        net = 0
        for _ in range(hours):
            week = kind.income * lvl * rng.uniform(*cfg.swing) - kind.upkeep * lvl
            net += int(week * cfg.crew_weeks_per_day / 24)
        from app.services import hubs  # owner request 23.10: a headquarters makes the business pay more

        pct = hubs.hq_biz_pct(content, crew)
        if pct and net > 0:
            net = int(net * (100 + pct) / 100)
        crew.bank = max(0, crew.bank + net)
        day = now.date().isoformat()
        d = dict(data.get("biz_day") or {})
        if d.get("d") != day:
            d = {"d": day, "sum": 0}
        d["sum"] = int(d.get("sum", 0)) + net
        data.update({"biz_at": (last + timedelta(hours=hours)).isoformat(), "biz_last": net, "biz_day": d})
        crew.data = data
        paid += 1
    await session.flush()
    return paid


async def biz_daily(session: AsyncSession, content: Content, rng) -> int:  # noqa: ANN001
    """Kept for the night tick: the business now pays hourly (`biz_tick`)."""
    return 0


# -- the crew heist (owner request): once a week, every member adds a roll, the sum decides --------------------------

HEIST_APPROACHES = {"loud": ("REF", "firearms", "Штурм"), "quiet": ("REF", "stealth", "Тихо"),
                    "hack": ("INT", "netrunning", "Взлом"), "drive": ("REF", "driving", "За рулём")}


def heist_of(crew: Crew) -> dict | None:
    h = (crew.data or {}).get("heist")
    return dict(h) if h and not h.get("done") else None


def _set_heist(crew: Crew, h: dict | None, **extra: object) -> None:
    data = dict(crew.data or {})
    data["heist"] = h
    data.update(extra)
    crew.data = data


def heist_need(tier, members_n: int) -> int:  # noqa: ANN001
    """A small crew needs fewer points (the tier numbers are for four members)."""
    return max(2, round(tier.need * min(1.0, members_n / 4)))


def heist_mult(content: Content, crew: Crew) -> float:
    won = min(5, int((crew.data or {}).get("raids_won", 0)))
    return 1 + content.balance.players.crew.heist_raid_bonus_pct * won / 100


async def heist_start(session: AsyncSession, content: Content, leader_tg: int, tier_key: str) -> str | None:
    from datetime import timedelta

    cfg = content.balance.players.crew
    crew = await crew_of(session, leader_tg)
    if crew is None or crew.leader_tg != leader_tg:
        return BLOCK_LEADER
    if heist_of(crew) is not None:
        return "running"
    last = (crew.data or {}).get("heist_last")
    if last and _now() - _parse(last) < timedelta(days=cfg.heist_every_days):
        return "cooldown"
    tier = next((t for t in cfg.heists if t.key == tier_key), None)
    if tier is None:
        return BLOCK_GONE
    now = _now()
    _set_heist(crew, {"tier": tier.key, "ends": (now + timedelta(hours=cfg.heist_hours)).isoformat(), "joined": {}},
               heist_last=now.isoformat())
    _log(crew, f"Налёт объявлен: {tier.name}.")
    await session.flush()
    return None


def _now():  # noqa: ANN202
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse(iso: str):  # noqa: ANN202
    from datetime import datetime

    return datetime.fromisoformat(iso)


async def heist_join(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                     tz: str, approach: str) -> tuple[str | None, object | None, int]:
    """A member adds a roll (1 AP). Returns (block, check result, points)."""
    from app.engine import checks
    from app.services import ap as ap_services

    cfg = content.balance.players.crew
    crew = await crew_of(session, ch.player_tg_id)
    h = heist_of(crew) if crew else None
    if crew is None or h is None:
        return "none", None, 0
    if str(ch.player_tg_id) in h["joined"]:
        return "joined", None, 0
    if approach not in HEIST_APPROACHES:
        return BLOCK_GONE, None, 0
    if int(ch.state.flags.get("jail_weeks", 0) or 0) > 0:
        return "jail", None, 0
    if not ap_services.can_spend(ch, 1):
        return "no_ap", None, 0
    ap_services.spend(ch, content, 1)
    stat, skill, _ = HEIST_APPROACHES[approach]
    extra = 0
    if approach == "drive":
        from app.services import vehicles

        extra = max(0, vehicles.speed(content, ch) - 5)
    res = checks.roll_check(ch.state, content, rng, stat, skill, cfg.heist_dv, ("gig_loud",) if approach == "loud" else (), extra=extra)
    points = 2 if res.ok and res.crit == "success" else 1 if res.ok else 0
    if not res.ok:
        apply_effects(ch.state, {"hp": -cfg.heist_fail_hp}, content, rng, year)
    await save_active(session, ch, tz)
    h["joined"][str(ch.player_tg_id)] = points
    _set_heist(crew, h)
    _log(crew, f"{ch.state.name}: {HEIST_APPROACHES[approach][2].lower()}, очков {points}.")
    await session.flush()
    return None, res, points


async def heist_tick(session: AsyncSession, content: Content) -> list[str]:
    """Resolve heists whose time ran out or where every member joined. Returns group posts."""
    cfg = content.balance.players.crew
    posts = []
    for crew in await all_crews(session):
        h = heist_of(crew)
        if h is None:
            continue
        mem = await members(session, crew.id)
        if _now() < _parse(h["ends"]) and len(h["joined"]) < len(mem):
            continue
        tier = next((t for t in cfg.heists if t.key == h["tier"]), None)
        if tier is None:
            _set_heist(crew, None)
            continue
        total = sum(int(v) for v in h["joined"].values())
        mult = heist_mult(content, crew)
        need = heist_need(tier, len(mem))
        if total >= need:
            bank = int(tier.bank * mult * max(0.5, min(1.0, len(mem) / 4)))
            crew.bank += bank
            for tg in h["joined"]:
                session.add(PlayerMail(to_tg_id=int(tg), from_tg_id=0, from_name=crew.name, kind="city",
                                       payload={"eddies": int(tier.share * mult), "cred": tier.cred, "fame": tier.fame,
                                                "text": f"Налёт банды «{crew.name}» удался: {tier.name}. "
                                                        f"Твоя доля {int(tier.share * mult)} эдди."}))
            _log(crew, f"Налёт удался: +{bank} в общак.")
            posts.append(f"💥 Банда «{crew.name}» берёт {tier.name}: {total}/{need}, "
                         f"в общак +{bank} эдди. Участников: {len(h['joined'])}.")
        else:
            for tg in h["joined"]:
                session.add(PlayerMail(to_tg_id=int(tg), from_tg_id=0, from_name=crew.name, kind="city",
                                       payload={"heat": cfg.heist_fail_heat, "mood": -4,
                                                "text": f"Налёт банды «{crew.name}» сорвался. Копы ищут участников."}))
            _log(crew, f"Налёт сорвался: {total}/{need}.")
            posts.append(f"🚨 Налёт банды «{crew.name}» на {tier.name} сорвался: {total}/{need}. "
                         f"Копы ищут участников.")
        h["done"] = True
        _set_heist(crew, h)
    await session.flush()
    return posts
