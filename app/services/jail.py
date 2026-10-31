"""Prison as a service (PLAN 9.5): the arrest, the weeks in the cell, the lawyer, the yard fight, the escape, the release.

A press of "live a week" in jail lives up to `jail.weeks_per_press` weeks (services/week.py calls `after_week`
for every week of it). The escape goes through `services/lifecheck.py`. The arrest is the `jail` followup
(services/followups.py), the vanished surrender is the `vanish` one.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts_life as T
from app.content.loader import Content
from app.db import repo
from app.engine import jail as eng
from app.engine.checks import CheckResult
from app.engine.dice import roll
from app.engine.effects import FU_RAID, apply_effects
from app.engine.state import (
    FLAG_EMPLOYER,
    FLAG_JAIL_LAWYER,
    FLAG_JAIL_WEEKS,
    FLAG_MAXTAC_CONTRACT,
    FLAG_MAXTAC_DUTY,
    FLAG_PENDING_COMBAT,
    FLAG_WORK_HARD,
)
from app.engine.week import advance_week
from app.services import ap as ap_services
from app.services import followups, lifecheck
from app.services.game import ActiveCharacter, save_active
from app.services.lifecheck import LifeResult

ESCAPE = "escape"


# -- the arrest ------------------------------------------------------------------------------------------------------


async def arrest(
    session: AsyncSession, content: Content, ch: ActiveCharacter, weeks: int, rng: random.Random, year: int, tz: str,
) -> list[str]:
    """Put the character in a cell for `weeks` (the longer of this and what is left). Gear into the stash,
    a long term costs the job. Returns the lines for the screen."""
    st = ch.state
    cfg = content.balance.jail
    was_jailed = eng.in_jail(st)
    st.flags[FLAG_JAIL_WEEKS] = max(eng.weeks_left(st), weeks)
    st.flags.pop(FLAG_PENDING_COMBAT, None)
    st.flags.pop(FLAG_MAXTAC_DUTY, None)  # a call cannot be answered from a cell
    lines = [T.JAIL_ARRESTED.format(weeks=eng.weeks_left(st))]
    if not was_jailed and eng.take_gear(st):
        lines.append(T.JAIL_GEAR_TAKEN)
    if weeks >= cfg.job_loss_weeks and st.career is not None:
        apply_effects(st, {"career_clear": True}, content, rng, year)
        for flag in (FLAG_WORK_HARD, FLAG_EMPLOYER, FLAG_MAXTAC_CONTRACT):
            st.flags.pop(flag, None)
        lines.append(T.JAIL_JOB_LOST)
    await repo.log_event(
        session, "arrest", {"name": st.name, "surname": st.surname, "weeks": eng.weeks_left(st), "heat": st.heat},
        character_id=ch.id, public=not was_jailed,
    )
    return lines


async def after_week(
    content: Content, ch: ActiveCharacter, rng: random.Random, year: int, was_isolated: bool,
) -> list[str]:
    """What one week in jail adds after the engine week: the therapy of the ward (the press sums it up), the release at the end."""
    st = ch.state
    lines: list[str] = []
    if was_isolated:
        n = content.balance.cyberpsychosis.isolation_humanity_per_week
        apply_effects(st, {"humanity": n}, content, rng, year)
    if not eng.in_jail(st):
        back = eng.release(st, content)
        lines.append(T.JAIL_RELEASED)
        if back:
            lines.append(T.JAIL_GEAR_BACK)
    return lines


# -- vanished ------------------------------------------------------------------------------------------------------


async def vanish(
    session: AsyncSession, content: Content, ch: ActiveCharacter, weeks: int, rng: random.Random, year: int, tz: str,
) -> tuple[list[str], bool, str | None]:
    """The weeks of a captive: they pass one by one (rent, debts, age) with no events and no raids.
    Returns (lines, died, cause)."""
    st = ch.state
    lines = [T.VANISHED_LINE.format(weeks=weeks)]
    for _ in range(weeks):
        report = advance_week(st, rng, content, year)
        fu = await followups.handle(
            session, content, ch, [f for f in report.followups if f.kind != FU_RAID], rng, year, tz)
        lines.extend(fu.lines)
        if fu.died:
            return lines, True, fu.cause
    await repo.log_event(session, "vanished", {"weeks": weeks}, character_id=ch.id)
    return lines, False, None


# -- the lawyer ------------------------------------------------------------------------------------------------------


@dataclass
class LawyerResult:
    blocked: str | None = None
    cut: int = 0
    cost: int = 0
    left: int = 0
    name: str = ""
    lines: list[str] = field(default_factory=list)


def lawyer_contact_name(ch: ActiveCharacter, content: Content) -> str:
    people = eng.lawyer_contacts(ch.state, content)
    return max(people, key=lambda n: n.relation).name if people else ""


async def hire_lawyer(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
) -> LawyerResult:
    st = ch.state
    res = LawyerResult()
    res.blocked = eng.lawyer_blocked(st, content)
    if res.blocked is not None:
        return res
    offer = eng.lawyer_offer(st, content)
    assert offer is not None
    res.cut, res.cost = offer
    res.name = lawyer_contact_name(ch, content)
    apply_effects(st, {"eddies": -res.cost}, content, rng, year)
    st.flags[FLAG_JAIL_WEEKS] = eng.weeks_left(st) - res.cut
    st.flags[FLAG_JAIL_LAWYER] = True
    res.left = eng.weeks_left(st)
    await save_active(session, ch, tz)
    await repo.log_event(session, "jail_lawyer", {"cut": res.cut, "cost": res.cost, "left": res.left}, character_id=ch.id)
    return res


# -- the yard fight --------------------------------------------------------------------------------------------------


async def fight(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
):  # noqa: ANN201 - returns a combat Step, or a block reason string
    """A fight in the yard (danger `jail.fight_danger`). Returns the combat Step, or a reason string."""
    from app.services import combat

    st = ch.state
    cfg = content.balance.jail
    if not eng.in_jail(st):
        return "free"
    if st.hp <= 1:
        return "weak"
    if not ap_services.can_spend(ch, cfg.fight_ap):
        return "no_ap"
    ap_services.spend(ch, content, cfg.fight_ap)
    return await combat.begin(
        session, content, ch, rng, year, tz, faction=combat.default_faction(content, ch), danger=cfg.fight_danger,
        src={"src": "jail"},
    )


# -- the escape ------------------------------------------------------------------------------------------------------


async def escape(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
) -> LifeResult | CheckResult:
    res = LifeResult(ESCAPE)
    block = eng.escape_blocked(ch.state)
    if block is not None:
        res.blocked = block
        return res
    cost = content.balance.jail.escape.ap
    if not ap_services.can_spend(ch, cost):
        res.no_ap = True
        return res
    ap_services.spend(ch, content, cost)
    check = eng.roll_escape(ch.state, content, rng)
    return await lifecheck.start(session, content, ch, rng, year, tz, ESCAPE, check, {})


async def _settle_escape(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    args: dict[str, Any], check: CheckResult,
) -> LifeResult:
    st = ch.state
    cfg = content.balance.jail.escape
    res = LifeResult(ESCAPE, ok=check.ok, check=check)
    if check.ok:
        eng.release(st, content)
        res.applied = apply_effects(st, {"heat": eng.escape_heat(st, content)}, content, rng, year)
        res.data["heat"] = st.heat
        await repo.log_event(session, "jail_escape", {"name": st.name, "surname": st.surname}, character_id=ch.id, public=True)
    else:
        weeks = cfg.fail_extra_weeks
        st.flags[FLAG_JAIL_WEEKS] = eng.weeks_left(st) + weeks
        damage = roll(cfg.fail_damage_dice, rng)
        res.applied = apply_effects(st, {"hp": -damage}, content, rng, year)
        res.data.update(weeks=weeks, damage=damage, left=eng.weeks_left(st))
        fu = await followups.handle(session, content, ch, res.applied.followups, rng, year, tz)
        res.lines, res.died, res.death_cause = fu.lines, fu.died, fu.cause
        if fu.died:
            return res
        await repo.log_event(session, "jail_escape_failed", {"weeks": weeks}, character_id=ch.id)
    await save_active(session, ch, tz)
    return res


lifecheck.register(ESCAPE, _settle_escape)


# -- work and pastimes in the cell (owner request 24.10: the debt keeps growing inside) ------------------------------


@dataclass
class CellResult:
    blocked: str | None = None
    text: str = ""
    check: CheckResult | None = None
    applied: Any = None


async def cell_work(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str, key: str,
) -> CellResult:
    from app.engine import checks

    job = content.balance.jail.jobs.get(key)
    if job is None:
        return CellResult("none")
    st = ch.state
    if not eng.in_jail(st):
        return CellResult("free")
    if not ap_services.can_spend(ch, job.ap):
        return CellResult("ap")
    ap_services.spend(ch, content, job.ap)
    res = CellResult()
    ok = True
    if job.stat:
        res.check = checks.roll_check(st, content, rng, job.stat, job.skill, job.dv)
        ok = res.check.ok
    if ok:
        fx: dict[str, Any] = dict(job.effects)
        if job.eddies:
            fx["eddies"] = int(fx.get("eddies", 0)) + job.eddies
        if job.mood:
            fx["mood"] = int(fx.get("mood", 0)) + job.mood
        if job.humanity:
            fx["humanity"] = int(fx.get("humanity", 0)) + job.humanity
        res.text = job.text
    else:
        fx = {"hp": -job.fail_hp} if job.fail_hp else {}
        if job.fail_weeks:
            st.flags[FLAG_JAIL_WEEKS] = eng.weeks_left(st) + job.fail_weeks
        res.text = job.fail_text or "Не выходит. Смена пропала."
    res.applied = apply_effects(st, fx, content, rng, year) if fx else None
    if st.hp <= 0:
        st.hp = 1
    await save_active(session, ch, tz)
    return res
