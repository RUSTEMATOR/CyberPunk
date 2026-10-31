"""Careers (PLAN 9.3, DESIGN.md section 11): who may join, the promotion rules, quitting.

Pure functions. A career has five ranks (careers.yaml). Salary and the weeks served are already
part of the week (engine/week.py). Here: the requirements to join (stats, skills, heat, and for MaxTac
the normal route: NCPD detective and implants worth 20+ PCh), the promotion check with the bonus of
"work harder", and what leaving does. The numbers are `balance.careers`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from app.content.loader import Content
from app.content.schemas import Career
from app.engine import checks, derived
from app.engine.checks import CheckResult
from app.engine.dice import clamp
from app.engine.state import (
    FLAG_JAIL_WEEKS,
    FLAG_MAXTAC_CONTRACT,
    FLAG_PROMO_RETRY,
    FLAG_WORK_HARD,
    CharacterState,
)

# Why a career cannot be joined / a promotion cannot be asked / work cannot be done.
BLOCK_JAIL = "jail"
BLOCK_HEAT = "heat"
BLOCK_HAVE_JOB = "have_job"
BLOCK_REQS = "requirements"
BLOCK_NO_JOB = "no_job"
BLOCK_TOP = "top_rank"
BLOCK_WEEKS = "weeks"
BLOCK_RETRY = "retry"
BLOCK_FUGITIVE = "fugitive"
BLOCK_CONTRACT = "contract"
BLOCK_BLACKLIST = "blacklist"


@dataclass
class Gap:
    """One unmet requirement: kind is stat | skill | career | humanity | heat."""

    kind: str
    key: str
    need: int
    have: int


@dataclass
class JoinInfo:
    career: Career
    gaps: list[Gap] = field(default_factory=list)
    blocked: str | None = None  # a hard reason that is not a gap (jail, have a job)

    @property
    def ok(self) -> bool:
        return not self.gaps and self.blocked is None


def pch_installed(state: CharacterState, content: Content) -> int:
    """Total PCh of the installed implants before tolerance (MaxTac requirement)."""
    return sum(content.cyberware[c.key].humanity_cost for c in state.cyberware if c.key in content.cyberware)


def heat_limit(content: Content, key: str) -> int:
    cfg = content.balance.careers
    return cfg.join_max_heat_law if key in cfg.law_careers else cfg.join_max_heat


def in_jail(state: CharacterState) -> bool:
    return int(state.flags.get(FLAG_JAIL_WEEKS, 0) or 0) > 0


def join_info(state: CharacterState, content: Content, career: Career) -> JoinInfo:
    """Gaps between the character and the career's requirements. Transfers (MaxTac from NCPD) are allowed
    from the required career; any other career needs the character to be without a job."""
    info = JoinInfo(career)
    req = career.requirements
    if career.requires_flag and not state.flags.get(career.requires_flag):
        info.blocked = BLOCK_REQS
    if in_jail(state):
        info.blocked = BLOCK_JAIL
    if state.flags.get("corp_blacklist") and career.key in content.balance.careers.blacklist_careers:
        info.blocked = info.blocked or BLOCK_BLACKLIST  # owner request 23.10: caught three times
    if state.career is not None:
        transfer = req.from_career is not None and state.career.key == req.from_career.career
        if not transfer:
            info.blocked = info.blocked or BLOCK_HAVE_JOB
    for stat, need in req.stats.items():
        have = derived.stat_total(state, content, stat)
        if have < need:
            info.gaps.append(Gap("stat", stat, need, have))
    for skill, need in req.skills.items():
        have = derived.skill_total(state, content, skill)
        if have < need:
            info.gaps.append(Gap("skill", skill, need, have))
    if req.from_career is not None:
        cur = state.career
        have_rank = cur.rank if cur is not None and cur.key == req.from_career.career else 0
        if have_rank < req.from_career.min_rank:
            info.gaps.append(Gap("career", req.from_career.career, req.from_career.min_rank, have_rank))
    if req.min_humanity_lost:
        have = pch_installed(state, content)
        if have < req.min_humanity_lost:
            info.gaps.append(Gap("humanity", "pch", req.min_humanity_lost, have))
    limit = heat_limit(content, career.key)
    if state.heat > limit:
        info.gaps.append(Gap("heat", "heat", limit, state.heat))
    return info


def joinable(content: Content, year: int, state: CharacterState | None = None) -> list[Career]:
    """The careers of this world year in file order (a career that needs a flag only for its holders)."""
    return [c for c in content.available("careers", year).values()
            if not c.requires_flag or (state is not None and state.flags.get(c.requires_flag))]


def bonus(state: CharacterState) -> int:
    return int(state.flags.get(FLAG_WORK_HARD, 0) or 0)


def add_work_hard(state: CharacterState, content: Content) -> int:
    """One 'work harder': +work_hard_promo_bonus to the next promotion check, up to the cap. Returns the new bonus."""
    cfg = content.balance.careers
    new = clamp(bonus(state) + cfg.work_hard_promo_bonus, 0, cfg.work_hard_cap)
    state.flags[FLAG_WORK_HARD] = new
    return new


def promotion_blocked(state: CharacterState, content: Content) -> tuple[str, int] | None:
    """(reason, number) while a promotion cannot be asked: number = weeks to wait, else 0."""
    cur = state.career
    if cur is None:
        return BLOCK_NO_JOB, 0
    career = content.careers[cur.key]
    if in_jail(state):
        return BLOCK_JAIL, 0
    rank = career.ranks[clamp(cur.rank, 1, len(career.ranks)) - 1]
    if cur.rank >= len(career.ranks) or rank.promotion_dv is None:
        return BLOCK_TOP, 0
    if cur.weeks < rank.min_weeks:
        return BLOCK_WEEKS, rank.min_weeks - cur.weeks
    retry = int(state.flags.get(FLAG_PROMO_RETRY, 0) or 0)
    if state.weeks_lived < retry:
        return BLOCK_RETRY, retry - state.weeks_lived
    return None


def promotion_dv(state: CharacterState, content: Content) -> int:
    cur = state.career
    assert cur is not None
    career = content.careers[cur.key]
    dv = career.ranks[clamp(cur.rank, 1, len(career.ranks)) - 1].promotion_dv
    assert dv is not None
    return dv


def roll_promotion(state: CharacterState, content: Content, rng: random.Random) -> CheckResult:
    """The promotion check: the career's stat + skill against the rank's dv, with the work-harder bonus."""
    cur = state.career
    assert cur is not None
    career = content.careers[cur.key]
    ck = career.promotion_check
    return checks.roll_check(
        state, content, rng, ck.stat, ck.skill, promotion_dv(state, content), ("career",), extra=bonus(state),
    )


def promote(state: CharacterState) -> int:
    """Move one rank up: rank + 1, weeks at the rank reset, the bonus is spent. Returns the new rank."""
    assert state.career is not None
    state.career.rank += 1
    state.career.weeks = 0
    state.flags.pop(FLAG_WORK_HARD, None)
    state.flags.pop(FLAG_PROMO_RETRY, None)
    return state.career.rank


def refuse(state: CharacterState, content: Content) -> None:
    """The promotion was refused: ask again after `promo_retry_weeks`."""
    state.flags[FLAG_PROMO_RETRY] = state.weeks_lived + content.balance.careers.promo_retry_weeks


def under_contract(state: CharacterState) -> bool:
    """A recruited MaxTac who has not served the term (flag set by the recruitment, M6.6a)."""
    until = state.flags.get(FLAG_MAXTAC_CONTRACT)
    return until is not None and state.weeks_lived < int(until)
