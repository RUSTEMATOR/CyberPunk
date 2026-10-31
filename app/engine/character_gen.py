"""Character generation (PLAN 2.3, DESIGN.md sections 5, 7, 7a, 10).

CY_BORG style: everything is random, the player chooses one thing, the starting preset.
A preset is a starting kit and NOT a role: roles are earned from play, so a new character has
no role and an empty `role_affinity`. Presets and origins are filtered by the world year through
`content.available`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from app.content.loader import Content
from app.content.schemas import GENDERS, SKILLS, STATS, Origin, Preset, Surname
from app.engine import derived
from app.engine.dice import choice, clamp, d, half_up, weighted_choice
from app.engine.world import WorldFx, districts_now
from app.engine.state import (
    KIND_ARMOR,
    KIND_WEAPON,
    CareerState,
    CharacterState,
    CyberItem,
    DebtState,
    InventoryItem,
    NpcState,
    SkillState,
)


FLAG_GENDER = "gender"  # CharacterState.flags key: "m" or "f"; family NPCs keep it in npc.data["gender"]


@dataclass(frozen=True)
class LineageInfo:
    """What the generator needs to know about the player's lineage (the service reads it from the DB)."""

    family_name: str | None = None  # surname of the lineage; None = a random one
    lives_count: int = 0  # characters this lineage has already had
    glitch_bonus: int = 0  # lineages.glitch_bonus: extra starting glitches


@dataclass
class CharacterDraft:
    """A generated, not yet saved character. The family is the 'family' NPCs of the state."""

    state: CharacterState
    preset_key: str
    origin_key: str
    lineage: LineageInfo
    heir: dict | None = None  # engine/legacy.py HeirSummary as a dict: the draft is an heir of a dead character

    @property
    def gender(self) -> str:
        """'m' or 'f' (stored in state.flags so it is saved with the character)."""
        return str(self.state.flags.get(FLAG_GENDER, "m"))

    @property
    def family(self) -> list[NpcState]:
        return [n for n in self.state.npcs if n.kind == "family"]


# ---------------------------------------------------------------------------
# Offers
# ---------------------------------------------------------------------------


def offer_presets(
    rng: random.Random, content: Content, year: int, count: int | None = None
) -> list[str]:
    """Preset keys to show the player: every preset available in `year`, the civilian last.

    DESIGN.md section 5 offers all presets at once. `count` (for example
    balance.character.role_offer_count) draws that many random ones instead; presets with
    `always_offered` (the civilian) are shown either way, at the end.
    """
    available = content.available("presets", year)
    regular = [k for k, p in available.items() if not p.always_offered]
    always = [k for k, p in available.items() if p.always_offered]
    if count is not None and 0 <= count < len(regular):
        picked = set(rng.sample(regular, count))
        regular = [k for k in regular if k in picked]  # keep the file order
    return regular + always


def pick_origin(
    rng: random.Random, content: Content, year: int, preset: Preset, forced: str | None = None
) -> str:
    """The origin key: forced, or the preset's hint, or a weighted random draw (d10 table of DESIGN 5).

    Origins that start chrome-free are rare (balance.character.rare_origin_weight) and never drawn
    for a preset that starts with implants. A preset that starts chrome-free always takes its hint.
    """
    available = content.available("origins", year)
    if not available:
        raise ValueError(f"no origin is available in {year}")
    if forced is not None:
        if forced not in available:
            raise ValueError(f"origin {forced!r} is not available in {year}")
        return forced
    ch = content.balance.character
    hint = preset.origin_hint
    if hint in available:
        if preset.chrome_free_start or rng.random() < ch.origin_hint_chance:
            return hint
    has_chrome = bool(preset.gear.cyberware)
    keys = [k for k, o in available.items() if not (o.chrome_free_start and has_chrome)]
    weights = [ch.rare_origin_weight if available[k].chrome_free_start else 1.0 for k in keys]
    return weighted_choice(rng, keys, weights)


# ---------------------------------------------------------------------------
# Pieces
# ---------------------------------------------------------------------------


def _roll_stats(rng: random.Random, content: Content, preset: Preset) -> dict[str, int]:
    ch = content.balance.character
    return {
        s: clamp(preset.stat_template[s] + rng.randint(-ch.stat_spread, ch.stat_spread),
                 ch.stat_min, ch.stat_max)
        for s in STATS
    }


