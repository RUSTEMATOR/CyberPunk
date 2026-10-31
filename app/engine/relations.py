"""Relations (PLAN 9.1-9.2, DESIGN.md section 10): what each action does to an NPC and to the character.

Pure functions. An action is planned here as a `Plan` (AP, effects for `apply_effects`, the change of
the NPC's relation, the data to write on the NPC); the service applies it. Numbers are
`balance.relations`. Randomness only through `rng`.

Kinds of NPC: family, partner, friend, contact, child, protege. Fixers live in `fixer_rel` and have
one action, the gift (`fixer_gift`).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from app.content.loader import Content
from app.engine.state import FLAG_JAIL_WEEKS, FLAG_PARTNER_MOVED_IN, CharacterState, NpcState

HANG, ASK, QUARREL, GIFT, INTRO = "hang", "ask", "quarrel", "gift", "intro"
BREAK, MOVE_IN, MOVE_OUT, TEACH = "break", "movein", "moveout", "teach"
# owner request 23.10: the family
DATE, NIGHT, PROPOSE = "date", "night", "propose"
BABY = "baby"  # owner request 25.10: a child with a partner, by choice
RAISE_MIND, RAISE_BODY, RAISE_STREET, SCHOOL = "rmind", "rbody", "rstreet", "school"
RAISE_STAT = {RAISE_MIND: "INT", RAISE_BODY: "BODY", RAISE_STREET: "COOL", SCHOOL: "INT"}
FLAG_MARRIED = "married"

ACTIONS_BY_KIND: dict[str, tuple[str, ...]] = {
    "family": (HANG, ASK, QUARREL, GIFT, INTRO),
    "partner": (DATE, NIGHT, BABY, HANG, GIFT, PROPOSE, MOVE_IN, ASK, QUARREL, INTRO, BREAK),  # MOVE_IN turns into MOVE_OUT
    "friend": (HANG, ASK, QUARREL, GIFT, INTRO, BREAK),
    "contact": (HANG, GIFT, INTRO, BREAK),
    "child": (HANG, RAISE_MIND, RAISE_BODY, RAISE_STREET, SCHOOL, GIFT),
    "protege": (TEACH, GIFT, BREAK),
}
JAIL_ACTIONS: frozenset[str] = frozenset({HANG, ASK})  # a cell allows visits and a call for money

# Bus event of a hang-out by the kind of NPC.
HANG_BUS: dict[str, str] = {
    "family": "family_time", "partner": "family_time", "child": "family_time", "protege": "family_time",
    "friend": "social_time", "contact": "social_time",
}

# Why an action cannot be done (the UI turns them into lines).
BLOCK_JAIL = "jail"
BLOCK_EDDIES = "eddies"
BLOCK_REL_LOW = "rel_low"
BLOCK_COOLDOWN = "cooldown"
BLOCK_NO_HOME = "no_home"
BLOCK_NO_OTHER = "no_other"
BLOCK_DEAD = "dead"
BLOCK_ALREADY = "already"
BLOCK_KIDS_MAX = "kids_max"
BLOCK_BABY_SOON = "baby_soon"


def youngest_weeks(state: CharacterState) -> int | None:
    """Lived weeks since the youngest child came, or None without children."""
    born = [int(n.data.get("born_week")) for n in state.npcs_of("child") if n.data.get("born_week") is not None]
    return state.weeks_lived - max(born) if born else None


def baby_blocked(state: CharacterState, content: Content, cost: int) -> str | None:
    """Owner request 25.10: what stops a new child (a partner's, an adopted or a child with another player)."""
    cfg = content.balance.relations
    if in_jail(state):
        return BLOCK_JAIL
    if len(state.npcs_of("child")) >= cfg.child_max:
        return BLOCK_KIDS_MAX
    w = youngest_weeks(state)
    if w is not None and w < cfg.baby_gap_weeks:
        return BLOCK_BABY_SOON
    if state.eddies < cost:
        return BLOCK_EDDIES
    return None


@dataclass
class Plan:
    action: str
    ap: int
    effects: dict[str, int | bool] = field(default_factory=dict)  # for apply_effects (eddies, mood)
    rel: int = 0  # change of the NPC's relation
    other_rel: int = 0  # change of the second NPC's relation (introduce)
    money: int = 0  # eddies received (ask)
    data: dict[str, Any] = field(default_factory=dict)  # written to npc.data
    remove: bool = False  # the NPC leaves the list
    moved_in: bool | None = None  # new value of the flag partner_moved_in
    enemy: bool = False  # the quarrel made an enemy
    bus: str | None = None


def in_jail(state: CharacterState) -> bool:
    return int(state.flags.get(FLAG_JAIL_WEEKS, 0) or 0) > 0


def partner_lives_with(state: CharacterState) -> bool:
    return bool(state.flags.get(FLAG_PARTNER_MOVED_IN)) and bool(state.npcs_of("partner"))


def actions_for(state: CharacterState, npc: NpcState) -> list[str]:
    """The actions of this NPC's card, in order (move in / move out depends on the household)."""
    out: list[str] = []
    for a in ACTIONS_BY_KIND.get(npc.kind, ()):
        if a == MOVE_IN and state.flags.get(FLAG_PARTNER_MOVED_IN):
            a = MOVE_OUT
        if a == PROPOSE and npc.data.get("married"):
            continue
        out.append(a)
    return out


def edu(npc: NpcState) -> dict[str, int]:
    """Upbringing points of a child by stat (owner request 23.10: the heir takes them)."""
    return {k: int(v) for k, v in (npc.data.get("edu") or {}).items()}


def introduce_candidates(state: CharacterState, npc: NpcState) -> list[int]:
    """Indexes of the living NPCs `npc` can be introduced to (everyone else, children and proteges aside)."""
    return [
        i for i, n in enumerate(state.npcs)
        if n is not npc and n.alive and n.kind in ("family", "partner", "friend", "contact")
    ]


def blocked(
    state: CharacterState, content: Content, action: str, npc: NpcState, *, other: NpcState | None = None,
) -> str | None:
    """Why `action` cannot start now, or None. AP is checked by the service."""
    cfg = content.balance.relations
    if not npc.alive:
        return BLOCK_DEAD
    if in_jail(state) and action not in JAIL_ACTIONS:
        return BLOCK_JAIL
    if action == GIFT and state.eddies < cfg.gift_cost:
        return BLOCK_EDDIES
    if action == DATE and state.eddies < cfg.date_cost:
        return BLOCK_EDDIES
    if action == SCHOOL and state.eddies < cfg.school_cost:
        return BLOCK_EDDIES
    if action == PROPOSE:
        if npc.relation < cfg.propose_min_rel:
            return BLOCK_REL_LOW
        if state.eddies < cfg.wedding_cost:
            return BLOCK_EDDIES
        if npc.data.get("married"):
            return BLOCK_ALREADY
    if action == BABY:
        if npc.relation < cfg.baby_min_rel:
            return BLOCK_REL_LOW
        return baby_blocked(state, content, cfg.baby_cost)
    if action == NIGHT:
        if npc.relation < cfg.night_min_rel:
            return BLOCK_REL_LOW
        if npc.data.get("night_press") == state.weeks_lived:
            return BLOCK_COOLDOWN
    if action in (RAISE_MIND, RAISE_BODY, RAISE_STREET, SCHOOL) and npc.data.get("raised_press") == state.weeks_lived:
        return BLOCK_COOLDOWN
    if action == ASK:
        if npc.relation < cfg.ask_money_min_rel:
            return BLOCK_REL_LOW
        asked = npc.data.get("asked_week")
        if asked is not None and state.weeks_lived - int(asked) < cfg.ask_money_cooldown_weeks:
            return BLOCK_COOLDOWN
    if action == MOVE_IN:
        if npc.relation < cfg.move_in_min_rel:
            return BLOCK_REL_LOW
        if state.housing not in content.housing:
            return BLOCK_NO_HOME
        if state.flags.get(FLAG_PARTNER_MOVED_IN):
            return BLOCK_ALREADY
    if action == INTRO and not introduce_candidates(state, npc):
        return BLOCK_NO_OTHER
    return None


def plan(
    state: CharacterState, content: Content, rng: random.Random, action: str, npc: NpcState,
) -> Plan:
    """What the action does. The caller has checked `blocked`."""
    cfg = content.balance.relations
    p = Plan(action, cfg.action_ap)
    if action == HANG:
        p.rel, p.effects["mood"] = cfg.hang_out_rel, cfg.hang_out_mood
        p.bus = HANG_BUS.get(npc.kind)
    elif action == ASK:
        p.money = rng.randint(cfg.ask_money_min, cfg.ask_money_max)
        p.effects["eddies"] = p.money
        p.rel = cfg.ask_money_rel
        p.data["asked_week"] = state.weeks_lived
    elif action == QUARREL:
        p.rel, p.effects["mood"] = cfg.quarrel_rel, cfg.quarrel_mood
        p.enemy = npc.relation + cfg.quarrel_rel <= cfg.enemy_rel
        if p.enemy:
            p.data["enemy"] = True
    elif action == GIFT:
        p.effects["eddies"], p.effects["mood"] = -cfg.gift_cost, cfg.gift_mood
        p.rel = cfg.gift_rel
    elif action == INTRO:
        p.rel = p.other_rel = cfg.introduce_rel
        p.bus = "social_time"
    elif action == BREAK:
        p.remove = True
        p.effects["mood"] = cfg.break_up_mood if npc.kind == "partner" else (
            cfg.break_other_mood if npc.kind in ("friend", "contact") else 0)
        if npc.kind == "partner":
            p.moved_in = False
            if npc.data.get("married"):  # owner request 23.10: a divorce costs
                p.effects["eddies_pct"] = -cfg.divorce_pct
                p.effects[f"flag_clear:{FLAG_MARRIED}"] = True
    elif action == MOVE_IN:
        p.moved_in = True
        p.effects["mood"] = cfg.move_in_mood
    elif action == MOVE_OUT:
        p.moved_in = False
        p.rel = cfg.move_out_rel
    elif action == DATE:
        p.effects["eddies"], p.effects["mood"] = -cfg.date_cost, cfg.date_mood
        p.rel = cfg.date_rel
        p.bus = "family_time"
    elif action == NIGHT:
        p.effects["mood"] = cfg.night_mood
        p.rel = cfg.night_rel
        p.data["night_press"] = state.weeks_lived
    elif action == BABY:
        p.effects.update({"add_npc:child": True, "eddies": -cfg.baby_cost, "mood": cfg.baby_mood})
        p.rel = cfg.baby_rel
        p.bus = "family_time"
    elif action == PROPOSE:
        p.effects["eddies"], p.effects["mood"] = -cfg.wedding_cost, cfg.wedding_mood
        p.effects[f"flag_set:{FLAG_MARRIED}"] = True
        p.data["married"] = True
        p.rel = 10
        p.bus = "family_time"
    elif action in (RAISE_MIND, RAISE_BODY, RAISE_STREET, SCHOOL):
        stat = RAISE_STAT[action]
        e = edu(npc)
        e[stat] = e.get(stat, 0) + (3 if action == SCHOOL else 1)
        p.data["edu"] = e
        p.data["raised_press"] = state.weeks_lived
        p.rel = cfg.raise_rel
        p.effects["mood"] = cfg.raise_mood
        if action == SCHOOL:
            p.effects["eddies"] = -cfg.school_cost
        p.bus = "family_time"
    elif action == TEACH:
        p.rel, p.effects["mood"] = cfg.protege_lesson_rel, cfg.protege_lesson_mood
        p.data["lessons"] = int(npc.data.get("lessons", 0) or 0) + 1
        p.bus = "family_time"
    else:
        raise ValueError(f"unknown relation action {action!r}")
    return p


def age_of(state: CharacterState, npc: NpcState, content: Content) -> int | None:
    """Age in years of a child (by `born_week`) or a protege / family member (stored `age`)."""
    born = npc.data.get("born_week")
    if born is not None:
        return max(0, (state.weeks_lived - int(born)) // content.balance.time.weeks_per_year)
    age = npc.data.get("age")
    return int(age) if age is not None else None
