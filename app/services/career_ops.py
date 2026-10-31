"""Inner tasks of a career (owner request 23.10): one a month, a promotion for the dirty work, a harsh failure."""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.engine import careers as ce
from app.engine import checks
from app.engine.effects import AppliedEffects, apply_effects
from app.engine.state import FLAG_CRIME_WEEK
from app.services import ap as ap_services
from app.services.game import ActiveCharacter, save_active

FLAG = "career_op_week"  # weeks_lived when the last task was taken: one a press
R_NONE, R_DONE, R_AP, R_RANK, R_JAIL, R_GONE = "none", "done", "ap", "rank", "jail", "gone"
DEATH = "corp_river"


def ops_for(content: Content, ch: ActiveCharacter) -> list:
    cur = ch.state.career
    return list(content.balance.careers.ops.get(cur.key, [])) if cur is not None else []


def done_this_press(ch: ActiveCharacter) -> bool:
    return ch.state.flags.get(FLAG) == ch.state.weeks_lived


@dataclass
class OpResult:
    blocked: str | None = None
    ok: bool = False
    check: object | None = None
    text: str = ""
    lines: list[str] = field(default_factory=list)
    applied: AppliedEffects | None = None
    died: bool = False
    death_cause: str | None = None


async def run(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
              key: str) -> OpResult:
    from app.services import followups

    st = ch.state
    cur = st.career
    op = next((o for o in ops_for(content, ch) if o.key == key), None)
    if cur is None or op is None:
        return OpResult(blocked=R_NONE if cur is None else R_GONE)
    if ce.in_jail(st):
        return OpResult(blocked=R_JAIL)
    if done_this_press(ch):
        return OpResult(blocked=R_DONE)
    if cur.rank < op.min_rank:
        return OpResult(blocked=R_RANK)
    if not ap_services.can_spend(ch, op.ap):
        return OpResult(blocked=R_AP)
    ap_services.spend(ch, content, op.ap)
    st.flags[FLAG] = st.weeks_lived
    chk = checks.roll_check(st, content, rng, op.stat, op.skill, op.dv, ("career",), extra=ce.bonus(st))
    res = OpResult(ok=chk.ok, check=chk)
    fx: dict = {}
    if chk.ok:
        res.text = op.ok_text
        lo, hi = op.pay
        if hi:
            fx["eddies"] = rng.randint(lo, hi)
        fx.update(op.ok_effects)
        career = content.careers.get(cur.key)
        if op.promo and career is not None and cur.rank < len(career.ranks):
            rank = ce.promote(st)
            res.lines.append(f"Повышение: {career.ranks[rank - 1].name}.")
    else:
        res.text = op.fail_text
        f = op.fail
        if f.hp:
            fx["hp"] = -f.hp
        if f.heat:
            fx["heat"] = f.heat
        if f.mood:
            fx["mood"] = -abs(f.mood)
        if f.fired:
            fx["career_clear"] = True
            res.lines.append("Тебя увольняют одним письмом.")
        if f.death_chance and rng.random() < f.death_chance:
            fx[f"death:{DEATH}"] = True
    if op.crime:
        st.flags[FLAG_CRIME_WEEK] = True  # the street sees it; the employer that ordered it does not care
        if chk.ok:
            st.flags.pop(FLAG_CRIME_WEEK, None)
    res.applied = apply_effects(st, fx, content, rng, year) if fx else None
    if res.applied is not None:
        fu = await followups.handle(session, content, ch, res.applied.followups, rng, year, tz)
        res.lines.extend(fu.lines)
        res.died, res.death_cause = fu.died, fu.cause
        if fu.died:
            return res
    await save_active(session, ch, tz)
    return res
