"""Activities of the week (PLAN 4.5, DESIGN.md sections 4, 7, 7a): what each one costs and does.

Pure functions. Every activity is described as a `Plan`: the AP and eddies it costs, the effects
it applies (the service gives them to `apply_effects`), and the side facts the service acts on
(an event drawn for the bar, a partner met, a vow broken, an overdose). Randomness comes from the
`rng` passed in; the numbers are `balance.activities`.

Keys: train, study, bar, braindance, therapy, rest, gamble, meditation, martial_arts, service.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from app.content.loader import Content
from app.content.schemas import SKILLS
from app.engine import checks, derived, events as engine_events
from app.engine.checks import CheckResult
from app.engine.dice import D10, roll
from app.engine.state import FLAG_ADDICTION, FLAG_JAIL_WEEKS, CharacterState

TRAIN, STUDY, BAR, BRAINDANCE, THERAPY = "train", "study", "bar", "braindance", "therapy"
REST, GAMBLE, MEDITATION, MARTIAL_ARTS, SERVICE = "rest", "gamble", "meditation", "martial_arts", "service"

# Listing order of the activities screen.
ORDER: tuple[str, ...] = (
    TRAIN, STUDY, BAR, BRAINDANCE, THERAPY, REST, GAMBLE, MEDITATION, MARTIAL_ARTS, SERVICE,
)
PATH_ACTIVITIES: tuple[str, ...] = (MEDITATION, MARTIAL_ARTS, SERVICE)  # DESIGN.md 7a

# The only things a convict can do in a cell.
JAIL_ALLOWED: frozenset[str] = frozenset({TRAIN, STUDY, REST, MEDITATION, MARTIAL_ARTS})

# Bus event name of each activity (services/game_events.py).
BUS_NAME: dict[str, str] = {
    TRAIN: "train", STUDY: "study", BAR: "bar", BRAINDANCE: "braindance", THERAPY: "therapy",
    REST: "rest", MEDITATION: "meditate", MARTIAL_ARTS: "martial_arts", SERVICE: "service",
}  # gamble publishes nothing

# Why an activity is closed.
BLOCK_JAIL = "jail"
BLOCK_EDDIES = "eddies"
BLOCK_SKILL_CAP = "skill_cap"
BLOCK_WRONG_SKILL = "wrong_skill"
BLOCK_CHROME_FREE = "chrome_free"
BLOCK_HUMANITY_FULL = "humanity_full"


def ap_cost(content: Content, activity: str) -> int:
    a = content.balance.activities
    cfg = {
        TRAIN: a.training, STUDY: a.study, BAR: a.bar, BRAINDANCE: a.braindance, THERAPY: a.therapy,
        REST: a.rest, GAMBLE: a.gamble, MEDITATION: a.meditation, MARTIAL_ARTS: a.martial_arts,
        SERVICE: a.service,
    }[activity]
    return cfg.ap_cost


def jail_free(state: CharacterState | None, content: Content, activity: str) -> bool:
    """Owner request 24.10: in a cell the training, the study and the other cell activities cost nothing."""
    return (state is not None and content.balance.jail.free_training and activity in JAIL_ALLOWED
            and int(state.flags.get(FLAG_JAIL_WEEKS, 0) or 0) > 0)


def eddy_cost(content: Content, activity: str, state: CharacterState | None = None) -> int:
    """Fixed price of the activity (a gamble has none: the stake is chosen; a cell activity is free)."""
    if jail_free(state, content, activity):
        return 0
    a = content.balance.activities
    return {
        TRAIN: a.training.cost, STUDY: a.study.cost, BAR: a.bar.cost, BRAINDANCE: a.braindance.cost,
        THERAPY: a.therapy.cost, REST: a.rest.cost, GAMBLE: 0, MEDITATION: a.meditation.cost,
        MARTIAL_ARTS: a.martial_arts.cost, SERVICE: a.service.cost,
    }[activity]


def trainable(content: Content, study: bool) -> tuple[str, ...]:
    """The skills a training (or a study) can raise, in the SKILLS order."""
    cfg = content.balance.activities.study if study else content.balance.activities.training
    return tuple(s for s in SKILLS if not cfg.skills or s in cfg.skills)


def sessions_to_level(state: CharacterState, content: Content, skill: str, xp_gain: int) -> int | None:
    """How many sessions of `xp_gain` xp the next level of `skill` still takes (xp modifiers included);
    None at the cap."""
    if state.skill_level(skill) >= content.balance.character.skill_cap:
        return None
    st = state.skills.get(skill)
    level, xp = (st.level, st.xp) if st else (0, 0)
    per = max(0.01, xp_gain * derived.xp_multiplier(state, content, skill))
    left = derived.skill_xp_needed(level, content) - xp
    return max(1, math.ceil(left / per))


def vows_against(state: CharacterState, content: Content, forbid: str) -> list[str]:
    """Held vows whose `forbids` list names `forbid` (`braindance`, `violence`, ...)."""
    return [k for k in state.vows if (v := content.vows.get(k)) is not None and forbid in v.forbids]


def blocked(
    state: CharacterState, content: Content, activity: str, *, skill: str | None = None
) -> str | None:
    """Why the activity cannot be started now, or None. AP is checked by the service."""
    if int(state.flags.get(FLAG_JAIL_WEEKS, 0) or 0) > 0 and activity not in JAIL_ALLOWED:
        return BLOCK_JAIL
    if state.eddies < eddy_cost(content, activity, state):
        return BLOCK_EDDIES
    if activity in (TRAIN, STUDY) and skill is not None:
        if skill not in trainable(content, activity == STUDY):
            return BLOCK_WRONG_SKILL
        if state.skill_level(skill) >= content.balance.character.skill_cap:
            return BLOCK_SKILL_CAP
    if activity == THERAPY:
        if state.chrome_free:
            return BLOCK_CHROME_FREE
        if state.humanity >= derived.humanity_max(state, content):
            return BLOCK_HUMANITY_FULL
    return None


def gamble_blocked(state: CharacterState, content: Content, stake: int) -> str | None:
    if int(state.flags.get(FLAG_JAIL_WEEKS, 0) or 0) > 0:
        return BLOCK_JAIL
    if stake not in content.balance.activities.gamble.stakes or state.eddies < stake:
        return BLOCK_EDDIES
    return None


@dataclass
class Plan:
    """What an activity does, before it is applied."""

    activity: str
    ap: int
    effects: dict[str, int | bool] = field(default_factory=dict)  # includes the (negative) eddy price
    check: CheckResult | None = None  # meditation
    bar_event: str | None = None  # an event id to put at the front of the queue
    addiction: int | None = None  # the counter after a braindance
    addiction_event: bool = False  # the addiction event must be offered
    overdose: bool = False
    broken_vows: list[str] = field(default_factory=list)
    humanity_roll: int = 0  # therapy: dice plus therapy_bonus
    skill: str | None = None


def plan_train(content: Content, skill: str, *, study: bool = False) -> Plan:
    cfg = content.balance.activities.study if study else content.balance.activities.training
    if skill not in SKILLS:
        raise ValueError(f"unknown skill {skill!r}")
    fx: dict[str, int | bool] = {f"skill_xp:{skill}": cfg.xp, "mood": cfg.mood}
    if cfg.cost:
        fx["eddies"] = -cfg.cost
    return Plan(STUDY if study else TRAIN, cfg.ap_cost, fx, skill=skill)


def plan_rest(content: Content) -> Plan:
    cfg = content.balance.activities.rest
    fx: dict[str, int | bool] = {"mood": cfg.mood, "hp": cfg.hp}
    if cfg.cost:
        fx["eddies"] = -cfg.cost
    return Plan(REST, cfg.ap_cost, fx)


def plan_bar(
    state: CharacterState, content: Content, year: int, rng: random.Random, recent: list[str],
    queued: list[str],
) -> Plan:
    """Mood, and with `event_chance` a bar event drawn from `bar.event_categories`; maybe a partner."""
    cfg = content.balance.activities.bar
    fx: dict[str, int | bool] = {"mood": cfg.mood}
    if cfg.cost:
        fx["eddies"] = -cfg.cost
    plan = Plan(BAR, cfg.ap_cost, fx)
    if rng.random() < cfg.event_chance:
        skip = list(recent) + list(queued)
        got = engine_events.pick_events(
            state, content, year, rng, 1, recent=skip, categories=list(cfg.event_categories),
        )
        if got and got[0] not in queued:
            plan.bar_event = got[0]
    if not state.npcs_of("partner") and rng.random() < cfg.meet_partner_chance:
        fx["add_npc:partner"] = True
    return plan


def plan_braindance(state: CharacterState, content: Content, rng: random.Random) -> Plan:
    """+mood, +1 addiction. A held `no_braindance` vow breaks. At the threshold the addiction event
    is offered; at the overdose threshold each braindance risks death."""
    cfg = content.balance.activities.braindance
    before = int(state.flags.get(FLAG_ADDICTION, 0) or 0)
    after = before + cfg.addiction_gain
    fx: dict[str, int | bool] = {}
    plan = Plan(BRAINDANCE, cfg.ap_cost, fx, addiction=after)
    if after >= cfg.overdose_threshold and rng.random() < cfg.overdose_chance:
        plan.overdose = True
        fx["death:overdose"] = True
        return plan
    fx["mood"] = cfg.mood
    if cfg.cost:
        fx["eddies"] = -cfg.cost
    for vow in vows_against(state, content, "braindance"):
        fx[f"vow_break:{vow}"] = True
        plan.broken_vows.append(vow)
    plan.addiction_event = after >= cfg.addiction_event_threshold
    return plan


def plan_therapy(state: CharacterState, content: Content, rng: random.Random) -> Plan:
    """2d6 humanity (plus the `therapy_bonus` modifier); the ceiling is applied by apply_effects."""
    cfg = content.balance.activities.therapy
    n = max(0, roll(cfg.humanity_dice, rng) + derived.mod(state, content, "therapy_bonus"))
    fx: dict[str, int | bool] = {"humanity": n}
    if cfg.cost:
        fx["eddies"] = -cfg.cost
    return Plan(THERAPY, cfg.ap_cost, fx, humanity_roll=n)


def plan_meditation(state: CharacterState, content: Content, rng: random.Random) -> Plan:
    """A calm check (a clean body adds `check.meditation`). Pass: mood, humanity, lay low. Fail: a little mood."""
    cfg = content.balance.activities.meditation
    res = checks.roll_check(state, content, rng, cfg.check.stat, None, cfg.check.dv, ("meditation",))
    fx: dict[str, int | bool] = {}
    if cfg.cost:
        fx["eddies"] = -cfg.cost
    if res.ok:
        fx["mood"] = cfg.mood
        if cfg.humanity and not state.chrome_free:
            fx["humanity"] = cfg.humanity
        if cfg.heat_drop and state.heat > 0:
            fx["heat"] = -cfg.heat_drop
    else:
        fx["mood"] = cfg.fail_mood
    return Plan(MEDITATION, cfg.ap_cost, fx, check=res)


def plan_martial_arts(content: Content) -> Plan:
    cfg = content.balance.activities.martial_arts
    fx: dict[str, int | bool] = {f"skill_xp:{cfg.skill}": cfg.xp, "mood": cfg.mood}
    if cfg.cost:
        fx["eddies"] = -cfg.cost
    return Plan(MARTIAL_ARTS, cfg.ap_cost, fx, skill=cfg.skill)


def plan_service(content: Content) -> Plan:
    cfg = content.balance.activities.service
    fx: dict[str, int | bool] = {
        "eddies": cfg.eddies - cfg.cost, "fame": cfg.fame, "mood": cfg.mood, "rel_npc:random": cfg.rel,
    }
    return Plan(SERVICE, cfg.ap_cost, fx)


# -- gambling ---------------------------------------------------------------------------------------


def roll_gamble(state: CharacterState, content: Content, rng: random.Random, die: D10 | None = None) -> CheckResult:
    """The gamble check; `die` is the slot machine's face already mapped to the check die (None rolls here)."""
    cfg = content.balance.activities.gamble
    return checks.roll_check(state, content, rng, cfg.check_stat, None, cfg.check_dv, ("gamble",), die=die)


def gamble_payout(content: Content, stake: int, won: bool) -> int:
    """Eddies paid back when the stake (already taken) wins: stake x win_mult; 0 on a loss."""
    return round(stake * content.balance.activities.gamble.win_mult) if won else 0
