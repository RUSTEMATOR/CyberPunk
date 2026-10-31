"""Prison (PLAN 9.5, DESIGN.md section 11): the arrest, the cell menu, the lawyer, the escape, the release.

Pure functions; numbers are `balance.jail`. The sentence is the flag `jail_weeks`. An arrest takes the
weapon and the armor off the character (they wait in `jail_stash`), the release gives them back.
"""

from __future__ import annotations

import random

from app.content.loader import Content
from app.engine import checks
from app.engine.checks import CheckResult
from app.engine.state import (
    FLAG_JAIL_ISOLATION,
    FLAG_JAIL_LAWYER,
    FLAG_JAIL_STASH,
    FLAG_JAIL_WEEKS,
    KIND_ARMOR,
    KIND_WEAPON,
    CharacterState,
    NpcState,
)

BLOCK_FREE = "free"
BLOCK_ISOLATION = "isolation"
BLOCK_LAWYER_DONE = "lawyer_done"
BLOCK_NO_CONTACT = "no_contact"
BLOCK_NO_MONEY = "no_money"
BLOCK_SHORT = "short"  # the sentence is too short to be worth a lawyer


def weeks_left(state: CharacterState) -> int:
    return int(state.flags.get(FLAG_JAIL_WEEKS, 0) or 0)


def in_jail(state: CharacterState) -> bool:
    return weeks_left(state) > 0


def isolated(state: CharacterState) -> bool:
    return bool(state.flags.get(FLAG_JAIL_ISOLATION))


def press_weeks(state: CharacterState, content: Content) -> int:
    """Weeks one press of "live a week" lives in jail: `weeks_per_press`, or what the sentence has left."""
    return max(1, min(content.balance.jail.weeks_per_press, weeks_left(state)))


# -- the arrest and the release ------------------------------------------------------------------------------


def take_gear(state: CharacterState) -> list[str]:
    """Take the equipped weapon and armor into the stash. Returns the item keys taken."""
    stash: dict[str, str] = dict(state.flags.get(FLAG_JAIL_STASH) or {})
    taken: list[str] = []
    for kind in (KIND_WEAPON, KIND_ARMOR):
        for it in state.inventory:
            if it.kind == kind and it.equipped:
                it.equipped = False
                stash[kind] = it.key
                taken.append(it.key)
    if stash:
        state.flags[FLAG_JAIL_STASH] = stash
    return taken


def give_gear_back(state: CharacterState) -> list[str]:
    """Equip what the stash holds (if the character still owns it and the slot is free). Returns the keys."""
    stash: dict[str, str] = dict(state.flags.pop(FLAG_JAIL_STASH, None) or {})
    back: list[str] = []
    for kind, key in stash.items():
        if state.equipped_key(kind) is not None:
            continue
        for it in state.inventory:
            if it.kind == kind and it.key == key:
                it.equipped = True
                back.append(key)
                break
    return back


def release(state: CharacterState, content: Content) -> list[str]:
    """The sentence is over (or the character got out): gear back, heat capped, the cell flags gone."""
    back = give_gear_back(state)
    state.flags.pop(FLAG_JAIL_WEEKS, None)
    state.flags.pop(FLAG_JAIL_LAWYER, None)
    state.flags.pop(FLAG_JAIL_ISOLATION, None)
    state.heat = min(state.heat, content.balance.jail.release_heat_cap)
    return back


# -- the lawyer ------------------------------------------------------------------------------------------------


def lawyer_contacts(state: CharacterState, content: Content) -> list[NpcState]:
    """Contacts and friends close enough to call a lawyer for you."""
    need = content.balance.jail.lawyer.min_relation
    return [n for n in state.npcs if n.alive and n.kind in ("contact", "friend") and n.relation >= need]


def lawyer_offer(state: CharacterState, content: Content) -> tuple[int, int] | None:
    """(weeks cut, price) of the lawyer for the rest of the sentence."""
    cfg = content.balance.jail.lawyer
    left = weeks_left(state)
    cut = max(cfg.min_cut, int(left * cfg.cut_pct))
    if cut >= left:
        cut = left - 1
    if cut < 1:
        return None
    return cut, cut * cfg.cost_per_week


def lawyer_blocked(state: CharacterState, content: Content) -> str | None:
    if not in_jail(state):
        return BLOCK_FREE
    if isolated(state):
        return BLOCK_ISOLATION
    if state.flags.get(FLAG_JAIL_LAWYER):
        return BLOCK_LAWYER_DONE
    if not lawyer_contacts(state, content):
        return BLOCK_NO_CONTACT
    offer = lawyer_offer(state, content)
    if offer is None:
        return BLOCK_SHORT
    if state.eddies < offer[1]:
        return BLOCK_NO_MONEY
    return None


# -- the escape ----------------------------------------------------------------------------------------------


def escape_blocked(state: CharacterState) -> str | None:
    if not in_jail(state):
        return BLOCK_FREE
    if isolated(state):
        return BLOCK_ISOLATION
    return None


def roll_escape(state: CharacterState, content: Content, rng: random.Random) -> CheckResult:
    ck = content.balance.jail.escape.check
    return checks.roll_check(state, content, rng, ck.stat, ck.skill, escape_dv(state, content), ("crime",))


def escape_dv(state: CharacterState, content: Content) -> int:
    """The escape dv, eased by the rank and the gang of the block (owner request 24.10)."""
    g = gang(state, content)
    return content.balance.jail.escape.check.dv - rank(state, content).escape - (g.escape if g else 0)


# -- the hierarchy and the gangs of the block (owner request 24.10) ------------------------------------------------

FLAG_RESPECT = "jail_respect"   # stays between terms: the block remembers
FLAG_GANG = "jail_gang"         # key of balance.jail.gangs


def respect(state: CharacterState) -> int:
    return int(state.flags.get(FLAG_RESPECT, 0) or 0)


def add_respect(state: CharacterState, n: int) -> int:
    state.flags[FLAG_RESPECT] = max(0, respect(state) + n)
    return respect(state)


def rank(state: CharacterState, content: Content):  # noqa: ANN201
    ranks = content.balance.jail.ranks
    r = respect(state)
    got = ranks[0]
    for x in ranks:
        if r >= x.min:
            got = x
    return got


def next_rank(state: CharacterState, content: Content):  # noqa: ANN201
    r = respect(state)
    return next((x for x in content.balance.jail.ranks if x.min > r), None)


def gang(state: CharacterState, content: Content):  # noqa: ANN201
    return content.balance.jail.gangs.get(str(state.flags.get(FLAG_GANG) or ""))


def job_pct(state: CharacterState, content: Content) -> int:
    g = gang(state, content)
    return rank(state, content).job_pct + (g.job_pct if g else 0)


def fight_respect(state: CharacterState, content: Content) -> int:
    g = gang(state, content)
    return content.balance.jail.respect_fight_win + rank(state, content).fight_respect + (g.fight_respect if g else 0)


def escape_heat(state: CharacterState, content: Content) -> int:
    """Heat to add so that an escapee has at least `success_heat`."""
    return max(0, content.balance.jail.escape.success_heat - state.heat)

