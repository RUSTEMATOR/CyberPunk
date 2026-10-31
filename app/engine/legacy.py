"""Death and legacy rules (PLAN 7.3, DESIGN.md 13): the main achievement of a life, the heirs and what they inherit.

Pure functions over states and the content. Numbers are `balance.legacy`; randomness only through `rng`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from app.content.loader import Content
from app.content.schemas import SKILLS
from app.engine import derived
from app.engine import relations as rel_engine
from app.engine.character_gen import FLAG_GENDER, CharacterDraft
from app.engine.dice import half_up
from app.engine.state import (
    FLAG_ENGRAM,
    FLAG_HEIR_OF,
    FLAG_SECURE_SOUL,
    CharacterState,
    DebtState,
    NpcState,
)

# Facts the achievement is picked from (keys of `legacy.achievement_scale`).
ACHIEVEMENT_NONE = "none"


def pick_achievement(content: Content, facts: dict[str, int]) -> tuple[str, int]:
    """(fact key, value) of the fact that stands out most: the biggest value / scale, ties by key.
    ("none", 0) when no fact reaches `legacy.achievement_min` of its scale."""
    cfg = content.balance.legacy
    best: tuple[float, str, int] | None = None
    for key, scale in sorted(cfg.achievement_scale.items()):
        value = int(facts.get(key, 0) or 0)
        score = value / scale if scale > 0 else 0.0
        if value > 0 and score >= cfg.achievement_min and (best is None or score > best[0]):
            best = (score, key, value)
    return (best[1], best[2]) if best else (ACHIEVEMENT_NONE, 0)


# -- the heirs (DESIGN.md 13) ------------------------------------------------------------------------------------------

OPT_RELATIVE = "relative"  # a child or a relative (the option); HEIR kinds below are what the heir is
OPT_PROTEGE = "protege"
OPT_ENGRAM = "engram"
OPT_SCRATCH = "scratch"
OPTIONS: tuple[str, ...] = (OPT_RELATIVE, OPT_PROTEGE, OPT_ENGRAM, OPT_SCRATCH)

KIND_CHILD = "child"
KIND_RELATIVE = "relative"
HEIRABLE_FAMILY_ROLES: tuple[str, ...] = ("sibling", "relative")  # family NPCs who can take the line on (parents cannot)


@dataclass(frozen=True)
class Candidate:
    """A living relative who can continue the line."""

    index: int  # position in the dead character's npcs
    kind: str  # child | relative
    role: str  # child | sibling | relative (for the label)
    name: str
    age: int  # the heir's age: the relative's age, at least legacy.heir_min_age
    relation: int


@dataclass(frozen=True)
class Identity:
    """Who the heir is, decided before the body is rolled."""

    name: str
    surname: str | None  # None = the family surname of the lineage
    age: int | None  # None = rolled as for a new character
    gender: str | None = None


@dataclass
class HeirSummary:
    """What the sheet of an heir shows (JSON-safe, kept in the draft)."""

    kind: str  # child | relative | protege | engram | scratch
    from_name: str = ""
    fixed_name: bool = False
    money: int = 0
    debt: int = 0
    home: str = ""
    street_cred: int = 0
    status: int = 0
    fame: int = 0
    skills: dict[str, int] = field(default_factory=dict)  # skill -> level the heir got from the past
    enemies: list[str] = field(default_factory=list)
    family: int = 0
    fixers: list[str] = field(default_factory=list)
    engram_penalty: int = 0
    engram_weeks: int = 0
    glitch_bonus: int = 0

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict) -> "HeirSummary":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def split_name(full: str) -> tuple[str, str]:
    """('Имя', 'Фамилия') from an NPC name; the surname may be empty."""
    first, _, rest = full.strip().partition(" ")
    return first, rest.strip()


def full_name(st: CharacterState) -> str:
    return f"{st.name} {st.surname}".strip()


def relative_candidates(dead: CharacterState, content: Content) -> list[Candidate]:
    """Children and relatives of the dead who are alive and not too old (`legacy.heir_max_age`).
    The heir is at least `legacy.heir_min_age` years old. Children first, then by the relation."""
    cfg = content.balance.legacy
    out: list[Candidate] = []
    for i, n in enumerate(dead.npcs):
        if not n.alive:
            continue
        if n.kind == "child":
            kind, role = KIND_CHILD, "child"
        elif n.kind == "family" and (n.role or "") in HEIRABLE_FAMILY_ROLES:
            kind, role = KIND_RELATIVE, n.role or "relative"
        else:
            continue
        age = rel_engine.age_of(dead, n, content)
        age = dead.age_years if age is None else age
        age = max(cfg.heir_min_age, age)
        if age > cfg.heir_max_age:
            continue
        out.append(Candidate(i, kind, role, n.name, age, n.relation))
    out.sort(key=lambda c: (c.kind != KIND_CHILD, -c.relation, c.index))
    return out


def protege_candidate(dead: CharacterState) -> tuple[int, NpcState] | None:
    for i, n in enumerate(dead.npcs):
        if n.kind == "protege" and n.alive:
            return i, n
    return None


def engram_paid(dead: CharacterState) -> bool:
    return bool(dead.flags.get(FLAG_SECURE_SOUL))


def scratch_bonus(content: Content, lives_ended: int) -> int:
    """Extra starting glitches for starting from scratch: per past life, capped (`legacy.scratch`)."""
    sc = content.balance.legacy.scratch
    return min(sc.glitch_bonus_cap, sc.glitch_bonus_per_life * max(0, lives_ended))


def identity(dead: CharacterState, content: Content, option: str, index: int | None) -> Identity | None:
    """The name, surname and age the heir is rolled with; None for the options that roll a stranger (scratch)."""
    cfg = content.balance.legacy
    if option == OPT_RELATIVE and index is not None:
        cand = next((c for c in relative_candidates(dead, content) if c.index == index), None)
        if cand is None:
            return None
        npc = dead.npcs[index]
        first, last = split_name(npc.name)
        return Identity(first, last or None, cand.age, str(npc.data.get("gender") or "") or None)
    if option == OPT_PROTEGE:
        found = protege_candidate(dead)
        if found is None:
            return None
        npc = found[1]
        first, last = split_name(npc.name)
        age = max(cfg.heir_min_age, int(npc.data.get("age") or cfg.heir_min_age))
        return Identity(first, last or None, age, str(npc.data.get("gender") or "") or None)
    if option == OPT_ENGRAM and engram_paid(dead):
        return Identity(dead.name, None, None, str(dead.flags.get(FLAG_GENDER) or "") or None)
    return None


# -- applying an option to a fresh draft ------------------------------------------------------------------------------------


def _set_age(st: CharacterState, age: int, year: int) -> None:
    st.age_years = age
    st.age_weeks = 0
    st.birth_year = year - age


def _reputation(st: CharacterState, dead: CharacterState, pct: float, summary: HeirSummary) -> None:
    for name in ("street_cred", "status", "fame"):
        value = max(getattr(st, name), half_up(getattr(dead, name) * pct))
        setattr(st, name, value)
        setattr(summary, name, value)


def _family_of_heir(
    dead: CharacterState, heir_index: int, heir_is_child: bool, content: Content, rng: random.Random,
) -> list[NpcState]:
    """The family of an heir, seen from the heir: the dead is a parent (or a sibling), the dead's partner, siblings and
    parents are re-labelled; relations are kept (`inherit.keep_family_rel`)."""
    ch = content.balance.character
    keep = content.balance.legacy.inherit.keep_family_rel

    def rel(n: NpcState) -> int:
        return n.relation if keep else rng.randint(ch.family_rel_min, ch.family_rel_max)

    gender = str(dead.flags.get(FLAG_GENDER) or "m")
    out = [NpcState(
        full_name(dead), "family", "parent" if heir_is_child else "sibling",
        content.balance.relations.start_rel.get("child", 80), False, {"gender": gender, "fate": "dead"},
    )]
    for i, n in enumerate(dead.npcs):
        if i == heir_index or not n.alive:
            continue
        role: str | None = None
        if heir_is_child:
            if n.kind == "partner":
                role = "parent"
            elif n.kind == "child":
                role = "sibling"
            elif n.kind == "family":
                role = "grandparent" if n.role == "parent" else "relative"
        else:
            if n.kind == "family":
                role = "parent" if n.role == "parent" else ("sibling" if n.role == "sibling" else "relative")
            elif n.kind in ("partner", "child"):
                role = "relative"
        if role is None:
            continue
        data = {k: v for k, v in n.data.items() if k in ("gender", "age", "district", "born_week")}
        out.append(NpcState(n.name, "family", role, rel(n), True, data))
    return out


def apply_relative(
    draft: CharacterDraft, dead: CharacterState, content: Content, rng: random.Random, year: int, index: int,
) -> HeirSummary | None:
    """A child or a relative takes over: money, home, a quarter of the debt, the family, a third of the reputation
    and the enemies in the gangs (`legacy.inherit`)."""
    cand = next((c for c in relative_candidates(dead, content) if c.index == index), None)
    if cand is None:
        return None
    inh = content.balance.legacy.inherit
    st = draft.state
    npc = dead.npcs[index]
    first, last = split_name(npc.name)
    st.name, st.surname = first, last or st.surname
    st.flags[FLAG_GENDER] = str(npc.data.get("gender") or st.flags.get(FLAG_GENDER) or "m")
    _set_age(st, cand.age, year)
    summary = HeirSummary(cand.kind, full_name(dead), True)
    summary.money = half_up(max(0, dead.eddies) * inh.money_pct)
    st.eddies += summary.money
    st.debts = [
        DebtState(d.creditor, share, d.weekly_rate, 0, d.creditor_kind, d.creditor_ref, d.key)
        for d in dead.debts if (share := half_up(d.principal * inh.debt_pct)) > 0
    ]
    summary.debt = sum(d.principal for d in st.debts)
    if inh.keep_housing and dead.housing:
        st.housing, st.district = dead.housing, dead.district
        summary.home = dead.housing
    # owner request 23.10: a family that lived well passes it on (a roguelike: the heir starts where the line got to)
    for key in ("lifestyle", "businesses", "business", "vehicle", "home"):
        if dead.flags.get(key):
            st.flags[key] = dead.flags[key]
    _reputation(st, dead, inh.street_cred_pct, summary)
    if inh.keep_gang_enemies:
        for key, value in dead.faction_rep.items():
            if key in content.gangs and value <= inh.enemy_rep_threshold:
                st.faction_rep[key] = min(st.faction_rep.get(key, 0), value)
                summary.enemies.append(key)
    if cand.kind == KIND_CHILD:  # owner request 23.10: how the child was raised shows in the heir
        rc = content.balance.relations
        for stat, pts in (npc.data.get("edu") or {}).items():
            bonus = min(rc.edu_cap, int(pts) // max(1, rc.edu_per_point))
            if bonus and stat in st.stats:
                st.stats[stat] = min(10, int(st.stats[stat]) + bonus)
    st.npcs = _family_of_heir(dead, index, cand.kind == KIND_CHILD, content, rng)
    summary.family = sum(1 for n in st.npcs if n.alive)
    st.flags[FLAG_HEIR_OF] = {"kind": cand.kind, "from": full_name(dead)}
    return summary


def apply_protege(
    draft: CharacterDraft, dead: CharacterState, content: Content, rng: random.Random, year: int,
) -> HeirSummary | None:
    """The student takes over: the fixers' trust, a fifth of the reputation, one skill two levels below the teacher's."""
    found = protege_candidate(dead)
    if found is None:
        return None
    pro = content.balance.legacy.protege
    npc = found[1]
    st = draft.state
    first, last = split_name(npc.name)
    st.name, st.surname = first, last or st.surname
    st.flags[FLAG_GENDER] = str(npc.data.get("gender") or st.flags.get(FLAG_GENDER) or "m")
    _set_age(st, max(content.balance.legacy.heir_min_age, int(npc.data.get("age") or 0)), year)
    summary = HeirSummary(OPT_PROTEGE, full_name(dead), True)
    _reputation(st, dead, pro.street_cred_pct, summary)
    if pro.keep_fixer_rel:
        for key, value in dead.fixer_rel.items():
            if key in content.fixers:
                st.fixer_rel[key] = max(st.fixer_rel.get(key, 0), value)
                summary.fixers.append(key)
    skill = str(npc.data.get("skill") or "")
    picks = [skill] if skill in SKILLS else []
    picks = (picks + sorted(SKILLS, key=lambda s: -dead.skill_level(s)))[: pro.skills_count]
    for s in picks:
        level = max(st.skills[s].level, dead.skill_level(s) - pro.skill_penalty)
        st.skills[s].level = level
        summary.skills[s] = level
    st.flags[FLAG_HEIR_OF] = {"kind": OPT_PROTEGE, "from": full_name(dead)}
    return summary


def apply_engram(
    draft: CharacterDraft, dead: CharacterState, content: Content, rng: random.Random, year: int,
) -> HeirSummary | None:
    """The paid engram wakes up in a new body: half of the skills, less humanity, a voice that argues."""
    if not engram_paid(dead):
        return None
    eg = content.balance.legacy.engram
    st = draft.state
    summary = HeirSummary(OPT_ENGRAM, full_name(dead), True)
    for s in SKILLS:
        level = int(dead.skill_level(s) * eg.skills_pct)
        if level > st.skills[s].level:
            st.skills[s].level = level
            summary.skills[s] = level
    st.humanity_lost += eg.humanity_penalty
    st.humanity = min(st.humanity, derived.humanity_max(st, content))
    summary.engram_penalty, summary.engram_weeks = eg.humanity_penalty, eg.conflict_event_every_weeks
    st.flags[FLAG_ENGRAM] = True
    st.flags[FLAG_HEIR_OF] = {"kind": OPT_ENGRAM, "from": full_name(dead)}
    return summary


def apply_scratch(draft: CharacterDraft, dead: CharacterState, content: Content, bonus: int) -> HeirSummary:
    draft.state.flags[FLAG_HEIR_OF] = {"kind": OPT_SCRATCH, "from": full_name(dead)}
    return HeirSummary(OPT_SCRATCH, full_name(dead), False, glitch_bonus=bonus)


def apply_option(
    draft: CharacterDraft, dead: CharacterState, content: Content, rng: random.Random, year: int,
    option: str, index: int | None, bonus: int,
) -> HeirSummary | None:
    """Put the chosen option on a rolled draft. None when the option does not hold any more (the relative is gone)."""
    if option == OPT_RELATIVE and index is not None:
        return apply_relative(draft, dead, content, rng, year, index)
    if option == OPT_PROTEGE:
        return apply_protege(draft, dead, content, rng, year)
    if option == OPT_ENGRAM:
        return apply_engram(draft, dead, content, rng, year)
    if option == OPT_SCRATCH:
        return apply_scratch(draft, dead, content, bonus)
    return None


# -- gifts of the dead life (owner request 24.10) ------------------------------------------------------------------------


def boon_offer(content: Content, rng: random.Random, achievement: str, cause: str, lives_ended: int) -> list[str]:
    """Keys of the gifts on offer: the ones that fit the achievement or the cause first, then any; weighted, no repeats."""
    cfg = content.balance.legacy
    n = cfg.boon_choices + (1 if lives_ended >= 3 else 0)
    fit: list[str] = []
    rest: list[str] = []
    for key, b in cfg.boons.items():
        if lives_ended < b.min_lives:
            continue
        targeted = bool(b.achievements or b.causes)
        if (achievement in b.achievements) or (cause in b.causes):
            fit.append(key)
        elif not targeted or b.min_lives:
            rest.append(key)
    out: list[str] = []
    for pool in (fit, rest):
        pool = list(pool)
        while pool and len(out) < n:
            weights = [max(1, cfg.boons[k].weight) for k in pool]
            k = rng.choices(pool, weights=weights)[0]
            pool.remove(k)
            out.append(k)
    return out


def apply_boon(draft: CharacterDraft, dead: CharacterState, content: Content, rng: random.Random, year: int,
               key: str) -> str | None:
    """Put the gift on the heir. Returns its name, None for an unknown key."""
    from app.engine.effects import apply_effects

    b = content.balance.legacy.boons.get(key)
    if b is None:
        return None
    st = draft.state
    for stat, n in b.stats.items():
        if stat in st.stats:
            st.stats[stat] = min(10, int(st.stats[stat]) + int(n))
    fx = dict(b.effects)
    if b.dead_eddies_pct:
        fx["eddies"] = int(fx.get("eddies", 0)) + max(0, dead.eddies) * b.dead_eddies_pct // 100
    if fx:
        apply_effects(st, fx, content, rng, year)
    return b.name
