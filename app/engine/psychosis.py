"""Cyberpsychosis (PLAN 6.6-6.6b, DESIGN.md 7 and 7b): the breakdown fight, the MaxTac arrival, the recruit roll,
the isolation ward and the duty call. Pure: randomness only through the `rng` passed in.

The flows around it (flags, screens, the character's writes) are in services/psychosis.py.
"""

from __future__ import annotations

import math
import random

from app.content.loader import Content
from app.engine import combat as cb
from app.engine import derived
from app.engine.dice import clamp
from app.engine.state import (
    FLAG_CIVILIAN_KILLS,
    FLAG_FUGITIVE,
    FLAG_MAXTAC_CONTRACT,
    CharacterState,
)

SRC_BREAKDOWN = "psychosis"  # the episode itself: the NCPD, then MaxTac on tick 2
SRC_HUNT = "psyhunt"  # a MaxTac hunt of a fugitive psycho
SRC_DUTY = "duty"  # a mandatory call of a MaxTac member
CAPTURE_SRC = (SRC_BREAKDOWN, SRC_HUNT)  # a surrender in these fights is a capture, not the exits table
PSYCHO_SRC = (SRC_BREAKDOWN, SRC_HUNT, SRC_DUTY)

INHIBITOR = "maxtac_inhibitor"
CAREER = "maxtac"


def is_fugitive(state: CharacterState) -> bool:
    return bool(state.flags.get(FLAG_FUGITIVE))


def has_leash(state: CharacterState) -> bool:
    return state.has_cyberware(INHIBITOR)


# -- the breakdown fight ---------------------------------------------------------------------------------------


def breakdown_ctx(content: Content, year: int) -> dict[str, object]:
    """The context of the breakdown fight: the NCPD pack is built by `services/combat.begin` (faction as_faction),
    MaxTac joins on `cyberpsychosis.maxtac_arrival_tick`."""
    cfg = content.balance.cyberpsychosis
    return {
        "src": SRC_BREAKDOWN, "as_faction": "maxtac",
        "join": {"tick": cfg.maxtac_arrival_tick, "danger": content.balance.crime.maxtac_danger, "year": year, "done": False},
    }


def maybe_join(cs: cb.CombatState, content: Content, rng: random.Random) -> bool:
    """MaxTac lands on its tick: a pack of danger 5 joins the enemies. Called before a tick is played."""
    j = cs.ctx.get("join")
    if not j or j.get("done") or cs.tick < int(j["tick"]):
        return False
    j["done"] = True
    pack = cb.build_pack("maxtac", int(j["danger"]), rng, content, int(j["year"]))
    names = cb._numbered([e.name for e in pack])
    first = len(cs.enemies)
    for i, e in enumerate(pack):
        cs.enemies.append(cb.build_enemy(e, first + i, content, names[i]))
    cs.pack_size += len(pack)
    cb._emit(cs, k="join")
    return True


# -- recruitment ---------------------------------------------------------------------------------------------------


def recruit_chance(state: CharacterState, content: Content) -> float:
    """The chance that MaxTac offers a contract to a caught psycho: tolerance, the best Solo or Lawman rank,
    street cred, minus a penalty for the civilians killed. Clamped, so nobody is sure either way."""
    r = content.balance.cyberpsychosis.recruit
    ranks = derived.role_ranks(state, content)
    best = max((ranks.get(k, 0) for k in r.roles), default=0)
    civ = int(state.flags.get(FLAG_CIVILIAN_KILLS, 0) or 0)
    p = (r.base_chance + r.tolerance_bonus[state.cyber_tolerance] + best * r.role_rank_bonus
         + state.street_cred * r.street_cred_bonus_per_point - civ * r.civilian_kill_penalty)
    return max(r.min_chance, min(r.max_chance, p))


def roll_recruit(state: CharacterState, content: Content, rng: random.Random) -> bool:
    return rng.random() < recruit_chance(state, content)


def isolation_weeks(content: Content, rng: random.Random) -> int:
    r = content.balance.cyberpsychosis.isolation_weeks
    return rng.randint(r.min, r.max)


def strip_count(state: CharacterState, content: Content) -> int:
    """How many implants the ward cuts out: `chrome_stripped_pct` of them, rounded up, at least one (the leash stays)."""
    n = len([c for c in state.cyberware if c.key != INHIBITOR])
    if n == 0:
        return 0
    return max(1, math.ceil(n * content.balance.cyberpsychosis.chrome_stripped_pct))


def strip_picks(state: CharacterState, content: Content, rng: random.Random) -> list[str]:
    pool = [c.key for c in state.cyberware if c.key != INHIBITOR]
    rng.shuffle(pool)
    return pool[: strip_count(state, content)]


def contract_end(state: CharacterState, content: Content) -> int:
    """weeks_lived when the contract of a recruit signed now ends."""
    career = content.careers[CAREER]
    return state.weeks_lived + int(career.service_weeks or 0)


# -- the duty call -------------------------------------------------------------------------------------------------


def duty_due(state: CharacterState, due_at: object) -> bool:
    return due_at is not None and state.weeks_lived >= int(due_at) and state.career is not None and state.career.key == CAREER


def duty_danger(content: Content, rng: random.Random) -> int:
    r = content.balance.cyberpsychosis.duty_danger
    return clamp(rng.randint(r.min, r.max), 1, 5)


def contract_active(state: CharacterState) -> bool:
    return state.flags.get(FLAG_MAXTAC_CONTRACT) is not None


def player_as_foe(state: CharacterState, content: Content, idx: int = 0) -> cb.Combatant:
    """Another player's fugitive character as an enemy of a duty call (PLAN 6.6b): the same fighter the
    character would be in his own fight, turned to the enemy side. His own weapon, armor, implants and programs count."""
    c = cb.build_player(state, content)
    c.id = f"e{idx}"
    c.side = "enemy"
    c.name = f"{state.name} {state.surname}".strip()
    c.archetype = "player"
    c.ai = "netrunner" if c.quickhacks else "tactical"
    c.danger = content.balance.cyberpsychosis.duty_danger.max
    c.leader = True
    return c


def duty_eligible_target(state: CharacterState) -> bool:
    """A fugitive psycho who is free to be hunted: not in a cell, not already taken, not in the middle of a fight."""
    from app.engine.state import FLAG_COMBAT, FLAG_PENDING_COMBAT, FLAG_PSYCHO_CAPTURE

    f = state.flags
    return bool(f.get(FLAG_FUGITIVE)) and not (
        f.get(FLAG_PSYCHO_CAPTURE) or f.get(FLAG_COMBAT) or f.get(FLAG_PENDING_COMBAT) or f.get("jail_weeks"))