def _roll_skills(content: Content, preset: Preset, origin: Origin) -> dict[str, SkillState]:
    cap = content.balance.character.skill_start_max
    skills = {s: SkillState(0, 0) for s in SKILLS}
    for skill, lvl in preset.skills.items():
        skills[skill].level = clamp(lvl, 0, cap)
    for skill, bonus in origin.skill_bonus.items():
        skills[skill].level = clamp(skills[skill].level + bonus, 0, cap)
    return skills


def _roll_debt(rng: random.Random, content: Content, year: int) -> list[DebtState]:
    pool = list(content.available("debts", year).values())
    if not pool:
        return []
    debt = choice(rng, pool)
    step = content.balance.debt.principal_step
    raw = rng.randint(debt.principal_min, debt.principal_max)
    principal = clamp(half_up(raw / step) * step, debt.principal_min, debt.principal_max)
    rate = debt.weekly_rate if debt.weekly_rate is not None else content.balance.debt.weekly_rate
    return [DebtState(
        creditor=debt.creditor, principal=principal, weekly_rate=rate, missed_weeks=0,
        creditor_kind=debt.creditor_kind, creditor_ref=debt.creditor_ref, key=debt.key,
    )]


# ---------------------------------------------------------------------------
# Names and gender
# ---------------------------------------------------------------------------

# Endings of Russian surnames that inflect, for surnames that are not in names.yaml
# (a player-typed surname, an old lineage): (male ending, female ending).
_SURNAME_ENDINGS: tuple[tuple[str, str], ...] = (
    ("ов", "ова"), ("ев", "ева"), ("ёв", "ёва"), ("ский", "ская"), ("цкий", "цкая"),
)


def find_surname(content: Content, text: str) -> Surname | None:
    """The names.yaml surname that has `text` as its male or female form."""
    low = text.strip().casefold()
    for sn in content.surnames:
        if low in (sn.male.casefold(), sn.female.casefold()):
            return sn
    return None


def surname_forms(content: Content, text: str) -> Surname:
    """Both gender forms of a surname: from names.yaml, else guessed by ending, else one form."""
    text = text.strip()
    known = find_surname(content, text)
    if known is not None:
        return known
    for male, female in _SURNAME_ENDINGS:
        if len(text) > len(female) + 1 and text.endswith(female):
            return Surname(male=text[: -len(female)] + male, female=text)
        if len(text) > len(male) + 1 and text.endswith(male):
            return Surname(male=text, female=text[: -len(male)] + female)
    return Surname(male=text, female=text)


def surname_for(content: Content, text: str, gender: str) -> str:
    """The form of a surname that agrees with `gender` ('m' or 'f')."""
    return surname_forms(content, text).form(gender)


def gender_of_name(rng: random.Random, content: Content, name: str) -> str:
    """Gender of a first name: names.yaml (unisex rolls), else the Russian ending (-а/-я is female)."""
    low = name.strip().casefold()
    for fn in content.given_names:
        if fn.name.casefold() == low:
            return fn.gender if fn.gender in GENDERS else choice(rng, list(GENDERS))
    return "f" if low.endswith(("а", "я")) else "m"


def roll_first_name(
    rng: random.Random, content: Content, gender: str | None = None, taken: set[str] | None = None
) -> tuple[str, str]:
    """(first name, gender). `gender` None = any name; a unisex name takes the asked or a random gender.

    `taken` holds casefolded first names that must not repeat; it is updated with the pick.
    """
    taken = taken if taken is not None else set()
    pool = [
        fn for fn in content.given_names
        if fn.name.casefold() not in taken and (gender is None or fn.gender in (gender, "n"))
    ] or [fn for fn in content.given_names if gender is None or fn.gender in (gender, "n")]
    fn = choice(rng, pool)
    taken.add(fn.name.casefold())
    return fn.name, gender or (fn.gender if fn.gender in GENDERS else choice(rng, list(GENDERS)))


