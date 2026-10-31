"""Aeon, the city of the eternal life (owner request: the «In Time» crossover). The service side.

The clock costs eddies once; after that every week of life costs a week of time. Time is earned at Aeon's jobs, bought
and sold at the exchange, won at poker, stolen from the rich, and given to other players (a letter, kind "city",
payload `chrono`).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.db import repo
from app.db.models import Character, PlayerMail
from app.engine import checks
from app.engine import chrono as ce
from app.engine.effects import AppliedEffects, apply_effects
from app.services import ap as ap_services
from app.services.game import ActiveCharacter, save_active

R_HAVE, R_NONE, R_EDDIES, R_TIME, R_AP, R_JAIL, R_GONE, R_BAD = (
    "have", "none", "eddies", "time", "ap", "jail", "gone", "bad")


@dataclass
class ChronoResult:
    blocked: str | None = None
    ok: bool = True
    check: object | None = None
    weeks: int = 0                    # time gained (+) or lost (-)
    eddies: int = 0
    text: str = ""
    lines: list[str] = field(default_factory=list)
    applied: AppliedEffects | None = None
    died: bool = False
    death_cause: str | None = None


def _jailed(ch: ActiveCharacter) -> bool:
    return int(ch.state.flags.get("jail_weeks", 0) or 0) > 0


async def install(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                  tz: str) -> ChronoResult:
    c = ce.cfg(content)
    if ce.has(ch.state):
        return ChronoResult(blocked=R_HAVE)
    if ch.state.eddies < c.implant_price:
        return ChronoResult(blocked=R_EDDIES)
    res = ChronoResult(weeks=c.implant_weeks, eddies=-c.implant_price)
    res.applied = apply_effects(ch.state, {"eddies": -c.implant_price}, content, rng, year)
    ce.install(ch.state, content)
    await save_active(session, ch, tz)
    from app.services import loud

    await loud.post(session, ch.id, f"{loud.who(ch.state.name, ch.state.surname)} ставит часы Эона: теперь вместо "
                                    f"денег у этого человека время.")
    await repo.log_event(session, "chrono_install", {}, character_id=ch.id)
    return res


async def exchange(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
                   weeks: int) -> ChronoResult:
    """weeks > 0: buy time for eddies; weeks < 0: sell time for eddies."""
    c = ce.cfg(content)
    if not ce.has(ch.state):
        return ChronoResult(blocked=R_NONE)
    if weeks > 0:
        price = weeks * c.buy_week
        if ch.state.eddies < price:
            return ChronoResult(blocked=R_EDDIES)
        res = ChronoResult(weeks=weeks, eddies=-price)
        res.applied = apply_effects(ch.state, {"eddies": -price}, content, rng, year)
    else:
        n = -weeks
        if ce.weeks(ch.state) <= n:  # selling the last week is selling the life: not at this counter
            return ChronoResult(blocked=R_TIME)
        res = ChronoResult(weeks=weeks, eddies=n * c.sell_week)
        res.applied = apply_effects(ch.state, {"eddies": n * c.sell_week}, content, rng, year)
    ce.add(ch.state, weeks)
    await save_active(session, ch, tz)
    return res


async def work(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
               key: str) -> ChronoResult:
    from app.services import followups

    c = ce.cfg(content)
    job = next((j for j in c.jobs if j.key == key), None)
    if job is None:
        return ChronoResult(blocked=R_GONE)
    if _jailed(ch):
        return ChronoResult(blocked=R_JAIL)
    if not ap_services.can_spend(ch, job.ap):
        return ChronoResult(blocked=R_AP)
    ap_services.spend(ch, content, job.ap)
    chk = checks.roll_check(ch.state, content, rng, job.stat, job.skill, job.dv, ("career",))
    res = ChronoResult(ok=chk.ok, check=chk, text=job.text)
    if chk.ok:
        n = rng.randint(*job.weeks)
        res.weeks = n
        if ce.has(ch.state):
            ce.add(ch.state, n)
        else:  # no clock: the pay is converted at the exchange rate
            res.eddies = n * c.sell_week
            res.applied = apply_effects(ch.state, {"eddies": res.eddies}, content, rng, year)
    elif job.fail_hp:
        res.applied = apply_effects(ch.state, {"hp": -job.fail_hp}, content, rng, year)
    if res.applied is not None:
        fu = await followups.handle(session, content, ch, res.applied.followups, rng, year, tz)
        res.lines, res.died, res.death_cause = fu.lines, fu.died, fu.cause
        if fu.died:
            return res
    await save_active(session, ch, tz)
    return res


async def poker(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
                stake: int) -> ChronoResult:
    """Poker for time: COOL + persuasion against the table. A win doubles the stake. Without the clock the stake is
    in eddies at the exchange rate."""
    c = ce.cfg(content)
    if stake not in c.poker_stakes:
        return ChronoResult(blocked=R_BAD)
    if not ap_services.can_spend(ch, 1):
        return ChronoResult(blocked=R_AP)
    have = ce.has(ch.state)
    if have and ce.weeks(ch.state) <= stake:
        return ChronoResult(blocked=R_TIME)
    if not have and ch.state.eddies < stake * c.buy_week:
        return ChronoResult(blocked=R_EDDIES)
    ap_services.spend(ch, content, 1)
    chk = checks.roll_check(ch.state, content, rng, "COOL", "persuasion", c.poker_dv, ("gamble", "social"))
    res = ChronoResult(ok=chk.ok, check=chk)
    gain = stake if chk.ok else -stake
    if have:
        res.weeks = gain
        ce.add(ch.state, gain)
    else:
        res.eddies = gain * c.buy_week
        res.applied = apply_effects(ch.state, {"eddies": res.eddies}, content, rng, year)
    await save_active(session, ch, tz)
    return res


async def rob(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
              tz: str) -> ChronoResult:
    """Take time off a rich man of the centre. The Timekeepers do not forgive."""
    from app.services import followups

    c = ce.cfg(content)
    if _jailed(ch):
        return ChronoResult(blocked=R_JAIL)
    if not ap_services.can_spend(ch, c.rob_ap):
        return ChronoResult(blocked=R_AP)
    ap_services.spend(ch, content, c.rob_ap)
    chk = checks.roll_check(ch.state, content, rng, "REF", "stealth", c.rob_dv, ("crime",))
    res = ChronoResult(ok=chk.ok, check=chk)
    if chk.ok:
        n = rng.randint(*c.rob_weeks)
        res.weeks = n
        if ce.has(ch.state):
            ce.add(ch.state, n)
        else:
            res.eddies = n * c.sell_week
            res.applied = apply_effects(ch.state, {"eddies": res.eddies}, content, rng, year)
    else:
        res.applied = apply_effects(ch.state, {"hp": -c.rob_fail_hp, "heat": c.rob_fail_heat}, content, rng, year)
        fu = await followups.handle(session, content, ch, res.applied.followups, rng, year, tz)
        res.lines, res.died, res.death_cause = fu.lines, fu.died, fu.cause
        if fu.died:
            return res
    await save_active(session, ch, tz)
    return res


async def gift(session: AsyncSession, content: Content, ch: ActiveCharacter, tz: str, to_char: int,
               weeks: int) -> ChronoResult:
    """Give weeks of own time to another player: a letter, applied when the receiver opens the window."""
    c = ce.cfg(content)
    if not ce.has(ch.state):
        return ChronoResult(blocked=R_NONE)
    if weeks <= 0 or weeks > c.gift_max:
        return ChronoResult(blocked=R_BAD)
    if ce.weeks(ch.state) <= weeks:
        return ChronoResult(blocked=R_TIME)
    other = await session.get(Character, to_char)
    if other is None or not other.alive or other.player_tg_id == ch.player_tg_id:
        return ChronoResult(blocked=R_GONE)
    ce.add(ch.state, -weeks)
    me = f"{ch.state.name} {ch.state.surname}".strip()
    session.add(PlayerMail(to_tg_id=other.player_tg_id, from_tg_id=ch.player_tg_id, from_char_id=ch.id,
                           to_char_id=other.id, from_name=me, kind="city",
                           payload={"text": f"{me} переводит тебе {weeks} нед. времени из Эона.", "chrono": weeks}))
    await save_active(session, ch, tz)
    return ChronoResult(weeks=-weeks)
