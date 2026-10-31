"""Crime and the law (PLAN 9.4, DESIGN.md section 11): the four actions, raids, MaxTac hunts, arrest terms.

Pure functions; numbers are `balance.crime` and `balance.jail`. A crime is a check against the action's
dv; success pays and adds heat, failure adds more heat, may hurt, and may end in an arrest. At heat 3 and
above the week can bring a police raid (20% x (heat - 2)); at heat 5 the squad is MaxTac.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from app.content.loader import Content
from app.content.schemas import CRIME_KINDS
from app.engine import checks, derived
from app.engine.checks import CheckResult
from app.engine.dice import clamp, roll
from app.engine.state import FLAG_FUGITIVE, FLAG_JAIL_WEEKS, CharacterState

PICKPOCKET, CAR_THEFT, STORE_ROBBERY, ASSAULT = CRIME_KINDS

BLOCK_JAIL = "jail"
BLOCK_FUGITIVE = "fugitive"  # a fugitive psycho has no ordinary life to commit crimes in

SRC_RAID, SRC_HUNT = "raid", "hunt"


def action_cfg(content: Content, kind: str):  # noqa: ANN201 - a CrimeActionBalance
    return getattr(content.balance.crime, kind)


def blocked(state: CharacterState, content: Content) -> str | None:
    if int(state.flags.get(FLAG_JAIL_WEEKS, 0) or 0) > 0:
        return BLOCK_JAIL
    if state.flags.get(FLAG_FUGITIVE):
        return BLOCK_FUGITIVE
    return None


def roll_crime(state: CharacterState, content: Content, rng: random.Random, kind: str) -> CheckResult:
    ck = action_cfg(content, kind).check
    return checks.roll_check(state, content, rng, ck.stat, ck.skill, ck.dv, ("crime",))


@dataclass
class CrimePlan:
    kind: str
    ok: bool
    effects: dict[str, int | bool] = field(default_factory=dict)
    reward: int = 0
    arrested: bool = False
    jail_weeks: int = 0
    damage: int = 0
    clean: bool = False  # passed cleanly: nobody saw it, no heat


def plan_crime(
    state: CharacterState, content: Content, rng: random.Random, kind: str, check: CheckResult,
) -> CrimePlan:
    """The final outcome of a crime whose check is settled."""
    cfg = action_cfg(content, kind)
    plan = CrimePlan(kind, check.ok)
    if check.ok:
        pct = derived.mod(state, content, "crime_reward_pct")
        plan.reward = max(0, round(rng.randint(cfg.reward_min, cfg.reward_max) * (1 + pct / 100)))
        plan.effects["eddies"] = plan.reward
        if cfg.heat_success and check.margin >= content.balance.wanted.clean_margin and content.balance.wanted.enabled:
            plan.clean = True  # owner request 26.10: a clean job leaves no witnesses
        elif cfg.heat_success:
            plan.effects["heat"] = cfg.heat_success
        return plan
    plan.effects["heat"] = cfg.heat_fail
    dice = content.balance.crime.fail_damage.get(kind)
    if dice:
        plan.damage = roll(dice, rng)
        plan.effects["hp"] = -plan.damage
    chance = cfg.arrest_chance_on_fail
    if check.crit == "fail":
        chance = min(1.0, chance * content.balance.crime.crit_fail_arrest_mult)
    if rng.random() < chance:
        plan.arrested = True
        plan.jail_weeks = rng.randint(cfg.jail_weeks_min, cfg.jail_weeks_max)
        plan.effects["jail_weeks"] = plan.jail_weeks
    return plan


# -- raids and hunts ----------------------------------------------------------------------------------------


@dataclass
class Raid:
    src: str  # raid | hunt
    faction: str  # ncpd | maxtac
    danger: int


def raid_chance(content: Content, heat: int, district_heat: int = 0) -> float:
    """Chance of a raid at this heat; a hot district (loud public gigs there, `world_state.district_heat`) adds
    `world.raid_bonus_per_district_heat` per point. Nothing happens below `raid_min_heat`."""
    c = content.balance.crime
    if heat < c.raid_min_heat:
        return 0.0
    bonus = content.balance.world.raid_bonus_per_district_heat * max(0, district_heat)
    return clamp(c.raid_chance_per_heat * (heat - c.raid_heat_offset) + bonus, 0.0, 1.0)


def raid_roll(state: CharacterState, content: Content, rng: random.Random, heat: int, district_heat: int = 0) -> Raid | None:
    """The raid (or the MaxTac hunt at the top heat) of this week, rolled with the heat the player pressed the
    button at. A jailed character cannot be raided, a fugitive psycho has the hunts of M6.6a instead."""
    if int(state.flags.get(FLAG_JAIL_WEEKS, 0) or 0) > 0 or state.flags.get(FLAG_FUGITIVE):
        return None
    c = content.balance.crime
    chance = raid_chance(content, heat, district_heat)
    if chance <= 0 or rng.random() >= chance:
        return None
    if heat >= c.maxtac_heat:
        return Raid(SRC_HUNT, "maxtac", c.maxtac_danger)
    return Raid(SRC_RAID, "ncpd", clamp(heat - c.raid_danger_offset, 1, c.raid_danger_max))


def arrest_weeks(state: CharacterState, content: Content, rng: random.Random, faction: str) -> int:
    """Term of an arrest after a surrender or a lost fight: MaxTac 26-52, everyone else by heat."""
    j = content.balance.jail
    if faction == "maxtac":
        return rng.randint(j.maxtac_surrender_min, j.maxtac_surrender_max)
    return clamp(max(j.min_weeks, state.heat * j.arrest_weeks_per_heat), j.min_weeks, j.max_weeks)
