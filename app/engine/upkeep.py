"""Weekly upkeep of a life (owner request, docs/PLAN_ECONOMY.md): immunosuppressants for the chrome, the lifestyle,
the staff, and the drugs with their habit. Pure: no DB, randomness through `rng`, changes through `apply_effects`.

Character flags:
- `immuno` = grade key the character takes (None = none), `immuno_black` = has the black market supplier,
  `immuno_miss` = weeks in a row without enough suppressants;
- `cw_mk` = {implant key: Mk step 1..2} (Mk2, Mk3);
- `lifestyle` = level key, `lifestyle_weeks` = weeks at it, `lifestyle_down` = {weeks, mood} after a drop;
- `staff` = [member keys];
- `drug_dose` = [drug keys taken since the last press], `habit` = {drug key: points}.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from math import ceil
from typing import Any, Iterator, Mapping

from app.content.loader import Content
from app.content.schemas import DrugSpec, ImmunoGrade, LifestyleLevel, StaffMember
from app.engine.effects import AppliedEffects, apply_effects
from app.engine.state import CharacterState

FLAG_IMMUNO = "immuno"
FLAG_IMMUNO_BLACK = "immuno_black"
FLAG_IMMUNO_MISS = "immuno_miss"
FLAG_MK = "cw_mk"
FLAG_LIFESTYLE = "lifestyle"
FLAG_LIFESTYLE_WEEKS = "lifestyle_weeks"
FLAG_LIFESTYLE_DOWN = "lifestyle_down"
FLAG_STAFF = "staff"
FLAG_DOSE = "drug_dose"
FLAG_HABIT = "habit"


# -- immunosuppressants -------------------------------------------------------------------------------------------------


def mk_of(state: CharacterState, key: str) -> int:
    return int((state.flags.get(FLAG_MK) or {}).get(key, 0) or 0)


def chrome_load(state: CharacterState, content: Content) -> int:
    """The load the chrome puts on the body: the humanity cost of every installed implant, Mk steps add a share."""
    pct = content.balance.economy.immuno.mk_load_pct
    total = 0
    for item in state.cyberware:
        cw = content.cyberware.get(item.key)
        if cw is None:
            continue
        total += cw.humanity_cost * (100 + pct * mk_of(state, item.key)) // 100
    from app.engine import derived  # owner request 23.10: a strong body carries the chrome more easily

    pct_less = max(-90, min(0, -derived.mod(state, content, "chrome_load_pct")))  # owner request 24.10: chrome resistance
    return int(round(total * derived.body_factor(state, content) * (100 + pct_less) / 100))


def grades(content: Content) -> list[ImmunoGrade]:
    return sorted(content.balance.economy.immuno.grades, key=lambda g: g.strength)


def grade(content: Content, key: str | None) -> ImmunoGrade | None:
    return next((g for g in content.balance.economy.immuno.grades if g.key == key), None)


def needed_grade(state: CharacterState, content: Content) -> ImmunoGrade | None:
    """The weakest grade that holds the chrome; None = the body copes alone (or the tolerance needs nothing)."""
    if tolerance_mult(state, content) <= 0:
        return None
    load = chrome_load(state, content)
    need = None
    for g in grades(content):
        if load >= g.from_load:
            need = g
    return need


def tolerance_mult(state: CharacterState, content: Content) -> float:
    return float(content.balance.economy.immuno.tolerance_mult.get(state.cyber_tolerance, 1.0))


def immuno_price(state: CharacterState, content: Content, g: ImmunoGrade) -> int:
    from app.engine import derived

    pct = derived.mod(state, content, "immuno_cost_pct")
    base = chrome_load(state, content) * g.price_per_point * tolerance_mult(state, content)
    return max(0, ceil(base * max(0, 100 + pct) / 100))


def can_take(state: CharacterState, g: ImmunoGrade) -> bool:
    return not g.black_market or bool(state.flags.get(FLAG_IMMUNO_BLACK))


# -- lifestyle and staff -------------------------------------------------------------------------------------------------


def levels(content: Content) -> list[LifestyleLevel]:
    return list(content.balance.economy.lifestyle.levels)


def lifestyle(state: CharacterState, content: Content) -> LifestyleLevel | None:
    cfg = content.balance.economy.lifestyle
    key = state.flags.get(FLAG_LIFESTYLE) or cfg.default
    return next((x for x in cfg.levels if x.key == key), None)


def level_index(content: Content, key: str | None) -> int:
    keys = [x.key for x in levels(content)]
    return keys.index(key) if key in keys else keys.index(content.balance.economy.lifestyle.default)


def set_lifestyle(state: CharacterState, content: Content, key: str) -> None:
    """Change the level; a drop after a long habit hurts the mood for a few weeks."""
    cfg = content.balance.economy.lifestyle
    old = state.flags.get(FLAG_LIFESTYLE) or cfg.default
    if old == key:
        return
    drop = level_index(content, old) - level_index(content, key)
    if drop > 0 and int(state.flags.get(FLAG_LIFESTYLE_WEEKS, 0) or 0) >= cfg.habit_weeks:
        state.flags[FLAG_LIFESTYLE_DOWN] = {"weeks": cfg.habit_weeks_penalty, "mood": cfg.habit_mood * drop}
    state.flags[FLAG_LIFESTYLE] = key
    state.flags[FLAG_LIFESTYLE_WEEKS] = 0


def staff(state: CharacterState, content: Content) -> list[StaffMember]:
    keys = list(state.flags.get(FLAG_STAFF) or [])
    by = {m.key: m for m in content.balance.economy.staff}
    return [by[k] for k in keys if k in by]


# -- drugs ---------------------------------------------------------------------------------------------------------------


def drug(content: Content, key: str) -> DrugSpec | None:
    return next((d for d in content.balance.economy.drugs.drugs if d.key == key), None)


def habit(state: CharacterState) -> dict[str, int]:
    return {str(k): int(v) for k, v in (state.flags.get(FLAG_HABIT) or {}).items() if int(v) > 0}


def dosed(state: CharacterState) -> list[str]:
    return list(state.flags.get(FLAG_DOSE) or [])


def addicted(state: CharacterState, content: Content) -> list[str]:
    th = content.balance.economy.drugs.addiction_threshold
    return [k for k, v in habit(state).items() if v >= th]


def withdrawal(state: CharacterState, content: Content) -> list[str]:
    """Drugs the character is hooked on and has not taken since the last press."""
    taken = set(dosed(state))
    return [k for k in addicted(state, content) if k not in taken]


def rehab_price(state: CharacterState, content: Content, key: str) -> int:
    cfg = content.balance.economy.drugs
    return cfg.rehab_base_price + cfg.rehab_price_per_point * habit(state).get(key, 0)


def rehab_chance(state: CharacterState, content: Content, key: str) -> float:
    cfg = content.balance.economy.drugs
    return max(cfg.rehab_min_chance, cfg.rehab_base_chance - cfg.rehab_chance_per_point * habit(state).get(key, 0))


# -- modifiers -----------------------------------------------------------------------------------------------------------


def mod_sources(state: CharacterState, content: Content) -> Iterator[tuple[str, Mapping[str, int]]]:
    """Modifiers of the lifestyle, the staff, the drugs taken and the withdrawal (engine/derived.py reads them)."""
    lvl = lifestyle(state, content)
    if lvl is not None and lvl.mods:
        yield f"lifestyle:{lvl.key}", lvl.mods
    for m in staff(state, content):
        if m.mods:
            yield f"staff:{m.key}", m.mods
    for k in dosed(state):
        d = drug(content, k)
        if d is not None and d.mods:
            yield f"drug:{k}", d.mods
    for k in withdrawal(state, content):
        d = drug(content, k)
        if d is not None and d.withdrawal:
            yield f"withdrawal:{k}", d.withdrawal
    for step in empire_steps(state, content):
        if step.mods:
            yield f"empire:{step.key}", step.mods
    for up, lvl in corp_ups(state, content):  # owner request 25.10: departments of the own megacorp
        if up.mods and lvl:
            yield f"corp_up:{up.key}", {k: v * lvl for k, v in up.mods.items()}


def corp_ups(state: CharacterState, content: Content) -> list:  # noqa: ANN001
    """(department, level) of the own megacorp (flag `corp_up`, a copy of the corp record for the owner's screens)."""
    if not any(s_.key == "megacorp" for s_ in empire_steps(state, content)):
        return []
    have = state.flags.get("corp_up") or {}
    return [(u, int(have.get(u.key, 0) or 0)) for u in content.balance.economy.megacorp.upgrades if have.get(u.key)]


def empire_steps(state: CharacterState, content: Content) -> list:  # noqa: ANN001
    """The steps of the empire the character holds (flag `empire` = how many)."""
    n = int(state.flags.get("empire", 0) or 0)
    return list(content.balance.economy.empire[:max(0, n)])


# -- the week ------------------------------------------------------------------------------------------------------------


@dataclass
class UpkeepWeek:
    immuno_paid: int = 0
    immuno_grade: str | None = None
    immuno_short: int = 0           # strength levels missing this week (0 = covered)
    immuno_rejected: str | None = None  # an implant the body pushed out
    lifestyle_paid: int = 0
    lifestyle_dropped: str | None = None  # the level the character could not pay for
    lifestyle_mood: int = 0         # the mood lost to a habit (after a drop)
    staff_paid: int = 0
    staff_quit: list[str] = field(default_factory=list)
    empire_paid: int = 0
    empire_income: int = 0
    empire_lost: str | None = None  # the step that fell for want of money
    withdrawal: list[str] = field(default_factory=list)  # drug names
    applied: list[AppliedEffects] = field(default_factory=list)

    def merge(self, other: "UpkeepWeek") -> None:
        self.immuno_paid += other.immuno_paid
        self.immuno_grade = other.immuno_grade or self.immuno_grade
        self.immuno_short = max(self.immuno_short, other.immuno_short)
        self.immuno_rejected = other.immuno_rejected or self.immuno_rejected
        self.lifestyle_paid += other.lifestyle_paid
        self.lifestyle_dropped = other.lifestyle_dropped or self.lifestyle_dropped
        self.lifestyle_mood += other.lifestyle_mood
        self.staff_paid += other.staff_paid
        self.staff_quit += [x for x in other.staff_quit if x not in self.staff_quit]
        self.empire_paid += other.empire_paid
        self.empire_income += other.empire_income
        self.empire_lost = other.empire_lost or self.empire_lost
        self.withdrawal += [x for x in other.withdrawal if x not in self.withdrawal]
        self.applied += other.applied


def _pay(state: CharacterState, content: Content, rng: random.Random, year: int, amount: int, out: UpkeepWeek) -> bool:
    if amount <= 0:
        return True
    if state.eddies < amount:
        return False
    out.applied.append(apply_effects(state, {"eddies": -amount}, content, rng, year))
    return True


def week(state: CharacterState, content: Content, rng: random.Random, year: int, *, jailed: bool = False) -> UpkeepWeek:
    """One week of upkeep: suppressants first (the body), then the staff, then the lifestyle; the drug habit."""
    out = UpkeepWeek()
    eco = content.balance.economy
    # immunosuppressants: in a cell the prison doctor gives the cheapest pills, nothing is paid and nothing is missed
    need = needed_grade(state, content)
    if need is None or jailed:
        state.flags.pop(FLAG_IMMUNO_MISS, None)
    else:
        taken = grade(content, state.flags.get(FLAG_IMMUNO))
        strength = 0
        if taken is not None and can_take(state, taken):
            price = immuno_price(state, content, taken)
            if _pay(state, content, rng, year, price, out):
                out.immuno_paid += price
                out.immuno_grade = taken.name
                strength = taken.strength
                if taken.mood:  # owner request 24.10: the suppressants calm the body and lift the mood
                    out.applied.append(apply_effects(state, {"mood": taken.mood}, content, rng, year))
        short = max(0, need.strength - strength)
        out.immuno_short = short
        if short == 0:
            state.flags.pop(FLAG_IMMUNO_MISS, None)
        else:
            missed = int(state.flags.get(FLAG_IMMUNO_MISS, 0) or 0) + 1
            state.flags[FLAG_IMMUNO_MISS] = missed
            m = eco.immuno.miss
            fx: dict[str, Any] = {"hp": -m.hp * short, "mood": -m.mood * short, "glitches": m.glitches * short}
            if missed >= m.humanity_from_week:
                fx["humanity"] = -m.humanity * short
            before = {c.key for c in state.cyberware}
            if missed >= m.reject_from_week and state.cyberware and rng.random() < m.reject_chance * short:
                fx["remove_random_cyberware"] = True
            out.applied.append(apply_effects(state, fx, content, rng, year))
            gone = before - {c.key for c in state.cyberware}
            if gone:
                k = next(iter(gone))
                out.immuno_rejected = content.cyberware[k].name if k in content.cyberware else k
    if jailed:
        return out
    # the empire: the upkeep of every step held; unpaid, the top step falls
    steps = empire_steps(state, content)
    if steps:
        cost = sum(s_.weekly for s_ in steps)
        if _pay(state, content, rng, year, cost, out):
            out.empire_paid += cost
            income = sum(rng.randint(*s_.income) for s_ in steps if s_.income[1] > 0)
            pct = sum(u.income_pct * lvl for u, lvl in corp_ups(state, content))  # owner request 25.10: the finance dept
            income = income * (100 + pct) // 100
            if income:  # owner report 23.10: the empire pays, not only eats
                out.applied.append(apply_effects(state, {"eddies": income}, content, rng, year))
                out.empire_income += income
        else:
            out.empire_lost = steps[-1].name
            state.flags["empire"] = len(steps) - 1
            if steps[-1].key == "megacorp":
                state.flags["megacorp_lost"] = True  # the service dissolves the corp of the shared city
    # staff: whoever is not paid leaves
    keep: list[str] = []
    for mem in staff(state, content):
        if _pay(state, content, rng, year, mem.weekly, out):
            out.staff_paid += mem.weekly
            keep.append(mem.key)
        else:
            out.staff_quit.append(mem.name)
    if keep:
        state.flags[FLAG_STAFF] = keep
    else:
        state.flags.pop(FLAG_STAFF, None)
    # lifestyle
    lvl = lifestyle(state, content)
    if lvl is not None and lvl.weekly > 0:
        if _pay(state, content, rng, year, lvl.weekly, out):
            out.lifestyle_paid += lvl.weekly
            state.flags[FLAG_LIFESTYLE_WEEKS] = int(state.flags.get(FLAG_LIFESTYLE_WEEKS, 0) or 0) + 1
        else:
            out.lifestyle_dropped = lvl.name
            set_lifestyle(state, content, eco.lifestyle.default)
    elif lvl is not None:
        state.flags[FLAG_LIFESTYLE_WEEKS] = int(state.flags.get(FLAG_LIFESTYLE_WEEKS, 0) or 0) + 1
    down = dict(state.flags.get(FLAG_LIFESTYLE_DOWN) or {})
    if down:
        mood = int(down.get("mood", 0) or 0)
        if mood:
            out.applied.append(apply_effects(state, {"mood": -mood}, content, rng, year))
            out.lifestyle_mood += mood
        left = int(down.get("weeks", 0) or 0) - 1
        if left > 0:
            down["weeks"] = left
            state.flags[FLAG_LIFESTYLE_DOWN] = down
        else:
            state.flags.pop(FLAG_LIFESTYLE_DOWN, None)
    # drugs: the habit fades without the drug, the withdrawal hits while hooked
    hb = habit(state)
    taken = set(dosed(state))
    th = eco.drugs.addiction_threshold
    for k, pts in list(hb.items()):
        d = drug(content, k)
        if d is None:
            hb.pop(k)
            continue
        if k in taken:
            continue
        if pts >= th:
            out.withdrawal.append(d.name)
            if d.withdrawal_effects:
                out.applied.append(apply_effects(state, dict(d.withdrawal_effects), content, rng, year))
        hb[k] = max(0, pts - eco.drugs.decay_per_week)
    hb = {k: v for k, v in hb.items() if v > 0}
    if hb:
        state.flags[FLAG_HABIT] = hb
    else:
        state.flags.pop(FLAG_HABIT, None)
    return out


def end_of_press(state: CharacterState) -> None:
    """The doses of a press wear off when the next one begins; the cap of wealth threats starts again."""
    state.flags.pop(FLAG_DOSE, None)
    state.flags.pop("wealth_press", None)


# -- wealth and its threats (owner request: the richer, the harder) --------------------------------------------------------


def wealth(state: CharacterState, content: Content) -> int:
    """Money on hand + the business + the ride: what the city sees."""
    total = max(0, state.eddies)
    from app.engine import biz as biz_engine

    for b in biz_engine.list_of(state):
        kind = next((k for k in content.balance.players.business.kinds if k.key == b.get("kind")), None)
        if kind is not None:
            total += kind.price * int(b.get("level", 1) or 1)
    ride = state.flags.get("vehicle")
    if isinstance(ride, dict) and ride.get("key") in content.vehicles:
        total += content.vehicles[str(ride["key"])].price
    return total


def wealth_tier(state: CharacterState, content: Content):  # noqa: ANN201
    best = None
    for t in content.balance.economy.wealth.tiers:
        if wealth(state, content) >= t.min and (best is None or t.min >= best.min):
            best = t
    return best


def wealth_threat(state: CharacterState, content: Content, rng: random.Random) -> str | None:
    """A week's roll of a threat to the rich: an event id from the tier and every lower one, or None.
    `wealth_guard` (bodyguard, netrunner, home security) cuts the chance."""
    from app.engine import derived

    tier = wealth_tier(state, content)
    if tier is None or tier.chance <= 0:
        return None
    guard = max(0, min(90, derived.mod(state, content, "wealth_guard")))
    if rng.random() >= tier.chance * (100 - guard) / 100:
        return None
    pool: list[str] = []
    for t in content.balance.economy.wealth.tiers:
        if t.min <= tier.min:
            pool += list(t.events)
    return rng.choice(pool) if pool else None
