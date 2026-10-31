"""Exits without a fight: pay, talk, flee, surrender (DESIGN.md section 6, PLAN 4.4 / 5.6).

Every exit is a check against a difficulty value, not a cancel button. The dv starts from the
faction's `disposition` (gangs.yaml / corps.yaml; MaxTac and cyberpsychos have theirs in
`balance.exits`), moves with the situation (faction reputation, street cred, fame and status for a
talk, a bigger bribe, a wound, a betrayed faction, an honest vow, `talk_bonus_if`), and is clamped
to the corridor in `balance.exits`. A dv of None means the exit is impossible.

M4 uses it for events that name a `faction`; M5.6 will use the same functions inside combat (there
`pack_half_lost` is known; here it is passed as False).
Pure: no clock, no DB, randomness only through the `rng`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from app.content.loader import Content
from app.content.schemas import Disposition
from app.engine import checks, derived
from app.engine.checks import CheckResult
from app.engine.dice import clamp, half_up, roll, weighted_choice
from app.engine.state import FLAG_BETRAYED_PREFIX, CharacterState

BRIBE, TALK, FLEE, SURRENDER = "bribe", "talk", "flee", "surrender"
EXIT_KINDS: tuple[str, ...] = (BRIBE, TALK, FLEE, SURRENDER)



@dataclass(frozen=True)
class ExitOption:
    kind: str
    possible: bool
    dv: int | None = None  # None for surrender and for impossible exits
    cost: int = 0  # eddies of a bribe
    step: int = 0  # bribe size step (0 = the base bribe)
    note: str = ""  # why it is impossible: "no_deal", "no_talk", "gate"


def disposition_for(content: Content, faction: str) -> Disposition | None:
    """Disposition of a gang, a corp, or the special `maxtac` / `cyberpsycho`; None for unknown keys."""
    if faction in content.gangs:
        return content.gangs[faction].disposition
    if faction in content.corps:
        return content.corps[faction].disposition
    if faction == "maxtac":
        return content.balance.exits.maxtac
    if faction == "cyberpsycho":
        return content.balance.exits.cyberpsycho
    return None


def _trunc_div(a: int, b: int) -> int:
    return int(a / b)  # toward zero, so -5 rep over 10 per dv is 0 and not -1


def _talk_bonus(state: CharacterState, content: Content, disp: Disposition) -> int:
    total = 0
    role = derived.main_role(state, content) or state.role
    for entry in disp.talk_bonus_if:
        if entry.districts and state.district not in entry.districts:
            continue
        if entry.origins and state.origin not in entry.origins:
            continue
        if entry.roles and role not in entry.roles:
            continue
        if entry.chrome == "chromed" and not state.cyberware:
            continue
        if entry.chrome == "clean" and state.cyberware:
            continue
        total += entry.bonus
    return min(total, content.balance.exits.corridor.talk_bonus_cap)


def _shift(
    state: CharacterState, content: Content, faction: str, danger: int, kind: str,
    disp: Disposition, *, pack_half_lost: bool, bribe_step: int,
) -> int:
    """Sum of the situational shifts of one exit, clamped to the corridor."""
    ex = content.balance.exits
    sit = ex.situation
    shift = 0
    if kind in (BRIBE, TALK):
        rep = clamp(state.faction_rep.get(faction, 0), sit.faction_rep.min, sit.faction_rep.max)
        shift -= clamp(_trunc_div(rep, sit.faction_rep.per_dv), -sit.faction_rep.dv_max, sit.faction_rep.dv_max)
        thr = sit.street_cred.threshold_by_danger[clamp(danger, 1, 5)]
        diff = _trunc_div(state.street_cred - thr, sit.street_cred.per_dv)
        shift -= clamp(diff, -sit.street_cred.dv_max, sit.street_cred.dv_max)
    if kind == TALK:
        shift -= min(sit.fame.dv_max, state.fame // sit.fame.per_dv)
        shift -= min(sit.status.dv_max, state.status // sit.status.per_dv)
        shift -= _talk_bonus(state, content, disp)
        if sit.honest_vow in state.vows:
            shift -= sit.honest_talk_bonus
    if kind == BRIBE:
        shift -= bribe_step * ex.bribe.dv_per_step
    per = {BRIBE: lambda a: a.bribe, TALK: lambda a: a.talk, FLEE: lambda a: a.flee}[kind]
    if pack_half_lost:
        shift += per(sit.pack_half_lost)
    if derived.heavy_wound(state, content):
        shift += per(sit.wounded)
    if state.flags.get(FLAG_BETRAYED_PREFIX + faction):
        shift += per(sit.betrayed)
    return clamp(shift, ex.corridor.situation_shift_min, ex.corridor.situation_shift_max)


def bribe_cost(content: Content, disp: Disposition, danger: int, step: int) -> int:
    ex = content.balance.exits.bribe
    base = ex.base_by_danger[clamp(danger, 1, 5)] * disp.bribe_cost_mult
    return half_up(base * (1 + ex.step_pct * clamp(step, 0, ex.max_steps)))


def build_exit(
    state: CharacterState, content: Content, faction: str, danger: int, kind: str,
    *, pack_half_lost: bool = False, bribe_step: int = 0, knows_target: bool = False,
) -> ExitOption:
    """One exit with its final dv (and cost for a bribe) against `faction`."""
    disp = disposition_for(content, faction)
    if disp is None or kind == SURRENDER:
        return ExitOption(SURRENDER, True)
    corr = content.balance.exits.corridor
    if kind == BRIBE:
        if disp.bribe_dv is None:
            return ExitOption(BRIBE, False, note="no_deal")
        step = clamp(bribe_step, 0, content.balance.exits.bribe.max_steps)
        dv = disp.bribe_dv + _shift(state, content, faction, danger, kind, disp, pack_half_lost=pack_half_lost, bribe_step=step)
        return ExitOption(BRIBE, True, clamp(dv, corr.dv_min, corr.dv_max), bribe_cost(content, disp, danger, step), step)
    if kind == TALK:
        if disp.talk_dv is None:
            return ExitOption(TALK, False, note="no_talk")
        gate = disp.talk_gate
        if gate is not None:
            role = derived.main_role(state, content) or state.role
            if not (role in gate.roles or (gate.knows_target and knows_target)):
                return ExitOption(TALK, False, note="gate")
        dv = disp.talk_dv + _shift(state, content, faction, danger, kind, disp, pack_half_lost=pack_half_lost, bribe_step=0)
        return ExitOption(TALK, True, clamp(dv, corr.dv_min, corr.dv_max))
    cb = content.balance.combat
    dv = cb.flee_dv_base + cb.flee_dv_per_danger * danger + disp.flee_mod
    dv += _shift(state, content, faction, danger, FLEE, disp, pack_half_lost=pack_half_lost, bribe_step=0)
    return ExitOption(FLEE, True, clamp(dv, corr.dv_min, corr.dv_max))


def build_exits(
    state: CharacterState, content: Content, faction: str, danger: int,
    *, pack_half_lost: bool = False, knows_target: bool = False,
) -> list[ExitOption]:
    """Bribe (base and double size), talk, flee, surrender for a fight against `faction`."""
    ex = content.balance.exits.bribe
    kw = {"pack_half_lost": pack_half_lost, "knows_target": knows_target}
    out = [build_exit(state, content, faction, danger, BRIBE, bribe_step=0, **kw)]
    if out[0].possible:
        out.append(build_exit(state, content, faction, danger, BRIBE, bribe_step=ex.max_steps // 2, **kw))
    out.append(build_exit(state, content, faction, danger, TALK, **kw))
    out.append(build_exit(state, content, faction, danger, FLEE, **kw))
    out.append(ExitOption(SURRENDER, True))
    return out


def roll_exit(state: CharacterState, content: Content, rng: random.Random, option: ExitOption) -> CheckResult:
    """Roll the check of a possible exit (pay, talk, flee). Surrender has no check."""
    if not option.possible or option.dv is None:
        raise ValueError(f"exit {option.kind} has no check")
    if option.kind == BRIBE:
        return checks.roll_check(state, content, rng, "COOL", "persuasion", option.dv, ("bribe", "social"))
    if option.kind == TALK:
        return checks.roll_check(state, content, rng, "COOL", "persuasion", option.dv, ("talk_exit", "social"))
    extra = derived.mod(state, content, "flee")
    return checks.roll_check(state, content, rng, "REF", "stealth", option.dv, ("escape",), extra=extra)


# -- failures and surrender -----------------------------------------------------------------------


def bribe_taken_anyway(content: Content, rng: random.Random) -> bool:
    """A failed bribe: the money is taken anyway with this chance, and they still attack."""
    return rng.random() < content.balance.exits.bribe.fail_taken_chance


@dataclass
class SurrenderResult:
    outcome: str  # one of schemas.SURRENDER_OUTCOMES
    effects: dict[str, int | bool] = field(default_factory=dict)  # for apply_effects
    strip: list[str] = field(default_factory=list)  # implant keys to cut out (apply_remove_cyberware)
    note: str = ""


def surrender(state: CharacterState, content: Content, rng: random.Random, faction: str) -> SurrenderResult:
    """Roll the outcome of a surrender by the faction's weights and turn it into effects.

    vanished: a beating and `vanished_weeks` weeks gone (the `vanish_weeks` effect, services/jail.py). chrome_stripped takes only the
    slots in `strip_slots`; with nothing to take it is a beating too.
    """
    disp = disposition_for(content, faction)
    if disp is None:
        return SurrenderResult("released")
    weights = {k: v for k, v in disp.surrender.model_dump().items() if v > 0}
    outcome = weighted_choice(rng, list(weights), list(weights.values()))
    cb, ex = content.balance.combat, content.balance.exits.surrender
    beat = {"hp": -roll(cb.surrender_gang_hp_dice, rng)}
    if outcome == "released":
        return SurrenderResult(outcome)
    if outcome == "robbed":
        return SurrenderResult(outcome, {"eddies": -half_up(state.eddies * cb.surrender_gang_eddies_pct)})
    if outcome == "beaten":
        return SurrenderResult(outcome, beat)
    if outcome == "chrome_stripped":
        pool = [
            c.key for c in state.cyberware
            if not disp.strip_slots or content.cyberware[c.key].slot in disp.strip_slots
        ]
        rng.shuffle(pool)
        strip = pool[: ex.chrome_strip_count]
        if not strip:
            return SurrenderResult("beaten", beat, note="nothing to strip")
        return SurrenderResult(outcome, {}, strip)
    if outcome == "arrested":
        from app.engine import crime  # local: crime imports checks, which exits is built on

        return SurrenderResult(outcome, {"jail_weeks": crime.arrest_weeks(state, content, rng, faction)})
    if outcome == "captured":
        return SurrenderResult(outcome, {"eddies": -half_up(state.eddies * ex.captured_ransom_pct)})
    if outcome == "executed":
        return SurrenderResult(outcome, {f"death:{'maxtac' if faction == 'maxtac' else 'combat'}": True})
    return SurrenderResult("vanished", {**beat, "vanish_weeks": ex.vanished_weeks})
