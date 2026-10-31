"""Crime as a service (PLAN 9.4): the four actions, raids and hunts, what a fight with the police changes.

A crime goes through `services/lifecheck.py` (luck, glitch, accept). The raid itself is rolled by the week
(engine/week.py) and arrives as `FU_RAID`, which leaves a pending fight (services/followups.py); the fight
and its exits are the M5 ones. Here are the hooks the fight service calls when a police fight ends.
"""

from __future__ import annotations

import random
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.db import repo
from app.engine import crime as eng
from app.engine.checks import CheckResult
from app.engine.effects import apply_effects
from app.engine.state import FLAG_CRIME_WEEK
from app.services import ap as ap_services
from app.services import followups, lifecheck
from app.services import game_events as bus
from app.services.game import ActiveCharacter, save_active
from app.services.lifecheck import LifeResult

CRIME = "crime"
POLICE_SRC = (eng.SRC_RAID, eng.SRC_HUNT)


async def commit(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str, kind: str,
) -> LifeResult | CheckResult:
    res = LifeResult(CRIME, data={"kind": kind})
    block = eng.blocked(ch.state, content)
    if block is not None:
        res.blocked = block
        return res
    cost = content.balance.crime.ap
    if not ap_services.can_spend(ch, cost):
        res.no_ap = True
        return res
    ap_services.spend(ch, content, cost)
    check = eng.roll_crime(ch.state, content, rng, kind)
    return await lifecheck.start(session, content, ch, rng, year, tz, CRIME, check, {"kind": kind})


async def _settle(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    args: dict[str, Any], check: CheckResult,
) -> LifeResult:
    kind = str(args["kind"])
    st = ch.state
    wanted_at = content.balance.crime.wanted_publish_heat
    heat_before = st.heat
    plan = eng.plan_crime(st, content, rng, kind, check)
    st.flags[FLAG_CRIME_WEEK] = True
    res = LifeResult(CRIME, ok=check.ok, check=check, data={
        "kind": kind, "reward": plan.reward, "damage": plan.damage, "arrested": plan.arrested,
        "jail_weeks": plan.jail_weeks, "crit": check.crit,
    })
    res.applied = apply_effects(st, dict(plan.effects), content, rng, year)
    fu = await followups.handle(session, content, ch, res.applied.followups, rng, year, tz)
    res.lines, res.died, res.death_cause = fu.lines, fu.died, fu.cause
    if plan.clean:
        res.lines.append("🕶 Чисто: свидетелей не осталось, розыск не вырос.")
    if fu.died:
        return res
    res.data["heat"] = st.heat - heat_before
    await save_active(session, ch, tz)
    await repo.log_event(
        session, "crime", {"kind": kind, "ok": check.ok, "reward": plan.reward, "arrested": plan.arrested, "heat": st.heat},
        character_id=ch.id,
    )
    if st.heat >= wanted_at > heat_before:
        await repo.log_event(
            session, "wanted", {"name": st.name, "surname": st.surname, "heat": st.heat}, character_id=ch.id, public=True,
        )
    kw = {"player_tg_id": ch.player_tg_id, "character_id": ch.id, "session": session, "ch": ch}
    await bus.publish("crime_done", kind=kind, success=check.ok, **kw)
    return res


lifecheck.register(CRIME, _settle)


# -- hooks of the fight service --------------------------------------------------------------------------------------


async def fight_over(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    src: str, faction: str, result: str,
) -> list[str]:
    """A police fight ended (src raid or hunt). Won: more heat. Downed: arrested. Returns lines for the closing screen."""
    st = ch.state
    cfg = content.balance.crime
    lines: list[str] = []
    if src == "jail":  # the yard: a won fight earns respect inside
        if result == "won":
            apply_effects(st, {"street_cred": content.balance.jail.fight_cred}, content, rng, year)
        return lines
    if src not in POLICE_SRC:
        return lines
    if result == "won":
        applied = apply_effects(st, {"heat": cfg.raid_won_heat}, content, rng, year)
        lines.extend((await followups.handle(session, content, ch, applied.followups, rng, year, tz)).lines)
    elif result == "downed":
        weeks = eng.arrest_weeks(st, content, rng, faction)
        applied = apply_effects(st, {"jail_weeks": weeks}, content, rng, year)
        lines.extend((await followups.handle(session, content, ch, applied.followups, rng, year, tz)).lines)
    return lines


def exit_over(
    content: Content, ch: ActiveCharacter, rng: random.Random, year: int, src: str, result: str,
) -> None:
    """The exits ended a police raid before the fight: a bribe or a talk cools the file."""
    if src == eng.SRC_RAID and result == "avoided":
        apply_effects(ch.state, {"heat": content.balance.crime.raid_avoid_heat}, content, rng, year)
