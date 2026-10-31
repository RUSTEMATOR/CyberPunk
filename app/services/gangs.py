"""Joining a city gang (owner request). Membership is the character flag `gang` = {key}; the rank is the standing
(faction_rep) with that gang, by `balance.players.gangs.ranks`. A member gets a weekly cut by rank; leaving is a
betrayal (standing down, the gang remembers it)."""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.content.schemas import GangRank
from app.engine import checks
from app.engine.checks import CheckResult
from app.engine.effects import AppliedEffects, apply_effects
from app.engine.state import FLAG_BETRAYED_PREFIX
from app.services.game import ActiveCharacter, save_active

FLAG = "gang"


def gang_of(ch: ActiveCharacter) -> str | None:
    return (ch.state.flags.get(FLAG) or {}).get("key")


def rank_of(content: Content, ch: ActiveCharacter) -> tuple[int, GangRank] | None:
    key = gang_of(ch)
    if key is None:
        return None
    rep = int((ch.state.faction_rep or {}).get(key, 0))
    ranks = content.balance.players.gangs.ranks
    idx = 0
    for i, r in enumerate(ranks):
        if rep >= r.rep:
            idx = i
    return idx, ranks[idx]


def joinable(content: Content, ch: ActiveCharacter, year: int) -> list[str]:
    cfg = content.balance.players.gangs
    return [k for k in content.available("gangs", year) if k not in cfg.not_joinable]


@dataclass
class JoinResult:
    ok: bool
    check: CheckResult | None = None
    applied: AppliedEffects | None = None
    blocked: str | None = None


async def join(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str, key: str,
) -> JoinResult:
    cfg = content.balance.players.gangs
    if gang_of(ch) is not None:
        return JoinResult(False, blocked="member")
    if key not in joinable(content, ch, year):
        return JoinResult(False, blocked="gone")
    if ch.state.flags.get(FLAG_BETRAYED_PREFIX + key) or (ch.state.faction_rep or {}).get(key, 0) < -10:
        return JoinResult(False, blocked="enemy")
    c = cfg.initiation
    res = checks.roll_check(ch.state, content, rng, c.stat, c.skill, c.dv, ())
    if res.ok:
        ch.state.flags[FLAG] = {"key": key}
        applied = apply_effects(ch.state, {f"faction_rep:{key}": cfg.join_rep, "street_cred": 1}, content, rng, year)
        from app.bot.texts import esc
        from app.services import loud

        g = content.gangs.get(key)
        await loud.post(session, ch.id, f"Новое лицо в банде «{esc(g.name if g else key)}»: "
                                        f"{loud.who(ch.state.name, ch.state.surname)}.")
    else:
        applied = apply_effects(ch.state, {"hp": -cfg.fail_hp, f"faction_rep:{key}": -2}, content, rng, year)
    await save_active(session, ch, tz)
    return JoinResult(res.ok, res, applied)


async def leave(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
) -> AppliedEffects | None:
    cfg = content.balance.players.gangs
    key = gang_of(ch)
    if key is None:
        return None
    ch.state.flags.pop(FLAG, None)
    applied = apply_effects(ch.state, {f"faction_rep:{key}": cfg.leave_rep, "heat": cfg.leave_heat,
                                       f"betray:{key}": True}, content, rng, year)
    await save_active(session, ch, tz)
    return applied


def week_cut(content: Content, ch: ActiveCharacter, rng: random.Random, year: int) -> str | None:
    """The weekly share of the gang's take by rank (a line for the week report, or None)."""
    rank = rank_of(content, ch)
    if rank is None or rank[1].cut <= 0:
        return None
    apply_effects(ch.state, {"eddies": rank[1].cut}, content, rng, year)
    name = content.gangs[gang_of(ch)].name if gang_of(ch) in content.gangs else ""
    return f"Доля от банды «{name}» ({rank[1].name}): +{rank[1].cut} эдди."


# -- a solo raid on a rival gang (owner request 25.10: «хочу устроить набег на другую банду, один») ---------------------

RAID_SRC = "graid"
RAID_AP = 2
RAID_FLAG = "graid_day"  # {gang key: kyiv date of the last raid on it}


def raid_targets(content: Content, ch: ActiveCharacter, year: int) -> list[str]:
    """Gangs a raid can hit: those of the character's district first, then every other gang of the year; never one's own."""
    mine = gang_of(ch)
    avail = content.available("gangs", year)
    d = content.districts.get(ch.state.district)
    local = [g for g in (d.gangs if d else []) if g in avail and g != mine]
    rest = [g for g, gg in avail.items() if g not in local and g != mine and not gg.fights_as] if d is None or d.city == "nc" else []
    return local + rest


def raid_danger(content: Content, ch: ActiveCharacter) -> int:
    from app.engine import perks

    lv = int(perks.rec(ch.state, content).get("level", 1) or 1)
    return max(2, min(5, 2 + lv // 6))


async def solo_raid(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                    tz: str, target: str):  # noqa: ANN201
    """Start the fight, or return a block reason (str)."""
    from app.clock import kyiv_date
    from app.engine import jail as jail_eng
    from app.services import ap as ap_services
    from app.services import combat
    from app.services import events as ev_service

    st = ch.state
    if target not in raid_targets(content, ch, year):
        return "gone"
    if jail_eng.in_jail(st):
        return "jail"
    if ev_service.has_pending(ch):
        return "busy"
    days = dict(st.flags.get(RAID_FLAG) or {})
    today = kyiv_date(tz)
    if days.get(target) == today:
        return "today"
    if not ap_services.can_spend(ch, RAID_AP):
        return "ap"
    ap_services.spend(ch, content, RAID_AP)
    days[target] = today
    st.flags[RAID_FLAG] = days
    await save_active(session, ch, tz)
    return await combat.begin(session, content, ch, rng, year, tz, faction=target, danger=raid_danger(content, ch),
                              src={"src": RAID_SRC, "target": target})


async def raid_over(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                    tz: str, result: str, ctx: dict) -> list[str]:
    from app.engine import combat as cb

    target = str(ctx.get("target", ""))
    g = content.gangs.get(target)
    if g is None:
        return []
    mine = gang_of(ch)
    if result == cb.WON:
        danger = raid_danger(content, ch)
        loot = rng.randint(1200, 3000) * danger // 2
        fx: dict = {"eddies": loot, "street_cred": 2, f"faction_rep:{target}": -8, "heat": 1, "enmity": 1}
        if mine:
            fx[f"faction_rep:{mine}"] = 6
        if rng.random() < 0.3:
            fx[f"flag_set:nem_spawn_{target}"] = True
        apply_effects(ch.state, fx, content, rng, year)
        await save_active(session, ch, tz)
        from app.services import season  # owner request 25.10

        await season.add_ch(session, content, ch, "graid", content.balance.season.pts.get("graid", 0))
        from app.services import control  # owner request 25.10: a raid cleans the district too

        where = control.gang_district(content, target, ch.state.district)
        if where:
            cc = content.balance.control
            await control.adjust(session, content, where, gang=cc.raid_gang, corp=cc.raid_corp)
        return [f"Налёт удался: точка «{g.name}» разгромлена. Касса и стволы — твои: +{loot} эдди."
                + (" Своя банда это оценит." if mine else "") + f" «{g.name}» запомнят, кто приходил."]
    apply_effects(ch.state, {f"faction_rep:{target}": -3, "enmity": 1}, content, rng, year)
    await save_active(session, ch, tz)
    return [f"Налёт сорвался. «{g.name}» знают твоё лицо."]
