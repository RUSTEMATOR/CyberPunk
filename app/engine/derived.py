"""Everything computed from a character state (PLAN 2.4, DESIGN.md sections 5-8).

Rule 10: implants, armor, weapons, traits, role perks, vows, buffs and the clean body change
numbers ONLY through the modifiers collected here. Game formulas ask `mod(state, content, key)`
or one of the helpers below; nothing checks `has_implant('...')`.

All functions are pure. `collect_mods` keeps a small cache on the state, validated against a
signature of everything that feeds it, so a stale cache cannot be read.
"""

from __future__ import annotations

import random
from bisect import bisect_right
from typing import Any, Iterable, Iterator, Mapping

from math import ceil

from app.content.loader import Content
from app.content.schemas import MOD_CHECK_CONTEXTS, ActiveSpec, Role, ToleranceEntry
from app.engine.dice import clamp, d100, half_up
from app.engine.state import (
    FLAG_ADDICTION,
    FLAG_FUGITIVE,
    FLAG_FULL_CONVERSION,
    FLAG_INFECTION,
    FLAG_JAIL_WEEKS,
    FLAG_TRAUMA_TEAM,
    CharacterState,
)

# ---------------------------------------------------------------------------
# Modifiers
# ---------------------------------------------------------------------------


def _crisis_version() -> int:
    from app.engine import crisis, equilib

    return crisis.version() * 100003 + equilib.version()  # owner request 26.10: the balance of the three forces too


def _signature(state: CharacterState) -> tuple[Any, ...]:
    return (
        tuple((c.key) for c in state.cyberware),
        tuple(it.key for it in state.inventory if it.kind == "weapon" and it.equipped),  # owner request: 3 slots
        state.armor_key,
        tuple(state.traits), tuple(state.marks), tuple(state.obsessions),
        tuple(sorted(state.vows.items())),
        tuple((b.source, tuple(sorted(b.mods.items()))) for b in state.buffs),
        tuple(sorted(state.role_affinity.items())),
        state.chrome_free,
        state.mood,  # owner request: the mood tier
        state.street_cred, state.fame,  # the rank of a career path needs them
        state.age_weeks,  # owner request: the season
        state.housing,  # owner request 23.10: the home gives mods
        tuple(sorted(state.stats.items())),  # owner request 24.10: perks that count attributes at 9+
        _crisis_version(),  # owner request 24.10: the city crisis changes everybody's modifiers
        # flags that carry modifiers: attachments, home, ride, Mk steps, lifestyle, staff, drugs and the habit
        repr([state.flags.get(k) for k in _MOD_FLAGS]),
        state.relics,  # owner request 25.10: relics
    )


_MOD_FLAGS = ("injuries", "capri_host", "weapon_mods", "home", "vehicle", "cw_mk", "lifestyle", "staff", "drug_dose", "habit", "caliber_mm",
              "empire", "path", "weather", "maniac", "fugitive_psycho", "cyberpsycho",
              "companion", "elysium", "sick", "lifestyle", "married", "libria", "abroad", "cohab", "fitness", "pl_ripper",
              "vampire", "vamp_blood", "lvl", "bw_rot", "bw_avatar", "bw_favor", "bw_cult_in", "wear", "bw_tree", "bwq_1", "bwq_2", "bwq_3", "bwq_4", "bwq_5")


def role_rank(role: Role, affinity: float) -> int:
    """Rank 0..max_rank: how many thresholds the affinity has reached."""
    return bisect_right(role.rank_thresholds, affinity)


def role_ranks(state: CharacterState, content: Content) -> dict[str, int]:
    """Rank of every filled role the character has affinity for (rank 0 omitted)."""
    out: dict[str, int] = {}
    for key, aff in state.role_affinity.items():
        role = content.roles.get(key)
        if role is None or role.is_placeholder:
            continue
        rank = role_rank(role, aff)
        if rank > 0:
            out[key] = rank
    return out