def parent_alive_chance(content: Content, parent_age: int, origin_key: str | None) -> float:
    """Chance that a parent of this age is alive, shaped by the origin (balance.character.parent_survival)."""
    ps = content.balance.character.parent_survival
    chance = ps.base - ps.decline_per_year * max(0, parent_age - ps.decline_from_age)
    chance = max(ps.min_chance, chance)
    return clamp_unit(chance * ps.origin_mult.get(origin_key or "", 1.0))


def clamp_unit(x: float) -> float:
    return max(0.0, min(1.0, x))


def make_family(
    rng: random.Random,
    content: Content,
    year: int,
    surname: str,
    *,
    origin: str | None = None,
    age: int | None = None,
    taken_names: tuple[str, ...] = (),
    fx: WorldFx | None = None,
) -> list[NpcState]:
    """A mother, a father (alive or not) and 0..siblings_max siblings as NPC drafts (DESIGN.md section 10).

    Roles are the neutral keys `parent` and `sibling`; the gender is in `data["gender"]`. Everybody
    shares the family surname in the form that agrees with their gender, and first names are unique
    in the family (`taken_names` = the character's own first name). Parents are `age` plus
    balance.character.parent_age_gap older; whether one is alive depends on that age and on the
    origin (`parent_survival`). Dead parents have `data["fate"]` = dead | unknown.
    """
    ch = content.balance.character
    ps = ch.parent_survival
    forms = surname_forms(content, surname)
    char_age = ch.age_min if age is None else age
    districts = list(districts_now(content, year, fx or WorldFx()))
    taken = {n.casefold() for n in taken_names}
    family: list[NpcState] = []

    def member(role: str, gender: str, alive: bool, data: dict[str, object]) -> NpcState:
        first, gender = roll_first_name(rng, content, gender, taken)
        data = {"gender": gender, **data}
        if alive and districts:
            data["district"] = choice(rng, districts)
        return NpcState(
            name=f"{first} {forms.form(gender)}", kind="family", role=role,
            relation=rng.randint(ch.family_rel_min, ch.family_rel_max),
            alive=alive, data=data,
        )

    orphan = origin in ps.orphan_origins
    for gender in ("f", "m"):
        parent_age = char_age + rng.randint(ch.parent_age_gap_min, ch.parent_age_gap_max)
        if orphan:
            alive = False
            fate = "unknown" if rng.random() < ps.orphan_unknown_chance else "dead"
        else:
            alive = rng.random() < parent_alive_chance(content, parent_age, origin)
            fate = "dead"
        family.append(member("parent", gender, alive, {"age": parent_age} if alive else {"fate": fate}))
    for _ in range(rng.randint(0, ch.siblings_max)):
        family.append(member("sibling", choice(rng, list(GENDERS)), True, {}))
    return family


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------


