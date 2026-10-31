"""Careers as a service (PLAN 9.3): join, work harder, ask for a promotion, quit.

The weekly salary and the weeks served are part of the week (engine/week.py); firing happens through
events (content/events_life.yaml). Join and quit change the character through `apply_effects`
(`career_set`, `career_clear`); a promotion changes the rank in `engine/careers.promote`.
The promotion check goes through `services/lifecheck.py` (luck, glitch, accept).
"""

from __future__ import annotations

import random
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.db import repo
from app.engine import careers as eng
from app.engine.checks import CheckResult
from app.engine.dice import clamp
from app.engine.effects import apply_effects
from app.engine.state import FLAG_EMPLOYER, FLAG_MAXTAC_CONTRACT, FLAG_MAXTAC_DUTY, FLAG_MAXTAC_DUTY_AT, FLAG_WORK_HARD
from app.services import ap as ap_services
from app.services import followups, lifecheck
from app.services import game_events as bus
from app.services.game import ActiveCharacter, save_active
from app.services.lifecheck import LifeResult

JOIN, HARD, PROMO, QUIT = "join", "work_hard", "promo", "quit"


def _kw(ch: ActiveCharacter, session: AsyncSession) -> dict[str, Any]:
    return {"player_tg_id": ch.player_tg_id, "character_id": ch.id, "session": session, "ch": ch}


async def join(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    key: str,
) -> LifeResult:
    res = LifeResult(JOIN, data={"career": key})
    career = content.available("careers", year).get(key)
    if career is None:
        res.blocked = "gone"
        return res
    info = eng.join_info(ch.state, content, career)
    if info.blocked is not None:
        res.blocked = info.blocked
        return res
    if info.gaps:
        res.blocked = eng.BLOCK_HEAT if all(g.kind == "heat" for g in info.gaps) else eng.BLOCK_REQS
        return res
    cost = content.balance.careers.join_ap
    if not ap_services.can_spend(ch, cost):
        res.no_ap = True
        return res
    ap_services.spend(ch, content, cost)
    res.applied = apply_effects(ch.state, {f"career_set:{key}": True}, content, rng, year)
    known = content.available("corps", year)  # owner report 26.10: no employer the year does not know (Kang Tao)
    if career.corps and [k for k in career.corps if k in known]:
        employer = rng.choice([k for k in career.corps if k in known])
        ch.state.flags[FLAG_EMPLOYER] = employer
        res.data["employer"] = employer
    ch.state.flags.pop(FLAG_WORK_HARD, None)
    await save_active(session, ch, tz)
    await repo.log_event(session, "career_join", {"career": key}, character_id=ch.id)
    return res


async def work_hard(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
) -> LifeResult:
    cfg = content.balance.careers
    res = LifeResult(HARD)
    st = ch.state
    if st.career is None:
        res.blocked = eng.BLOCK_NO_JOB
        return res
    if eng.in_jail(st):
        res.blocked = eng.BLOCK_JAIL
        return res
    if not ap_services.can_spend(ch, cfg.work_hard_ap):
        res.no_ap = True
        return res
    ap_services.spend(ch, content, cfg.work_hard_ap)
    res.applied = apply_effects(st, {"mood": cfg.work_hard_mood}, content, rng, year)
    res.data = {"career": st.career.key, "bonus": eng.add_work_hard(st, content), "cap": cfg.work_hard_cap}
    fu = await followups.handle(session, content, ch, res.applied.followups, rng, year, tz)
    res.lines, res.died, res.death_cause = fu.lines, fu.died, fu.cause
    if fu.died:
        return res
    await save_active(session, ch, tz)
    await repo.log_event(session, "work_hard", {"career": st.career.key, "bonus": res.data["bonus"]}, character_id=ch.id)
    await bus.publish("work_hard", career=st.career.key, **_kw(ch, session))
    return res


async def ask_promotion(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
) -> LifeResult | CheckResult:
    """Roll the promotion check. A pass or a hopeless miss settles at once; a miss luck or a glitch could
    save waits as pending."""
    cfg = content.balance.careers
    st = ch.state
    block = eng.promotion_blocked(st, content)
    if block is not None:
        return LifeResult(PROMO, blocked=block[0], data={"n": block[1]})
    if not ap_services.can_spend(ch, cfg.promo_ap):
        return LifeResult(PROMO, no_ap=True)
    ap_services.spend(ch, content, cfg.promo_ap)
    check = eng.roll_promotion(st, content, rng)
    return await lifecheck.start(session, content, ch, rng, year, tz, PROMO, check, {})


async def _settle_promo(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
    args: dict[str, Any], check: CheckResult,
) -> LifeResult:
    st = ch.state
    assert st.career is not None
    career = content.careers[st.career.key]
    res = LifeResult(PROMO, ok=check.ok, check=check, data={"career": career.key})
    if check.ok:
        rank = eng.promote(st)
        r = career.ranks[clamp(rank, 1, len(career.ranks)) - 1]
        res.data.update(rank=rank, rank_name=r.name, salary=r.salary)
    else:
        eng.refuse(st, content)
        res.data["retry"] = content.balance.careers.promo_retry_weeks
    await save_active(session, ch, tz)
    await repo.log_event(
        session, "promotion", {"career": career.key, "ok": check.ok, "rank": st.career.rank}, character_id=ch.id,
        public=check.ok and st.career.rank >= 4,
    )
    if check.ok:
        await bus.publish("promotion", career=career.key, **_kw(ch, session))
    return res


lifecheck.register(PROMO, _settle_promo)


async def quit_job(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
) -> LifeResult:
    """Leave the job. A recruited MaxTac who has not served the term deserts: heat and a fugitive again."""
    cfg = content.balance.careers
    st = ch.state
    res = LifeResult(QUIT)
    if st.career is None:
        res.blocked = eng.BLOCK_NO_JOB
        return res
    key = st.career.key
    res.data["career"] = key
    career = content.careers.get(key)
    if career is not None and career.bound_flag and st.flags.get(career.bound_flag):
        res.blocked = "bound"  # owner request 24.10: the night taxi does not let its own go
        return res
    desertion = key == "maxtac" and eng.under_contract(st)
    fx: dict[str, int | bool] = {"career_clear": True, "mood": cfg.quit_mood}
    if desertion:
        fx.update({"heat": cfg.desertion_heat, "flag_set:fugitive_psycho": True})
        res.data["desertion"] = True
    res.applied = apply_effects(st, fx, content, rng, year)
    for flag in (FLAG_WORK_HARD, FLAG_EMPLOYER, FLAG_MAXTAC_CONTRACT, FLAG_MAXTAC_DUTY_AT, FLAG_MAXTAC_DUTY):
        st.flags.pop(flag, None)
    if desertion:  # M6.6b: a deserter is a fugitive psycho again: no home, hunts every few weeks
        from app.services import psychosis

        await psychosis.ensure_fugitive(session, content, ch, rng, year, tz, quiet=True)
    fu = await followups.handle(session, content, ch, res.applied.followups, rng, year, tz)
    res.lines, res.died, res.death_cause = fu.lines, fu.died, fu.cause
    if fu.died:
        return res
    await save_active(session, ch, tz)
    await repo.log_event(session, "career_quit", {"career": key, "desertion": desertion}, character_id=ch.id)
    return res