def ranked_roles(state: CharacterState, content: Content) -> list[tuple[str, float, int]]:
    """(role key, affinity, rank) of filled roles with affinity > 0, strongest first."""
    rows: list[tuple[str, float, int]] = []
    for key, aff in state.role_affinity.items():
        role = content.roles.get(key)
        if role is None or role.is_placeholder or aff <= 0:
            continue
        rows.append((key, aff, role_rank(role, aff)))
    rows.sort(key=lambda r: (-r[1], r[0]))
    return rows


def main_role(state: CharacterState, content: Content) -> str:
    """The role with the highest affinity, '' while nothing has grown yet."""
    rows = ranked_roles(state, content)
    return rows[0][0] if rows else ""


def second_role(state: CharacterState, content: Content) -> str:
    """The next role, shown from balance.roles.second_role_min_rank; '' otherwise."""
    rows = ranked_roles(state, content)
    if len(rows) < 2:
        return ""
    key, _, rank = rows[1]
    return key if rank >= content.balance.roles.second_role_min_rank else ""


def _mod_sources(state: CharacterState, content: Content) -> Iterator[tuple[str, Mapping[str, int]]]:
    """(label, mods) for everything on the character that carries modifiers."""
    mk_steps = content.balance.economy.mk.steps
    mk_all = state.flags.get("cw_mk") or {}
    for item in state.cyberware:
        cw = content.cyberware.get(item.key)
        if cw is not None and cw.mods:
            step = int(mk_all.get(item.key, 0) or 0)  # owner request: Mk2/Mk3 of an installed implant
            if 0 < step <= len(mk_steps):
                mult = mk_steps[step - 1].mod_mult
                yield f"cyberware:{item.key}", {k: (ceil(v * mult) if v > 0 else v) for k, v in cw.mods.items()}
            else:
                yield f"cyberware:{item.key}", cw.mods
    held = [it.key for it in state.inventory if it.kind == "weapon" and it.equipped]  # owner request: 3 slots
    for wkey in held:
        weapon = content.weapons.get(wkey)
        if weapon is not None and weapon.mods:
            yield f"weapon:{weapon.key}", weapon.mods
    armor = content.armor.get(state.armor_key or "")
    if armor is not None and armor.mods:
        yield f"armor:{armor.key}", armor.mods
    up = content.balance.upgrades  # owner request: attachments of the equipped weapon, things at home
    wm = [k for wkey in held for k in ((state.flags.get("weapon_mods") or {}).get(wkey) or [])]
    for key in wm:
        m = next((x for x in up.weapon_mods if x.key == key), None)
        if m is not None:
            yield f"weapon_mod:{key}", m.mods
    for key in state.flags.get("home") or []:
        h = next((x for x in up.home if x.key == key), None)
        if h is not None:
            yield f"home:{key}", h.mods
    ride = state.flags.get("vehicle")  # owner request: your ride (app/services/vehicles.py)
    if isinstance(ride, dict):
        v = content.vehicles.get(str(ride.get("key")))
        if v is not None:
            speed = v.speed + int(ride.get("tune", 0) or 0) - int(ride.get("dmg", 0) or 0) // 34
            derived_mods = {"check.escape": max(0, speed) // 3, "check.social": v.style // 4}
            if ride.get("stolen"):
                derived_mods["heat_decay"] = content.balance.vehicles.stolen_heat_decay
            yield f"vehicle:{v.key}", {k: n for k, n in derived_mods.items() if n}
            if v.mods:
                yield f"vehicle_mods:{v.key}", v.mods
    for table, keys in (
        (content.traits, state.traits), (content.marks, state.marks),
        (content.obsessions, state.obsessions),
    ):
        for k in keys:
            entry = table.get(k)
            if entry is not None and entry.mods:
                yield f"trait:{k}", entry.mods
    # Role perks work at the rank of their own role, so a hybrid gets both.
    for rkey, rank in role_ranks(state, content).items():
        for perk in content.roles[rkey].perks:
            if perk.rank <= rank:
                yield f"role:{rkey}:{perk.rank}", perk.mods
    # A held vow gives the streak bonuses reached so far.
    for vkey, weeks in state.vows.items():
        vow = content.vows.get(vkey)
        if vow is None:
            continue
        for bonus in vow.streak_bonuses:
            if weeks >= bonus.weeks:
                yield f"vow:{vkey}:{bonus.weeks}", bonus.mods
    for buff in state.buffs:
        if buff.mods:
            yield f"buff:{buff.source}", buff.mods
    tier = mood_tier(state, content)  # owner request: the mood matters
    if tier is not None:
        mm: dict[str, int] = dict(tier.mods)
        if tier.checks:
            for ctx in MOD_CHECK_CONTEXTS:
                mm[f"check.{ctx}"] = mm.get(f"check.{ctx}", 0) + tier.checks
        if tier.xp_pct:
            mm["xp.all"] = mm.get("xp.all", 0) + tier.xp_pct
        if tier.ap_week:
            mm["ap_week"] = mm.get("ap_week", 0) + tier.ap_week
        if mm:
            yield f"mood:{tier.name}", mm
    mm = state.flags.get("caliber_mm")  # owner request: the caliber of the day helps in the game
    if mm is not None:
        best = None
        for t in content.balance.caliber.titles:
            if int(mm) >= t.mm and (best is None or t.mm >= best.mm):
                best = t
        if best is not None and best.mods:
            yield f"caliber:{best.title}", best.mods
    from app.engine import upkeep  # local: upkeep imports effects, which imports this module

    yield from upkeep.mod_sources(state, content)  # owner request: lifestyle, staff, drugs, withdrawal
    from app.engine import paths

    yield from paths.mod_sources(state, content)  # owner request: the rank of a career path
    from app.engine import weather

    yield from weather.mod_sources(state, content)  # owner request: the season and the weather
    from app.engine import maniac

    yield from maniac.mod_sources(state, content)  # owner request: the maniac mode
    from app.engine import cyberpsycho

    yield from cyberpsycho.mod_sources(state, content)  # owner request: the cyberpsychopath mode
    from app.engine import companion

    yield from companion.mod_sources(state, content)  # owner request: a hired city NPC
    from app.engine import elysium

    yield from elysium.mod_sources(state, content)  # owner request: the citizens of Elysium
    from app.engine import libria

    yield from libria.mod_sources(state, content)  # owner request 23.10: Prozium and the Grammaton Clerics
    from app.engine import vampire

    yield from vampire.mod_sources(state, content)  # owner request 24.10: the drained blood or the hunger
    from app.engine import perks

    yield from perks.mod_sources(state)  # owner request 24.10: the perk trees
    from app.engine import blackwall

    yield from blackwall.mod_sources(state, content)  # owner request 24.10: the Blackwall taint and the Avatar
    from app.engine import crisis

    yield from crisis.mod_sources(state)  # owner request 24.10: the city crisis and its aftermath
    from app.engine import equilib

    yield from equilib.mod_sources(state)  # owner request 26.10: the balance of the three forces
    from app.engine import injury

    yield from injury.mod_sources(state)  # owner request 26.10: a lost limb, a damaged implant
    from app.engine import capri

    yield from capri.mod_sources(state, content)  # owner request 26.10: project «Каприкорн»
    from app.engine import abroad

    yield from abroad.mod_sources(state, content)  # owner request 23.10: life in another country
    from app.engine import wear

    yield from wear.mod_sources(state, content)  # owner request 25.10: the price of power
    from app.engine import bwtree

    yield from bwtree.mod_sources(state, content)  # owner request 25.10: the Blackwall branch of the character
    from app.engine import possession

    yield from possession.mod_sources(state, content)  # owner request 26.10: the AI in the head
    from app.engine import abilities

    yield from abilities.mod_sources(state, content)  # owner request 26.10: the chrome build lines
    from app.engine import ab_power

    yield from ab_power.mod_sources(state, content)  # owner request 27.10: the abilities help their own checks
    for rk in state.relics:  # owner request 25.10: relics
        rd = next((x for x in content.balance.relics.items if x.key == rk), None)
        if rd is not None:
            yield f"relic:{rk}", rd.mods
    plr = state.flags.get("pl_ripper")  # owner request 24.10: a player ripper's regular client
    if isinstance(plr, dict) and int(plr.get("until", -1)) >= state.weeks_lived and int(plr.get("pct", 0)):
        yield "pl_ripper", {"ripper_discount_pct": int(plr["pct"])}
    fit = int(state.flags.get("fitness", 0) or 0)  # owner request 23.10: sport builds health and the body
    if fit > 0:
        g = content.balance.gym
        fm: dict[str, int] = {"hp_max": fit // g.hp_per_points} if fit >= g.hp_per_points else {}
        body = sum(1 for x in g.body_at if fit >= x)
        if body:
            fm["stat.BODY"] = body
        if fm:
            yield "fitness", fm
    cm = content.balance.hubs.cohab_mods
    if cm and isinstance(state.flags.get("cohab"), dict):  # owner request 23.10: a home shared with other players
        yield "cohab", cm
    from app.engine import living

    yield from living.mod_sources(state, content)  # owner request 23.10: where and how you live
    mm = content.balance.relations.married_mods
    if mm and state.flags.get("married"):  # owner request 23.10: a family is a shoulder
        yield "married", mm
    if state.chrome_free:
        cf = content.balance.chrome_free
        clean: dict[str, int] = {"xp.all": cf.xp_pct}
        for ctx, bonus in cf.check_bonus.items():
            clean[f"check.{ctx}"] = bonus
        yield "chrome_free", clean


def mood_tier(state: CharacterState, content: Content):  # noqa: ANN201
    """The mood tier of the character (balance.healing.mood_tiers), None when none are set."""
    best = None
    for t in content.balance.healing.mood_tiers:
        if state.mood >= t.min and (best is None or t.min >= best.min):
            best = t
    return best


def _apply_caps(total: dict[str, int], content: Content) -> dict[str, int]:
    caps = content.balance.mods_caps
    upper = {
        "ap_week": caps.ap_week,
        "shop_discount_pct": caps.shop_discount_pct,
        "ripper_discount_pct": caps.ripper_discount_pct,
        "humanity_floor": caps.humanity_floor,
    }
    for key, cap in upper.items():
        if key in total:
            total[key] = min(total[key], cap)
    if "humanity_loss_pct" in total:
        total["humanity_loss_pct"] = max(total["humanity_loss_pct"], caps.humanity_loss_pct)
    return total


def sum_mods(sources: Iterable[Mapping[str, int]], content: Content) -> dict[str, int]:
    """Add up mod dicts and apply the caps. Also used for fighters that are not characters."""
    total: dict[str, int] = {}
    for mods in sources:
        for key, val in mods.items():
            total[key] = total.get(key, 0) + val
    return _apply_caps(total, content)


def collect_mods(state: CharacterState, content: Content) -> dict[str, int]:
    """All modifiers on the character, summed, with `balance.mods_caps` applied.

    Includes buffs, role perks, vow streak bonuses and the bonuses of a clean body
    (xp.all and check.social / heal / meditation). Returns a copy; the cache lives on the state.
    """
    sig = _signature(state)
    cached = state._mods_cache
    if cached is not None and cached[0] == sig:
        return dict(cached[1])
    total = sum_mods((m for _, m in _mod_sources(state, content)), content)
    state._mods_cache = (sig, total)
    return dict(total)


def mods_breakdown(state: CharacterState, content: Content) -> dict[str, dict[str, int]]:
    """Per source view of the modifiers (before caps), for the profile screen."""
    return {label: dict(mods) for label, mods in _mod_sources(state, content)}


def mod(state: CharacterState, content: Content, key: str, default: int = 0) -> int:
    """One summed modifier, e.g. mod(state, content, 'skill.melee')."""
    cached = state._mods_cache
    if cached is not None and cached[0] == _signature(state):
        return cached[1].get(key, default)
    return collect_mods(state, content).get(key, default)


# ---------------------------------------------------------------------------
# Era
# ---------------------------------------------------------------------------


def era_humanity_mult(content: Content, year: int) -> float:
    """Humanity-loss multiplier of the era: early chrome is rougher."""
    return content.era_for_year(year).chrome_humanity_mult


def era_price_mult(content: Content, year: int) -> float:
    return content.era_for_year(year).chrome_price_mult


# ---------------------------------------------------------------------------
# Cyber tolerance and humanity
# ---------------------------------------------------------------------------


def tolerance_entry(content: Content, key: str) -> ToleranceEntry:
    for e in content.balance.cyber.tolerance:
        if e.key == key:
            return e
    raise KeyError(f"unknown tolerance type {key!r}")


def roll_tolerance(rng: random.Random, content: Content) -> str:
    """The hidden d100 roll at creation (DESIGN.md section 7). 00 counts as 100."""
    roll = d100(rng)
    for e in content.balance.cyber.tolerance:
        if e.min <= roll <= e.max:
            return e.key
    raise ValueError(f"tolerance table does not cover the roll {roll}")


def tolerance_mult(state: CharacterState, content: Content) -> float:
    return tolerance_entry(content, state.cyber_tolerance).mult


def is_full_conversion(state: CharacterState, content: Content) -> bool:
    if state.flags.get(FLAG_FULL_CONVERSION):
        return True
    return any(
        (cw := content.cyberware.get(c.key)) is not None and (cw.smasher_only or cw.borg_risk) for c in state.cyberware
    )


def chrome_count(state: CharacterState) -> int:
    return len(state.cyberware)


def is_borg(state: CharacterState, content: Content) -> bool:
    """Owner request 27.10: a borg — a full conversion, or so much chrome that the body is more machine than meat."""
    return is_full_conversion(state, content) or chrome_count(state) >= content.balance.cyber.borg_chrome_implants


def psychosis_immune(state: CharacterState, content: Content) -> bool:
    """A clean body and a full conversion cannot go cyberpsycho (DESIGN.md 7, 7a)."""
    return state.chrome_free or is_full_conversion(state, content)


def humanity_floor(state: CharacterState, content: Content) -> int:
    """Lowest humanity the character can fall to. 0 normally, the inhibitor leash raises it."""
    return max(0, mod(state, content, "humanity_floor"))


def humanity_loss(
    base: int,
    state: CharacterState,
    content: Content,
    *,
    era_mult: float = 1.0,
    complication: bool = False,
) -> int:
    """Humanity an implant takes from this character.

    base (PCh of the implant) x tolerance x era x (1 + humanity_loss_pct / 100) [x complication].
    Rounded half up; an implant with a cost never takes less than 1.
    """
    if base <= 0:
        return 0
    pct = mod(state, content, "humanity_loss_pct")
    factor = tolerance_mult(state, content) * era_mult * max(0.0, 1 + pct / 100) * body_factor(state, content)
    if complication:
        factor *= content.balance.cyber.complication_loss_mult
    if factor <= 0:
        return 0
    return max(1, half_up(base * factor))


def body_factor(state: CharacterState, content: Content) -> float:
    """Owner request 23.10: how well the body holds chrome. 1.0 at BODY 5 and TECH 5 (24.10); less is better."""
    c = content.balance.cyber
    f = (1 - c.body_pct * (stat_total(state, content, "BODY") - c.body_base) / 100
         - c.tech_pct * (stat_total(state, content, "TECH") - c.tech_base) / 100)
    return max(c.body_min, min(c.body_max, f))


def humanity_loss_pct_display(state: CharacterState, content: Content) -> int:
    """The summed stabilizer percent, for the profile."""
    return mod(state, content, "humanity_loss_pct")


def base_emp(state: CharacterState, content: Content) -> int:
    """EMP before the loss from implants: the stat plus stat.EMP modifiers."""
    return max(0, state.stats.get("EMP", 0) + mod(state, content, "stat.EMP"))


def effective_emp(state: CharacterState, content: Content) -> int:
    """EMP now: every `humanity_per_emp_loss` of lost humanity costs 1. A full conversion fixes it."""
    cyber = content.balance.cyber
    if is_full_conversion(state, content):
        return cyber.full_conversion.emp_fixed
    return max(0, base_emp(state, content) - state.humanity_lost // cyber.humanity_per_emp_loss)


def humanity_base_max(state: CharacterState, content: Content) -> int:
    """Humanity of a body without any implants: EMP x 10."""
    return base_emp(state, content) * content.balance.character.humanity_per_emp


def humanity_max(state: CharacterState, content: Content) -> int:
    """The ceiling humanity can be restored to: EMP x 10 minus what the implants permanently took."""
    return max(humanity_floor(state, content), humanity_base_max(state, content) - state.humanity_lost)


# ---------------------------------------------------------------------------
# Stats, skills, checks
# ---------------------------------------------------------------------------


def stat_total(state: CharacterState, content: Content, stat: str) -> int:
    """Stat with modifiers. EMP is the effective EMP (implant loss included)."""
    if stat == "EMP":
        return effective_emp(state, content)
    return max(0, state.stats.get(stat, 0) + mod(state, content, f"stat.{stat}"))


def skill_total(state: CharacterState, content: Content, skill: str | None) -> int:
    if skill is None:
        return 0
    return max(0, state.skill_level(skill) + mod(state, content, f"skill.{skill}"))


def heavy_wound(state: CharacterState, content: Content) -> bool:
    """Below half of max hp (DESIGN.md section 5)."""
    return state.hp < max_hp(state, content) * content.balance.character.heavy_wound_ratio


def check_bonus(
    state: CharacterState, content: Content, contexts: str | Iterable[str] = ()
) -> int:
    """Modifier added to a check: check.<context> for every context given, minus the wound penalty."""
    if isinstance(contexts, str):
        contexts = (contexts,)
    total = sum(mod(state, content, f"check.{c}") for c in contexts)
    if heavy_wound(state, content):
        total -= content.balance.character.heavy_wound_penalty
    return total


def max_hp(state: CharacterState, content: Content) -> int:
    ch = content.balance.character
    return max(1, ch.hp_base + ch.hp_per_body * stat_total(state, content, "BODY")
               + mod(state, content, "hp_max"))


def armor_value(state: CharacterState, content: Content) -> int:
    """Damage taken off every hit: the worn armor plus the `armor` modifiers (subdermal plating)."""
    worn = content.armor.get(state.armor_key or "")
    return max(0, (worn.defense if worn else 0) + mod(state, content, "armor"))


def luck_max(state: CharacterState, content: Content) -> int:
    """Size of the luck pool: the LUCK stat with modifiers."""
    return stat_total(state, content, "LUCK")


def glitch_cap(state: CharacterState, content: Content) -> int:
    return max(0, content.balance.luck.glitch_cap + mod(state, content, "glitch_max"))


def ap_per_week(state: CharacterState, content: Content) -> int:
    return content.balance.time.ap_per_week + mod(state, content, "ap_week")


def ap_per_press(state: CharacterState, content: Content) -> int:
    """AP of one press of «Прожить»: the weeks it lives x the AP of a week (owner request: four weeks a press)."""
    return ap_per_week(state, content) * content.balance.time.weeks_per_press


def skill_xp_needed(level: int, content: Content) -> int:
    """XP to reach level + 1."""
    sk = content.balance.skills
    return sk.xp_base + sk.xp_step * level


def xp_multiplier(state: CharacterState, content: Content, skill: str) -> float:
    pct = mod(state, content, f"xp.{skill}") + mod(state, content, "xp.all")
    return max(0.0, 1 + pct / 100)


# ---------------------------------------------------------------------------
# Active implants
# ---------------------------------------------------------------------------


def active_spec_with_mods(
    content: Content, cyberware_key: str, mods: Mapping[str, int]
) -> ActiveSpec | None:
    """ActiveSpec of an implant with the `active.<group|all>.<field>` mods applied and clamped.

    Works for any fighter: pass the summed mods of a character or of an enemy.
    """
    cw = content.cyberware.get(cyberware_key)
    if cw is None or cw.active is None:
        return None
    spec = cw.active
    group = cw.exclusive_group

    def delta(field_name: str) -> int:
        total = mods.get(f"active.all.{field_name}", 0)
        if group:
            total += mods.get(f"active.{group}.{field_name}", 0)
        return total

    update: dict[str, int] = {
        "min_charge": clamp(spec.min_charge + delta("min_charge"), 1, 100),
        "recharge_per_tick": clamp(spec.recharge_per_tick + delta("recharge_per_tick"), 0, 100),
        "recharge_on_kill": clamp(spec.recharge_on_kill + delta("recharge_on_kill"), 0, 100),
        "recharge_on_takedown": clamp(
            spec.recharge_on_takedown + delta("recharge_on_takedown"), 0, 100
        ),
    }
    if spec.mode == "duration":
        update["duration_ticks"] = max(1, spec.duration_ticks + delta("duration_ticks"))
    else:
        cost = clamp(spec.cost + delta("cost"), 1, 100)
        if spec.mode == "shot":
            cost = clamp(cost, 1, max(1, 100 // spec.effects.targets))
        update["cost"] = cost
        # It cannot trigger on less charge than it spends.
        update["min_charge"] = max(update["min_charge"], cost)
    return spec.model_copy(update=update)


def active_spec(state: CharacterState, content: Content, cyberware_key: str) -> ActiveSpec | None:
    """ActiveSpec of an installed implant as this character runs it (boosters applied)."""
    if not state.has_cyberware(cyberware_key):
        return None
    return active_spec_with_mods(content, cyberware_key, collect_mods(state, content))


# ---------------------------------------------------------------------------
# Flags for event conditions
# ---------------------------------------------------------------------------


def event_flags(state: CharacterState, content: Content) -> set[str]:
    """The EventFlag values that hold for this character (schemas.EVENT_FLAGS)."""
    flags: set[str] = set()
    bal = content.balance
    if state.cyberware:
        flags.add("has_cyberware")
    cyber_defs = [content.cyberware.get(c.key) for c in state.cyberware]
    if any(cw is not None and cw.exclusive_group == "cyberdeck" for cw in cyber_defs):
        flags.add("has_cyberdeck")
    if any(i.kind == "weapon" for i in state.inventory) or any(
        cw is not None and cw.weapon_damage for cw in cyber_defs
    ):
        flags.add("has_weapon")
    if any(i.kind == "armor" for i in state.inventory):
        flags.add("has_armor")
    if state.debts:
        flags.add("has_debt")
    if any(d.missed_weeks > 0 for d in state.debts):
        flags.add("debt_overdue")
    if state.career is not None:
        flags.add("has_job")
    if state.npcs_of("partner"):
        flags.add("has_partner")
    if state.npcs_of("child"):
        flags.add("has_child")
    if state.npcs_of("protege"):
        flags.add("has_protege")
    if state.npcs_of("family"):
        flags.add("has_family_alive")
    if state.flags.get(FLAG_TRAUMA_TEAM) in bal.trauma_team:
        flags.add("has_trauma_team")
    if state.flags.get(FLAG_INFECTION):
        flags.add("is_infected")
    if int(state.flags.get(FLAG_ADDICTION, 0) or 0) >= bal.activities.braindance.addiction_event_threshold:
        flags.add("is_addicted")
    if heavy_wound(state, content):
        flags.add("is_wounded")
    if state.heat >= 1:
        flags.add("is_wanted")
    if state.mood <= bal.healing.low_mood_threshold:
        flags.add("low_mood")
    if int(state.flags.get(FLAG_JAIL_WEEKS, 0) or 0) > 0:
        flags.add("in_jail")
    if state.flags.get(FLAG_FUGITIVE):
        flags.add("is_fugitive_psycho")
    if state.chrome_free:
        flags.add("chrome_free")
    if state.vows:
        flags.add("has_vow")
    return flags
