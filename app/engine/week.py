"""One week of life (PLAN 4.2, DESIGN.md section 4).

`advance_week(state, rng, content, year)` closes the week for a character: salary, rent, food,
Trauma Team, the delayed outcomes that came due (effect `later`), debt interest and the minimum payment, regeneration, mood drift, heat decay, the luck
pool, glitches, aging with the old-age roll, the AP of the next week, implant charge, vow streaks
and the buffs that ran out. State is mutated in place (the caller saves it) and every change of a
scale goes through `apply_effects` (rule 3). What `apply_effects` cannot express is changed here
and listed in QUESTIONS.md M4a: the per-debt interest and payments (a debt is a row, not a scale),
aging, vow streaks, implant charge and the flags that count weeks.

Pure: no clock (the service passes `now` for buffs), no DB, randomness only through `rng`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime
from math import ceil
from typing import Any

from app.content.loader import Content
from app.engine import crime as crime_engine
from app.engine import derived
from app.engine import roles as roles_engine
from app.engine.dice import clamp, d, half_up, roll
from app.engine.effects import FU_DEATH, FU_DEATH_SAVE, FU_RAID, AppliedEffects, Followup, apply_effects
from app.engine.state import (
    FLAG_STORY_QUEUE,
    FLAG_ADDICTION,
    FLAG_CRIME_WEEK,
    FLAG_ENGRAM,
    FLAG_FUGITIVE,
    FLAG_INFECTION,
    FLAG_JAIL_WEEKS,
    FLAG_LATER,
    FLAG_PARTNER_MOVED_IN,
    FLAG_STIM,
    FLAG_TRAUMA_TEAM,
    CharacterState,
)


@dataclass
class LaterResult:
    """A delayed outcome (effect `later`) that came due this week."""

    text: str
    ok: bool
    applied: AppliedEffects | None  # None when that side had no effects


@dataclass
class WeekReport:
    """What the week did. Numbers are real changes; the UI turns them into lines."""

    weeks_lived: int = 0
    weather: str | None = None  # owner request: the weather of the week (the last one of a press)
    salary: int = 0
    rent: int = 0
    food: int = 0
    trauma: int = 0  # Trauma Team subscription for the week
    interest: int = 0  # added to the debts
    debt_paid: int = 0  # minimum payments made
    eddies_delta: int = 0  # every change of the eddies the week itself made (salary, rent, debts, delayed outcomes)
    events_eddies: int = 0  # eddies moved by the events played since the last report (the service fills it)
    events_debt: int = 0  # debt moved by those events
    later: list[LaterResult] = field(default_factory=list)  # delayed outcomes that came due
    missed: list[str] = field(default_factory=list)  # creditors whose payment was missed
    collector: bool = False
    forced_events: list[str] = field(default_factory=list)  # event ids that must be played this week
    hp_regen: int = 0
    infection_damage: int = 0
    hp_delta: int = 0
    mood_delta: int = 0
    heat_delta: int = 0
    luck_refilled: int = 0
    glitch_gain: int = 0
    birthday: bool = False
    age_years: int = 0
    ap: int = 0
    recharged: bool = False
    vow_weeks: list[str] = field(default_factory=list)  # vows whose streak grew
    new_main_role: str | None = None  # the cooling of the affinity made another role the main one
    buffs_expired: list[str] = field(default_factory=list)
    crime_free: bool = True
    idle_week: bool = False  # in jail or on the run: no salary
    followups: list[Followup] = field(default_factory=list)  # old-age death, death save from infection, ...
    applied: list[AppliedEffects] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    loan_lines: list[str] = field(default_factory=list)  # owner request 24.10: what unpaid lenders did
    span: int = 1  # weeks this report covers (a press in jail lives several)
    upkeep: Any = None  # engine/upkeep.UpkeepWeek: suppressants, lifestyle, staff, drug habit (owner request)


def merge_reports(reports: list[WeekReport]) -> WeekReport:
    """One report out of several consecutive weeks (a press in jail): amounts are summed, the state at the end wins."""
    if len(reports) == 1:
        return reports[0]
    out = WeekReport()
    for r in reports:
        for name in ("salary", "rent", "food", "trauma", "interest", "debt_paid", "hp_regen", "infection_damage",
                     "hp_delta", "mood_delta", "heat_delta", "luck_refilled", "glitch_gain", "eddies_delta",
                     "events_eddies", "events_debt"):
            setattr(out, name, getattr(out, name) + getattr(r, name))
        out.collector = out.collector or r.collector
        out.birthday = out.birthday or r.birthday
        out.recharged = out.recharged or r.recharged
        out.new_main_role = r.new_main_role or out.new_main_role
        for name in ("missed", "forced_events", "vow_weeks", "buffs_expired", "followups", "applied", "notes", "later",
                     "loan_lines"):
            getattr(out, name).extend(getattr(r, name))
    for r in reports:
        if r.upkeep is not None:
            if out.upkeep is None:
                from app.engine.upkeep import UpkeepWeek

                out.upkeep = UpkeepWeek()
            out.upkeep.merge(r.upkeep)
    last = reports[-1]
    out.weeks_lived, out.age_years, out.ap = last.weeks_lived, last.age_years, last.ap
    out.weather = last.weather
    out.crime_free = all(r.crime_free for r in reports)
    out.idle_week = all(r.idle_week for r in reports)
    out.vow_weeks = list(dict.fromkeys(out.vow_weeks))
    out.span = len(reports)
    return out


# -- pieces ---------------------------------------------------------------------------


def expire_buffs(state: CharacterState, now: datetime | None) -> list[str]:
    """Drop buffs whose stored expiry has passed. A buff with the marker 'kyiv_midnight' has no
    time yet (the DB layer fixes it on save), so it stays. Returns the sources removed."""
    if now is None:
        return []
    gone: list[str] = []
    keep = []
    for b in state.buffs:
        try:
            until = datetime.fromisoformat(b.expires)
        except ValueError:
            keep.append(b)
            continue
        if until <= now:
            gone.append(b.source)
        else:
            keep.append(b)
    state.buffs = keep
    return gone


def weekly_salary(state: CharacterState, content: Content) -> int:
    """Salary of the current rank x (1 + (salary_pct.<career> + salary_pct.all) / 100)."""
    if state.career is None:
        return 0
    career = content.careers.get(state.career.key)
    if career is None:
        return 0
    rank = clamp(state.career.rank, 1, len(career.ranks))
    base = career.ranks[rank - 1].salary
    pct = derived.mod(state, content, f"salary_pct.{career.key}") + derived.mod(state, content, "salary_pct.all")
    return max(0, half_up(base * (1 + pct / 100)))


def housing_costs(state: CharacterState, content: Content) -> tuple[int, int]:
    """(rent, food) per week. No housing = no rent and the street food price."""
    home = content.housing.get(state.housing)
    if home is None:
        return 0, content.balance.housing.street_food
    rent = half_up(home.rent * state.world_housing_mult)  # the world's housing price effect
    if state.flags.get(FLAG_PARTNER_MOVED_IN):
        rent = half_up(rent * (1 - content.balance.relations.partner_shared_rent_pct))
    cohab = state.flags.get("cohab")  # owner request 23.10: players sharing a home split the rent
    if isinstance(cohab, dict) and int(cohab.get("n", 1) or 1) > 1:
        rent = half_up(rent / int(cohab["n"]))
    kids = len(state.npcs_of("child"))  # owner request 23.10: children eat, wear out shoes and go to school
    return rent, home.food + kids * content.balance.relations.child_upkeep


def trauma_price(state: CharacterState, content: Content) -> int:
    tier = state.flags.get(FLAG_TRAUMA_TEAM)
    entry = content.balance.trauma_team.get(tier) if isinstance(tier, str) else None
    return entry.weekly_price if entry else 0


def regen_amount(state: CharacterState, content: Content) -> int:
    """BODY x regen per BODY, doubled in apartments and above, plus the hp_regen modifier."""
    h = content.balance.healing
    amount = derived.stat_total(state, content, "BODY") * h.hp_regen_per_body
    home = content.housing.get(state.housing)
    if home is not None and home.tier >= h.good_housing_tier:
        amount *= h.good_housing_mult
    return max(0, amount + derived.mod(state, content, "hp_regen"))


def old_age_chance(age: int, content: Content) -> float:
    """Death chance of the birthday roll: the entry for the largest listed age not above `age`."""
    d_ = content.balance.death
    if age < d_.old_age_start:
        return 0.0
    chance = 0.0
    for a, p in d_.old_age_chance.items():
        if a <= age:
            chance = p
    return chance


def decay_role_affinity(state: CharacterState, content: Content) -> str | None:
    """Role affinity cools by `decay_pct` at the end of the week (engine/roles.py).
    Returns the new main role when the cooling changed it."""
    return roles_engine.decay(state, content).new_main


def min_payment(principal: int, content: Content) -> int:
    """The weekly minimum of a debt: `min_payment_pct` of it, at least `min_payment_floor`, never more than the rest."""
    bal = content.balance.debt
    return min(principal, max(bal.min_payment_floor, ceil(principal * bal.min_payment_pct)))


def resolve_later(
    state: CharacterState, content: Content, rng: random.Random, year: int, *, heat_mult: float = 1.0,
) -> list[LaterResult]:
    """Roll the delayed outcomes whose week has come (the week being closed is `weeks_lived + 1`).

    The chance is rolled with the game rng; the side that came is applied through `apply_effects`. An outcome may
    schedule the next one of a chain (its effects hold another `later`)."""
    pending: list[dict[str, Any]] = list(state.flags.get(FLAG_LATER) or [])
    now = state.weeks_lived + 1
    due = [e for e in pending if int(e.get("due", 0)) <= now]
    if not due:
        return []
    rest = [e for e in pending if int(e.get("due", 0)) > now]
    if rest:
        state.flags[FLAG_LATER] = rest
    else:
        state.flags.pop(FLAG_LATER, None)
    out: list[LaterResult] = []
    for e in due:
        chance = float(e.get("chance", 1.0))
        ok = chance >= 1.0 or rng.random() < chance
        fx = dict((e.get("effects") if ok else e.get("fail_effects")) or {})
        text = str(e.get("text") if ok else (e.get("fail_text") or e.get("text")))
        applied = apply_effects(state, fx, content, rng, year, heat_mult=heat_mult) if fx else None
        if applied is not None:
            for rec in applied.later_added:  # the next link of a chain counts from the week being closed
                rec["due"] = int(rec["due"]) + 1
        out.append(LaterResult(text, ok, applied))
    return out


def _pay_debts(state: CharacterState, content: Content, rng: random.Random, year: int, rep: WeekReport) -> None:
    """Interest first, then the minimum payment of every debt in order while the eddies last (taken automatically).
    A minimum that cannot be paid in full is a missed week; the collector comes after `collector_missed_weeks`."""
    bal = content.balance.debt
    for debt in list(state.debts):
        interest = half_up(debt.principal * debt.weekly_rate)
        debt.principal += interest
        rep.interest += interest
        minimum = min_payment(debt.principal, content)
        if state.eddies >= minimum:
            rep.applied.append(apply_effects(state, {"eddies": -minimum}, content, rng, year))
            debt.principal -= minimum
            debt.missed_weeks = 0
            rep.debt_paid += minimum
            if debt.principal <= 0:
                state.debts.remove(debt)
        else:
            debt.missed_weeks += 1
            rep.missed.append(debt.creditor)
    from app.engine import loans  # owner request 24.10: every lender has its own way to remind

    lines, applied = loans.risks(state, content, rng, year)
    rep.loan_lines.extend(lines)
    rep.applied.extend(applied)
    if any(x.missed_weeks >= bal.collector_missed_weeks for x in state.debts):
        rep.collector = True
        rep.forced_events.append(bal.collector_event)
    # a debt left unpaid for long enough: the collector may finish the job (never inside a cell)
    dc = content.balance.death
    if (not state.flags.get(FLAG_JAIL_WEEKS) and any(x.missed_weeks >= dc.collector_kill_weeks for x in state.debts)
            and rng.random() < dc.collector_kill_chance):
        rep.followups.append(Followup(FU_DEATH, "collector"))


def partner_child_roll(state: CharacterState, content: Content, rng: random.Random) -> bool:
    """A partner who lives with the character may bring a child (balance.relations): the weekly chance
    needs a good relation and fewer than `child_max` children. The service plays the event."""
    cfg = content.balance.relations
    if not state.flags.get(FLAG_PARTNER_MOVED_IN) or int(state.flags.get(FLAG_JAIL_WEEKS, 0) or 0) > 0:
        return False
    partners = state.npcs_of("partner")
    if not partners or partners[0].relation < cfg.child_min_rel:
        return False
    if len(state.npcs_of("child")) >= cfg.child_max:
        return False
    chance = cfg.child_chance_per_week
    night = partners[0].data.get("night_press")  # owner request 23.10: a night together in this press
    if night is not None and state.weeks_lived - int(night) < content.balance.time.weeks_per_press:
        chance += cfg.night_child_bonus
    return rng.random() < chance


def _age(state: CharacterState, content: Content, rng: random.Random, rep: WeekReport) -> None:
    from app.engine import chrono

    if chrono.tick(state):  # owner request (the «In Time» crossover): the clock ran out
        rep.followups.append(Followup(FU_DEATH, chrono.DEATH))
    from app.engine import elysium

    if elysium.tick_radiation(state):  # owner request (the «Elysium» crossover): the dose of the plant
        rep.followups.append(Followup(FU_DEATH, elysium.DEATH))
    state.age_weeks += 1 + max(0, derived.mod(state, content, "age_weeks"))  # regeneration chrome ages the body
    while state.age_weeks >= content.balance.time.weeks_per_year:
        state.age_weeks -= content.balance.time.weeks_per_year
        state.age_years += 1
        rep.birthday = True
        from app.engine import chrono

        chance = 0.0 if chrono.has(state) else old_age_chance(state.age_years, content)  # the clock: no old age
        if chance > 0 and rng.random() < chance and not any(f.kind == FU_DEATH for f in rep.followups):
            rep.followups.append(Followup(FU_DEATH, "old_age"))
    rep.age_years = state.age_years


# -- the week ---------------------------------------------------------------------------------


def advance_week(
    state: CharacterState,
    rng: random.Random,
    content: Content,
    year: int,
    *,
    now: datetime | None = None,
    heat_mult: float = 1.0,
    district_heat: int = 0,
) -> WeekReport:
    """Close one week. `year` is the world year. Returns the report; `state` is changed in place."""
    bal = content.balance
    rep = WeekReport()
    heat_at_press = state.heat
    eddies_at_press = state.eddies
    rep.buffs_expired = expire_buffs(state, now)

    # money: salary, rent, food, Trauma Team
    jailed = int(state.flags.get(FLAG_JAIL_WEEKS, 0) or 0) > 0
    on_run = bool(state.flags.get(FLAG_FUGITIVE))
    rep.idle_week = jailed or on_run
    rep.salary = 0 if rep.idle_week else weekly_salary(state, content)
    rep.rent, rep.food = housing_costs(state, content)
    rep.food = rep.food * (1 + max(0, derived.mod(state, content, "food_x")))  # regeneration chrome: a burning metabolism
    if jailed:
        rep.rent = 0  # the rent is paused while inside, the housing is kept
    rep.trauma = trauma_price(state, content)
    net = rep.salary - rep.rent - rep.food - rep.trauma
    if net:
        rep.applied.append(apply_effects(state, {"eddies": net}, content, rng, year))
    if state.career is not None and not rep.idle_week:
        state.career.weeks += 1
    from app.engine import upkeep  # owner request: suppressants, staff, lifestyle, drug habit

    rep.upkeep = upkeep.week(state, content, rng, year, jailed=jailed)
    rep.applied.extend(rep.upkeep.applied)

    # delayed outcomes (investments, promised payments) come before the debts, so their money can pay the minimum
    rep.later = resolve_later(state, content, rng, year, heat_mult=heat_mult)
    rep.applied.extend(r.applied for r in rep.later if r.applied is not None)
    for eid in state.flags.pop(FLAG_STORY_QUEUE, None) or []:  # story arcs: the next step comes with this week
        if eid not in rep.forced_events:
            rep.forced_events.append(eid)

    # debts
    _pay_debts(state, content, rng, year, rep)
    rep.eddies_delta = state.eddies - eddies_at_press
    if partner_child_roll(state, content, rng):
        rep.forced_events.append(bal.relations.child_event)

    # body and mind: regeneration, infection, mood drift, heat decay, luck, glitches
    h = bal.healing
    fx: dict[str, int | bool] = {}
    rep.hp_regen = regen_amount(state, content)
    if state.flags.get(FLAG_INFECTION):
        rep.infection_damage = roll(bal.cyber.infection_damage_dice, rng)
    hp = rep.hp_regen - rep.infection_damage
    from app.engine import weather  # owner request: the weather of the week

    wk = weather.roll(state, content, rng)
    if wk is not None:
        rep.weather = wk.key
        for k, v in wk.weekly.items():
            if k == "hp":
                hp += int(v)
            elif isinstance(v, int) and not isinstance(v, bool):
                fx[k] = int(fx.get(k, 0)) + v
    if hp:
        fx["hp"] = hp
    from app.engine import vampire  # owner request 24.10: the body of a night-taxi vampire burns the blood

    vampire.weekly(state, content)
    from app.engine import blackwall  # owner request 24.10: the Blackwall taint eats humanity

    for k, v in blackwall.weekly_fx(state, content).items():
        fx[k] = int(fx.get(k, 0)) + v
    down = h.mood_drift_down if h.mood_drift_down is not None else h.mood_drift_step
    drift = clamp(h.mood_drift_target - state.mood, -down, h.mood_drift_step)
    mood = drift + derived.mod(state, content, "mood_weekly")
    if mood:
        fx["mood"] = mood
    state.flags.pop(FLAG_STIM, None)  # a stim lasts until the week closes
    rep.crime_free = not state.flags.pop(FLAG_CRIME_WEEK, False)
    cfg_c = bal.careers
    chance = cfg_c.crime_discovery_by_career.get(state.career.key, cfg_c.crime_discovery_fire_chance) \
        if state.career is not None else 0.0
    if (not rep.crime_free and state.career is not None and not rep.idle_week and state.career.key != "maxtac"
            and rng.random() < chance):
        strikes = int(state.flags.get("crime_strikes", 0) or 0)  # owner request 23.10: it gets worse every time
        seq = [cfg_c.crime_discovery_event, *cfg_c.crime_strike_events]
        rep.forced_events.append(seq[min(strikes, len(seq) - 1)])  # the job found out: security calls you in
        state.flags["crime_strikes"] = strikes + 1
    no_decay = bal.wanted.enabled and state.heat >= bal.wanted.no_decay_from  # owner request 26.10: drop it yourself
    if rep.crime_free and state.heat > 0 and not no_decay:
        decay = bal.crime.heat_decay_per_week + derived.mod(state, content, "heat_decay")
        if decay > 0:
            fx["heat"] = -decay
    if h.luck_refill:
        refill = derived.luck_max(state, content) - state.luck_pool
        if refill > 0:
            fx["luck_pool"] = refill
    lk = bal.luck
    gain = max(0, d(lk.glitch_die, rng) + lk.glitch_weekly_offset)
    if gain:
        fx["glitches"] = gain
    if not jailed:  # owner request 23.10: the home, the food and cheap chrome
        from app.engine import living

        lfx, llines = living.week(state, content, rng)
        for k, v in lfx.items():
            fx[k] = int(fx.get(k, 0)) + v
        if llines:
            state.flags["_living_lines"] = list(state.flags.get("_living_lines") or []) + llines
    before = (state.hp, state.mood, state.heat, state.luck_pool, state.glitches)
    if fx:
        week_fx = apply_effects(state, fx, content, rng, year, heat_mult=heat_mult)
        rep.applied.append(week_fx)
        if rep.infection_damage:  # a body that fails because of the infection dies of the implants
            for fu in week_fx.followups:
                if fu.kind == FU_DEATH_SAVE:
                    fu.data["cause"] = "implant_failure"
    if not jailed:  # owner request: the richer, the harder
        threat = upkeep.wealth_threat(state, content, rng)
        cap = bal.economy.wealth.max_per_press
        already = sum(1 for e in state.flags.get("wealth_press", []) or [])
        if threat and already < cap and threat not in rep.forced_events:
            rep.forced_events.append(threat)
            state.flags["wealth_press"] = list(state.flags.get("wealth_press", []) or []) + [threat]
    tiers = sorted(h.mood_tiers, key=lambda t: t.min)  # owner request: a dark week in a depression
    if (len(tiers) > 1 and state.mood < tiers[1].min and h.depression_events and not jailed
            and rng.random() < h.depression_event_chance):
        ev = rng.choice(h.depression_events)
        if ev not in rep.forced_events:
            rep.forced_events.append(ev)
    rep.hp_delta = state.hp - before[0]
    rep.mood_delta = state.mood - before[1]
    rep.heat_delta = state.heat - before[2]
    rep.luck_refilled = state.luck_pool - before[3]
    rep.glitch_gain = state.glitches - before[4]
    for ap_ in rep.applied:
        rep.followups.extend(ap_.followups)
    raid = crime_engine.raid_roll(state, content, rng, heat_at_press, district_heat)
    if raid is not None:  # the police come for the heat the player went into the week with
        rep.followups.append(Followup(FU_RAID, raid.danger, {"faction": raid.faction, "src": raid.src}))

    # braindance addiction fades
    addiction = int(state.flags.get(FLAG_ADDICTION, 0) or 0)
    if addiction > 0:
        state.flags[FLAG_ADDICTION] = max(0, addiction - bal.activities.braindance.addiction_decay_per_week)

    # the jail term runs down (the jail itself is M9; this keeps the flag from living forever)
    if jailed:
        left = int(state.flags[FLAG_JAIL_WEEKS]) - 1
        if left > 0:
            state.flags[FLAG_JAIL_WEEKS] = left
        else:
            state.flags.pop(FLAG_JAIL_WEEKS, None)
            rep.notes.append("jail term over")

    # time, AP, charge, vows, roles
    state.weeks_lived += 1
    rep.weeks_lived = state.weeks_lived
    _age(state, content, rng, rep)
    state.ap = derived.ap_per_week(state, content)
    rep.ap = state.ap
    if bal.cyber.active.full_recharge_on_week:
        for item in state.cyberware:
            cw = content.cyberware.get(item.key)
            if cw is not None and cw.active is not None and item.charge != 100:
                item.charge = 100
                rep.recharged = True
    for vkey in list(state.vows):
        state.vows[vkey] += 1
        rep.vow_weeks.append(vkey)
    rep.new_main_role = decay_role_affinity(state, content)
    # an engram lives with a stranger's voice: the conflict event comes every few weeks (DESIGN.md 13)
    eg = bal.legacy.engram
    if state.flags.get(FLAG_ENGRAM) and state.weeks_lived % eg.conflict_event_every_weeks == 0:
        rep.forced_events.append(eg.conflict_event)
    return rep
