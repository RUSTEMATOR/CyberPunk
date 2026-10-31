"""Random events of the week (PLAN 4.4, DESIGN.md sections 5a, 12).

Pure functions: which events fit a character in a world year, how many fall in a week, which
ones, and how a choice resolves. Effects are applied by the caller through `apply_effects`.

Selection: `content.available("events", year)` (rule 12), then the conditions of the event
(district, main role, role rank, career, heat, humanity, street cred, mood and the flag lists of
`derived.event_flags`), then a weighted draw. Weight = `weight` x district match x role match x the
world's per-category multiplier. The last `events.recent_memory` shown events are skipped while
anything else fits.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from app.content.loader import Content
from app.content.schemas import ChoiceOutcome, Event, EventChoice
from app.engine import checks, derived
from app.engine.checks import CheckResult
from app.engine.dice import weighted_choice
from app.engine.state import CharacterState

# Mod contexts a skill brings into a check (derived.check_bonus reads `check.<context>` mods).
_SKILL_CONTEXTS: dict[str, str] = {
    "persuasion": "social", "medicine": "heal", "stealth": "escape", "firearms": "combat", "melee": "combat",
}


def main_role_key(state: CharacterState, content: Content) -> str:
    """The role events react to: the earned main role, else the one stored on the character."""
    return derived.main_role(state, content) or state.role


def matches(
    event: Event, state: CharacterState, content: Content, flags: set[str] | None = None
) -> bool:
    """Do the event's conditions hold for the character now?"""
    if getattr(event, "id", None) in content.balance.pressure.racket_events:  # owner request 25.10: a krysha keeps the racket away (actions have no id)
        from app.engine import pressure as pr_eng

        if pr_eng.krysha(state, content)[1] is not None:
            return False
    c = event.conditions
    flags = derived.event_flags(state, content) if flags is None else flags
    if c.districts is not None and state.district not in c.districts:
        return False
    if c.roles is not None and main_role_key(state, content) not in c.roles:
        return False
    if c.min_role_rank:
        ranks = derived.role_ranks(state, content)
        if any(ranks.get(role, 0) < need for role, need in c.min_role_rank.items()):
            return False
    if c.careers is not None and (state.career is None or state.career.key not in c.careers):
        return False
    if c.min_heat is not None and state.heat < c.min_heat:
        return False
    if c.max_heat is not None and state.heat > c.max_heat:
        return False
    if c.max_humanity is not None and state.humanity > c.max_humanity:
        return False
    if c.min_street_cred is not None and state.street_cred < c.min_street_cred:
        return False
    if c.min_status is not None and state.status < c.min_status:
        return False
    if c.min_fame is not None and state.fame < c.min_fame:
        return False
    if c.max_mood is not None and state.mood > c.max_mood:
        return False
    if any(f not in flags for f in c.requires):
        return False
    if any(f in flags for f in c.forbids):
        return False
    if any(state.has_cyberware(k) for k in c.lacks_cyberware):
        return False
    if c.abilities:  # owner request 26.10: chrome abilities
        from app.engine import abilities

        have = abilities.owned(state, content)
        if any(abilities.mine_for(state, content, k, have) is None for k in c.abilities):
            return False
    if c.employers is not None and state.flags.get("employer") not in c.employers:
        return False
    if c.in_gang is not None and bool(state.flags.get("gang")) != c.in_gang:
        return False
    if c.has_business is not None and bool(state.flags.get("business")) != c.has_business:
        return False
    if c.seasons is not None or c.weather is not None:  # owner request: seasonal and weather events
        from app.engine import weather

        if c.seasons is not None:
            s = weather.season(state, content)
            if s is None or s.key not in c.seasons:
                return False
        if c.weather is not None and state.flags.get(weather.FLAG) not in c.weather:
            return False
    if any(not state.flags.get(f) for f in c.flags_all):
        return False
    if any(state.flags.get(f) for f in c.flags_none):
        return False
    return True


def eligible(
    state: CharacterState, content: Content, year: int, extra_flags: set[str] | None = None,
) -> dict[str, Event]:
    """Events of this world year whose conditions hold (rule 12: through content.available).
    `extra_flags` are added to the character's flags (the press that releases still draws prison events)."""
    flags = derived.event_flags(state, content) | (extra_flags or set())
    return {
        eid: ev for eid, ev in content.available("events", year).items()
        if not ev.forced_only and ev.business is None and matches(ev, state, content, flags)
    }