def generate(
    rng: random.Random,
    content: Content,
    preset_key: str,
    origin_key: str | None = None,
    year: int | None = None,
    lineage: LineageInfo | None = None,
    *,
    name: str | None = None,
    surname: str | None = None,
    fx: WorldFx | None = None,
) -> CharacterDraft:
    """Roll a new character from a preset.

    `year` is the world year (default balance.world.start_year). `origin_key` None = random.
    Everything hidden stays hidden: the cyber tolerance is rolled here and only stored.
    """
    bal = content.balance
    ch = bal.character
    lineage = lineage or LineageInfo()
    year = bal.world.start_year if year is None else year

    available_presets = content.available("presets", year)
    if preset_key not in available_presets:
        raise ValueError(f"preset {preset_key!r} is not available in {year}")
    preset = available_presets[preset_key]
    origin_key = pick_origin(rng, content, year, preset, origin_key)
    origin = content.origins[origin_key]

    st = CharacterState()
    st.preset = preset_key
    st.origin = origin_key
    st.role = ""
    st.role_affinity = {}

    # name, gender and age: the surname agrees with the gender; an explicit surname is kept as typed
    first = (name or "").strip()
    if first:
        gender = gender_of_name(rng, content, first)
    else:
        first, gender = roll_first_name(rng, content)
    st.name = first
    st.flags[FLAG_GENDER] = gender
    explicit = (surname or "").strip()
    family_surname = explicit or (lineage.family_name or "").strip()
    if not family_surname:
        family_surname = choice(rng, list(content.surnames)).male
    st.surname = explicit or surname_for(content, family_surname, gender)
    st.age_years = rng.randint(ch.age_min, ch.age_max)
    st.age_weeks = 0
    st.birth_year = year - st.age_years
    st.weeks_lived = 0

    # body
    st.stats = _roll_stats(rng, content, preset)
    st.skills = _roll_skills(content, preset, origin)
    st.cyber_tolerance = derived.roll_tolerance(rng, content)  # hidden
    st.tolerance_known = False

    # place
    districts = districts_now(content, year, fx or WorldFx())
    st.district = origin.start_district if origin.start_district in districts else next(iter(districts))
    st.housing = ch.start_housing

    # standing and money
    st.street_cred = origin.start_street_cred
    st.eddies = ch.start_eddies
    st.debts = _roll_debt(rng, content, year) if content.balance.debt.start_enabled else []
    if origin.enemy_gang:
        st.faction_rep[origin.enemy_gang] = ch.enemy_start_rep
    if preset.starting_career:
        st.career = CareerState(preset.starting_career, 1, 0)

    # gear: first weapon and the armor are equipped
    for i, wkey in enumerate(preset.gear.weapons):
        st.inventory.append(InventoryItem(wkey, KIND_WEAPON, equipped=(i == 0)))
    if preset.gear.armor:
        st.inventory.append(InventoryItem(preset.gear.armor, KIND_ARMOR, equipped=True))
    if preset.gear.vehicle:  # a starting ride, a bit worn
        st.flags["vehicle"] = {"key": preset.gear.vehicle, "tune": 0, "dmg": 20, "stolen": False}
    era_mult = derived.era_humanity_mult(content, year)
    for ckey in preset.gear.cyberware:
        base = content.cyberware[ckey].humanity_cost
        paid = derived.humanity_loss(base, st, content, era_mult=era_mult)
        st.cyberware.append(CyberItem(ckey, charge=100, humanity_paid=paid))
        st.humanity_lost += paid
    # DESIGN.md 7a: a body without a single implant is chrome-free until the first one goes in.
    st.chrome_free = not st.cyberware

    # path: the starting vow (a vow against implants cannot be held by someone who has them)
    if origin.start_vow:
        vow = content.vows.get(origin.start_vow)
        if vow is not None and not ("cyberware" in vow.forbids and st.cyberware):
            st.vows[origin.start_vow] = 0

    # narrative picks (d20 tables)
    st.traits = [choice(rng, list(content.traits))]
    st.marks = [choice(rng, list(content.marks))]
    st.obsessions = [choice(rng, list(content.obsessions))]

    # scales, now that implants, vows and traits can change the caps
    st.hp = derived.max_hp(st, content)
    st.mood = ch.mood_start
    st.humanity = derived.humanity_max(st, content)
    st.luck_pool = derived.luck_max(st, content)
    lk = bal.luck
    st.glitches = clamp(
        d(lk.glitch_die, rng) + lk.glitch_weekly_offset + lineage.glitch_bonus,
        0, derived.glitch_cap(st, content),
    )
    st.death_glitch_used = False
    st.ap = derived.ap_per_press(st, content)
    _roll_vice(st, content, rng)

    # family
    st.npcs = make_family(
        rng, content, year, family_surname, origin=origin_key, age=st.age_years,
        taken_names=(st.name,), fx=fx,
    )

    return CharacterDraft(state=st, preset_key=preset_key, origin_key=origin_key, lineage=lineage)


def _roll_vice(st: CharacterState, content: Content, rng: random.Random) -> None:
    """Owner request: Night City is a city of vices. With `vice_chance` the character starts hooked on a street drug
    (habit points above the addiction threshold) or on braindance."""
    ch = content.balance.character
    if rng.random() >= ch.vice_chance:
        return
    drugs = [d for d in content.balance.economy.drugs.drugs if not d.black_market]
    if drugs and rng.random() < ch.vice_drug_share:
        d = rng.choice(drugs)
        st.flags["habit"] = {d.key: rng.randint(*ch.vice_drug_points)}
    else:
        from app.engine.state import FLAG_ADDICTION

        st.flags[FLAG_ADDICTION] = rng.randint(*ch.vice_braindance)