def weight(
    event: Event, state: CharacterState, content: Content, category_mult: dict[str, float] | None = None
) -> float:
    w = content.balance.events.weights
    value = float(event.weight)
    c = event.conditions
    if c.districts and state.district in c.districts:
        value *= w.district_match_mult
    if c.roles and main_role_key(state, content) in c.roles:
        value *= w.role_match_mult
    if category_mult:
        value *= category_mult.get(event.category, 1.0)
    return value


def roll_event_count(rng: random.Random, content: Content) -> int:
    """1 guaranteed, then the extra chances in order."""
    ev = content.balance.events
    n = ev.guaranteed
    for chance in ev.extra_chances:
        if rng.random() < chance:
            n += 1
        else:
            break
    return n


def pick_events(
    state: CharacterState, content: Content, year: int, rng: random.Random, count: int,
    *, forced: list[str] | None = None, recent: list[str] | None = None,
    category_mult: dict[str, float] | None = None, categories: list[str] | None = None,
    only_requires: str | None = None, extra_flags: set[str] | None = None,
) -> list[str]:
    """Event ids for the week: `forced` ones first (they count toward `count`), then weighted draws.

    No event twice in a week. With `categories` only those categories are drawn from (bar events);
    with `only_requires` only events that require that flag (the prison events: `in_jail`).
    """
    chosen: list[str] = []
    avail = content.available("events", year)
    for eid in forced or []:
        if eid in avail and eid not in chosen:
            chosen.append(eid)
    pool = {
        eid: ev for eid, ev in eligible(state, content, year, extra_flags).items()
        if (categories is None or ev.category in categories)
        and (only_requires is None or only_requires in ev.conditions.requires)
    }
    skip = set(recent or [])
    while len(chosen) < count:
        candidates = [eid for eid in pool if eid not in chosen]
        fresh = [eid for eid in candidates if eid not in skip]
        use = fresh or candidates
        if not use:
            break
        weights = [weight(pool[eid], state, content, category_mult) for eid in use]
        chosen.append(weighted_choice(rng, use, weights))
    return chosen


# -- choices ----------------------------------------------------------------------------------


def contexts_for(choice: EventChoice) -> tuple[str, ...]:
    """Mod contexts of a choice's check: the skill's context, plus 'combat' for a violent choice."""
    out: list[str] = []
    if choice.check is not None and choice.check.skill in _SKILL_CONTEXTS:
        out.append(_SKILL_CONTEXTS[choice.check.skill])
    if choice.is_violent and "combat" not in out:
        out.append("combat")
    return tuple(out)


def roll_choice(
    state: CharacterState, content: Content, choice: EventChoice, rng: random.Random
) -> CheckResult | None:
    """Roll the check of a choice (None when the choice has none). Nothing is spent or applied."""
    if choice.check is None:
        return None
    ck = choice.check
    return checks.roll_check(
        state, content, rng, ck.stat, ck.skill, ck.dv, contexts_for(choice),
    )


def outcome_for(choice: EventChoice, ok: bool | None) -> ChoiceOutcome:
    """The text and effects that follow: success/fail of a checked choice, or the plain outcome."""
    if choice.check is None:
        assert choice.text is not None and choice.effects is not None
        return ChoiceOutcome(text=choice.text, effects=choice.effects)
    out = choice.success if ok else choice.fail
    assert out is not None
    return out


def is_nonviolent(choice: EventChoice, combat_started: bool) -> bool:
    return not choice.is_violent and not combat_started


@dataclass(frozen=True)
class LuckOffer:
    points: int  # luck points that turn the failed check into a pass
    pool: int


def luck_offer(result: CheckResult, state: CharacterState, content: Content) -> LuckOffer | None:
    """'Luck +N' is offered only when the pool can cover the miss (PLAN 5.4 rule, used here too)."""
    if result.ok:
        return None
    need = checks.luck_points_to_pass(result, state.luck_pool, content.balance.luck)
    return LuckOffer(need, state.luck_pool) if need else None
