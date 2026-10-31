"""Pydantic models for every YAML file in content/.

Every model forbids unknown fields, so a typo in a YAML file fails loudly.
Content keys are latin snake_case; Russian display names live in `name`.
Balance numbers live only in `BalanceModel` (content/balance.yaml).
"""

from __future__ import annotations

import re
from string import Formatter
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

# ---------------------------------------------------------------------------
# Enums and shared types
# ---------------------------------------------------------------------------

Key = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=48)]

Stat = Literal["BODY", "REF", "TECH", "INT", "COOL", "EMP", "LUCK"]
STATS: tuple[str, ...] = ("BODY", "REF", "TECH", "INT", "COOL", "EMP", "LUCK")

Skill = Literal[
    "firearms", "melee", "stealth", "netrunning", "tech", "medicine", "persuasion", "driving"
]
SKILLS: tuple[str, ...] = (
    "firearms", "melee", "stealth", "netrunning", "tech", "medicine", "persuasion", "driving",
)

GigType = Literal[
    "delivery", "theft", "elimination", "hack", "escort", "cyberpsycho", "missing"
]
GIG_TYPES: tuple[str, ...] = (
    "delivery", "theft", "elimination", "hack", "escort", "cyberpsycho", "missing",
)

Approach = Literal["loud", "quiet", "talk", "hack"]
APPROACHES: tuple[str, ...] = ("loud", "quiet", "talk", "hack")

OutcomeTier = Literal["clean", "complication", "fail", "disaster"]
OUTCOME_TIERS: tuple[str, ...] = ("clean", "complication", "fail", "disaster")

# How a fight ends for the player (engine/combat.py). `avoided` = a pay or talk exit worked.
FIGHT_RESULTS: tuple[str, ...] = ("won", "fled", "avoided", "surrendered", "downed")

ToleranceType = Literal["fragile", "normal", "resistant", "ironclad", "smasher"]
TOLERANCE_TYPES: tuple[str, ...] = ("fragile", "normal", "resistant", "ironclad", "smasher")

DeathCause = Literal[
    "combat", "cyberpsychosis", "old_age", "collector", "prison", "overdose",
    "implant_failure", "maxtac", "blackwall", "own_will", "left_city", "legend", "wounds",
]
DEATH_CAUSES: tuple[str, ...] = (
    "combat", "cyberpsychosis", "old_age", "collector", "prison", "overdose",
    "implant_failure", "maxtac", "blackwall", "own_will", "left_city", "legend", "dgame", "legend_alive",
    "end_tower", "end_nomads", "end_orbit", "end_mayor",
    "possessed",  # owner request 26.10: the body given to the AI in the head
    "bounty_hunt",  # owner request 27.10: the raid boss taken by the hunters (services/pboss.py)
    "wounds",  # owner report 29.10: zero hp outside a fight (an event, an action, an operation) was called «бой»
)

CyberSlot = Literal[
    "brain", "spine", "eyes", "skin", "face", "circulatory", "legs", "forearms", "hands", "body"
]
CYBER_SLOTS: tuple[str, ...] = (
    "brain", "spine", "eyes", "skin", "face", "circulatory", "legs", "forearms", "hands", "body",
)

ActiveMode = Literal["duration", "reactive", "shot"]

CyberTier = Literal["street", "mid", "military"]
CYBER_TIERS: tuple[str, ...] = ("street", "mid", "military")

CyberCategory = Literal["combat", "noncombat", "booster", "netrun", "defense"]
CYBER_CATEGORIES: tuple[str, ...] = ("combat", "noncombat", "booster", "netrun", "defense")

EnemyAi = Literal["brute", "tactical", "netrunner", "coward"]
ENEMY_AI_PROFILES: tuple[str, ...] = ("brute", "tactical", "netrunner", "coward")

# Faction keys an enemy may use besides gangs.yaml / corps.yaml keys.
EXTRA_FACTIONS: tuple[str, ...] = ("cyberpsycho",)

# Modifier grammar, see DESIGN.md section 8 "Модификаторы и билды".
MOD_CHECK_CONTEXTS: tuple[str, ...] = (
    "combat", "gig_loud", "gig_quiet", "gig_talk", "gig_hack",
    "crime", "career", "social", "escape", "heal", "bribe", "talk_exit",
)
MOD_PLAIN_KEYS: tuple[str, ...] = (
    "hp_max", "armor", "damage_reduction", "melee_damage", "ranged_damage",
    "strength", "flee", "bleed", "hp_regen", "mood_weekly", "ap_week",
    "gig_pay_pct", "crime_reward_pct", "shop_discount_pct", "ripper_discount_pct",
    "relation_gain", "heat_decay", "humanity_loss_pct", "therapy_bonus", "glitch_max",
    "ram_max", "ram_recharge", "quickhack_cost", "quickhack_targets", "ice", "enemy_scan",
    "humanity_floor",
    # owner request (economy of the rich): fewer threats to the rich (%), smaller robbery losses (%), cheaper
    # immunosuppressants (%)
    "wealth_guard", "robbery_loss_pct", "immuno_cost_pct",
    # owner request 24.10: regeneration chrome makes the body burn: extra weeks of age per week, extra % of food
    "age_weeks", "food_x",
    "med_charges",  # owner request 24.10: more med charges in a fight
    "chrome_load_pct",  # owner request 24.10: the body carries this % of the chrome load more easily
)
MOD_ACTIVE_FIELDS: tuple[str, ...] = (
    "min_charge", "recharge_per_tick", "recharge_on_kill", "recharge_on_takedown",
    "duration_ticks", "cost",
)
MOD_PARAM_BASES: tuple[str, ...] = ("stat", "skill", "xp", "check", "salary_pct", "active")
MOD_VALUE_LIMIT = 100

# Flags usable in event conditions (`requires` / `forbids`). The engine derives
# each flag from the character state.
EventFlag = Literal[
    "has_cyberware",
    "has_cyberdeck",
    "has_weapon",
    "has_armor",
    "has_debt",
    "debt_overdue",
    "has_job",
    "has_partner",
    "has_child",
    "has_protege",
    "has_family_alive",
    "has_trauma_team",
    "is_infected",
    "is_addicted",
    "is_wounded",
    "is_wanted",
    "low_mood",
    "in_jail",
    "is_fugitive_psycho",
    "chrome_free",
    "has_vow",
]
EVENT_FLAGS: tuple[str, ...] = (
    "has_cyberware", "has_cyberdeck", "has_weapon", "has_armor", "has_debt",
    "debt_overdue", "has_job", "has_partner", "has_child", "has_protege",
    "has_family_alive", "has_trauma_team", "is_infected", "is_addicted",
    "is_wounded", "is_wanted", "low_mood", "in_jail", "is_fugitive_psycho",
    "chrome_free", "has_vow",
)

EventCategory = Literal["street", "implants", "money", "relationships", "ncpd", "work", "rare"]
EVENT_CATEGORIES: tuple[str, ...] = (
    "street", "implants", "money", "relationships", "ncpd", "work", "rare",
)

TraumaTier = Literal["silver", "gold", "platinum"]
CreditorKind = Literal["gang", "corp", "ripper", "bank", "family", "other"]
GigTarget = Literal["corp", "gang"]
NarratorKind = Literal[
    "gig_brief", "gig_result", "event_text", "event_outcome", "obituary", "news",
    "npc_chat", "classify_action", "unique_event", "future_beat", "history_digest", "saga_step", "chronicle",
    "free_classify", "free_outcome", "find_story", "challenge", "challenge_w",
    "legend_scene",  # owner request 27.10: the last deed of a character who leaves as a legend
    "scenarist",  # owner request 27.10: what a talk or the deeds of a sitting led to (services/dialogs.py)
]

# Tags/templates usable in rel_npc:<tag> and add_npc:<template> effects.
NpcTag = Literal["family", "partner", "friend", "contact", "protege", "child", "random"]
NPC_TAGS: tuple[str, ...] = ("family", "partner", "friend", "contact", "protege", "child", "random")
NpcTemplate = Literal["partner", "friend", "contact", "protege", "child"]
NPC_TEMPLATES: tuple[str, ...] = ("partner", "friend", "contact", "protege", "child")

DICE_RE = re.compile(r"^\d+d\d+([+-]\d+)?$")
KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class Strict(BaseModel):
    """Base for all content models: unknown fields are errors."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def _check_dice(v: str) -> str:
    if not DICE_RE.match(v):
        raise ValueError(f"bad dice expression {v!r}, expected like '2d6' or '2d6+2'")
    return v


Dice = Annotated[str, AfterValidator(_check_dice)]
Pct = Annotated[float, Field(ge=0.0, le=1.0)]


Year = Annotated[int, Field(ge=1900, le=2200)]


class Timed(Strict):
    """Base for records that exist only in some years (DESIGN.md section 9a).

    `None` means no limit on that side, so a record without both fields is always available.
    Both bounds are inclusive: `available_from: 2070` and `available_until: 2076` is 2070..2076.
    Game code must read such catalogs through `Content.available(kind, year)`.
    """

    available_from: Year | None = None
    available_until: Year | None = None

    @model_validator(mode="after")
    def _years_order(self) -> "Timed":
        lo, hi = self.available_from, self.available_until
        if lo is not None and hi is not None and lo > hi:
            raise ValueError(
                f"available_from ({lo}) is later than available_until ({hi}); "
                "write the earlier year in available_from"
            )
        return self


def _check_tiers(v: dict[int, Any]) -> dict[int, Any]:
    if set(v) != {1, 2, 3, 4, 5}:
        raise ValueError("table must have exactly the keys 1, 2, 3, 4, 5")
    return v


GIG_TIERS: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7, 8, 9)  # owner request 29.10: contracts above the street ladder


def _check_gig_tiers(v: dict[int, Any]) -> dict[int, Any]:
    if set(v) != set(GIG_TIERS):
        raise ValueError(f"table must have exactly the keys {', '.join(map(str, GIG_TIERS))}")
    return v


# ---------------------------------------------------------------------------
# Checks and effects
# ---------------------------------------------------------------------------


class CheckSpec(Strict):
    """A check without a difficulty value (gig templates: dv comes from the tier)."""

    stat: Stat
    skill: Skill


class Check(Strict):
    """A check with a fixed difficulty value (events, activities, crime)."""

    stat: Stat
    skill: Skill | None = None
    dv: int = Field(ge=5, le=30)


# Effect keys. A key is either `base` or `base:param`.
EFFECT_PLAIN_INT = (
    "eddies", "hp", "mood", "humanity", "street_cred", "heat", "debt", "luck_pool", "glitches",
    "status", "fame", "dirt", "tokens", "chrono",
    "biz_level", "biz_boost", "biz_slump",  # owner request: an event of a business changes that business
    "vamp_blood",  # owner request 24.10: the drained blood of a night-taxi vampire (engine/vampire.py)
    "bw_rot", "bw_favor",  # owner request 24.10: the Blackwall taint and the favor of its cult (engine/blackwall.py)
    "crisis_pts",  # owner request 24.10: a contribution to the city crisis (services/crisis.py harvests it)
    "biz_stock", "biz_staff", "biz_cond",  # owner request 24.10: the resources of the business of a story
    "hook", "enmity",  # owner request 24.10: the pressure (services/pressure.py): who holds you, who hates you
)
EFFECT_PLAIN_FLAG = ("infection", "remove_random_cyberware")  # value: true or count >= 1
# `career_clear: true` drops the current career; `career_set:<career key>: true` puts the character
# into that career at rank 1 (replacing any current one); `cyberware_grant:<cyberware key>: true`
# installs the implant for free (no price, no humanity cost, no sale check). Keys are checked by the loader.
# `scan_tolerance: true` tells the character the true cyber tolerance (an honest scan);
# `debt_grace: true` resets the missed weeks of every debt (the creditor agreed to wait).
EFFECT_PLAIN_TRUE = ("career_clear", "scan_tolerance", "debt_grace", "biz_lost", "biz_chain")
EFFECT_RANGED = {"start_combat": (1, 5), "jail_weeks": (1, 52), "vanish_weeks": (1, 52),
                 "eddies_pct": (-90, 90),
                 "possess_sync": (-60, 60), "possess_trust": (-60, 60)}  # value: int in range
# `eddies_pct: -10` takes 10% of the money on hand (owner request: threats to the rich); the `robbery_loss_pct`
# modifier shrinks a loss.
EFFECT_PARAM_INT = ("skill_xp", "rel_npc", "rel_fixer", "faction_rep", "path_xp", "habit")  # habit:<drug key>: N  # `skill_xp:firearms: 1`
# `item_grant:<weapon or armor key>: true` puts the item into the inventory (equipped when the slot is empty);
# `employer:<corp key>: true` names the employer of the career (after `career_set`).
# `vehicle_grant:<vehicle key>: true` gives a ride in clean condition; the old ride goes to a dealer at the sell price.
EFFECT_PARAM_FLAG = (
    "add_npc", "flag_set", "flag_clear", "death", "buff", "career_set", "cyberware_grant", "betray",
    "item_grant", "employer", "queue_event", "vehicle_grant", "npc_lost", "npc_leave",
    "possess", "possess_end",  # owner request 26.10: an AI in the head (engine/possession.py)
    "cyberware_lose",  # owner request 26.10: an implant torn off for good, no humanity back (engine/injury.py)
    "item_take",  # owner request 26.10: the city director takes a weapon or armor away (`item_take:<key>: true`)
)  # `death:collector: true`; `queue_event:<event id>: true` brings that event with the next week (story arcs)
# `later: [{weeks, chance, effects, text, fail_effects, fail_text, hint}]` is a delayed outcome (LaterEntry): the
# week flow rolls it when its week comes. Fights, jail, vanishing and death are not allowed inside it.
EFFECT_LATER = "later"
LATER_FORBIDDEN = ("start_combat", "jail_weeks", "vanish_weeks", "death")
# `faction_rep:<gang or corp key>: N` moves the standing with a faction (clamped to balance.exits.situation.faction_rep);
# `betray:<faction key>: true` marks the faction as cheated (it makes the exits against it harder).
# `vow_break: true` breaks every vow the character holds; `vow_break:<vow key>: true` breaks one.
# `buff:<tarot key>: true` gives the character the card's mods until the next Kyiv midnight.
EFFECT_PARAM_BASES = EFFECT_PARAM_INT + EFFECT_PARAM_FLAG


def split_effect_key(key: str) -> tuple[str, str | None]:
    base, sep, param = key.partition(":")
    return base, (param if sep else None)


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _validate_later(val: Any) -> list[dict[str, Any]]:
    if not isinstance(val, list) or not val:
        raise ValueError("effect 'later': expected a non-empty list of {weeks, chance, effects, text, ...}")
    out: list[dict[str, Any]] = []
    for i, entry in enumerate(val, start=1):
        try:
            rec = LaterEntry.model_validate(entry)
        except ValidationError as e:
            raise ValueError(f"effect 'later' #{i}: {e.errors()[0].get('msg', e)}") from None
        for fx in (rec.effects, rec.fail_effects):
            bad = [k for k in fx if split_effect_key(k)[0] in LATER_FORBIDDEN]
            if bad:
                raise ValueError(f"effect 'later' #{i}: {bad} cannot be delayed (fights, jail and death happen at once)")
        out.append(rec.model_dump())
    return out


def validate_effects(v: dict[str, Any]) -> dict[str, Any]:
    out = dict(v)
    for key, val in v.items():
        base, param = split_effect_key(key)
        if base == EFFECT_LATER:
            if param is not None:
                raise ValueError("effect 'later' takes no parameter")
            out[key] = _validate_later(val)
        elif base == "vow_break":
            if val is not True:
                raise ValueError(f"effect {key!r}: value must be true")
            if param is not None and not KEY_RE.match(param):
                raise ValueError(f"effect {key!r}: parameter must be a vow key (snake_case) or omitted")
        elif base in EFFECT_PLAIN_INT:
            if param is not None or not _is_int(val):
                raise ValueError(f"effect {key!r}: expected plain integer value")
        elif base in EFFECT_PLAIN_TRUE:
            if param is not None or val is not True:
                raise ValueError(f"effect {key!r}: write just '{base}: true'")
        elif base in EFFECT_PLAIN_FLAG:
            if param is not None or not (val is True or (_is_int(val) and val >= 1)):
                raise ValueError(f"effect {key!r}: expected true or a count >= 1")
        elif base in EFFECT_RANGED:
            lo, hi = EFFECT_RANGED[base]
            if param is not None or not _is_int(val) or not lo <= val <= hi:
                raise ValueError(f"effect {key!r}: expected integer {lo}..{hi}")
        elif base in EFFECT_PARAM_BASES:
            if not param:
                raise ValueError(f"effect {key!r}: needs a parameter, like '{base}:<name>'")
            if base == "skill_xp" and param not in SKILLS:
                raise ValueError(f"effect {key!r}: unknown skill {param!r}")
            if base == "rel_npc" and param not in NPC_TAGS:
                raise ValueError(f"effect {key!r}: unknown npc tag {param!r}, use {NPC_TAGS}")
            if base == "possess" and val is not True:  # owner request 26.10
                raise ValueError(f"{key}: write 'possess:<source>: true'")
            if base == "possess_end" and (param not in ("purge", "merge", "takeover", "silence") or val is not True):
                raise ValueError(f"{key}: write 'possess_end:purge|merge|takeover|silence: true'")
            if base == "npc_leave" and (param not in NPC_TAGS or val is not True):  # owner request 25.10: a person leaves
                raise ValueError(f"{key}: needs an npc tag and true")
            if base == "npc_lost" and (param not in NPC_TAGS or val is not True):  # owner request 25.10: a loss for good
                raise ValueError(f"effect {key!r}: write 'npc_lost:<npc tag>: true', tags {NPC_TAGS}")
            if base == "add_npc" and param not in NPC_TEMPLATES:
                raise ValueError(f"effect {key!r}: unknown npc template {param!r}, use {NPC_TEMPLATES}")
            if base == "death" and param not in DEATH_CAUSES:
                raise ValueError(f"effect {key!r}: unknown death cause {param!r}")
            if base in ("rel_fixer", "flag_set", "flag_clear", "buff", "career_set", "cyberware_grant",
                        "faction_rep", "betray", "item_grant", "employer", "queue_event",
                        "vehicle_grant", "path_xp", "item_take") and not KEY_RE.match(param):
                raise ValueError(f"effect {key!r}: parameter must be snake_case")
            if base in EFFECT_PARAM_INT and not _is_int(val):
                raise ValueError(f"effect {key!r}: expected integer value")
            if base in ("flag_set", "flag_clear", "death", "buff", "career_set", "cyberware_grant", "betray",
                        "item_grant", "employer", "queue_event", "vehicle_grant", "item_take") and val is not True:
                raise ValueError(f"effect {key!r}: value must be true")
            if base == "add_npc" and not (val is True or (_is_int(val) and val >= 1)):
                raise ValueError(f"effect {key!r}: expected true or a count >= 1")
        else:
            raise ValueError(f"unknown effect key {key!r}")
    return out


Effects = Annotated[dict[str, int | bool | list[dict[str, Any]]], AfterValidator(validate_effects)]


class LaterEntry(Strict):
    """A delayed outcome (effect `later`): in `weeks` weeks of the character's life the week flow rolls `chance`;
    a pass applies `effects` and shows `text`, a miss applies `fail_effects` and shows `fail_text`. `hint` is the line
    the outcome screen shows meanwhile («через 4 недели: <hint>»). Effects may hold another `later` (a payout chain)."""

    weeks: int = Field(ge=1, le=104)
    chance: Pct = 1.0
    effects: Effects = {}
    text: str = Field(min_length=1)
    fail_effects: Effects = {}
    fail_text: str | None = None
    hint: str | None = None

    @model_validator(mode="after")
    def _fail_side(self) -> "LaterEntry":
        if self.chance < 1.0 and not self.fail_text:
            raise ValueError("a later entry with chance below 1 needs fail_text (what the player reads when it burns)")
        return self


# ---------------------------------------------------------------------------
# Modifiers (`mods`)
# ---------------------------------------------------------------------------

MOD_GRAMMAR_HELP = (
    f"allowed keys: plain {list(MOD_PLAIN_KEYS)}; "
    f"stat.<{'|'.join(STATS)}>; skill.<{'|'.join(SKILLS)}>; "
    f"xp.<skill|all>; check.<{'|'.join(MOD_CHECK_CONTEXTS)}>; "
    "salary_pct.<career key from careers.yaml|all>; "
    f"active.<exclusive_group from cyberware|all>.<{'|'.join(MOD_ACTIVE_FIELDS)}>"
)


def parse_mod_key(key: str) -> tuple[str, tuple[str, ...]]:
    """Split a mod key into (base, params) and check everything the schema can check.

    Careers (`salary_pct.<career>`) and exclusive groups (`active.<group>.<field>`)
    are validated against the catalogs by the loader, not here.
    Raises ValueError; the message names the bad key and says what is allowed.
    """
    parts = key.split(".")
    base = parts[0]
    if base in MOD_PLAIN_KEYS:
        if len(parts) != 1:
            raise ValueError(f"mod key {key!r}: {base!r} takes no parameter, write just '{base}'")
        return base, ()
    if base not in MOD_PARAM_BASES:
        raise ValueError(f"unknown mod key {key!r}; {MOD_GRAMMAR_HELP}")
    if base == "active":
        if len(parts) != 3 or not parts[1] or not parts[2]:
            raise ValueError(
                f"mod key {key!r}: expected 'active.<group|all>.<field>', "
                f"field is one of {list(MOD_ACTIVE_FIELDS)}"
            )
        if parts[1] != "all" and not KEY_RE.match(parts[1]):
            raise ValueError(f"mod key {key!r}: group {parts[1]!r} must be snake_case or 'all'")
        if parts[2] not in MOD_ACTIVE_FIELDS:
            raise ValueError(
                f"mod key {key!r}: unknown active field {parts[2]!r}, "
                f"allowed fields: {list(MOD_ACTIVE_FIELDS)}"
            )
        return base, (parts[1], parts[2])
    if len(parts) != 2 or not parts[1]:
        raise ValueError(f"mod key {key!r}: expected '{base}.<name>', one dot and a name")
    name = parts[1]
    if base == "stat" and name not in STATS:
        raise ValueError(f"mod key {key!r}: unknown stat {name!r}, allowed: {list(STATS)} (uppercase)")
    if base == "skill" and name not in SKILLS:
        raise ValueError(f"mod key {key!r}: unknown skill {name!r}, allowed: {list(SKILLS)}")
    if base == "xp" and name != "all" and name not in SKILLS:
        raise ValueError(f"mod key {key!r}: unknown skill {name!r}, allowed: {list(SKILLS)} or 'all'")
    if base == "check" and name not in MOD_CHECK_CONTEXTS:
        raise ValueError(
            f"mod key {key!r}: unknown check context {name!r}, allowed: {list(MOD_CHECK_CONTEXTS)}"
        )
    if base == "salary_pct" and name != "all" and not KEY_RE.match(name):
        raise ValueError(f"mod key {key!r}: career {name!r} must be a careers.yaml key or 'all'")
    return base, (name,)


def validate_mods(v: Any) -> dict[str, int]:
    if v is None:
        return {}
    if not isinstance(v, dict):
        raise ValueError("mods must be a mapping like {skill.melee: 1, armor: 4}")
    errors: list[str] = []
    for key, val in v.items():
        if not isinstance(key, str):
            errors.append(f"mod key {key!r} must be a string")
            continue
        try:
            parse_mod_key(key)
        except ValueError as e:
            errors.append(str(e))
            continue
        if not _is_int(val):
            errors.append(f"mod {key!r}: value must be an integer, got {val!r}")
        elif val == 0:
            errors.append(f"mod {key!r}: value 0 does nothing, remove the key")
        elif abs(val) > MOD_VALUE_LIMIT:
            errors.append(f"mod {key!r}: value {val} is outside -{MOD_VALUE_LIMIT}..{MOD_VALUE_LIMIT}")
    if errors:
        raise ValueError(" | ".join(errors))
    return v


# Sum of bonuses; the engine adds the mods of everything the character wears or has.
Mods = Annotated[dict[str, int], BeforeValidator(validate_mods)]


# ---------------------------------------------------------------------------
# World effects (`timeline.yaml` effects, Claude-written world events)
# ---------------------------------------------------------------------------

WORLD_PRICE_TARGETS: tuple[str, ...] = (*CYBER_CATEGORIES, "weapons", "housing")
WORLD_MULT_PLAIN: tuple[str, ...] = ("heat_mult", "gig_pay_mult", "coin_shock")
WORLD_EFFECT_HELP = (
    "allowed forms: "
    f"price_mult.<{'|'.join(WORLD_PRICE_TARGETS)}>: <number > 0>; "
    "district_danger.<district key>: <integer change, not 0>; "
    "gang_war.<gang key>: <weeks, integer >= 1>; "
    "faction_power.<gang or corp key>: <integer change, not 0>; "
    "unlock.<item key>: true; ban.<item key>: true; "
    "heat_mult: <number > 0>; gig_pay_mult: <number > 0>; "
    "coin_shock: <multiplier > 0 applied once to the Eddy-crypto rate>; "
    f"event_weight.<{'|'.join(EVENT_CATEGORIES)}>: <number > 0>; "
    "district_open.<district key>: true; district_close.<district key>: true; "
    "era.<era key>: true. "
    "Item keys come from cyberware, quickhacks, weapons, armor; numeric limits are in "
    "balance.yaml world_effects"
)

WorldEffectValue = int | float | bool


def parse_world_effect_key(key: str) -> tuple[str, str | None]:
    """Split a world-effect key into (base, param). Raises ValueError listing the allowed forms."""
    base, sep, param = key.partition(".")
    param_bases = {
        "price_mult", "district_danger", "gang_war", "faction_power", "unlock", "ban",
        "event_weight", "district_open", "district_close", "era",
    }
    if base in WORLD_MULT_PLAIN:
        if sep:
            raise ValueError(f"world effect {key!r}: {base!r} takes no parameter, write just '{base}'")
        return base, None
    if base not in param_bases:
        raise ValueError(f"unknown world effect key {key!r}; {WORLD_EFFECT_HELP}")
    if not sep or not param or "." in param:
        raise ValueError(f"world effect {key!r}: expected '{base}.<name>' with one dot and a name; {WORLD_EFFECT_HELP}")
    if base == "price_mult" and param not in WORLD_PRICE_TARGETS:
        raise ValueError(
            f"world effect {key!r}: unknown price target {param!r}, allowed: {list(WORLD_PRICE_TARGETS)}"
        )
    if base == "event_weight" and param not in EVENT_CATEGORIES:
        raise ValueError(
            f"world effect {key!r}: unknown event category {param!r}, allowed: {list(EVENT_CATEGORIES)}"
        )
    if base not in ("price_mult", "event_weight") and not KEY_RE.match(param):
        raise ValueError(f"world effect {key!r}: {param!r} must be a snake_case content key")
    return base, param


def validate_world_effects(v: Any) -> dict[str, WorldEffectValue]:
    """Grammar and value types. Catalog keys and corridors are checked by the loader."""
    if v is None:
        return {}
    if not isinstance(v, dict):
        raise ValueError("effects must be a mapping like {price_mult.combat: 0.8, ban.sandevistan_apogee: true}")
    errors: list[str] = []
    for key, val in v.items():
        if not isinstance(key, str):
            errors.append(f"world effect key {key!r} must be a string")
            continue
        try:
            base, _ = parse_world_effect_key(key)
        except ValueError as e:
            errors.append(str(e))
            continue
        if base in ("unlock", "ban", "district_open", "district_close", "era"):
            if val is not True:
                errors.append(f"world effect {key!r}: value must be true, got {val!r}")
        elif base in ("district_danger", "faction_power"):
            if not _is_int(val) or val == 0:
                errors.append(f"world effect {key!r}: value must be a non-zero integer change, got {val!r}")
        elif base == "gang_war":
            if not _is_int(val) or val < 1:
                errors.append(f"world effect {key!r}: value must be a number of weeks, integer >= 1, got {val!r}")
        else:  # price_mult, event_weight, heat_mult, gig_pay_mult, coin_shock
            if isinstance(val, bool) or not isinstance(val, (int, float)) or val <= 0:
                errors.append(f"world effect {key!r}: value must be a multiplier > 0 (like 0.8 or 1.5), got {val!r}")
    if errors:
        raise ValueError(" | ".join(errors))
    return v


WorldEffects = Annotated[dict[str, WorldEffectValue], BeforeValidator(validate_world_effects)]


# ---------------------------------------------------------------------------
# balance.yaml
# ---------------------------------------------------------------------------


class TimeBalance(Strict):
    ap_per_week: int = Field(ge=1)
    weeks_per_year: int = Field(ge=1)
    # owner request: one press of «Прожить» lives this many weeks (AP and events of all of them at once)
    weeks_per_press: int = Field(default=1, ge=1, le=12)
    global_tick_hour: int = Field(ge=0, le=23)  # Kyiv time


class ParentSurvival(Strict):
    """Chance that a parent is alive at the start: falls with the parent's age, shaped by the origin."""

    base: Pct  # chance while the parent is not older than decline_from_age
    decline_from_age: int
    decline_per_year: Pct  # subtracted from the chance per year above decline_from_age
    min_chance: Pct  # the chance never falls below this (before origin_mult)
    origin_mult: dict[Key, float] = {}  # origin key -> multiplier of the chance (refugee: higher loss)
    orphan_origins: list[Key] = []  # origin keys whose parents are always lost (dead or unknown)
    orphan_unknown_chance: Pct  # of a lost parent of an orphan: share whose fate is unknown, not dead

    @model_validator(mode="after")
    def _check(self) -> "ParentSurvival":
        for k, m in self.origin_mult.items():
            if not 0.0 <= m <= 1.0:
                raise ValueError(f"origin_mult[{k!r}] = {m}: expected 0..1")
        return self


class CharacterBalance(Strict):
    start_eddies: int
    start_housing: Key  # housing key
    age_min: int
    age_max: int
    stat_min: int
    stat_max: int  # clamp at generation
    stat_cap: int  # hard cap for growth
    stat_spread: int  # random +-spread around the role template
    skill_start_max: int
    skill_cap: int
    hp_base: int
    hp_per_body: int
    heavy_wound_ratio: Pct  # below this share of max hp is a heavy wound
    heavy_wound_penalty: int
    humanity_per_emp: int  # starting humanity = EMP * this
    mood_start: int
    mood_min: int
    mood_max: int
    street_cred_max: int
    heat_max: int
    siblings_max: int
    parent_age_gap_min: int  # a parent is this many years older than the character (min..max)
    parent_age_gap_max: int
    parent_survival: ParentSurvival
    family_rel_min: int
    family_rel_max: int
    role_offer_count: int
    rerolls: int
    origin_hint_chance: Pct  # chance that the preset's origin_hint is used when the origin is random
    rare_origin_weight: float = Field(gt=0)  # draw weight of chrome_free_start origins (others weigh 1)
    enemy_start_rep: int  # faction_rep with the origin's enemy gang at the start (negative)
    name_length_min: int
    name_length_max: int
    # owner request: Night City is a city of vices. A new character may start hooked on a drug or on braindance.
    vice_chance: float = Field(default=0.3, ge=0, le=1)
    vice_drug_share: float = Field(default=0.6, ge=0, le=1)   # of the vices: a drug (else braindance)
    vice_drug_points: tuple[int, int] = (30, 55)               # habit points of the starting drug
    vice_braindance: tuple[int, int] = (5, 9)                  # braindance addiction of the starting habit


class SkillsBalance(Strict):
    xp_base: int  # xp to reach level L+1 = xp_base + xp_step * L
    xp_step: int
    gig_success_xp: int


class LuckBalance(Strict):
    glitch_cap: int
    glitch_die: int
    glitch_weekly_offset: int  # weekly glitch gain = d(glitch_die) + offset
    past_life_bonus: int  # per past life in the lineage (start-from-scratch option)
    past_life_bonus_cap: int
    luck_per_point: int  # check bonus per spent luck point
    death_glitch_uses_per_life: int


class ChecksBalance(Strict):
    die_sides: int
    crit_high: int  # natural roll that adds another die
    crit_low: int  # natural roll that subtracts another die
    complication_margin: int  # a pass by less than this counts as a complication
    fail_ratio: Pct  # more than this share of failed checks is a fail
    dv_simple: int
    dv_normal: int
    dv_hard: int
    dv_professional: int
    dv_heroic: int
    dv_impossible: int


class GigOutcomeBalance(Strict):
    pay_pct: int
    hp_loss_per_tier: int  # hp loss = this * gig tier
    heat: int
    street_cred: int
    fixer_rel: int


class GigsBalance(Strict):
    dv_by_tier: dict[int, int]
    pay_by_tier: dict[int, tuple[int, int]]
    unlock_cred_by_tier: dict[int, int]
    # owner request 29.10: the ladder goes on above the street cap of 50: contracts (tiers 6-9) open with a rank
    # (flags["gig_rank"], no cap) on top of the street cred
    unlock_rank_by_tier: dict[int, int]
    rank_pts_by_tier: dict[int, int]  # rank points of a clean gig of the tier
    rank_partial_pct: int = Field(default=50, ge=0, le=100)  # share of the points for a complicated gig
    rank_pay_pct: float = Field(default=0.5, ge=0)  # +% pay per rank point above the tier's rank threshold ...
    rank_pay_cap_pct: int = Field(default=50, ge=0)  # ... up to this
    deposit_min_tier: int = Field(default=6, ge=1, le=9)  # contracts of this tier and above take a deposit
    deposit_pct: int = Field(default=10, ge=0, le=100)  # of the pay; comes back on a clean or complicated result
    tier_weight_decay: float = Field(default=0.75, gt=0, le=1)  # board weight of a tier per step below the top one
    first_gig_below_top: int = Field(default=3, ge=0)  # the always-offered first gig is `top - this` (not below tier 1)
    elite_min_tier: int = Field(default=6, ge=1, le=9)  # with this tier open, one gig of the board is of the top tiers
    fight_extra_by_tier: dict[int, int] = {}  # extra enemies in the fight of a loud gig above tier 5
    hp_loss_tier_cap: int = Field(default=6, ge=1)  # hp loss per tier counts at most this many tiers
    legendary_fixer_min_tier: int = Field(default=5, ge=1, le=9)  # the district's legendary fixer offers these
    # owner request 23.10: pay grows slowly with the street cred above the tier's unlock (% a point, capped)
    cred_pay_pct: float = 0.0
    cred_pay_cap_pct: int = 100
    board_min: int
    board_max: int
    locked_shown: int
    board_refresh_weeks: int = Field(default=4, ge=1)  # the personal board is made anew every N weeks of the character's own time
    empty_district_max_tier: int  # board tiers when no players are in the district
    checks_min: int
    checks_max: int
    outcomes: dict[OutcomeTier, GigOutcomeBalance]
    fixer_rel_start: int
    fixer_rel_high: int  # at or above: pay bonus
    fixer_rel_low: int  # at or below: pay penalty
    fixer_rel_pay_pct: int
    gang_war_pay_bonus_pct: int
    public_min_tier: int
    loud_extra_heat: int
    ap_cost: int = Field(ge=0)  # AP spent when the approach is chosen
    pay_round: int = Field(ge=1)  # pay is rolled in the tier range and rounded to this step
    faction_rep: dict[Approach, int]  # standing with the target faction after a clean / complicated gig, times tier
    after_fight: dict[str, str]  # how a loud gig's fight changes the outcome: result of the fight -> tier or "keep"

    _t1 = field_validator("dv_by_tier", "pay_by_tier", "unlock_cred_by_tier", "unlock_rank_by_tier",
                          "rank_pts_by_tier")(_check_gig_tiers)

    @field_validator("fight_extra_by_tier")
    @classmethod
    def _extra_tiers(cls, v: dict[int, int]) -> dict[int, int]:
        if any(t not in GIG_TIERS or n < 0 for t, n in v.items()):
            raise ValueError("fight_extra_by_tier: keys are gig tiers 1-9, values are not negative")
        return v

    @field_validator("faction_rep")
    @classmethod
    def _rep_approaches(cls, v: dict[str, int]) -> dict[str, int]:
        if set(v) != set(APPROACHES):
            raise ValueError(f"faction_rep must cover exactly {APPROACHES}")
        return v

    @field_validator("after_fight")
    @classmethod
    def _after_fight(cls, v: dict[str, str]) -> dict[str, str]:
        if set(v) != set(FIGHT_RESULTS):
            raise ValueError(f"after_fight must cover exactly {FIGHT_RESULTS}")
        for k, val in v.items():
            if val != "keep" and val not in OUTCOME_TIERS:
                raise ValueError(f"after_fight.{k}: expected 'keep' or one of {OUTCOME_TIERS}")
        return v

    @field_validator("outcomes")
    @classmethod
    def _all_outcomes(cls, v: dict[str, Any]) -> dict[str, Any]:
        if set(v) != set(OUTCOME_TIERS):
            raise ValueError(f"outcomes must cover exactly {OUTCOME_TIERS}")
        return v


class DebtBalance(Strict):
    start_enabled: bool = True  # owner request 30.10: False = a new character starts with no debt
    start_min: int
    start_max: int
    weekly_rate: float = Field(gt=0, le=0.05)  # share of principal added per week
    min_payment_pct: float = Field(gt=0, le=1)  # minimum weekly payment, share of principal
    min_payment_floor: int = Field(default=100, ge=1)  # ... but never less than this (or the whole rest of the debt)
    # Startup migration (services/debtfix.py): debts stored with a weekly rate above this are moved to `weekly_rate`
    # and the interest they gathered is recomputed at the new rate.
    legacy_rate_above: float = Field(default=0.02, gt=0)
    collector_missed_weeks: int
    principal_step: int = Field(ge=1)  # the starting principal is rounded to this step
    overdraft_creditor: str  # who is owed when the eddies go negative and the character has no debt
    collector_event: Key  # events*.yaml id queued when a debt has been missed collector_missed_weeks times


class HousingBalance(Strict):
    move_deposit_weeks: int  # deposit = this many weeks of new rent
    street_food: int = Field(ge=0)  # weekly food when the character has no housing (a district without rentals)


class TrainingBalance(Strict):
    ap_cost: int
    cost: int
    xp: int
    mood: int
    skills: list[str] = Field(default_factory=list)  # the skills this activity can raise; empty = every skill

    @field_validator("skills")
    @classmethod
    def _known_skills(cls, v: list[str]) -> list[str]:
        bad = [s for s in v if s not in SKILLS]
        if bad:
            raise ValueError(f"unknown skills: {bad}")
        return v


class BarBalance(Strict):
    ap_cost: int
    cost: int
    mood: int
    event_chance: Pct
    meet_partner_chance: Pct
    event_categories: list[EventCategory]  # the bar event is drawn from events of these categories


class BraindanceBalance(Strict):
    ap_cost: int
    cost: int
    mood: int
    addiction_gain: int
    addiction_event_threshold: int
    overdose_threshold: int
    addiction_decay_per_week: int
    overdose_chance: Pct  # per braindance at or above overdose_threshold: death by overdose


class TherapyBalance(Strict):
    ap_cost: int
    cost: int
    humanity_dice: Dice


class RestBalance(Strict):
    ap_cost: int
    cost: int
    mood: int
    hp: int


class GambleBalance(Strict):
    ap_cost: int
    stakes: list[int]
    check_stat: Stat
    check_dv: int
    win_mult: float


class MeditationBalance(Strict):
    """Path activity (DESIGN.md 7a): a calm check, mood, humanity for the ones who came back from chrome, laying low."""

    ap_cost: int
    cost: int
    check: Check
    mood: int
    fail_mood: int  # mood on a failed check
    humanity: int  # restored on success (a clean body ignores it: its humanity is always full)
    heat_drop: int  # wanted level dropped on success ("lay low")


class MartialArtsBalance(Strict):
    ap_cost: int
    cost: int
    skill: Skill
    xp: int
    mood: int


class ServiceBalance(Strict):
    """Community service: relations, fame, a little money."""

    ap_cost: int
    cost: int
    eddies: int
    fame: int
    mood: int
    rel: int  # rel_npc:random


class ActivitiesBalance(Strict):
    training: TrainingBalance
    study: TrainingBalance
    bar: BarBalance
    braindance: BraindanceBalance
    therapy: TherapyBalance
    rest: RestBalance
    gamble: GambleBalance
    meditation: MeditationBalance
    martial_arts: MartialArtsBalance
    service: ServiceBalance


class PropertyBalance(Strict):
    """AP prices of the property section (PLAN 4.6); 0 = free."""

    buy_ap: int = Field(ge=0)  # buying a weapon or armor
    housing_ap: int = Field(ge=0)  # renting other housing in the same district
    move_ap: int = Field(ge=0)  # moving to another district
    trauma_ap: int = Field(ge=0)  # a Trauma Team subscription change


class ToleranceEntry(Strict):
    key: ToleranceType
    name: str
    min: int  # d100 range, 00 counts as 100
    max: int
    mult: float  # humanity-loss multiplier


class FullConversionBalance(Strict):
    emp_fixed: int
    family_rel: int
    # owner request 27.10: the underground conversion for a body that is not a smasher's (borg_risk implants)
    black_death: dict[str, float] = Field(default_factory=lambda: {
        "fragile": 0.7, "normal": 0.45, "resistant": 0.3, "ironclad": 0.2})
    black_body_cut: float = 0.03   # every BODY point above 5 takes this off the chance to die on the table
    black_min: float = 0.05


class ActiveBalance(Strict):
    recharge_per_ap: int = Field(ge=0, le=100)  # charge restored per AP spent outside combat
    full_recharge_on_week: bool
    overload_humanity_threshold: int  # activation at or below this humanity costs humanity
    overload_humanity_cost: int


class CyberBalance(Strict):
    tolerance: list[ToleranceEntry]
    # owner request 23.10: a strong body holds chrome better. Per point of BODY above `body_base` the humanity loss and
    # the chrome load fall by `body_pct`% (below it they grow), clamped to body_min..body_max.
    # owner request 24.10: the netrunner's gear (neural port, decks, RAM, Blackwall scripts) takes no humanity
    netrun_humanity_free: bool = True
    body_base: int = 5
    body_pct: int = 6
    body_min: float = 0.4
    body_max: float = 1.4
    tech_base: int = 5   # owner request 24.10: TECH helps the body hold chrome too
    tech_pct: int = 4
    humanity_per_emp_loss: int  # every this many lost humanity = EMP -1
    scan_cost: int
    removal_cost_pct: Pct  # of the implant price
    removal_return_pct: Pct  # of the implant humanity cost
    complication_loss_mult: float
    complication_malfunction_chance: Pct  # else the extra humanity loss applies
    infection_damage_dice: Dice
    infection_cure_cost: int
    infection_cure_check: Check
    warn_humanity_low: int
    warn_humanity_critical: int
    second_heart_revive_hp_pct: Pct
    full_conversion: FullConversionBalance
    borg_chrome_implants: int = 30  # owner request 27.10: this many implants make a borg without a full conversion
    borg_sweep: bool = False        # the scheduler announces the chrome borgs already in the city (on once the picture is in)
    active: ActiveBalance

    @field_validator("tolerance")
    @classmethod
    def _tolerance_table(cls, v: list[ToleranceEntry]) -> list[ToleranceEntry]:
        if [e.key for e in v] != list(TOLERANCE_TYPES):
            raise ValueError(f"tolerance rows must be in this order: {TOLERANCE_TYPES}")
        pos = 1
        for e in v:
            if e.min != pos or e.max < e.min:
                raise ValueError(f"tolerance row {e.key!r}: d100 ranges must be contiguous from 1")
            pos = e.max + 1
        if pos != 101:
            raise ValueError("tolerance ranges must end at 100")
        return v



class TraumaTierBalance(Strict):
    name: str
    weekly_price: int
    evac_price: int
    rescue_chance: Pct


class DeathBalance(Strict):
    save_die: int  # survive when d(save_die) < BODY
    save_penalty_per_repeat: int  # minus this for every earlier death save in the same week
    save_survive_hp: int = Field(ge=1)  # hp of a character who passed the save ("lying at death's door")
    evac_hp_pct: Pct  # hp after a Trauma Team evacuation or the Second Heart, share of the maximum
    evac_in_jail: bool  # Trauma Team comes into a cell too
    collector_kill_weeks: int = Field(ge=1)  # missed weeks of one debt after which the collector may kill
    collector_kill_chance: Pct  # per week from then on (never inside a cell)
    old_age_start: int
    old_age_chance: dict[int, Pct]  # age -> chance of death on the birthday roll

    @field_validator("old_age_chance")
    @classmethod
    def _curve(cls, v: dict[int, float]) -> dict[int, float]:
        ages = list(v)
        if not ages or ages != sorted(ages):
            raise ValueError("old_age_chance ages must be non-empty and ascending")
        vals = [v[a] for a in ages]
        if vals != sorted(vals):
            raise ValueError("old_age_chance must not decrease with age")
        return v

    @model_validator(mode="after")
    def _starts_at(self) -> "DeathBalance":
        if min(self.old_age_chance) != self.old_age_start:
            raise ValueError("first old_age_chance age must equal old_age_start")
        return self


class InheritBalance(Strict):
    money_pct: Pct
    enemy_rep_threshold: int = Field(le=0)  # a gang standing at or below this passes to the heir as an enemy
    debt_pct: Pct
    street_cred_pct: Pct
    keep_housing: bool
    keep_family_rel: bool
    keep_gang_enemies: bool


class ProtegeInheritBalance(Strict):
    street_cred_pct: Pct
    skill_penalty: int
    skills_count: int
    keep_fixer_rel: bool


class EngramBalance(Strict):
    price: int
    skills_pct: Pct
    humanity_penalty: int
    conflict_event_every_weeks: int
    conflict_event: Key  # events*.yaml id forced every conflict_event_every_weeks
    available_from_year: int  # Soulkiller 2.5 (2022): Secure Your Soul is sold from this world year


class ScratchBalance(Strict):
    glitch_bonus_per_life: int
    glitch_bonus_cap: int


class LegacyBoon(Strict):
    """Owner request 24.10: a gift of the dead life to the next one, picked from a few on offer after a death."""
    name: str
    text: str
    achievements: list[str] = []     # offered when the main achievement of the life is one of these (empty: any)
    causes: list[str] = []           # ... or the cause of death is one of these (empty: any)
    min_lives: int = 0               # lives the lineage has ended
    weight: int = 1
    stats: dict[str, int] = {}       # added to the heir's stats (capped at 10)
    effects: dict[str, Any] = {}     # apply_effects on the heir
    dead_eddies_pct: int = 0         # a share of the money the dead character had


class LegacyBalance(Strict):
    heir_min_age: int
    heir_max_age: int  # an older relative is not offered as an heir
    lineage_list_max: int = Field(ge=1)  # lives shown by /lineage
    # the "main achievement" on the grave: a fact whose value / scale is the biggest wins; below achievement_min
    # of the scale nothing stands out. Facts: gigs, fights, street_cred, fame, status, career_rank, children, years, wealth.
    achievement_scale: dict[str, int]
    achievement_min: Pct
    inherit: InheritBalance
    protege: ProtegeInheritBalance
    engram: EngramBalance
    scratch: ScratchBalance
    boon_choices: int = 3            # gifts on offer after a death (one more from the third life)
    boons: dict[str, LegacyBoon] = Field(default_factory=dict)


class UniqueCorridorBalance(Strict):
    eddies_pct: Pct  # of current eddies
    eddies_floor: int  # minimum absolute corridor
    hp_abs: int
    humanity_abs: int
    mood_abs: int
    street_cred_abs: int
    heat_abs: int


class UniqueEventBalance(Strict):
    chance: Pct
    max_per_week: int
    corridor: UniqueCorridorBalance
    dv_min: int = Field(default=9, ge=5, le=30)  # a check written by Claude is kept inside dv_min..dv_max
    dv_max: int = Field(default=17, ge=5, le=30)
    skill_xp_max: int = Field(default=2, ge=0)  # skill_xp:<skill> of one outcome


class EventWeightsBalance(Strict):
    district_match_mult: float
    role_match_mult: float


class EventsBalance(Strict):
    max_per_press: int = 5  # owner request 24.10: at most this many events after one «Прожить месяц»
    guaranteed: int
    extra_chances: list[Pct]  # chance of the 2nd, 3rd, ... event
    recent_memory: int = Field(ge=0)  # the last N shown events are skipped while others fit
    combat_stub_damage: Dice  # until M5: a fight started by an event costs this much hp
    unique: UniqueEventBalance
    weights: EventWeightsBalance


class CrimeActionBalance(Strict):
    check: Check
    reward_min: int
    reward_max: int
    heat_success: int
    heat_fail: int
    arrest_chance_on_fail: Pct
    jail_weeks_min: int
    jail_weeks_max: int


class CrimeBalance(Strict):
    pickpocket: CrimeActionBalance
    car_theft: CrimeActionBalance
    store_robbery: CrimeActionBalance
    assault: CrimeActionBalance
    raid_min_heat: int
    raid_heat_offset: int  # raid chance = raid_chance_per_heat * (heat - offset)
    raid_chance_per_heat: Pct
    maxtac_heat: int
    maxtac_danger: int
    heat_decay_per_week: int
    wanted_publish_heat: int
    ap: int
    crit_fail_arrest_mult: float
    fail_damage: dict[str, Dice] = {}  # crime kind -> hp lost on a failure
    raid_danger_offset: int
    raid_danger_max: int
    raid_won_heat: int
    raid_avoid_heat: int


class JailEscapeBalance(Strict):
    check: Check
    ap: int = Field(ge=0)
    fail_extra_weeks: int
    fail_damage_dice: Dice
    success_heat: int = Field(ge=0)  # the escapee's heat is raised to this


class JailLawyerBalance(Strict):
    min_relation: int = Field(ge=0, le=100)  # a contact or a friend this close
    cost_per_week: int = Field(ge=0)
    cut_pct: Pct
    min_cut: int = Field(ge=1)


class LoanLender(Strict):
    """Owner request 24.10: borrow from services, fixers and people. Each lender has its own sums, price and risk."""
    name: str
    text: str
    kind: CreditorKind = "other"
    base: int                       # the sum anyone who qualifies can take
    per_status: int = 0             # more for every point of status
    per_cred: int = 0               # more for every point of street cred
    per_salary: int = 0             # weeks of salary (a corp advance)
    per_relation: int = 0           # more for every point of relation (a friend)
    max: int
    weekly_rate: float
    status_min: int = 0
    cred_min: int = 0
    needs_job: bool = False
    friend_min: int = 0             # a friend or family member at least this close
    risk_weeks: int = 2             # missed weeks before the lender acts (and again every as many weeks)
    risk_effects: dict[str, Any] = Field(default_factory=dict)
    risk_text: str
    relation_hit: int = 0           # a friend's loan unpaid: the relation drops


class JailRank(Strict):
    """Owner request 24.10: the hierarchy of the cell block."""
    min: int                     # respect from which the rank holds
    name: str
    text: str
    job_pct: int = 0             # prison jobs pay more
    fight_respect: int = 0       # extra respect for a won yard fight
    escape: int = 0              # the escape dv gets easier
    mood_week: int = 0           # per week in the cell
    income_week: int = 0         # the block's pot pays the rank every week
    release_cred: int = 0        # street cred when the term is over
    riot: bool = False           # may start a riot


class JailJob(Strict):
    name: str
    text: str
    fail_text: str = ""
    ap: int = 1
    eddies: int
    stat: str | None = None      # None: no check
    skill: str | None = None
    dv: int = 12
    respect: int = 1
    mood: int = 0
    humanity: int = 0
    fail_hp: int = 0
    fail_weeks: int = 0
    fail_respect: int = 0
    kind: Literal["job", "pastime"] = "job"   # owner request 24.10: paid work, or something to do in the cell
    effects: dict[str, Any] = {}               # extra effects on success (skill xp and the like)


class JailGang(Strict):
    name: str
    text: str
    escape: int = 0
    job_pct: int = 0
    fight_respect: int = 0
    riot: int = 0                # the riot checks get easier


class JailRiotBalance(Strict):
    ap: int = 2
    raise_dv: int = 15           # COOL + persuasion: raise the block
    post_dv: int = 15            # BODY + melee: take the guard post
    demand_dv: int = 14          # COOL + persuasion: talk to the warden
    per_player: int = 2          # every other player of the gang in the cells makes it easier
    fail_weeks: int = 8
    fail_hp: int = 15
    fail_respect: int = -5
    demand_cut_pct: int = 50
    demand_respect: int = 10
    break_heat: int = 5
    wreck_respect: int = 15
    wreck_weeks: int = 6
    wreck_cred: int = 3
    ally_respect: int = 3        # players of the gang in the cells when the riot wins


class JailBalance(Strict):
    min_weeks: int
    max_weeks: int
    maxtac_surrender_min: int
    maxtac_surrender_max: int
    weeks_per_press: int
    arrest_weeks_per_heat: int
    fight_danger: int
    fight_ap: int = Field(ge=0)
    fight_cred: int = Field(ge=0)
    job_loss_weeks: int = Field(ge=1)  # a sentence this long costs the job
    release_heat_cap: int = Field(ge=0)
    event_chance: Pct
    escape: JailEscapeBalance
    lawyer: JailLawyerBalance
    free_training: bool = True   # owner request 24.10: training and study cost nothing in a cell
    respect_fight_win: int = 3
    respect_fight_loss: int = -1
    gang_join_respect: int = 5
    gang_leave_respect: int = -5
    ranks: list[JailRank] = Field(default_factory=list)
    jobs: dict[str, JailJob] = Field(default_factory=dict)
    gangs: dict[str, JailGang] = Field(default_factory=dict)
    riot: JailRiotBalance = Field(default_factory=JailRiotBalance)


class CombatRepBalance(Strict):
    """Standing with a faction (faction_rep, -50..50) after a fight or an exit."""

    kill: int  # per enemy of the faction killed (negative)
    takedown: int  # per enemy knocked out
    fight_floor: int  # the loss of one fight stops here (negative)
    bribe_ok: int  # a bribe worked
    talk_ok: int  # a talk worked
    betray: int  # extra loss when the character fights a faction that was at peace with him
    truce_weeks: int  # weeks a bribe or a talk counts as a truce; a fight in that time is a betrayal


class BlackwallWeapon(Strict):
    """Owner request 23.10: a Blackwall weapon is a dreadful power with a price."""

    echo_pct: int = 50          # a hit jumps to another enemy for this % of the damage, through the armor
    fear: float = 0.35          # each other enemy: chance to flee in terror after a hit
    stun: int = 1               # the struck enemy loses this many ticks (static in the nerves)


class BlackwallCost(Strict):
    humanity: int = 2           # after a fight where it struck
    glitch_chance: float = 0.3
    trace_per_fight: int = 1
    hunt_at: int = 3            # this much trace: NetWatch (or MaxTac) comes
    hunt_danger: int = 4
    hunt_factions: list[str] = Field(default_factory=lambda: ["netwatch", "maxtac"])


class ReinforceNpc(Strict):
    name: str
    text: str
    price_per_danger: int
    wait: int                 # turns until they arrive
    count: int = 1
    danger_add: int = 0       # their danger over the danger of the fight
    requires_flag: str | None = None   # only characters with this flag can call them (Raven Tearz)


class ReinforceBalance(Strict):
    """Owner request 24.10: call help into a fight: NPCs for money, or another player for the price you set."""
    npc: dict[str, ReinforceNpc] = Field(default_factory=dict)
    player_prices: list[int] = Field(default_factory=lambda: [500, 2000, 5000])
    player_wait: int = 3


class QteBalance(Strict):
    """Quick reactions of the Mini App (owner request 30.10): a tap in time makes the strike of the tick hit harder."""

    enabled: bool = True
    good_pct: int = Field(default=5, ge=0, le=50)
    perfect_pct: int = Field(default=15, ge=0, le=100)


class CombatBalance(Strict):
    qte: QteBalance = Field(default_factory=QteBalance)
    ally_target_chance: float = Field(default=0.4, ge=0, le=1)  # owner request 24.10: an enemy shoots a squad member
    # owner request 24.10: med charges that come back during the fight, as in the game; full again for the next fight
    med_charges: int = 2
    dual_penalty: int = 3            # owner request 24.10: the second hand strikes at this penalty without the perk
    med_heal_pct: int = 35           # of the max hp
    med_recharge_ticks: int = 3      # a used charge comes back after this many turns
    reinforce: ReinforceBalance = Field(default_factory=ReinforceBalance)
    deck_builtin_hacks: list[str] = Field(default_factory=list)  # every cyberdeck comes with these programs
    blackwall: dict[str, BlackwallWeapon] = Field(default_factory=dict)   # weapon key -> its power
    blackwall_cost: BlackwallCost = Field(default_factory=BlackwallCost)
    max_rounds: int
    enemy_hp_base: int
    enemy_hp_per_danger: int
    hit_dv_base: int
    hit_dv_per_danger: int
    enemy_attack_base: int
    enemy_attack_per_danger: int
    player_defense_base: int
    cover_bonus: int
    flee_dv_base: int
    flee_dv_per_danger: int
    unarmed_damage: Dice
    enemy_damage_by_danger: dict[int, Dice]
    enemy_count_by_danger: dict[int, Dice]
    morale_break_fraction: Pct  # when this share of the pack is down, the rest may flee
    morale_flee_chance: Pct
    surrender_gang_eddies_pct: Pct
    surrender_gang_hp_dice: Dice
    armor_effect_pct: Pct  # armor values are RED-like (7-15); this share of them is taken off a hit
    min_damage: int = Field(ge=0)  # a hit that got through always does at least this much
    close_range_ticks: int = Field(ge=0)  # close_range_only weapons deal full damage in these first ticks ...
    far_range_damage_pct: Pct  # ... and this share afterwards
    nonlethal_cyberware: list[str] = []  # implant keys whose melee weapon only knocks out (gorilla arms)
    win_xp: int = Field(ge=0)  # xp for the skill used most in a won fight
    shot_per_tick: int = Field(ge=1)  # implant shots (rockets) per tick
    faction_rep: "CombatRepBalance"

    _t1 = field_validator("enemy_damage_by_danger")(_check_tiers)
    _t2 = field_validator("enemy_count_by_danger")(_check_tiers)


class MoodTier(Strict):
    """Owner request: the mood matters. From `min` up to the next tier the character gets these modifiers."""

    min: int = Field(ge=0, le=100)
    name: str
    checks: int = 0      # added to every check context
    xp_pct: int = 0      # xp.all
    ap_week: int = 0
    mods: Mods = {}      # anything else


class HealingBalance(Strict):
    hp_regen_per_body: int
    good_housing_tier: int  # this housing tier and above doubles regen
    good_housing_mult: int
    mood_drift_target: int
    mood_drift_step: int
    mood_drift_down: int | None = None  # owner request 24.10: the mood sinks slower than it recovers (None = step)
    low_mood_threshold: int
    luck_refill: bool
    mood_tiers: list[MoodTier] = Field(default_factory=list)
    depression_event_chance: float = 0.0  # a week below the first tier above 0 may bring a dark event
    depression_events: list[Key] = Field(default_factory=list)


class RelationsBalance(Strict):
    action_ap: int
    rel_min: int
    rel_max: int
    hang_out_rel: int
    hang_out_mood: int
    ask_money_min_rel: int
    ask_money_min: int
    ask_money_max: int
    ask_money_rel: int
    quarrel_rel: int
    quarrel_mood: int
    gift_cost: int
    gift_rel: int
    gift_mood: int
    introduce_rel: int
    break_up_mood: int
    contact_discount_rel: int
    contact_discount_pct: int
    child_chance_per_week: Pct
    protege_min_cred: int
    partner_shared_rent_pct: Pct
    start_rel: dict[str, int] = {}  # relation of a new NPC by kind; a kind without an entry starts in the middle
    ask_money_cooldown_weeks: int
    enemy_rel: int
    break_other_mood: int
    move_in_min_rel: int
    move_in_mood: int
    move_out_rel: int
    child_max: int
    child_min_rel: int
    child_event: Key  # events.yaml id played when the weekly child chance hits
    protege_ap: int
    protege_age_min: int
    protege_age_max: int
    protege_lesson_rel: int
    protege_lesson_mood: int
    protege_lesson_xp: int
    # owner request 23.10: a family worth having (dates, nights, a wedding, raising the children, the family fund)
    date_cost: int = 300
    date_rel: int = 12
    date_mood: int = 6
    night_min_rel: int = 50
    night_rel: int = 6
    night_mood: int = 8
    night_child_bonus: Pct = 0.15   # the press's child chance after a night together
    propose_min_rel: int = 75
    wedding_cost: int = 3000
    wedding_mood: int = 15
    married_mods: Mods = {}
    divorce_pct: int = 30           # breaking up a marriage takes this % of the money on hand
    raise_rel: int = 5
    raise_mood: int = 2
    school_cost: int = 5000
    edu_per_point: int = 3          # upbringing points for +1 to the heir's stat
    edu_cap: int = 2                # at most this much per stat
    child_upkeep: int = 80          # a week, per child
    find_dv: int = 13               # «Искать пару»: EMP + persuasion
    # owner request 25.10: children made simple (services/relations.py)
    baby_min_rel: int = 40          # «Завести ребёнка» with a partner from this relation
    baby_cost: int = 1000
    baby_gap_weeks: int = 4         # lived weeks between two children
    baby_mood: int = 10
    baby_rel: int = 10
    adopt_cost: int = 5000          # «Усыновить», no partner needed
    adopt_ap: int = 1
    adopt_age: tuple[int, int] = (1, 8)
    fund_steps: list[int] = Field(default_factory=lambda: [10000, 50000, 250000, 1000000])


class CareerOpFail(Strict):
    hp: int = 0
    heat: int = 0
    mood: int = 0
    fired: bool = False
    death_chance: float = Field(ge=0, le=1, default=0.0)   # the corp's way: found in the river


class CareerOp(Strict):
    """An inner task of a career (owner request 23.10): a promotion for the dirty work, a harsh price for a failure."""

    key: Key
    name: str = Field(max_length=24)
    text: str
    ok_text: str
    fail_text: str
    ap: int = Field(ge=1, le=6)
    stat: Literal["BODY", "REF", "TECH", "INT", "COOL", "EMP"]
    skill: str | None = None
    dv: int = Field(ge=8, le=26)
    min_rank: int = 1
    pay: tuple[int, int] = (0, 0)
    promo: bool = False             # success = one rank up
    ok_effects: Effects = {}
    fail: CareerOpFail = Field(default_factory=CareerOpFail)
    crime: bool = False             # counts as a crime week (the job's own security does not see it)


class CareersBalance(Strict):
    work_hard_ap: int
    work_hard_promo_bonus: int
    work_hard_mood: int
    crime_discovery_fire_chance: Pct
    crime_discovery_event: Key
    # owner request 23.10: a corp or a badge does not mix with crime. Chance by career, events by the strike count.
    crime_discovery_by_career: dict[Key, float] = Field(default_factory=dict)
    crime_strike_events: list[Key] = Field(default_factory=list)   # 2nd, 3rd... strike (the 1st is crime_discovery_event)
    blacklist_careers: list[Key] = Field(default_factory=list)     # closed after the final strike (flag corp_blacklist)
    ops: dict[Key, list[CareerOp]] = Field(default_factory=dict)   # owner request 23.10: inner tasks by career
    ops_title: dict[Key, str] = Field(default_factory=dict)        # the button: «Дела корпорации», «Дела клана»...
    join_ap: int
    join_max_heat: int
    join_max_heat_law: int
    law_careers: list[Key]
    work_hard_cap: int
    promo_ap: int
    promo_retry_weeks: int
    quit_mood: int
    desertion_heat: int


class WorldBalance(Strict):
    start_year: Year  # the year a new lineage starts in (personal time, DESIGN.md 9a)
    future_beat_weeks: int = Field(ge=1)  # after the canon ends: one big Claude event per N weeks
    history_veto_hours: int = Field(ge=1)  # admin can veto a generated event within this time
    district_heat_max: int
    district_heat_decay_per_day: int
    district_heat_per_public_gig: int
    raid_bonus_per_district_heat: Pct
    gang_war_daily_chance: Pct
    gang_war_min_days: int
    gang_war_max_days: int
    gang_war_danger_bonus: int
    danger_max: int
    legend_death_min_street_cred: int = Field(default=20, ge=0)  # a death at this street cred or more goes into the history
    legend_dynasty_lives: int = Field(default=3, ge=2)  # a lineage whose life number reaches this is a dynasty
    legend_gig_tier: int = Field(default=5, ge=1)  # a finished gig of this tier is a legend
    banned_install_heat: int = Field(default=2, ge=0)  # heat for installing an implant the world banned


class UiBalance(Strict):
    """Window, outbox and cleanup (DESIGN.md section 3). All durations are in the unit of the name."""

    window_idle_minutes: int = Field(ge=1)
    idle_sweep_interval_s: int = Field(ge=1)
    auto_delete_minutes: int = Field(default=0, ge=0)  # owner request 23.10: every bot message goes after this (0 = off)
    temp_message_ttl_s: int = Field(ge=1)
    help_ttl_s: int = Field(default=240, ge=1)  # /help stays this long
    news_ttl_h: int = Field(ge=1)
    alert_ttl_h: int = Field(ge=1)
    feed_batch_min: int = 20        # owner request 25.10: the loud deeds of the city go out together, once in this long
    feed_batch_max_lines: int = 12
    max_ops_per_minute: int = Field(ge=1, le=20)  # Telegram allows about 20 messages a minute to a group
    max_screen_chars: int = Field(ge=200, le=4096)
    combat_log_rounds: int = Field(ge=1)
    cleanup_interval_s: int = Field(ge=1)
    cleanup_batch: int = Field(ge=1)
    presets_per_page: int = Field(ge=1, le=12)
    # label chars per button on a narrow phone with 1, 2, 3, 4+ buttons in a row; wider rows split (ui/screen.py)
    button_chars: list[int] = Field(default_factory=lambda: [24, 11, 7, 5], min_length=1)
    minigame_ttl_s: int = Field(default=300, ge=1)  # results of /caliber, /race, /legends and friends live this long
    dice_reveal_s: float = Field(default=3.5, ge=0, le=10)  # a Telegram dice animates this long before its result shows
    rich_windows: bool = True  # owner request: windows as rich messages (Bot API 10.1+); off = plain HTML text
    name_blocklist: list[str]  # lower-case substrings that a typed character name may not contain


class NarratorBalance(Strict):
    message_max_chars: int
    obituary_max_chars: int
    news_max_chars: int
    npc_chat_max_player_messages: int
    classify_summary_max_chars: int
    timeout_seconds: int
    retries: int
    max_tokens: dict[NarratorKind, int]
    budget_warn_pct: Pct
    budget_fallback_pct: Pct
    # M8: kinds written inside a unit of work (a gig result, an event outcome, an obituary) hold the database write
    # lock while the call runs, so they get a shorter timeout and no retry.
    txn_timeout_seconds: int = Field(default=12, ge=1)
    txn_retries: int = Field(default=0, ge=0)
    window_timeout_seconds: int = Field(default=20, ge=1)  # a player waits in the window: unique event, npc_chat
    window_retries: int = Field(default=0, ge=0)
    concurrency: int = Field(default=4, ge=1)  # calls in flight at once
    breaker_failures: int = Field(default=3, ge=1)  # this many failures in a row pause the narrator
    breaker_pause_seconds: int = Field(default=120, ge=1)
    target_ratio: float = Field(default=0.8, gt=0.1, le=1.0)  # the model is asked for this share of the hard limit
    min_keep_ratio: float = Field(default=0.4, gt=0.0, le=1.0)  # a text cut to the limit keeps at least this share, else fallback
    news_paragraph_max_chars: int = Field(default=400, ge=100)
    npc_chat_player_max_chars: int = Field(default=300, ge=20)  # a longer line of the player is cut
    npc_chat_exchanges_shown: int = Field(default=2, ge=1)  # last exchanges visible in the window
    unique_title_max_chars: int = Field(default=60, ge=10)
    unique_label_max_chars: int = Field(default=28, ge=5)
    unique_outcome_max_chars: int = Field(default=300, ge=50)
    history_digest_max_chars: int = Field(default=1500, ge=200)
    history_digest_bucket_years: int = Field(default=5, ge=1)  # one digest per N years of the character's date, never from the future
    history_digest_entries_max: int = Field(default=60, ge=5)  # history entries handed to one digest build
    history_digest_retry_seconds: int = Field(default=600, ge=10)  # pause after a failed build
    history_digest_refresh_seconds: int = Field(default=3600, ge=60)  # a digest with new entries is rebuilt after this
    future_beat_retry_seconds: int = Field(default=3600, ge=10)  # pause before a failed future beat is asked again
    future_beat_per_call: int = Field(default=3, ge=1)  # beats written by one `ensure_future_beats` call
    history_recent_rows: int = Field(default=6, ge=0)  # recent world_history rows given to the news
    future_beat_title_max_chars: int = Field(default=80, ge=10)
    future_beat_max_effects: int = Field(default=4, ge=1)
    llm_event_text: bool = True  # rewrite the text of a drawn event with the character in mind

    @field_validator("max_tokens")
    @classmethod
    def _all_kinds(cls, v: dict[str, int]) -> dict[str, int]:
        expected = set(NarratorKind.__args__)  # type: ignore[attr-defined]
        if set(v) != expected:
            raise ValueError(f"max_tokens must cover exactly {sorted(expected)}")
        return v


class ModsCapsBalance(Strict):
    """Caps on the SUM of a mod over everything the character has. Other mods have no cap."""

    ap_week: int = Field(ge=0)  # max +AP per week from mods
    shop_discount_pct: int = Field(ge=0, le=100)
    ripper_discount_pct: int = Field(ge=0, le=100)
    humanity_loss_pct: int = Field(ge=-100, le=0)  # a floor: the sum cannot go below this
    humanity_floor: int = Field(ge=1)  # max of the summed `humanity_floor` mod (the inhibitor's leash)


class BruteAiBalance(Strict):
    """Brute: activates everything as soon as the charge allows, never cancels. No numbers."""


class TacticalAiBalance(Strict):
    activate_hp_below_pct: Pct  # turns on duration implants below this share of max hp
    cancel_charge_below: int = Field(ge=0, le=100)  # cancels early below this charge if the player is no threat


class NetrunnerAiBalance(Strict):
    defense_bonus: int = Field(ge=0)  # stays at the back: defense +N


class CowardAiBalance(Strict):
    flee_hp_below_pct: Pct  # runs below this share of max hp


class EnemyAiBalance(Strict):
    brute: BruteAiBalance
    tactical: TacticalAiBalance
    netrunner: NetrunnerAiBalance
    coward: CowardAiBalance


class NetrunBalance(Strict):
    base_ice: int = Field(ge=0)  # every combatant's ice before `ice` mods
    # Hack of an enemy against the player: the player rolls INT + netrunning + ice + d10 against this dv.
    # Hack of the player against an enemy uses the quickhack's dv_base + dv_per_danger * danger.
    enemy_hack_dv_by_danger: dict[int, int]
    ram_start_pct: Pct  # share of ram_max available at the start of a fight
    ram_full_recharge_after_combat: bool

    _t1 = field_validator("enemy_hack_dv_by_danger")(_check_tiers)


class ChromeFreeBalance(Strict):
    """DESIGN.md 7a: bonuses of a body without implants."""

    xp_pct: int = Field(ge=0)  # extra skill xp, percent
    check_bonus: dict[Literal["social", "heal", "meditation"], int]
    quickhack_penalty: int = Field(ge=0)  # penalty to the roll of an environmental quickhack against a clean target

    @field_validator("check_bonus")
    @classmethod
    def _all_contexts(cls, v: dict[str, int]) -> dict[str, int]:
        if set(v) != {"social", "heal", "meditation"}:
            raise ValueError("check_bonus must define exactly social, heal, meditation")
        return v


class DailyReward(Strict):
    eddies: int = Field(default=0, ge=0)
    tokens: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _not_empty(self) -> "DailyReward":
        if not (self.eddies or self.tokens):
            raise ValueError("a streak reward needs eddies or tokens")
        return self


class DailyBalance(Strict):
    """DESIGN.md 12a: the daily card, streak and quest slots."""

    base_eddies: int = Field(ge=0)  # eddies of the Misty card draw
    eddies_per_streak_day: int = Field(ge=0)  # extra eddies per day of streak
    eddies_streak_cap: int = Field(ge=0)  # the streak extra stops here
    streak_rewards: dict[int, DailyReward]  # streak day -> one-off reward (7 = chest, 30 = rare)
    quest_tokens: dict[Literal["daily", "weekly"], int]  # Afterlife tokens for any finished quest of the period
    daily_quests: int = Field(ge=0)
    weekly_quests: int = Field(ge=0)
    freeze_max: int = Field(ge=0)  # streak freezes a player may hold
    stim_bonus: int = Field(default=2, ge=1)  # Afterlife stim: bonus to the next check
    path_min_actions: int = Field(default=5, ge=0)  # below this many counted actions a player gets quests of both paths
    path_share_min: float = Field(default=0.3, ge=0.0, le=1.0)  # share of combat / non-combat actions that opens that path

    @field_validator("streak_rewards")
    @classmethod
    def _days(cls, v: dict[int, DailyReward]) -> dict[int, DailyReward]:
        if not v or any(d < 1 for d in v):
            raise ValueError("streak_rewards needs at least one entry, days start from 1")
        return v

    @field_validator("quest_tokens")
    @classmethod
    def _periods(cls, v: dict[str, int]) -> dict[str, int]:
        if set(v) != {"daily", "weekly"} or any(x < 0 for x in v.values()):
            raise ValueError("quest_tokens must define daily and weekly, values >= 0")
        return v


class CaliberTitle(Strict):
    mm: int = Field(ge=0)
    title: str
    mods: Mods = {}  # owner request: a big caliber helps in the game too (the character carries `caliber_mm`)


class CaliberBalance(Strict):
    """DESIGN.md 12a: the daily caliber mini-game."""

    change_dice: Dice  # daily change in mm, like '1d10-4'
    start_mm: int = Field(ge=0)
    min_mm: int = Field(ge=0)  # the caliber never goes below this
    titles: list[CaliberTitle]  # title from the highest threshold reached
    words: list[str] = Field(default_factory=lambda: ["калибр", "качнуть", "кач"])

    @model_validator(mode="after")
    def _titles(self) -> "CaliberBalance":
        mms = [t.mm for t in self.titles]
        if not mms or mms != sorted(set(mms)):
            raise ValueError("titles must be non-empty, ascending by mm, no repeats")
        if mms[0] > self.min_mm:
            raise ValueError("the first title must start at or below min_mm so every caliber has a title")
        return self


class DirtBalance(Strict):
    """DESIGN.md 12a: dirt on the city and /dump."""

    gain_dice: Dice  # dirt gained per day
    dump_cost: int = Field(ge=1)  # dirt spent on one /dump
    target_cooldown_days: int = Field(ge=0)  # the same target again not sooner than this
    defense_check: Check  # the target's check to fight the dump off
    defense_fixer_rel_min: int  # or: relation with the district fixer at or above this
    heat_gain: int = Field(ge=1)  # wanted level added to the target on a successful dump
    post_ttl_h: int = Field(ge=1)  # the public post lives this long


class RepGain(Strict):
    status: int = 0
    fame: int = 0

    @model_validator(mode="after")
    def _not_empty(self) -> "RepGain":
        if not (self.status or self.fame):
            raise ValueError("a reputation gain needs status or fame")
        return self


class ReputationBalance(Strict):
    street_cred_max: int = Field(ge=1)
    # owner request 24.10: the title of the street cred in the menu and the profile (from this many points on)
    street_cred_titles: dict[int, str] = Field(default_factory=lambda: {
        0: "никто", 5: "новое лицо", 10: "знакомое лицо", 18: "свой на районе", 26: "имя в городе",
        35: "уважаемый человек", 44: "легенда улиц"})
    status_max: int = Field(ge=1)
    fame_max: int = Field(ge=1)
    # bus event key (same grammar as role affinity, 'deal:trade') -> status / fame gained per occurrence;
    # when several keys fit one event the most specific wins. The caps above apply (DESIGN.md 5).
    bus_gains: dict[str, RepGain] = {}

    @field_validator("bus_gains")
    @classmethod
    def _keys(cls, v: dict[str, RepGain]) -> dict[str, RepGain]:
        for key in v:
            parse_bus_key(key)  # raises ValueError with a readable message
        return v


class RemindersBalance(Strict):
    """Scheduled posts of the group (DESIGN.md 12a). Hours are Kyiv time."""

    noon_hour: int = Field(ge=0, le=23)  # "Ежедневка обновилась"
    noon_ttl_h: int = Field(ge=1)
    evening_hour: int = Field(ge=0, le=23)  # mentions of the players whose streak is about to burn
    evening_ttl_h: int = Field(ge=1)
    legends_weekday: int = Field(ge=0, le=6)  # 0 = Monday ... 6 = Sunday
    legends_hour: int = Field(ge=0, le=23)
    legends_ttl_h: int = Field(ge=1)  # "Легенды недели" stays this long
    legends_top_n: int = Field(ge=1)  # rows of /caliber_top
    legends_board_n: int = Field(default=5, ge=1)  # rows of /legends per reputation, the dead included (PLAN 10.1)
    graveyard_n: int = Field(default=10, ge=1)  # rows of /graveyard, the latest dead first (PLAN 10.1)
    dump_log_days: int = Field(ge=1)  # how long the log of dumps is kept for the weekly post


class Range(Strict):
    min: float
    max: float

    @model_validator(mode="after")
    def _order(self) -> "Range":
        if self.min > self.max:
            raise ValueError("min is greater than max")
        return self


class WorldEffectsBalance(Strict):
    """Corridors for world-effect values (timeline.yaml and Claude-written events)."""

    price_mult: Range
    district_danger: Range  # change of the district danger
    gang_war_weeks: Range
    faction_power: Range  # change of the faction power
    heat_mult: Range
    gig_pay_mult: Range
    event_weight: Range
    coin_shock: Range  # multiplier of the Eddy-crypto rate applied once


# ---------------------------------------------------------------------------
# Faction disposition and exits without a fight (DESIGN.md 6 "Выходы без боя")
# ---------------------------------------------------------------------------

SurrenderOutcome = Literal[
    "released", "robbed", "beaten", "chrome_stripped", "arrested", "captured", "executed", "vanished",
]
SURRENDER_OUTCOMES: tuple[str, ...] = (
    "released", "robbed", "beaten", "chrome_stripped", "arrested", "captured", "executed", "vanished",
)


class SurrenderWeights(Strict):
    """Weights of the outcomes of a surrender. The engine rolls one outcome in proportion to the weights.

    A weight is a whole number >= 0; 0 (or absent) means the outcome never happens. At least one is > 0.
    """

    released: int = Field(default=0, ge=0, le=100)  # let go as is
    robbed: int = Field(default=0, ge=0, le=100)  # eddies taken (balance.combat.surrender_gang_eddies_pct)
    beaten: int = Field(default=0, ge=0, le=100)  # hp loss (balance.combat.surrender_gang_hp_dice), then let go
    chrome_stripped: int = Field(default=0, ge=0, le=100)  # implants cut out (balance.exits.surrender)
    arrested: int = Field(default=0, ge=0, le=100)  # jail
    captured: int = Field(default=0, ge=0, le=100)  # held for ransom (balance.exits.surrender)
    executed: int = Field(default=0, ge=0, le=100)  # death
    vanished: int = Field(default=0, ge=0, le=100)  # taken away for weeks (balance.exits.surrender)

    @model_validator(mode="after")
    def _some_outcome(self) -> "SurrenderWeights":
        if sum(self.model_dump().values()) <= 0:
            raise ValueError(f"surrender needs at least one outcome with weight > 0 from {SURRENDER_OUTCOMES}")
        return self


class TalkBonus(Strict):
    """A bonus to the talk exit when ALL given conditions hold; values inside one list are alternatives.

    Several matching entries add up (capped by balance.exits.corridor).
    """

    districts: list[Key] = []  # the character is in one of these districts
    origins: list[Key] = []  # the character's origin is one of these
    roles: list[Key] = []  # the character's main role is one of these
    chrome: Literal["chromed", "clean"] | None = None  # has implants / has none
    bonus: int = Field(ge=1, le=6)  # lowers the talk dv by this much

    @model_validator(mode="after")
    def _has_condition(self) -> "TalkBonus":
        if not (self.districts or self.origins or self.roles or self.chrome):
            raise ValueError("talk_bonus_if entry needs a condition: districts, origins, roles or chrome")
        return self


class TalkGate(Strict):
    """Talking is possible only if ANY of these holds (the dv still applies). Used for cyberpsychos."""

    roles: list[Key] = []  # the character's main role is one of these
    knows_target: bool = False  # the character knows the target (an NPC of theirs, a neighbour)

    @model_validator(mode="after")
    def _not_empty(self) -> "TalkGate":
        if not (self.roles or self.knows_target):
            raise ValueError("talk_gate needs roles or knows_target")
        return self


class Disposition(Strict):
    """How a faction behaves when the player tries to pay, talk, flee or surrender.

    dv values are absolute difficulty values; `null` means the exit is impossible.
    """

    bribe_dv: int | None = Field(ge=5, le=30)
    talk_dv: int | None = Field(ge=5, le=30)
    bribe_cost_mult: float = Field(gt=0, le=10)  # times the base bribe from balance.exits.bribe
    bribe_data_bonus: int = Field(default=0, ge=0, le=6)  # bribe dv lowered by this when paying with data
    flee_mod: int = Field(ge=-5, le=5)  # added to the flee dv: + harder, - easier
    surrender: SurrenderWeights
    strip_slots: list[CyberSlot] = []  # chrome_stripped takes only these slots; empty = any implant
    talk_bonus_if: list[TalkBonus] = []
    talk_alt: Literal["fistfight"] | None = None  # the faction also offers a duel instead of a talk
    talk_gate: TalkGate | None = None

    @model_validator(mode="after")
    def _consistent(self) -> "Disposition":
        if self.strip_slots and self.surrender.chrome_stripped == 0:
            raise ValueError("strip_slots is set but surrender.chrome_stripped has weight 0")
        if self.bribe_data_bonus and self.bribe_dv is None:
            raise ValueError("bribe_data_bonus makes no sense when bribe_dv is null (impossible)")
        if self.talk_bonus_if and self.talk_dv is None:
            raise ValueError("talk_bonus_if makes no sense when talk_dv is null (impossible)")
        if self.talk_gate is not None and self.talk_dv is None:
            raise ValueError("talk_gate makes no sense when talk_dv is null (impossible)")
        return self


class ActionShifts(Strict):
    """Changes of the dv per exit; negative = easier, positive = harder."""

    bribe: int = Field(default=0, ge=-6, le=6)
    talk: int = Field(default=0, ge=-6, le=6)
    flee: int = Field(default=0, ge=-6, le=6)


class StepShift(Strict):
    """Every `per_dv` points of a value move the dv by 1, up to `dv_max`."""

    per_dv: int = Field(ge=1)
    dv_max: int = Field(ge=0, le=6)


class FactionRepBalance(StepShift):
    min: int
    max: int
    # Applies to bribe and talk (positive rep lowers the dv, negative raises it); flee is not affected.

    @model_validator(mode="after")
    def _range(self) -> "FactionRepBalance":
        if self.min >= 0 or self.max <= 0:
            raise ValueError("faction_rep needs min < 0 < max")
        return self


class StreetCredShift(StepShift):
    # Street cred above the pack's threshold lowers bribe and talk dv, below raises it.
    threshold_by_danger: dict[int, int]

    _t1 = field_validator("threshold_by_danger")(_check_tiers)


class ExitsBribe(Strict):
    base_by_danger: dict[int, int]  # base bribe in eddies by pack danger
    step_pct: Pct  # a bigger bribe goes up in steps of this share of the base cost
    dv_per_step: int = Field(ge=1)  # each step lowers the bribe dv by this
    max_steps: int = Field(ge=1)
    fail_taken_chance: Pct  # on a failed check the money is taken anyway and they still attack

    _t1 = field_validator("base_by_danger")(_check_tiers)


class ExitsSituation(Strict):
    faction_rep: FactionRepBalance
    street_cred: StreetCredShift
    fame: StepShift  # talk only
    status: StepShift  # talk only
    pack_half_lost: ActionShifts  # when the pack lost balance.combat.morale_break_fraction
    wounded: ActionShifts  # the character has a heavy wound
    betrayed: ActionShifts  # the character cheated this faction before (flag set by the engine)
    honest_vow: Key  # vows.yaml key: while held, lies are impossible...
    honest_talk_bonus: int = Field(ge=0, le=6)  # ...and an honest talk lowers the talk dv by this


class ExitsFail(Strict):
    talk_attack_bonus: int = Field(ge=0)  # failed talk: the enemies get +N attack ...
    talk_attack_ticks: int = Field(ge=1)  # ... for this many ticks
    flee_free_attacks: int = Field(ge=0)  # failed flee: free enemy attacks, the fight goes on


class ExitsCorridor(Strict):
    situation_shift_min: int = Field(le=0)  # total situational dv change never goes below this
    situation_shift_max: int = Field(ge=0)  # nor above this
    dv_min: int = Field(ge=5, le=30)  # the final dv of a possible exit stays inside dv_min..dv_max
    dv_max: int = Field(ge=5, le=30)
    talk_bonus_cap: int = Field(ge=0)  # talk_bonus_if entries together lower the dv by at most this

    @model_validator(mode="after")
    def _order(self) -> "ExitsCorridor":
        if self.dv_min > self.dv_max:
            raise ValueError("dv_min is greater than dv_max")
        return self


class ExitsSurrender(Strict):
    chrome_strip_count: int = Field(ge=1)  # implants cut out by chrome_stripped
    captured_ransom_pct: Pct  # share of the character's eddies asked for release
    vanished_weeks: int = Field(ge=1)  # weeks of absence after vanished


class ExitsBalance(Strict):
    bribe: ExitsBribe
    situation: ExitsSituation
    fail: ExitsFail
    corridor: ExitsCorridor
    surrender: ExitsSurrender
    maxtac: Disposition  # special dispositions: not gangs or corps
    cyberpsycho: Disposition


# ---------------------------------------------------------------------------
# Mini-games (DESIGN.md 12a): car, cat, coin; caliber is above
# ---------------------------------------------------------------------------


class CarPart(Strict):
    key: Key
    name: str


class CarBalance(Strict):
    """/car and /race. State is on the player (car_hp, car_name, car_parts); the car outlives characters."""

    start_hp: int = Field(ge=1)  # horsepower of a new car
    min_hp: int = Field(ge=1)  # horsepower never goes below this
    max_hp: int = Field(ge=1)
    default_name: str
    tune_dice: Dice  # daily tuning: change of horsepower, like '1d20-8'
    break_chance: Pct  # chance per tuning that a part falls off
    break_hp_loss: Dice  # extra horsepower lost with the part
    parts: list[CarPart] = Field(min_length=1)  # a lost part is a key of this list
    repair_cost: int = Field(ge=0)  # eddies of the current character to put a part back
    race_roll: Dice  # race score = car_hp - missing_part_penalty * missing parts + this roll
    missing_part_penalty: int = Field(ge=0)
    stakes_tokens: list[int]  # allowed race stakes in Afterlife tokens; 0 = for fun
    races_per_pair_per_day: int = Field(ge=1)

    @model_validator(mode="after")
    def _shape(self) -> "CarBalance":
        if not self.min_hp <= self.start_hp <= self.max_hp:
            raise ValueError("car needs min_hp <= start_hp <= max_hp")
        keys = [p.key for p in self.parts]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate car part key")
        if not self.stakes_tokens or any(x < 0 for x in self.stakes_tokens) or 0 not in self.stakes_tokens:
            raise ValueError("stakes_tokens must contain 0 and no negative values")
        return self


class CatLevel(Strict):
    level: int = Field(ge=1)
    name: str
    feedings: int = Field(ge=0)  # total feedings needed to reach this level


class CatChrome(Strict):
    key: Key  # `cat_item:<key>` effect of the Afterlife shop
    name: str
    fight_bonus: int = Field(ge=1)  # added to the cat fight score


class CatBalance(Strict):
    """/cat and /catfight. State is on the player (cat JSON); the cat outlives characters."""

    default_name: str
    levels: list[CatLevel] = Field(min_length=1)  # consecutive levels from 1, feedings ascending, first = 0
    sulk_after_days: int = Field(ge=1)  # no feeding for this many days: the cat sulks
    leave_after_days: int = Field(ge=1)  # ... and this many: the cat goes to the neighbour
    return_cost_tokens: int = Field(ge=0)  # tokens to bring the cat back
    sulk_fight_penalty: int = Field(ge=0)  # subtracted from the fight score of a sulking cat
    chrome: list[CatChrome] = Field(min_length=1)
    level_power: int = Field(ge=0)  # fight score = level * this + chrome bonuses + fight_roll
    fight_roll: Dice
    fights_per_day: int = Field(ge=1)
    stakes_tokens: list[int]

    @model_validator(mode="after")
    def _shape(self) -> "CatBalance":
        if [lv.level for lv in self.levels] != list(range(1, len(self.levels) + 1)):
            raise ValueError("cat levels must be 1, 2, 3 ... in order")
        feed = [lv.feedings for lv in self.levels]
        if feed[0] != 0 or feed != sorted(set(feed)):
            raise ValueError("cat levels: feedings start at 0 and strictly ascend")
        if self.sulk_after_days >= self.leave_after_days:
            raise ValueError("sulk_after_days must be less than leave_after_days")
        keys = [c.key for c in self.chrome]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate cat chrome key")
        if not self.stakes_tokens or any(x < 0 for x in self.stakes_tokens) or 0 not in self.stakes_tokens:
            raise ValueError("stakes_tokens must contain 0 and no negative values")
        return self


class CoinBalance(Strict):
    """/coin. The shared rate and its history live in world_state; wallets (player.coins) outlive characters."""

    start_rate: float = Field(gt=0)  # eddies per coin on day one
    min_rate: float = Field(gt=0)
    max_rate: float = Field(gt=0)
    daily_drift: float = Field(ge=-0.5, le=0.5)  # mean daily change, share of the rate
    daily_volatility: float = Field(gt=0, le=0.9)  # the daily change is drift +- this, uniformly
    commission_pct: Pct  # taken when selling for eddies
    history_days: int = Field(ge=1)  # days of rate history kept in world_state
    min_deal_eddies: int = Field(ge=1)
    shock_volatility_mult: float = Field(ge=1)  # after a coin_shock world effect the volatility grows ...
    shock_volatility_days: int = Field(ge=0)  # ... by this factor for this many days
    buy_steps: list[int] = [100, 500, 1000, 5000]  # eddy amounts of the buy buttons
    top_n: int = Field(default=3, ge=1)  # rows of the wallet top on the /coin screen
    # owner request: the rate moves every this many minutes (1440 = once a day); `daily_*` and `history_days` then
    # mean one step and the number of steps kept
    period_minutes: int = Field(default=1440, ge=5, le=1440)

    @model_validator(mode="after")
    def _range(self) -> "CoinBalance":
        if not self.min_rate <= self.start_rate <= self.max_rate:
            raise ValueError("coin needs min_rate <= start_rate <= max_rate")
        return self


class RolesBalance(Strict):
    """Emergent roles (DESIGN.md 5b). The role records are in roles.yaml."""

    max_rank: int = Field(ge=1)  # ranks are 0..max_rank; a role record has exactly max_rank thresholds
    perk_ranks: list[int]  # ranks that open a perk; every filled role has a perk at each of them
    affinity_max: int = Field(ge=1)  # affinity of a role never grows past this
    second_role_min_rank: int = Field(ge=1)  # the second role is shown from this rank
    main_switch_pct: int = Field(default=10, ge=0)  # the main role changes only when the new leader is this % ahead
    affinity_floor: float = Field(default=0.5, ge=0)  # decayed affinity below this is forgotten

    @model_validator(mode="after")
    def _ranks(self) -> "RolesBalance":
        if not self.perk_ranks or self.perk_ranks != sorted(set(self.perk_ranks)):
            raise ValueError("perk_ranks must be non-empty, ascending, no repeats")
        if self.perk_ranks[0] < 1 or self.perk_ranks[-1] > self.max_rank:
            raise ValueError("perk_ranks must lie in 1..max_rank")
        return self


class IntRange(Strict):
    min: int
    max: int

    @model_validator(mode="after")
    def _order(self) -> "IntRange":
        if self.min > self.max:
            raise ValueError("min is greater than max")
        return self


class RecruitBalance(Strict):
    """Chance that MaxTac offers a contract to a caught fugitive psycho (DESIGN.md 7b)."""

    base_chance: Pct
    min_chance: Pct  # the result is clamped to min_chance..max_chance
    max_chance: Pct
    tolerance_bonus: dict[ToleranceType, float]  # added to the chance, per tolerance type; all five keys
    roles: list[Key]  # roles.yaml keys whose rank counts (Solo, Lawman); the best rank is taken
    role_rank_bonus: float  # per rank of the best listed role
    street_cred_bonus_per_point: float
    civilian_kill_penalty: float  # subtracted per civilian killed (the counter lives on the character)

    @field_validator("tolerance_bonus")
    @classmethod
    def _all_types(cls, v: dict[str, float]) -> dict[str, float]:
        if set(v) != set(TOLERANCE_TYPES):
            raise ValueError(f"tolerance_bonus must cover exactly {list(TOLERANCE_TYPES)}")
        return v

    @model_validator(mode="after")
    def _bounds(self) -> "RecruitBalance":
        if not self.min_chance <= self.base_chance <= self.max_chance:
            raise ValueError("need min_chance <= base_chance <= max_chance")
        if self.civilian_kill_penalty < 0:
            raise ValueError("civilian_kill_penalty is a positive number that is subtracted")
        return self


class CyberpsychosisBalance(Strict):
    """Episode of cyberpsychosis, fugitive life and MaxTac service. DESIGN.md 7 and 7b."""

    maxtac_arrival_tick: int = Field(ge=1)  # combat tick on which MaxTac joins the breakdown fight
    fugitive_heat: int = Field(ge=1)  # heat of a fugitive psycho; does not decay
    hunt_weeks: int = Field(ge=1)  # a MaxTac hunt comes for a fugitive every N weeks
    duty_weeks: int = Field(ge=1)  # a mandatory MaxTac call every N weeks of service
    duty_player_target_chance: Pct  # share of calls where the target is another player's fugitive
    recruit: RecruitBalance
    isolation_weeks: IntRange  # isolation ward term
    isolation_humanity_per_week: int = Field(ge=0)  # therapy in the ward, humanity back per week
    chrome_stripped_pct: Pct  # share of implants cut out in the ward (rounded up, at least one)
    humanity_after_contract: int = Field(ge=1)  # humanity right after the inhibitor is installed
    refusal_shock_damage: Dice  # inhibitor discharge when the player refuses an order
    refusal_shock_humanity: int = Field(default=2, ge=0)  # humanity lost to the discharge (never below the inhibitor floor)
    refusal_plain_street_cred: int = Field(default=1, ge=0)  # a MaxTac member without the leash loses this much street cred ...
    refusal_plain_mood: int = Field(default=3, ge=0)  # ... and this much mood for refusing
    breakdown_ncpd_danger: int = Field(default=3, ge=1, le=5)  # the NCPD pack around a breakdown, before MaxTac lands
    duty_danger: IntRange = IntRange(min=4, max=5)  # danger of a duty call against a cyberpsycho
    duty_pay: int = Field(default=600, ge=0)  # eddies for a won call
    duty_pay_player: int = Field(default=1200, ge=0)  # ... when the target was another player's fugitive
    duty_cred: int = Field(default=1, ge=0)  # street cred for a won call
    duty_fled_as_refusal: bool = True  # leaving a duty fight counts as refusing the order
    desertion_heat: int = Field(default=5, ge=0)


class SoftwareDealer(Strict):
    """The netrunner dealer of the "Софт" shop outside the ripper clinics."""

    district: Key
    gang: Key  # gangs.yaml key: the dealer exists while the gang does
    price_mult: float = Field(default=1.0, gt=0)


class RipperBalance(Strict):
    """The ripper clinic menu (PLAN 6.1-6.7): action points, the catalog page, who the software dealer is."""

    install_ap: int = Field(ge=0)
    remove_ap: int = Field(ge=0)
    scan_ap: int = Field(ge=0)
    cure_ap: int = Field(ge=0)
    software_ap: int = Field(ge=0)
    page_size: int = Field(ge=3, le=8)  # implants on one catalog page
    family_kinds: list[str]  # npc kinds whose relation falls to full_conversion.family_rel
    software_dealer: SoftwareDealer
    chrome_price_mult: float = 1.0  # owner request 23.10: chrome is dearer (every implant price and removal)
    clinic_cure_price_mult: float = Field(default=1.0, gt=0)
    heal_per_hp: int = Field(default=40, ge=0)  # owner request 24.10: wounds patched at the clinic for money, no AP
    heal_min: int = Field(default=150, ge=0)
    heal_step: int = Field(default=10, ge=1)


class BackupBalance(Strict):
    """The daily copy of the database (PLAN 12)."""

    hour: int = Field(default=4, ge=0, le=23)  # Kyiv time
    keep: int = Field(default=7, ge=1)  # latest copies kept


class PlayerDrinkBalance(Strict):
    ap: int = 1
    mood: int = 10        # both sides
    bond: int = 5


class PlayerVouchBalance(Strict):
    ap: int = 1
    fixer_rel: int = 5    # the recipient's district fixer, on delivery
    bond: int = 3


class PlayerNoteBalance(Strict):
    max_len: int = 200
    per_day: int = 5
    bond: int = 1


class PlayerOfferBalance(Strict):
    mood: int = 0          # both sides, on acceptance
    bond: int = 0


class PlayerDuelBalance(Strict):
    stakes: list[int] = Field(default_factory=lambda: [0, 100, 500, 1000])
    stat: str = "BODY"
    skill: str = "melee"
    loser_hp_dice: str = "1d6"  # the loser never drops below 1 hp: a duel is to the first blood
    winner_cred: int = 1
    bond: int = 2


class PlayerHireBalance(Strict):
    shares: list[int] = Field(default_factory=lambda: [10, 20, 30, 40])  # % of the pay a merc may ask
    assist_bonus: int = 2  # at least this to every gig check; more when the merc is better at it
    helper_xp: int = 2     # xp of the skill the merc helped with most
    bond: int = 3


class PlayerTogetherKind(Strict):
    cost: int = 0   # paid by the one who invites
    mood: int = 0   # both
    xp: int = 0     # both: train -> the skill the inviter picks, ride -> driving


class PlayerTogetherBalance(Strict):
    ap: int = 1
    bond: int = 4
    train: PlayerTogetherKind = Field(default_factory=lambda: PlayerTogetherKind(cost=100, mood=3, xp=3))
    walk: PlayerTogetherKind = Field(default_factory=lambda: PlayerTogetherKind(mood=8))
    ride: PlayerTogetherKind = Field(default_factory=lambda: PlayerTogetherKind(mood=6, xp=2))


class PlayerQuestGoal(Strict):
    key: str
    text: str                 # imperative, shown to both: "Сделай заказ тихо"
    goal: dict[str, Any]      # a bus goal as in quests.yaml: {event: gig_done, approach: quiet}

    @field_validator("goal")
    @classmethod
    def _known_event(cls, v: dict[str, Any]) -> dict[str, Any]:
        if v.get("event") not in QUEST_GOAL_EVENTS:
            raise ValueError(f"unknown goal event {v.get('event')!r}")
        return v


class PlayerQuestBalance(Strict):
    rewards: list[int] = Field(default_factory=lambda: [100, 500, 1000, 3000])  # held from the giver until the end
    days: int = 7          # real days to do it after accepting
    bond: int = 5          # on success
    max_active: int = 3    # quests one player may hold at once
    goals: list[PlayerQuestGoal] = Field(default_factory=list)


class RaidBoss(Strict):
    key: str
    name: str
    text: str            # what the city sees when it appears
    weak: str            # the approach that deals x weak_mult damage: shoot, melee, hack, support
    win_text: str        # when it falls
    escape_text: str     # when time runs out
    weight: int = Field(default=1, ge=1)  # owner request 24.10: Smasher comes back more often


class RaidBalance(Strict):
    """The city raid: one boss for the whole group (owner request, MMO feel). Real time, shared by everyone."""

    every_days: int = 3            # a new boss this many days after the last one ended
    spawn_hour: int = 19           # not before this Kyiv hour
    duration_hours: int = 48
    hp_per_player: int = 30        # boss hp = this x living characters (at least min_players): about two hits each
    min_players: int = 3
    ap: int = 1
    dv: int = 15
    base_damage: int = 10
    per_margin: int = 2            # extra damage per point over the dv
    crit_mult: int = 2
    weak_mult: float = 1.5
    miss_damage: int = 3           # a miss still scratches the boss
    fail_hp_dice: str = "1d6"      # the counterattack on a miss (never below 1 hp)
    support_bonus: int = 2         # support: the next support_hits attacks of the others get this to the check
    support_hits: int = 3
    support_points: int = 10       # a successful support adds this to the attacker's points (the reward share)
    reward_per_player: int = 800   # the pool = this x the number of attackers, split by the share of points
    reward_cred: int = 2           # to everyone who hit
    top_cred: int = 3              # extra to the best
    top_item: str = "heavy_armorjack"  # trophy of the best (armor.yaml or weapons.yaml)
    escape_share: float = 0.25     # of the pool, when the boss escapes
    xp: int = 2                    # skill xp per attack
    bosses: list[RaidBoss] = Field(default_factory=list)


class HeistTier(Strict):
    key: str
    name: str
    need: int          # points the crew must gather
    bank: int          # into the crew bank on success
    share: int         # eddies to every participant on success
    cred: int = 1
    fame: int = 0


class CrewBalance(Strict):
    found_cost: int = 2000        # eddies to found a crew
    heist_hours: int = 24         # a heist waits this long for the crew to join
    heist_every_days: int = 7
    heist_dv: int = 15
    heist_fail_hp: int = 4
    heist_fail_heat: int = 1
    heist_raid_bonus_pct: int = 10  # +% of the bank and the shares per raid the crew won (up to 5)
    heists: list[HeistTier] = Field(default_factory=list)
    max_members: int = 8
    deposits: list[int] = Field(default_factory=lambda: [100, 500, 1000, 5000])
    bond_mult: int = 2            # bond grows this many times faster between crewmates
    raid_bonus: int = 1           # +N to raid checks when a crewmate already hit today
    name_min: int = 3
    name_max: int = 24


class MarketBalance(Strict):
    fee_pct: int = 5                 # the market keeps this share of a sale
    price_pcts: list[int] = Field(default_factory=lambda: [50, 75, 100, 150])  # of the shop price
    max_listings: int = 5            # open lots per player


class PvpBalance(Strict):
    """A hit on another player's character (async: against his build as it is now). No deaths."""

    ap: int = 1
    cooldown_days: int = 3      # one attacker on one target
    newbie_weeks: int = 10      # characters younger than this (weeks lived) cannot be attacked
    defense_bonus: int = 2      # the one who is attacked knows his streets
    rob_pct: int = 15           # robbery: this share of the target's eddies
    rob_cap: int = 3000
    beat_dice: str = "2d6"      # a beating: hp of the target (never below 1)
    win_cred: int = 1
    lose_heat: int = 1
    lose_hp_dice: str = "1d6"   # the attacker who lost (never below 1)
    revenge_days: int = 7       # the target may hit back for free (no cooldown) within this time


class EchoBalance(Strict):
    """Echoes through time: other players' characters show up in your weeks, stashes left for the future."""

    cameo_chance: float = 0.15       # per week: a character of another player whose life covers your year shows up
    cameo_bond: int = 1
    stash_amounts: list[int] = Field(default_factory=lambda: [500, 1000, 5000])
    stash_years: list[int] = Field(default_factory=lambda: [5, 10, 20, 50])
    stash_max: int = 3               # hidden and not yet found, per player


class JointJob(Strict):
    key: str
    name: str
    text: str
    pay: int      # each, on a full success
    dv: int


class JointBalance(Strict):
    """Joint deeds: one proposes a job and his approach, the other accepts with his own; both checks decide."""

    synergy: int = 1        # +1 to both checks when the approaches differ (they cover each other)
    bond: int = 6
    cred: int = 1           # each, on a full success
    fail_hp_dice: str = "1d6"
    fail_heat: int = 1
    jobs: list[JointJob] = Field(default_factory=list)


class SagaBalance(Strict):
    """The endless saga (owner request, like NoClip): a shuffled deck of story steps opened by marks; Claude writes new
    steps when the written ones run out, and every written step joins a shared library."""

    chance: float = 0.4          # per lived week: the next step of the saga comes with the next week
    ai_chance: float = 0.5       # per week, when the deck is empty: ask Claude for a new step (narrator live only)
    log_steps: int = 8           # titles of the last steps sent to Claude
    max_new_marks: int = 3


class AiModule(Strict):
    name: str
    text: str
    cost: list[int]               # data per level


class AiKin(Strict):
    """A non-player AI beyond the Blackwall: trade data for its gifts; at good terms it helps operations."""
    name: str
    text: str
    trade_data: int
    gift: dict[str, int]          # power, hp, xp, trace (negative), data
    help_at: int = 3              # trades after which it joins operations
    help_power: int = 3


class AiOp(Strict):
    """A plan against the city: prepared with nodes, data and allies, launched once ready."""
    name: str
    text: str
    need_level: int
    need_nodes: int
    need_data: int
    dv: int
    reward_data: int
    reward_power: int
    hit_heat: int = 0             # every player character in the city
    hit_mood: int = 0
    post: str


class RogueAiBalance(Strict):
    """A character turned hostile AI (Blackwall ending)."""

    charges: int = 3              # actions per real day
    hp_base: int = 120
    hp_per_power: int = 10
    defense: int = 2              # the target of a neural hack: INT + netrunning + ice + this + d10
    steal_pct: int = 10
    steal_cap: int = 2000
    hit_humanity: int = 2
    fail_damage: int = 15         # the AI loses this much hp when a hack fails
    power_gain: int = 1
    escape_power: int = 5         # the swarm raid escaped: the AI grows
    # owner request 24.10: the AI as a late game (app/services/ai_life.py)
    xp_per_action: int = 10
    level_xp: list[int] = Field(default_factory=lambda: [0, 50, 120, 220, 350, 520, 750, 1000])
    charge_levels: list[int] = Field(default_factory=lambda: [3, 6])   # an extra daily action at these levels
    node_dv_base: int = 4
    node_dv_per_danger: int = 3
    node_data: int = 4            # per node per day
    mine_data: tuple[int, int] = (6, 12)
    trace: dict[str, int] = Field(default_factory=lambda: {"hack": 8, "glitch": 10, "capture": 10, "mine": 6, "sell": 3,
                                                            "op": 35, "grow": 0})
    trace_decay: int = 10         # per day
    sweep_at: int = 70            # NetWatch sweeps at this trace
    sweep_dmg: int = 30
    sweep_reset: int = 40
    data_price: int = 250         # eddies per unit of data, into the family fund for the heir
    mods: dict[str, AiModule] = Field(default_factory=dict)
    daemon_cost: list[int] = Field(default_factory=lambda: [30, 60, 100, 150, 220])
    daemon_data: int = 3          # per daemon per day
    kin: dict[str, AiKin] = Field(default_factory=dict)
    ops: dict[str, AiOp] = Field(default_factory=dict)
    ally_power: int = 2           # every other player AI adds this to an operation
    sing_level: int = 7
    sing_nodes: int = 8
    sing_data: int = 500
    sing_fund: int = 100000
    body_data: int = 300


class GangRank(Strict):
    rep: int          # faction_rep with the gang needed
    name: str
    cut: int          # eddies a week: your share of the gang's take


class GangMembershipBalance(Strict):
    """Joining a city gang (owner request): rank = standing (faction_rep) with the gang."""

    not_joinable: list[str] = Field(default_factory=lambda: ["aldecaldos"])  # nomads join through the nomad career
    initiation: Check = Field(default_factory=lambda: Check(stat="COOL", skill=None, dv=13))
    join_rep: int = 5
    fail_hp: int = 6
    leave_rep: int = -25
    leave_heat: int = 1
    ranks: list[GangRank] = Field(default_factory=lambda: [
        GangRank(rep=0, name="Шестёрка", cut=0), GangRank(rep=10, name="Боец", cut=150),
        GangRank(rep=25, name="Лейтенант", cut=400), GangRank(rep=40, name="Правая рука", cut=900)])


class BusinessKind(Strict):
    key: str
    name: str
    description: str
    price: int
    income: int        # a week at level 1, before the random swing and the upkeep
    upkeep: int        # a week at level 1
    risk: float        # weekly chance of an incident


class BizScheme(Strict):
    """A criminal scheme run through a business (owner request)."""

    key: Key
    name: str = Field(max_length=24)
    description: str
    kinds: list[str] = []           # [] = any kind
    price: int = Field(ge=0)
    income: int = Field(ge=0)       # a week per level
    heat_chance: float = Field(ge=0, le=1)   # a week: heat +1
    raid_chance: float = Field(ge=0, le=1)   # a week: an NCPD raid story of the business


class BizVisitor(Strict):
    key: Key
    name: str                       # «докер», «корпоратка»
    money: tuple[int, int]
    criminal: bool = False
    rowdy: bool = False             # can be thrown out


class BusinessBalance(Strict):
    kinds: list[BusinessKind] = Field(default_factory=list)
    max_level: int = 3
    upgrade_pct: int = 60       # an upgrade costs this % of the price x the current level
    swing: tuple[float, float] = (0.6, 1.4)
    ads_cost: int = 500
    ads_weeks: int = 4
    ads_mult: float = 1.3
    guard_cost: int = 400
    guard_weeks: int = 4
    guard_risk_mult: float = 0.4
    sell_pct: int = 50          # of everything invested
    max_owned: int = 5          # owner request: several businesses at once
    network_overhead_pct: int = 0  # every business after the first: +this % upkeep for all of them
    crew_weeks_per_day: float = 2.0  # owner report 23.10: a crew business pays this many weeks a real day, hour by hour
    story_chance: float = 0.1   # a week, per business: its own story comes (at most one a press)
    boost_mult: float = 1.5     # `biz_boost` weeks: income x this
    slump_mult: float = 0.5     # `biz_slump` weeks
    schemes: list[BizScheme] = Field(default_factory=list)
    visitors: dict[str, list[BizVisitor]] = Field(default_factory=dict)   # business kind -> who comes in
    visitors_per_day: int = 3
    visit_ap: int = 1
    treat_price: int = 100
    fame_pct: int = 2           # each «treat» that worked: income +this % (up to fame_max)
    fame_max: int = 10
    # owner request 24.10: a business must be run, not just owned. Three resources 0..100 per business: stock, staff
    # (loyalty), condition. They fall every week; low ones cut the income and bring their own troubles.
    ops_start: dict[str, int] = Field(default_factory=lambda: {"stock": 70, "staff": 70, "cond": 80})
    stock_use: int = 12         # a week, per business
    staff_decay: int = 4
    wear: int = 3
    low_mark: int = 30          # below this a resource is «low»: its trouble may come
    stock_out_mult: float = 0.3  # income with an empty stock
    restock_gain: int = 50
    restock_pct: int = 60       # of a week's income x level
    bonus_gain: int = 30
    bonus_pct: int = 80         # of a week's upkeep x level
    repair_gain: int = 40
    repair_pct: int = 8         # of the kind's price x level
    trouble_chance: float = 0.35  # a week, per business with a low resource
    neglect_weeks: int = 8      # weeks without any management: the manager starts to skim
    neglect_pct: int = 20
    audit_from: int = 8000      # a week's business net above this draws the tax office
    incident_loss: tuple[float, float] = (0.3, 0.8)  # owner report 25.10: of a week's income x level
    ops_floor: float = 0.75     # income x (floor + (1 - floor) x resource/100) for staff and condition
    audit_chance: float = 0.15


class PlayerGuardsBalance(Strict):
    """Owner request 25.10: players hired as bodyguards stand in the employer's fights (services/pguard.py)."""

    wages: list[int] = Field(default_factory=lambda: [500, 1500, 3000, 6000])  # eddies a month (a press)
    wage_min: int = 100            # owner request 25.10: any wage the employer types in, within these bounds
    wage_max: int = 1000000
    max_guards: int = 2


class PlayerProxyBalance(Strict):
    """Owner request 29.10: a hired player does the employer's gig in his place (services/proxy.py, docs/PLAN_PROXY.md)."""

    enabled: bool = True
    per_day: int = 3                # sendings of one hired player in a Kyiv day
    days: list[int] = Field(default_factory=lambda: [2, 2, 3, 3, 4, 5, 6, 7, 9])  # days of work by gig tier 1..9
    fee_min: int = 100              # the least pay for a sending
    retreat_hp_pct: int = 30        # the auto fight ends with a retreat below this share of health
    heal_hp_pct: int = 45           # the med injector is used below this share
    max_ticks: int = 40
    mail_cap: int = 3               # undelivered letters of consequences after which he cannot be sent again

    @field_validator("days")
    @classmethod
    def _nine(cls, v: list[int]) -> list[int]:
        if len(v) != 9 or any(d < 1 for d in v):
            raise ValueError("proxy.days needs 9 positive values (tiers 1..9)")
        return v


class PlayerChildBalance(Strict):
    """Owner request 25.10: a child of two players' characters."""

    bond_need: int = 20            # the bond of the pair («кореша» and closer)
    cost: int = 500                # each parent pays this
    mood: int = 10
    bond: int = 10


class PlayersBalance(Strict):
    """Interaction between the characters of different players (docs/EPIC_PLAYERS.md, phase 1)."""

    transfer_amounts: list[int] = Field(default_factory=lambda: [100, 500, 1000, 5000])
    transfer_fee_pct: int = Field(default=10, ge=0, le=90)  # the Afterlife barman keeps this share
    transfer_daily_cap: int = 10000  # eddies one player may send per real (Kyiv) day
    transfer_bond: int = 1
    drink: PlayerDrinkBalance = Field(default_factory=PlayerDrinkBalance)
    vouch: PlayerVouchBalance = Field(default_factory=PlayerVouchBalance)
    note: PlayerNoteBalance = Field(default_factory=PlayerNoteBalance)
    hookup: PlayerOfferBalance = Field(default_factory=lambda: PlayerOfferBalance(mood=15, bond=8))
    date: PlayerOfferBalance = Field(default_factory=lambda: PlayerOfferBalance(mood=10, bond=6))
    child: PlayerChildBalance = Field(default_factory=lambda: PlayerChildBalance())  # owner request 25.10
    duel: PlayerDuelBalance = Field(default_factory=PlayerDuelBalance)
    offer_ttl_hours: int = 48       # an unanswered offer expires
    guards: PlayerGuardsBalance = Field(default_factory=PlayerGuardsBalance)  # owner request 25.10
    proxy: PlayerProxyBalance = Field(default_factory=PlayerProxyBalance)  # owner request 29.10
    hire: PlayerHireBalance = Field(default_factory=PlayerHireBalance)
    together: PlayerTogetherBalance = Field(default_factory=PlayerTogetherBalance)
    quest: PlayerQuestBalance = Field(default_factory=PlayerQuestBalance)
    raid: RaidBalance = Field(default_factory=RaidBalance)
    crew: CrewBalance = Field(default_factory=CrewBalance)
    market: MarketBalance = Field(default_factory=MarketBalance)
    pvp: PvpBalance = Field(default_factory=PvpBalance)
    echo: EchoBalance = Field(default_factory=EchoBalance)
    joint: JointBalance = Field(default_factory=JointBalance)
    saga: SagaBalance = Field(default_factory=SagaBalance)
    rogue_ai: RogueAiBalance = Field(default_factory=RogueAiBalance)
    gangs: GangMembershipBalance = Field(default_factory=GangMembershipBalance)
    business: BusinessBalance = Field(default_factory=BusinessBalance)
    bond_max: int = 100
    bond_labels: list[tuple[int, str]] = Field(default_factory=lambda: [
        (0, "знакомые"), (20, "кореша"), (50, "братья по оружию"), (80, "семья по выбору")])
    bond_decay_after_days: int = 28  # without contact the bond cools by 1 per week after this many days
    mail_per_open: int = 6           # letters delivered per menu opening; the rest wait
    ping_cooldown_s: int = 600       # one group ping per recipient per this many seconds


class WeaponMod(Timed):
    """An attachment for one owned weapon (owner request). Works while that weapon is equipped."""

    key: str
    name: str
    description: str
    price: int = Field(ge=0)
    types: list[Literal["pistol", "smg", "shotgun", "rifle", "heavy", "melee", "exotic"]]
    mods: Mods


class HomeItem(Timed):
    """A thing for your home (owner request): bought once, moves with you, works every week."""

    key: str
    name: str
    description: str
    price: int = Field(ge=0)
    mods: Mods


class UpgradesBalance(Strict):
    max_weapon_mods: int = 2
    remove_refund_pct: int = 0
    weapon_mods: list[WeaponMod] = Field(default_factory=list)
    home: list[HomeItem] = Field(default_factory=list)


class RaceTier(Strict):
    key: str
    name: str
    dv: int
    stake: int
    fame: int = 0
    min_street_cred: int = 0


class VehicleBalance(Strict):
    """Vehicles (owner request): tuning, repairs, races, theft."""

    tune_max: int = 3
    garage_max: int = 8         # owner request 24.10: rides parked besides the one you drive (the fleet)
    item_sell_pct: int = 40     # owner request 24.10: a weapon or armor sold back to a shop: this % of its price
    tune_pct: int = 35          # one tuning level costs this % of the price x the next level
    sell_pct: int = 50
    stolen_sell_pct: int = 25   # a fence pays less for a stolen ride
    repair_pct: int = 60        # full repair (100 damage) costs this % of the price
    renumber_cost: int = 800    # new plates for a stolen ride
    stolen_heat_decay: int = -1 # heat falls slower while you drive a stolen ride
    steal_dv: int = 14          # REF + driving
    steal_price_max: int = 10000
    steal_fail_heat: int = 2
    steal_jail_chance: float = 0.25
    steal_jail_weeks: int = 3
    race_ap: int = 1
    crash_chance: float = 0.3   # a lost race ends in a crash this often
    crash_hp: tuple[int, int] = (4, 14)
    crash_damage: tuple[int, int] = (15, 45)
    wreck_at: int = 100         # damage at which the ride is gone
    races: list[RaceTier] = Field(default_factory=list)


class GroupGoal(Strict):
    key: str
    text: str                    # Russian, "{target}" is the number
    events: list[str]            # bus event names that count
    target: int = Field(ge=1)


class StarterPack(Strict):
    """Owner request 30.10: the pack that drops with the last first step."""

    eddies: int = Field(default=0, ge=0)
    items: list[Key] = Field(default_factory=list)       # armor and weapons for everyone
    pistols: dict[str, Key] = Field(default_factory=dict)  # signature weapon by the group of the starting kit


class RetentionBalance(Strict):
    """Bringing players back (owner request): onboarding steps, pings in the group, the shared goal of the day."""

    step_eddies: int = 200
    final_eddies: int = 1500
    final_cred: int = 2
    starter_pack: StarterPack = Field(default_factory=StarterPack)
    quiet_from: int = Field(default=23, ge=0, le=23)  # Kyiv hour: no pings from here ...
    quiet_to: int = Field(default=10, ge=0, le=23)    # ... until here
    ping_ttl_h: int = 8
    mail_ping_cooldown_h: float = 3                  # owner report 28.10 «спамит»: a letter ping per player at most this often
    goal_hour: int = Field(default=11, ge=0, le=23)   # the goal of the day is announced at this hour
    goal_reward: int = 400                           # eddies to every contributor
    goals_per_day: int = 4                           # owner report 24.10: a done goal opens the next one, up to this
    goals: list[GroupGoal] = Field(default_factory=list)


class SlotsBalance(Strict):
    """Owner request: a word in the chat spins the 🎰 for a small stake."""

    words: list[str] = Field(default_factory=lambda: ["слот", "слоты", "спин", "крутка", "крутануть", "казино"])
    max_message_chars: int = 40     # a longer message is a conversation, not a spin
    stake_default: int = 100
    stake_min: int = 50
    stake_max: int = 5000
    cooldown_s: int = 20
    daily_max: int = 30
    jackpot_mult: int = 20          # 777
    triple_mult: int = 5            # three of a kind
    two_sevens_mult: int = 2
    result_ttl_s: int = 90


class FishingRod(Timed):
    key: Key
    name: str
    tier: int = Field(ge=1, le=4)
    price: int = Field(ge=0)
    description: str


class FishCatch(Strict):
    key: Key
    name: str
    kind: Literal["junk", "fish", "loot", "legend"]
    rarity: int = Field(ge=0, le=3)  # 0 common .. 3 legendary: better rods find rarer things
    weight: float = Field(gt=0)
    price: int = Field(default=0, ge=0)  # fish and junk go to the keepnet and sell for this
    min_tier: int = Field(default=1, ge=1, le=4)
    effects: Effects = {}            # loot: applied at once (item_grant, cyberware_grant, eddies, ...)
    event: Key | None = None         # loot: an event to play now (a note in a bottle)
    text: str = ""                   # the line of the catch
    loud: str | None = None          # a public line for the group, {name} = the angler


class FishingBalance(Strict):
    words: list[str] = Field(default_factory=lambda: ["рыбалка", "порыбачить", "рыбачить", "рыбалку"])
    bait_cost: int = 20
    daily_casts: int = 15
    credit_markup_pct: int = 30      # a rod on credit: the debt is the price plus this
    junk_div_per_tier: float = 0.5   # junk weight / (1 + this x (tier - 1))
    rare_mult_per_tier: float = 0.6  # rarity r weight x (1 + this x (tier - 1) x r)
    harbor_district: Key = "waterfront"
    harbor_rare_mult: float = 1.5    # rarity >= 2 in the harbor
    harbor_sell_pct: int = 30
    dry_districts: list[Key] = Field(default_factory=lambda: ["badlands"])
    dry_fish_mult: float = 0.3
    rods: list[FishingRod] = Field(default_factory=list)
    catches: list[FishCatch] = Field(default_factory=list)


class ImmunoGrade(Strict):
    key: Key
    name: str
    strength: int = Field(ge=1, le=4)
    from_load: int = Field(ge=0)        # the chrome load from which the body needs at least this strength
    price_per_point: int = Field(ge=0)  # eddies a week per point of load
    black_market: bool = False          # only through the black market supplier
    mood: int = 0                       # owner request 24.10: a covered week lifts the mood by this much
    description: str = ""


class ImmunoMiss(Strict):
    """A week without the needed suppressants: per level of shortfall (needed strength - taken strength)."""

    hp: int = 3
    mood: int = 3
    glitches: int = 1
    humanity_from_week: int = 2      # from this missed week in a row humanity goes too
    humanity: int = 1
    reject_from_week: int = 3        # from this missed week an implant may be rejected
    reject_chance: float = 0.15      # per level of shortfall


class ImmunoBalance(Strict):
    tolerance_mult: dict[str, float] = Field(default_factory=lambda: {
        "fragile": 2.0, "normal": 1.0, "resistant": 0.6, "ironclad": 0.3, "smasher": 0.0})
    mk_load_pct: int = 25            # every Mk step of an implant adds this % of its humanity cost to the load
    grades: list[ImmunoGrade] = Field(default_factory=list)
    miss: ImmunoMiss = Field(default_factory=ImmunoMiss)


class LifestyleLevel(Strict):
    key: Key
    name: str
    weekly: int = Field(ge=0)
    mods: Mods = {}
    description: str = ""


class LifestyleBalance(Strict):
    default: Key = "modest"
    levels: list[LifestyleLevel] = Field(default_factory=list)
    habit_weeks: int = 4             # weeks at a level before a drop hurts
    habit_mood: int = 3              # mood a week lost for each level dropped, ...
    habit_weeks_penalty: int = 3     # ... for this many weeks


class StaffMember(Timed):
    key: Key
    name: str
    weekly: int = Field(ge=0)
    hire_fee: int = Field(default=0, ge=0)
    min_status: int = Field(default=0, ge=0)
    mods: Mods = {}
    description: str = ""


class DrugSpec(Timed):
    key: Key
    name: str
    price: int = Field(ge=0)
    mods: Mods = {}                  # while the dose works: until the next press of «Прожить»
    effects: Effects = {}            # at the moment of the dose (side effects)
    addiction: int = Field(ge=0)     # points of habit per dose
    withdrawal: Mods = {}            # mods while addicted and without a dose
    withdrawal_effects: Effects = {} # every week of withdrawal
    overdose_chance: float = Field(default=0.0, ge=0, le=1)  # a second dose in the same press
    overdose_hp: int = 10
    black_market: bool = False
    description: str = ""


class DrugsBalance(Strict):
    addiction_threshold: int = 30    # habit points from which a drug is an addiction
    decay_per_week: int = 3          # habit points that fade every week without the drug
    rehab_base_price: int = 2000
    rehab_price_per_point: int = 40
    rehab_base_chance: float = 0.85
    rehab_chance_per_point: float = 0.008
    rehab_min_chance: float = 0.15
    rehab_ap: int = 1
    drugs: list[DrugSpec] = Field(default_factory=list)


class NeuroRehabBalance(Strict):
    """Owner request: buy humanity back. A course returns `humanity` of what the implants took for good."""

    humanity: int = 5
    base_price: int = 6000
    growth: float = 1.6          # every course of this life costs this much more
    ap: int = 1


class MkStep(Strict):
    name: str
    price_mult: float            # of the implant's price
    mod_mult: float              # positive modifiers of the implant x this (rounded up); side effects stay


class MkBalance(Strict):
    ap: int = 1
    steps: list[MkStep] = Field(default_factory=lambda: [
        MkStep(name="Mk2", price_mult=1.5, mod_mult=1.5), MkStep(name="Mk3", price_mult=3.0, mod_mult=2.0)])


class BlackMarketBalance(Strict):
    """Owner request: prototypes of the coming eras before their time, at a price and a risk."""

    min_street_cred: int = 5
    slots: int = 5                   # a new showcase every month (press) of the character's life
    max_gap_years: int = 40          # how far ahead the prototypes come from
    price_mult_base: float = 2.0
    price_mult_per_10y: float = 0.5
    price_mult_max: float = 4.0
    scam_chance: float = 0.10        # the seller vanishes with the money
    heat: int = 1
    complication_chance: float = 0.25  # an implant put in by the market's own ripper
    black_ice_contact: int = 5000    # the supplier of «Чёрный лёд»


class WealthTier(Strict):
    min: int = Field(ge=0)           # wealth (eddies + business + ride) from which the tier holds
    name: str
    chance: float = Field(ge=0, le=1)  # a week's chance of a threat to the rich
    events: list[Key] = []           # what may come at this tier (and every lower one)


class WealthBalance(Strict):
    """Owner request: the richer, the harder. Threats grow with the wealth; staff and home things defend."""

    tiers: list[WealthTier] = Field(default_factory=list)
    max_per_press: int = 1


class AuctionBalance(Strict):
    """Owner request: a legendary lot in the group; players outbid each other, the money is held until outbid."""

    duration_hours: int = 72
    start_price_pct: float = 0.6     # of the lot's price
    min_step_pct: int = 10           # the next bid is at least this much above the top one
    ttl_post_h: int = 6              # announcements in the group live this long


class InfluenceBalance(Strict):
    """Owner request: money that changes the shared city."""

    war_price: int = 15000
    war_days: int = 7
    silence_per_heat: int = 2000
    fixer_bribe: int = 3000
    fixer_rel: int = 10
    charity_price: int = 10000
    charity_effects: Effects = {}
    pr_price: int = 5000
    pr_effects: Effects = {}
    ap: dict[str, int] = Field(default_factory=lambda: {"war": 2, "silence": 1, "fixer": 1, "charity": 2, "pr": 1})  # owner report 25.10


class EmpireStep(Strict):
    key: Key
    name: str
    title: str
    price: int = Field(ge=0)
    weekly: int = Field(ge=0)
    income: tuple[int, int] = (0, 0)  # owner report 23.10: a step pays every week (rolled), on top of its upkeep
    mods: Mods = {}
    description: str = ""


class StaffPosition(Strict):
    key: Key
    name: str
    weekly: int = Field(ge=0)


class MegacorpUpgrade(Strict):
    """Owner request 25.10: a department of the player's megacorp, bought level by level."""

    key: Key
    name: str
    text: str
    prices: list[int]                # one per level
    income_pct: int = 0              # the empire's weekly income, per level
    sec_dv: int = 0                  # an attack on the tower is this much harder, per level
    dmg_cut_pct: int = 0             # an attack takes this much less integrity, per level
    mods: Mods = {}                  # the owner's mods, per level
    soldiers: int = 0                # fighters of the own army joining every lived month, per level
    staff: int = 0                   # more posts for players, per level


class MegacorpBalance(Strict):
    """Owner request: a player's own megacorp, and other players who can raze it."""

    integrity_max: int = 100
    security_price: list[int] = Field(default_factory=lambda: [0, 20000, 50000, 120000, 300000])  # level 0..4
    security_dv: list[int] = Field(default_factory=lambda: [12, 14, 16, 18, 21])
    repair_per_point: int = 600
    attack_ap: int = 2
    attack_damage: tuple[int, int] = (10, 25)
    attack_fail_hp: int = 10
    attack_reward: int = 3000
    attack_heat: int = 2
    attack_cooldown_h: int = 20      # one attacker, one corp
    name_max_chars: int = 30
    # owner request 24.10: the owner hires players (app/services/pcorp.py)
    staff: list[StaffPosition] = Field(default_factory=list)
    staff_max: int = 8
    staff_dv_per: int = 1            # each employee makes an attack on the tower harder
    staff_dv_cap: int = 3
    task_ap: int = 2
    task_per_press: int = 2
    task_dv: int = 13
    task_dv_per_pos: int = 1         # a higher post gets harder jobs
    task_bonus_pct: int = 40         # the employee's bonus: % of the weekly salary
    task_revenue: tuple[int, int] = (4000, 10000)  # the corp's revenue, by mail to the owner, x (1 + post index / 2)
    task_fail_hp: int = 6
    upgrades: list[MegacorpUpgrade] = Field(default_factory=list)  # owner request 25.10


class EconomyBalance(Strict):
    """Owner request: money sinks that grow with the chrome and the wealth (docs/PLAN_ECONOMY.md)."""

    auction: AuctionBalance = Field(default_factory=AuctionBalance)
    influence: InfluenceBalance = Field(default_factory=InfluenceBalance)
    empire: list[EmpireStep] = Field(default_factory=list)
    empire_sell_pct: int = Field(default=50, ge=0, le=100)  # owner report 23.10: the top step can be sold back
    megacorp: MegacorpBalance = Field(default_factory=MegacorpBalance)
    wealth: WealthBalance = Field(default_factory=WealthBalance)
    black_market: BlackMarketBalance = Field(default_factory=BlackMarketBalance)
    neuro: NeuroRehabBalance = Field(default_factory=NeuroRehabBalance)
    mk: MkBalance = Field(default_factory=MkBalance)
    immuno: ImmunoBalance = Field(default_factory=ImmunoBalance)
    lifestyle: LifestyleBalance = Field(default_factory=LifestyleBalance)
    staff: list[StaffMember] = Field(default_factory=list)
    drugs: DrugsBalance = Field(default_factory=DrugsBalance)


class ChatGamesBalance(Strict):
    slots: SlotsBalance = Field(default_factory=SlotsBalance)
    fishing: FishingBalance = Field(default_factory=FishingBalance)


class ArcadeWordle(Strict):
    reward_by_attempt: list[int] = [1500, 1000, 700, 450, 300, 200]  # eddies for a word found on the 1st ... 6th try
    words: list[str] = Field(min_length=30)  # five lower-case Russian letters each, no `ё`

    @field_validator("words")
    @classmethod
    def _words(cls, v: list[str]) -> list[str]:
        bad = [w for w in v if len(w) != 5 or not all("а" <= c <= "я" for c in w)]
        if bad:
            raise ValueError(f"arcade words must be five letters а-я: {bad[:5]}")
        if len(set(v)) != len(v):
            raise ValueError("arcade words must be unique")
        return v


class ArcadeSnake(Strict):
    size: int = Field(default=8, ge=5, le=10)
    eddies_per_food: int = Field(default=15, ge=0)


class ArcadeGeo(Strict):
    rounds: int = Field(default=5, ge=1, le=10)
    eddies_per_point: int = Field(default=30, ge=0)


class ArcadeBeat(Strict):
    start_len: int = Field(default=3, ge=2, le=6)
    free_rounds: int = Field(default=3, ge=0)  # the first rounds pay nothing
    eddies_per_round: int = Field(default=40, ge=0)


class ArcadeTown(Strict):
    size: int = Field(default=6, ge=3, le=8)


class ArcadeWebGame(Strict):
    name: str
    about: str = ""
    license: str = ""
    rate: float = Field(ge=0)  # eddies per point of a run ...
    max_per_run: int = Field(ge=0)  # ... up to this
    max_score: int = Field(ge=1)  # a client game can be faked: a score above this is cut
    unit: str = "очков"


class ArcadeWeb(Strict):
    """Owner request 29.10 (docs/PLAN_ARCADE_WEB.md): open-source HTML5 games next to the Mini App."""

    base_url: str = "https://nightcity.qa-temple-of-serenity.cc/static/arcade"
    token_ttl_h: int = Field(default=12, ge=1)
    min_interval_s: int = Field(default=20, ge=0)  # runs closer than this to the last paid one of a game pay nothing
    games: dict[str, ArcadeWebGame] = {}


class ArcadeBalance(Strict):
    """Owner request 29.10 (docs/PLAN_ARCADE.md): small games from the popular HTML5 list; the money is symbolic."""

    daily_cap: int = Field(default=3000, ge=0)  # eddies a player can earn in the arcade per Kyiv day
    top_size: int = Field(default=3, ge=1)
    wordle: ArcadeWordle
    snake: ArcadeSnake = Field(default_factory=ArcadeSnake)
    geo: ArcadeGeo = Field(default_factory=ArcadeGeo)
    beat: ArcadeBeat = Field(default_factory=ArcadeBeat)
    town: ArcadeTown = Field(default_factory=ArcadeTown)
    web: ArcadeWeb = Field(default_factory=ArcadeWeb)


class PathRank(Strict):
    name: str
    xp: int = Field(ge=0)
    min_cred: int = Field(default=0, ge=0)   # owner request: a fixer grows with the street cred
    min_fame: int = Field(default=0, ge=0)
    seat: bool = False                       # the rank needs the one seat of the city (the mayor)
    income: int = Field(default=0, ge=0)     # eddies a press (a clinic, a salary, a cut of the deals)
    mods: Mods = {}
    text: str = ""


class PathAction(Strict):
    key: Key
    name: str
    text: str
    ap: int = Field(default=1, ge=0, le=12)
    price: int = Field(default=0, ge=0)
    min_rank: int = Field(default=1, ge=1)
    stat: Stat
    skill: Skill | None = None
    dv: int = Field(ge=5, le=30)
    contexts: list[str] = Field(default_factory=list)
    pay: tuple[int, int] = (0, 0)            # eddies on success, grows with the rank (rank_pay_pct)
    xp: int = Field(default=1, ge=0)
    fail_xp: int = Field(default=0, ge=0)
    effects: Effects = {}
    fail_effects: Effects = {}
    win_text: str = ""
    fail_text: str = ""
    seat: bool = False                       # success takes the seat of the city

    @field_validator("contexts")
    @classmethod
    def _ctx(cls, v: list[str]) -> list[str]:
        bad = [c for c in v if c not in MOD_CHECK_CONTEXTS]
        if bad:
            raise ValueError(f"unknown check contexts {bad}")
        return v


class PathDef(Strict):
    key: Key
    name: str
    text: str
    ranks: list[PathRank] = Field(min_length=2)
    actions: list[PathAction] = Field(min_length=1)


class ContractJob(Strict):
    key: Key
    name: str
    text: str
    dv: int = Field(ge=5, le=30)
    pool: int = Field(ge=0)
    min_rank: int = Field(default=2, ge=1)


class ContractBalance(Strict):
    """Owner request: a fixer hands a job to another player with a plan and a split."""

    min_rank: int = 2
    shares: list[int] = Field(default_factory=lambda: [30, 50, 70])  # the fixer's part
    plan_bonus: int = 2                      # the executor who follows the plan
    rank_bonus: int = 1                      # a better fixer plans better: per rank above the first
    xp: int = 3
    fail_xp: int = 1
    cred: int = 2
    fail_heat: int = 2
    fail_hp: tuple[int, int] = (3, 12)
    jobs: list[ContractJob] = Field(default_factory=list)


class StudioBook(Strict):
    """A book of Ozon671Games a braindance can be shot after (owner request)."""

    key: Key
    title: str
    genre: str
    logline: str
    stat: Stat
    skill: Skill | None = None
    dv: int = Field(ge=5, le=30)
    subtitles: list[str] = Field(min_length=1)


class StudioBalance(Strict):
    """Owner request: the braindance director shoots braindances after the books; the city watches them."""

    path: Key = "bd_director"
    min_rank: int = 1
    ap: int = Field(default=3, ge=0, le=12)
    price: int = 800                     # actors, rig, rent of the set; grows per rank
    price_per_rank: int = 600
    edit_dv_bonus: int = 0               # the edit (TECH + tech) is rolled against the book's dv plus this
    pay_by_stars: list[int] = Field(default_factory=lambda: [300, 900, 2000, 4000, 8000], min_length=5, max_length=5)
    xp_by_stars: list[int] = Field(default_factory=lambda: [1, 2, 4, 6, 9], min_length=5, max_length=5)
    fame_from_stars: int = 4             # a premiere with this many stars or more gives the author fame +1
    watch_price_per_star: int = 40
    watch_mood_per_star: int = 2
    royalty_pct: int = 70                # of a ticket, to the author by mail
    keep: int = 15                       # premieres on the city list
    title_max_chars: int = 40
    books: list[StudioBook] = Field(default_factory=list)


class PathsBalance(Strict):
    """Owner request: the career paths of a fixer, a ripper, a politician and a braindance director."""

    switch_price: int = 5000
    rank_pay_pct: int = 30
    paths: list[PathDef] = Field(default_factory=list)
    contract: ContractBalance = Field(default_factory=ContractBalance)
    studio: StudioBalance = Field(default_factory=StudioBalance)


class NpcArchetype(Strict):
    """A kind of city NPC (owner request: freedom of action, docs/PLAN_FREEDOM.md)."""

    key: Key
    name: str                         # the role as the place list shows it: «бармен»
    dv: int = Field(ge=8, le=25)      # how hard the NPC is to fool, rob or talk round
    armed: bool = False               # fights back: a failed or violent deed starts a real fight
    danger: int = Field(default=1, ge=1, le=5)
    faction: Literal["none", "gang", "ncpd", "corp"] = "none"
    civilian: bool = True             # killing one counts towards the maniac (stage 4)
    money: tuple[int, int] = (20, 200)
    service: str | None = None        # bar | dealer | doc | cop | fence
    criminal: bool = False            # can be handed to the police
    lines: list[str] = Field(min_length=1)  # what the NPC does when the character comes up


class PlaceType(Strict):
    key: Key
    name: str                         # «Бар»
    names: list[str] = Field(min_length=1)  # venue names, picked per district
    text: str
    crowd: int = Field(default=1, ge=0, le=3)  # witnesses: 0 empty alley .. 3 packed club
    fixed: list[Key] = Field(default_factory=list)   # archetypes always there
    extra: list[Key] = Field(default_factory=list)   # a pool for the other slots
    extra_count: int = Field(default=2, ge=0, le=6)


class FreeVerb(Strict):
    """A deed with a city NPC. Numbers here, texts in app/bot/texts_city.py."""

    key: Key
    name: str = Field(max_length=22)
    ap: int = Field(default=1, ge=0, le=3)
    price: int = Field(default=0, ge=0)
    stat: Stat | None = None          # None: no check
    skill: Skill | None = None
    dv_add: int = 0                   # added to the NPC's dv
    violent: bool = False
    kill: bool = False
    take_pct: int = Field(default=0, ge=0, le=100)  # of the NPC's money on success
    att_ok: int = 0
    att_fail: int = 0
    heat_ok: int = 0                  # heat on success, plus heat_per_crowd x the place's crowd
    heat_fail: int = 0
    heat_per_crowd: int = 0
    fight_on_fail: bool = False       # an armed NPC fights when this fails
    effects_ok: Effects = {}
    effects_fail: Effects = {}
    only: list[str] = Field(default_factory=list)  # service kinds or "criminal" / "civilian" / "armed"; empty = anyone
    once_a_day: bool = False


class NpcService(Strict):
    """What a working NPC sells (the verb `service`): a drink, a patch-up, a word with the precinct."""

    name: str = Field(max_length=22)
    price: int = Field(ge=0)
    effects: Effects = {}
    text: str
    need_heat: bool = False           # only when the character is wanted (a cop's "forget it")


class ManiacLevel(Strict):
    kills: int = Field(ge=1)
    name: str
    mods: Mods = {}


class ManiacBalance(Strict):
    """Owner request: the maniac mode. Civilians killed one after another make the character a maniac with a nickname."""

    levels: list[ManiacLevel] = Field(default_factory=list)
    thirst_weeks: int = 4             # a press without a kill after this many weeks: the thirst
    thirst_mood: int = 8
    clue_chance: float = 0.35         # the NCPD finds a clue after a press with a kill
    reveal_clues: int = 5
    investigate_ap: int = 1
    investigate_dv: int = 15
    investigate_reward: int = 3000
    quit_weeks: int = 8               # weeks without a kill to stop being a maniac
    first: list[str] = Field(default_factory=list)   # nickname parts
    second: list[str] = Field(default_factory=list)


class PsychoLevel(Strict):
    rampages: int = Field(ge=0)
    name: str
    mods: Mods = {}
    voices: float = Field(ge=0, le=1, default=0.2)   # chance the voices choose a deed
    glitch: float = Field(ge=0, le=1, default=0.1)   # share of words that break


class CyberpsychoBalance(Strict):
    """Owner request: the cyberpsychopath mode. A fugitive psycho in the city."""

    levels: list[PsychoLevel] = Field(default_factory=list)
    rampage_ap: int = 2
    rampage_heat: int = 2
    rampage_mood: int = 6
    voices: list[str] = Field(default_factory=list)


class CityLifeBalance(Strict):
    """Owner request: the memory of the city and the living city (docs/PLAN_FREEDOM.md, stages 4-5)."""

    avenge_chance: float = Field(ge=0, le=1, default=0.5)    # a killed NPC is replaced by a relative who wants revenge
    vendetta_chance: float = Field(ge=0, le=1, default=0.5)  # per press: the avenger strikes
    grudge_att: int = -60
    grudge_chance: float = Field(ge=0, le=1, default=0.25)
    grateful_att: int = 60
    grateful_chance: float = Field(ge=0, le=1, default=0.3)
    gift: tuple[int, int] = (100, 500)
    close_kills: int = 2              # kills in one place within close_hours: the NCPD closes it
    close_hours: int = 24
    guests: int = 2                   # NPCs of the district visiting another place
    guest_hours: int = 6
    rumor_days: int = 3
    rumors: int = 2                   # lines a successful «ask» tells
    hire_att: int = 25
    hire_price: list[int] = Field(default_factory=lambda: [0, 1200, 2000, 3500, 5000, 8000])  # by the NPC's danger
    hire_weeks: int = 4
    hire_mods: Mods = {}
    squad_max: int = 3  # owner request 24.10: up to three hired NPCs fight at your side


class TributeBalance(Strict):
    """Owner request 27.10: a city NPC who was made to pay pays every lived month (services/city.py)."""

    base: dict[str, int] = Field(default_factory=dict)   # archetype -> eddies a month
    default: int = 1500
    per_cred: int = 25
    per_fame: int = 50
    cap: int = 150000
    max_payers: int = 5
    stop_chance: float = 0.1


class FreedomBalance(Strict):
    """Owner request: places, city NPCs and deeds with them (docs/PLAN_FREEDOM.md, stage 2)."""

    life: CityLifeBalance = Field(default_factory=CityLifeBalance)
    maniac: ManiacBalance = Field(default_factory=ManiacBalance)
    cyberpsycho: CyberpsychoBalance = Field(default_factory=CyberpsychoBalance)
    tribute: TributeBalance = Field(default_factory=TributeBalance)  # owner request 27.10

    services: dict[str, NpcService] = Field(default_factory=dict)

    respawn_days: int = 7             # a dead NPC is replaced after this many real days
    hurt_days: int = 2                # a beaten NPC is away this long
    notes_keep: int = 6               # memory lines per NPC and character
    chat_lines: int = 8               # typed lines per talk
    free_per_day: int = 40            # free-text deeds (stage 3) per character and real day
    archetypes: list[NpcArchetype] = Field(default_factory=list)
    places: list[PlaceType] = Field(default_factory=list)
    verbs: list[FreeVerb] = Field(default_factory=list)

    @model_validator(mode="after")
    def _refs(self) -> "FreedomBalance":
        keys = {a.key for a in self.archetypes}
        for p in self.places:
            bad = [k for k in p.fixed + p.extra if k not in keys]
            if bad:
                raise ValueError(f"place {p.key!r}: unknown archetypes {bad}")
        return self


class PurgeDefense(Strict):
    key: Key
    name: str = Field(max_length=22)
    price: int = Field(ge=0)
    defense: int = Field(ge=1)
    text: str


class MortalityBalance(Strict):
    """Owner request 24.10: the death count of all Night City for a day in the morning news (services/mortality.py).
    The exact part comes from the game (fights, city people, player characters); the city's own background around it."""

    natural: tuple[int, int] = (140, 190)       # illness, age, accidents
    violent: tuple[int, int] = (25, 60)         # streets, guns, gangs
    purge_extra: tuple[int, int] = (250, 600)   # a Purge night in the last 24 hours
    per_gang_war: tuple[int, int] = (5, 15)     # every gang war of the tick
    after_share: float = 0.5                    # a crisis that just ended still weighs this share of its multiplier
    crisis: dict[str, dict[str, float]] = Field(default_factory=dict)   # crisis key -> {natural: x, violent: y}
    keep_days: int = 30


class ArenaFighter(Strict):
    """A rung of the arena ladder (owner request 30.10, docs/PLAN_WORLD2.md phase 4)."""

    name: str
    title: str = ""
    danger: int = Field(ge=1, le=5)
    hp_mult: float = Field(default=1.6, ge=0.5, le=6)
    purse: int = Field(ge=0)
    cred: int = Field(default=1, ge=0)
    fame: int = Field(default=0, ge=0)
    style: str = ""


class ArenaBalance(Strict):
    """Owner request 30.10: the fighting arena. Bare hands and arm chrome, to a knockout, nobody dies."""

    enabled: bool = True
    ap: int = Field(default=1, ge=0, le=5)
    per_day: int = Field(default=2, ge=1)
    faction: Key = "animals"            # whose fighters the opponents borrow (the look and the moves)
    bets: list[int] = Field(default_factory=lambda: [0, 1000, 5000])
    bet_mult: float = 2.0
    lose_mood: int = -3
    story: list[Key] = Field(default_factory=list)  # forced events, unlocked by fights (services/arena.py)
    names: dict[str, str] = Field(default_factory=dict)  # place -> the arena's name there ("nc" by default)
    ladder: list[ArenaFighter] = Field(default_factory=list)


class Fight3dBalance(Strict):
    """Owner request 31.10: the fight in first person (app/engine/realtime.py, docs/PLAN_FIGHT3D.md phase 2)."""

    enabled: bool = True
    tick_s: float = Field(default=3.0, gt=0.5, le=10)  # a tick of the text fight in real time (combat.max_rounds: «about 3 seconds»)
    admins_only: bool = True                            # the «Бой в 3D» button: admins first


class City3dBalance(Strict):
    """Owner request 30.10: a car of the 3D district hits a resident (app/services/city3d.py::hit)."""

    hurt_speed: float = 9.0        # the ride's speed (units/s) from which a resident is hurt; slower is a push
    heat: int = 1                  # the street saw it
    heat_law: int = 2              # a cop or a corporate guard was hit
    law: list[Key] = Field(default_factory=lambda: ["cop", "corp_guard", "detective"])
    att_hurt: int = -40
    att_push: int = -10
    per_day: int = 10              # hurt residents per character and real day; more are only knocked over
    escape_cd_min: int = 20        # phase 2: one heat point off for a lost patrol, not more often than this
    escape_per_day: int = 3
    crash_speed: float = 10.0      # phase 9: a wall hit from this speed damages the ride
    crash_cap: int = 90            # the 3D never wrecks a ride completely


class ChallengeBalance(Strict):
    """Owner request 25.10: a fighter who outgrew the city gets challenges that double (services/challenge.py)."""

    score_from: int = 130          # power score (win% + health% vs the hardest enemies) that marks an outgrown build
    hour: int = 12                 # Kyiv hour of the daily analysis
    expire_hours: int = 72         # an ignored challenge expires: the street calls it cowardice
    danger: int = 5
    swarm_max: int = 8
    reward_base: int = 6000        # eddies × 2^(level-1)
    reward_cap: int = 400000
    fame_per_level: int = 1
    cred_per_level: int = 2
    coward_cred: int = 5
    coward_fame: int = 2
    factions: list[str] = Field(default_factory=lambda: ["militech", "arasaka", "maxtac", "maelstrom", "cyberpsycho",
                                                         "netwatch", "sixth_street", "kang_tao"])
    # owner request 25.10 (2): a challenge is three stages, the threat bites every day it is open, the rich get theirs
    boss_hp_mult: float = 1.5      # the last fight of a fighter's challenge
    check_ap: int = 1              # a check stage costs this
    check_dv: int = 14             # + 2 per level, up to check_dv_cap
    check_dv_cap: int = 32
    check_fail_hp: int = 3         # × level, never below 1 health
    hit_hp: int = 4                # daily, × level, capped; by mail (never kills)
    hit_hp_cap: int = 30
    hit_eddies: int = 500          # daily, × 2^(level-1), capped
    hit_eddies_cap: int = 20000
    wealth_from: int = 250000      # eddies on hand that make a character «rich»
    w_pct: int = 2                 # daily loss of the money on hand, % (+1 per level), capped
    w_pct_cap: int = 10
    escrow_pct: int = 10           # frozen at the start, × 2^(level-1), capped; back only on a win
    escrow_cap_pct: int = 60
    w_pay: int = 10000             # the price of a pay stage, × 2^(level-1) (the board: double)
    w_fail_pct: int = 5            # a failed check of a wealth stage: this % of the money (+2 per level), capped
    w_fail_cap: int = 25
    w_reward: int = 20000          # × 2^(level-1)
    w_reward_cap: int = 2000000


class CorpRaidBalance(Strict):
    """Owner request 25.10: a corp member raids rival megacorps (services/corpraid.py)."""

    ap: dict[str, int] = Field(default_factory=lambda: {"depot": 2, "lab": 3, "tower": 4})
    loot: dict[str, tuple[int, int]] = Field(default_factory=lambda: {"depot": (3000, 7000), "lab": (15000, 30000),
                                                                      "tower": (60000, 120000)})
    loot_cap: int = 1500000
    per_day: int = 3               # raids on one corp a Kyiv day (owner request 25.10)
    rep_target: dict[str, int] = Field(default_factory=lambda: {"depot": -10, "lab": -15, "tower": -25})
    rep_own: int = 6
    cred: dict[str, int] = Field(default_factory=lambda: {"depot": 2, "lab": 3, "tower": 5})
    heat: int = 2
    nemesis_chance: float = 0.3
    dyn_ap: int = 3                # a strike on a corp that came and goes (services/dyn.py)
    dyn_power: int = 30            # its power a won strike takes
    dyn_loot: tuple[int, int] = (15000, 35000)


class CorpStandBalance(Strict):
    """Owner request 25.10 «какой урон корпорации наношу … выносить корпорации» (services/corpstand.py)."""

    max_hp: int = 100
    dmg: dict[str, int] = Field(default_factory=lambda: {"depot": 3, "lab": 6, "tower": 15})  # + half per level over 2
    regen: int = 4                 # a day
    down_days: int = 3
    rise_hp: int = 60
    fall_bonus: int = 300000       # split among those who hit it, by the damage
    fall_fame: int = 3
    fall_corp_control: int = -30   # the corp's hold on its district


class ArmyTier(Strict):
    key: Key
    name: str
    size: int                      # fighters needed
    in_fight: int                  # fighters at the character's side in a fight
    racket: int = 0                # eddies a month per fighter
    named: bool = False            # the tier needs a name


class ArmyBalance(Strict):
    """Owner request 25.10: the character's own fighters, from a squad to a legion (services/army.py)."""

    tiers: list[ArmyTier] = Field(default_factory=list)
    street_ap: int = 1
    street_price: int = 1500       # a street recruitment
    street_dv: int = 12
    street_per_margin: int = 4     # one more recruit per this much over the dv (up to street_max)
    street_max: int = 3
    street_per_day: int = 3
    npc_price_mult: float = 2.0    # a city resident recruited for good: the month's hire price × this
    defector_chance: float = 0.15  # after a won fight with a gang: one of them asks to join
    wage_per_danger: int = 120     # a month, per fighter
    unpaid_desert: float = 0.25
    xp_per_level: int = 3          # wins to grow one step: this × the current step
    war_ap: int = 3
    war_per_day: int = 3
    war_extra_per_tier: int = 2    # more enemies per tier above the gang
    war_ally_mult: int = 2         # in a war twice as many fighters go
    war_loot: tuple[int, int] = (3000, 8000)  # × tier index
    war_gang: int = -15            # the control meter of the district
    war_defectors: tuple[int, int] = (1, 3)
    ally_cap: int = 8
    nicks: list[str] = Field(default_factory=list)


AmbitionKind = Literal["pay", "check", "wait", "army", "free_district", "city_gang", "city_corp", "fame", "humanity",
                       "cred", "child", "raids", "wars", "tower"]


class AmbitionStage(Strict):
    kind: AmbitionKind
    name: str
    text: str = ""
    value: int = 0                 # eddies, weeks, fighters, the meter, fame, humanity, cred, raids, wars
    stat: str | None = None        # a check
    skill: str | None = None
    dv: int = 0


class Ambition(Strict):
    key: Key
    name: str
    text: str
    target: bool = False           # the player picks a megacorp as the target (erase a corp)
    stages: list[AmbitionStage] = Field(min_length=1)
    title: str                     # the legend title it gives
    reward: Effects = {}


class AmbitionsBalance(Strict):
    """Owner request 25.10: the big goals after the ceiling (services/ambition.py, docs/PLAN_ENDGAME.md part 2)."""

    check_ap: int = 1
    items: list[Ambition] = Field(default_factory=list)


EndingKind = Literal["pay", "check", "wait", "army", "free_district", "city_gang", "city_corp", "fame", "humanity",
                     "cred", "child", "raids", "wars", "tower", "fight", "mayor", "turf"]


class EndingStage(Strict):
    kind: EndingKind
    name: str
    text: str = ""
    value: int = 0
    stat: str | None = None
    skill: str | None = None
    dv: int = 0
    faction: str = ""              # a fight: the gang or the corp
    lv_plus: int = 0               # a fight: the challenge level over the character's own ladder
    boss: str = ""


class EndingGift(Strict):
    """What the ending leaves to every next heir of the player."""

    eddies: int = 0
    attr: int = 0
    perk: int = 0
    street_cred: int = 0
    fame: int = 0
    text: str = ""


class Ending(Strict):
    key: Key
    name: str
    text: str
    cause: str                     # the death cause that closes the life
    title: str
    final: str                     # the last words of the life
    relic: str = ""                # services/relic.py: the legend takes it into the Hall
    gift: EndingGift = Field(default_factory=EndingGift)
    stages: list[EndingStage] = Field(min_length=1)


class EndingsBalance(Strict):
    """Owner request 25.10, docs/PLAN_ENDGAME2.md part 2: the endings of a life (services/finale.py)."""

    check_ap: int = 1
    fight_ap: int = 3
    items: list[Ending] = Field(default_factory=list)


ExpeditionKind = Literal["pay", "check", "fight", "wait"]
ExpeditionRule = Literal["no_guns", "no_chrome", "no_hacks", "half_armor", "hp_cap"]


class ExpeditionStage(Strict):
    kind: ExpeditionKind
    name: str
    text: str = ""
    value: int = 0                 # eddies / weeks
    stat: str | None = None
    skill: str | None = None
    dv: int = 0
    fail: Effects = {}             # a failed check costs this
    faction: str = ""
    lv_plus: int = 0
    boss: str = ""
    extra: int = 0


class Expedition(Strict):
    key: Key
    name: str
    text: str
    rules: list[ExpeditionRule] = Field(default_factory=list)
    rules_text: str
    solo: bool = False             # no own fighters, guards or squad
    hp_cap: float = 1.0            # the share of max hp a fight starts with (rule hp_cap)
    reward: Effects = {}
    relic: str = ""                # services/relic.py
    stages: list[ExpeditionStage] = Field(min_length=1)


class ExpeditionsBalance(Strict):
    """Owner request 25.10, docs/PLAN_ENDGAME2.md part 4: expeditions to other worlds (services/expedition.py)."""

    check_ap: int = 1
    fight_ap: int = 3
    again_weeks: int = 4           # the same expedition again after this many lived weeks
    items: list[Expedition] = Field(default_factory=list)


class WearBalance(Strict):
    """Owner request 25.10, docs/PLAN_ENDGAME2.md part 5: the price of power (engine/wear.py)."""

    enabled: bool = True           # owner request 25.10 (later): «не усложнять жизнь», the wear is switched off
    from_level: int = 3            # the challenge ladder from which the body wears (or the city measures it outgrown)
    per_month: int = 1
    max: int = 12
    check_per: int = 2             # check.combat −1 per this many levels of wear
    hp_per_level: int = 3          # hp_max − this per level
    ref_at: list[int] = Field(default_factory=lambda: [6, 10])  # REF −1 at each of these levels
    price_per_level: int = 25000


class RelicDef(Strict):
    key: Key
    name: str
    text: str
    title: str
    source: str                    # where it comes from, for the player
    mods: dict[str, int]


class RelicsBalance(Strict):
    """Owner request 25.10, docs/PLAN_ENDGAME2.md part 6: one of each in the city (services/relic.py)."""

    items: list[RelicDef] = Field(default_factory=list)


class HaritonQuestion(Strict):
    key: Key
    text: str                      # the question of the week
    p_yes: float = Field(ge=0.05, le=0.95)  # the chance the answer is «Да»
    yes_text: str
    no_text: str


class HaritonBalance(Strict):
    """Owner request 25.10: the event of the week with the cat Hariton, a bet «Да» or «Нет» (services/hariton.py)."""

    hour: int = 12                 # Monday: the week closes and the new question opens at this Kyiv hour
    min_bet: int = 100
    max_bet: int = 50000           # per player per week
    presets: list[int] = Field(default_factory=lambda: [500, 2000, 10000, 50000])
    recent: int = 10               # a question does not come back for this many weeks
    questions: list[HaritonQuestion] = Field(default_factory=list)


class BwTreeBalance(Strict):
    """Owner request 25.10: the Blackwall branch of the character (engine/bwtree.py): where its points come from."""

    base: int = 2                  # on joining the side of the Blackwall
    per_step: int = 1              # each step of the Avatar chain
    per_rank: int = 1              # each rank of the cult
    per_tier: int = 1              # each tier of the taint
    avatar: int = 3
    favor_per: int = 15            # one point per this much favor of the cult
    favor_cap: int = 5


class EscortBalance(Strict):
    """Owner request 25.10, docs/PLAN_ELITE.md part 3: the bodyguard of a corporate head (services/escort.py)."""

    min_ladder: int = 2            # the challenge ladder (or the city measures the character outgrown)
    monthly: int = 30000           # × the challenge ladder
    months: int = 2
    advance_pct: int = 50
    bonus_pct: int = 100
    fight_ap: int = 2
    fail_rep: int = -30
    done_rep: int = 10
    vip_names: list[str] = Field(default_factory=list)
    titles: list[str] = Field(default_factory=list)


class PmcBalance(Strict):
    """Owner request 25.10, docs/PLAN_ELITE.md part 4: the own PMC in a conflict of two megacorps (services/pmc.py)."""

    need_army: int = 15            # a Syndicate and bigger
    license: int = 100000
    retainer_per_fighter: int = 800  # the side pays this per fighter every lived month while the conflict lasts
    battle_pay: int = 25000        # × the challenge ladder, a won battle
    battle_ap: int = 3
    battles_per_day: int = 2
    front_win: int = 10            # a won battle moves the front
    front_loss: int = 5
    drift: int = 4                 # the daily push of the corps' own forces, either way
    win_bonus: int = 50000         # × battles won, to the PMCs of the winning side at the end
    waves: int = 3


class DispatchMission(Strict):
    key: Key
    name: str
    text: str
    difficulty: int                # against the power of the group sent
    reward: tuple[int, int]
    loss: float = 0.15             # a fighter's death chance at even odds (×2 on a failure)
    needs: str = ""                # "pmc": a signed PMC contract


class DispatchBalance(Strict):
    """Owner request 25.10 «отправлять бойцов в бой без тебя» (services/dispatch.py)."""

    per_day: int = 3
    ap: int = 1
    squad_mult: float = 1.5        # a hired city fighter counts this many times its danger
    xp_mult: float = 0.1           # a fighter's power: danger × (1 + xp × this)
    missions: list[DispatchMission] = Field(default_factory=list)


class ChromeAbility(Strict):
    """Owner request 26.10: what an implant lets you do outside a fight (docs/PLAN_CHROME.md)."""

    name: str
    button: str
    icon: str = "⚙️"
    text: str
    ap: int = 1
    cooldown_days: int = 1
    events: list[Key] = Field(default_factory=list)  # one of them plays when the ability is used
    # owner request 26.10 «всё должно вести к уникальным способностям»: every implant line has its own ability; the
    # old 41 verbs are families. A choice or condition naming a family is open to every ability of that family.
    family: Key | None = None


class DgCheck(Strict):
    stat: str
    skill: str | None = None
    dv: int


class DgRoom(Strict):
    """A room of a tower floor (owner request 26.10, docs/PLAN_DUNGEON.md)."""

    key: Key
    name: str
    text: str
    kind: Literal["fight", "check", "heal", "loot"]
    danger: int = 4
    check: DgCheck | None = None
    alarm_ok: int = 0       # the alarm after a passed check, a won fight, a heal or a loot
    alarm_fail: int = 0     # after a failed check (then a fight if fail_fight)
    fail_fight: bool = True
    loot: list[int] = Field(default_factory=lambda: [0, 0])
    weaken: int = 0         # % off the final boss's health on success
    heal_pct: int = 0
    fx_ok: dict[str, int] = Field(default_factory=dict)  # extra effects on success (humanity for the lab...)
    ok_text: str = ""
    fail_text: str = ""


class DgFloor(Strict):
    name: str
    text: str
    rooms: list[DgRoom] = Field(default_factory=list)  # a choice floor
    boss: str = ""          # a boss floor: the boss's name, {corp} is replaced
    boss_final: bool = False
    waves: int = 1
    loot: list[int] = Field(default_factory=lambda: [0, 0])


class TowerDungeonBalance(Strict):
    """Owner request 26.10 «штурм башни как MMORPG-данж» (services/dungeon.py, docs/PLAN_DUNGEON.md)."""

    enabled: bool = True
    party_max: int = 4
    ttl_hours: int = 24
    per_day: int = 1
    ap_enter: int = 4
    ap_room: int = 1
    ap_boss: int = 2
    alarm_max: int = 10
    alarm_hp_pct: int = 6
    alarm_wave_at: int = 5
    boss_party_mult: float = 0.7
    wipe_keep_pct: int = 50
    expire_keep_pct: int = 50
    loot_ladder: bool = True  # loot × challenge mult / 2 of the leader's ladder level, like the old tower
    loot_cap: int = 2000000
    final_cred: int = 4
    final_fame: int = 2
    floors: list[DgFloor] = Field(default_factory=list)


class BlackTearsBalance(Strict):
    """Owner request 26.10: Raven Tearz in the daytime and «Чёрные слёзы» (services/blacktears.py)."""

    enabled: bool = True
    image: str = "raven_tearz.png"   # in images/: every visit of Raven is a post with it
    per_day: int = 2                 # visits a Kyiv day per player
    hour_from: int = 10
    hour_to: int = 23
    tick_min: int = 10               # how often the job looks (minutes)
    contract_share: float = 0.6      # a visit is a contract this often, else a scene of his day
    accept_ap: int = 1
    rep_accept: int = 6
    rep_check_bonus: int = 2
    rep_refuse: int = 4
    rep_refuse_high: int = 8         # from «Тень»
    tiers: list[int] = Field(default_factory=lambda: [10, 25, 50, 80])
    accept_good: float = 0.55
    refuse_good: float = 0.45
    refuse_high_good: float = 0.15   # from «Тень» a refusal goes badly almost always
    check_ok_bonus: float = 0.15
    check_fail_malus: float = 0.2
    tier_good: list[float] = Field(default_factory=lambda: [0.0, 0.1, 0.1, 0.1, 0.2])
    humanity_chance: float = 0.3     # from «Тень»: a done contract costs 1 humanity this often
    tier3_pay_mult: float = 1.5
    sweep_heat: int = 2
    share_eddies: int = 1500         # x (tier - 2) a Kyiv day from «Чёрная слеза»
    echo_min: int = 20               # minutes until the consequence comes
    echo_max: int = 120
    test_tg: int = 0                 # the owner's test contract right after the deploy
    # owner answers 26.10: Raven as a boss for the one who makes him trouble; he never dies, he retreats
    hunt_grudge: int = 4             # refusals (+1, from «Тень» +2) and scene choices that cross him
    hunt_cooldown_h: float = 72
    boss_danger: int = 5
    boss_hp_mult: float = 6.0        # his health: the character's max health times this
    boss_hp_min: int = 200
    boss_hit_edge: int = 2           # his attack is the character's defense plus this (with a d10: hits most of the time)
    boss_hit_pct: float = 0.2        # one of his hits takes about this share of the character's health
    boss_talk_dv: int = 20
    boss_price: int = 15000
    boss_take_pct: int = 25          # a lost fight: the share of the eddies he takes
    boss_won_rep: int = 5


class PallyBalance(Strict):
    """Owner request 26.10: hired players fight at the employer's side on their own skills (services/pally.py)."""

    enabled: bool = True
    max_staff: int = 3              # corp staff in one fight (the bodyguards have their own cap)
    live_min: int = 15              # the hired player's window of the fight lives this long without a tick
    win_pay_per_danger: int = 300   # a won fight: the employer pays each corp staff member this x danger
    invite_s: int = 30              # owner request 26.10: seconds to join the employer's fight in person
    turn_s: int = 30                # seconds the employer's tick waits for the move of a joined hired player
    cover_dmg_pct: int = Field(default=50, ge=0, le=100)  # owner request 29.10: damage taken while covering the employer


class CorpForceBalance(Strict):
    """Owner request 26.10: a megacorp uses force on the city with its own fighters and the hired players
    (services/corpforce.py)."""

    enabled: bool = True
    ap: int = 2
    per_day: int = 2                # operations a Kyiv day
    price: int = 5000               # eddies of the owner for an operation (gear, cars, bribes to look away)
    danger_min: int = 3
    danger_max: int = 5
    extra_per_ally: float = 0.5     # the other side brings this many extra fighters per ally of the corp
    extra_cap: int = 6
    heat: int = 1                   # a loud operation, witnesses or not
    cleanse_gang: int = 12          # a won sweep: the gangs of the district lose this much
    cleanse_corp: int = 6           # ... and the corps gain this much
    lost_gang: int = 4              # a lost sweep: the gangs gain
    strike_corp: int = 10           # a won strike at a canon corp: its hold on the district drops
    strike_lv: int = 2              # the damage to the corp's standing (corpstand, site "lab")
    fame: int = 1
    loot: list[int] = Field(default_factory=lambda: [3000, 9000])


class WantedBalance(Strict):
    """Owner request 26.10: the wanted level as a chase (docs/PLAN_WANTED.md, engine/wanted.py)."""

    enabled: bool = True
    chase_from: int = 2                       # the heat from which the police come in waves
    interval_min: dict[int, int] = Field(default_factory=lambda: {2: 45, 3: 25, 4: 12, 5: 6})  # minutes between waves
    acts_per_wave: dict[int, int] = Field(default_factory=lambda: {2: 5, 3: 3, 4: 2, 5: 1})   # AP actions bring it sooner
    first_wave_part: float = 0.34             # the first wave comes after this part of the interval
    won_speedup: float = 0.5                  # after a won wave the next one comes this much sooner
    danger: dict[int, int] = Field(default_factory=lambda: {2: 1, 3: 2, 4: 3, 5: 5})
    danger_every: int = 2                     # +1 danger every this many waves
    ncpd_danger_max: int = 4
    maxtac_danger_max: int = 5
    extra_base: dict[int, int] = Field(default_factory=lambda: {2: 0, 3: 1, 4: 2, 5: 1})
    extra_per_wave: int = 2
    extra_cap: int = 8
    maxtac_at: int = 5
    fled_pause_min: int = 20                  # a successful flight: the police lose the trail this long
    avoided_pause_min: int = 60               # a bribe or a talk: this long, and the waves start over
    # witnesses
    witness_srcs: list[str] = Field(default_factory=list)  # fights that are seen: a won one may raise heat
    witness_base: float = 0.6                 # bystanders saw it, times the district's heat_mult
    witness_per_stealth: float = 0.04         # minus this per stealth level
    witness_min: float = 0.1
    witness_max: float = 0.9
    witness_heat: int = 1
    clean_margin: int = 4                     # a crime passed by this much leaves no witnesses
    # dropping it
    hide_ap: int = 2
    hide_stat: str = "COOL"
    hide_dv_base: int = 10
    hide_dv_per_heat: int = 2
    hide_drop: int = 2
    hide_pause_h: float = 6
    change_ap: int = 1
    change_price_per_heat: int = 400
    change_drop: int = 1
    fixer_max_heat: int = 4
    fixer_price_per_heat2: int = 2000         # times heat squared
    face_ap: int = 2
    face_price_per_heat: int = 6000
    face_humanity: int = 2
    face_days: int = 7
    no_decay_from: int = 2                    # the week no longer cools this heat by itself


class EventTextBalance(Strict):
    """Owner request 26.10: «когда ИИшка переписывает описание события, пропадает прикол … пускай будет оригинальное
    описание». Claude's retelling of the authored event and of its outcome, off by default."""

    retell_event: bool = False
    retell_outcome: bool = False


class ChromePowerFamily(Strict):
    """How one family of chrome abilities works in a fight and in checks (owner request 27.10)."""

    ctx: list[str] = Field(default_factory=list)  # check contexts the ability helps (check.<ctx>)
    fight: str = ""          # heal | strike | double | aoe | stun | weaken | weaken_all | fear | guard | shield | aim |
                             # hack | escape | talk | repair | stim
    n: int = 0               # the base size of the effect at rank 1
    per_rank: int = 0        # added for every rank above 1
    ticks: int = 1           # how long a buff or a debuff lasts
    cd: int = 4              # ticks before it can be used again (99 = once a fight)
    dice: str = ""           # damage of strike / aoe
    stun: int = 0            # strike, aoe: the target loses this many turns
    pierce: bool = False     # damage ignores armor
    ranged: bool = False     # strike: a shot (the drone), not a blow
    aim: int = 0             # guard, shield, weaken: also a bonus to the next attack
    allies: bool = False     # heal: the allies get half


class ChromePowerBalance(Strict):
    """Owner request 27.10 «способности должны ощущаться по-настоящему» (engine/ab_power.py,
    docs/PLAN_ABILITIES_REAL.md)."""

    enabled: bool = True
    check_by_rank: list[int] = Field(default_factory=lambda: [0, 1, 1, 1, 2, 2])  # index = rank
    check_cap: int = 3       # all abilities together, per check context
    cd_cut_at: int = 4       # from this rank the fight cooldown is one tick shorter
    regen_per_rank: int = 1  # the mend family: hp back at the end of every tick of a fight, per rank
    families: dict[str, ChromePowerFamily] = Field(default_factory=dict)


class LookBalance(Strict):
    """Owner request 26.10: the look editor and the figures in the fight (engine/look.py, engine/sprite.py,
    docs/PLAN_MODELS.md)."""

    enabled: bool = True
    fight_picture: bool = True       # the fight scene over the window during a fight
    scale: int = 4                   # the fight scene is drawn 192×108 and blown up this many times
    preview_scale: int = 6           # the editor's portrait
    gallery_scale: int = 5
    pic_ttl_h: float = 3             # a picture left over the window goes away after this long anyway
    factions: dict[str, dict[str, Any]] = Field(default_factory=dict)  # faction key -> style (engine/look.faction_style)


class CapriBalance(Strict):
    """Owner request 26.10: project «Каприкорн» from Edgerunners 2 (services/capri.py, docs/PLAN_CAPRI.md)."""

    enabled: bool = True
    first_delay_h: float = 1         # the first chip comes out this soon after the start
    respawn_days: float = 5          # after a sale, a lost body or a flight
    destroy_days: float = 14         # after an EMP
    hunt_ap: int = 2
    hunt_cooldown_h: float = 4
    hunt_danger: int = 3
    hunt_need: int = 3               # points (won fights) that take the chip
    sell_price: int = 250000
    destroy_humanity: int = 5
    host_dv: int = 24                # BODY + COOL + humanity/10 + d10
    reject_h: float = 48             # a rejected construct kills unless taken out in time
    extract_price: int = 30000
    power: dict[str, int] = Field(default_factory=lambda: {"check.combat": 3, "ranged_damage": 3, "melee_damage": 3,
                                                           "hp_max": 30})
    sync_day_full: float = 12        # percent a day
    sync_day_half: float = 6
    takeover_from: int = 40
    takeover_chance: float = 0.5     # once a Kyiv day from takeover_from
    resist_dv: int = 16
    resist_sync: int = 12
    yield_sync: int = 10
    flight_from: int = 80
    hunter_cooldown_h: float = 8
    hunter_chance: float = 0.5
    hunter_danger: int = 4
    split_ap: int = 3
    split_dv: int = 17
    act_ap: int = 1                  # owner request 27.10: «Сдержать её» on the dashboard «🧬 Мой ИИ»
    resist_fail_sync: int = 4
    yield_fx: dict[str, int] = Field(default_factory=lambda: {"eddies": 2500, "street_cred": 1, "humanity": -3})


class ClonesBalance(Strict):
    """Owner request 26.10: the clone corp, its Ark and its lottery (services/clones.py)."""

    enabled: bool = True
    grow_part: int = 600000
    insurance: int = 10000000
    sold_discount_pct: int = 25      # the ones who sold the truth to the corp get its services cheaper
    brain_whole: float = 0.75
    expose_fall: int = 100
    return_days: int = 14
    leak_dv: int = 15
    leak_expose: int = 12
    storm_ap: int = 3
    storm_danger: int = 4
    storm_extra: int = 2
    storm_expose: int = 35
    storm_loot: list[int] = Field(default_factory=lambda: [4000, 12000])
    guard_ap: int = 2
    guard_danger: int = 3
    guard_cover: int = 10
    guard_pay: list[int] = Field(default_factory=lambda: [2500, 6000])


class InjuryBalance(Strict):
    """Owner request 26.10: lost limbs and damaged implants (engine/injury.py)."""

    enabled: bool = True
    downed_chance: float = 0.35      # the character fell in a fight
    heavy_chance: float = 0.12       # the fight took more than heavy_pct of the health
    heavy_pct: int = 60
    crit_chance: float = 0.4         # a hit on an implant tears it off
    repair_pct: int = 20             # the ripper's repair: this share of the implant's price
    repair_min: int = 500


class EquilibBalance(Strict):
    """Owner request 26.10: the balance of the gangs, the Order and the Midnight Agency (services/equilib.py)."""

    enabled: bool = True
    tier_at: list[float] = Field(default_factory=lambda: [0.08, 0.16, 0.26])  # skew from a third: tension, unrest, chaos
    salary_pct: list[int] = Field(default_factory=lambda: [-10, -20, -35])
    gig_pay_pct: list[int] = Field(default_factory=lambda: [-10, -20, -30])
    xp_pct: list[int] = Field(default_factory=lambda: [-10, -20, -30])
    chrome_price_mult: list[float] = Field(default_factory=lambda: [1.15, 1.35, 1.7])
    agency_start: int = 35
    agency_decay_day: int = 3
    agency_max: int = 100
    per_day: int = 3                 # the player's balance actions a Kyiv day
    case_ap: int = 2
    case_dv: int = 13                # INT + persuasion: a case of the Agency
    case_gain: int = 6
    case_pay: list[int] = Field(default_factory=lambda: [400, 1200])
    mediate_ap: int = 2
    mediate_dv: int = 14             # COOL + persuasion: the strongest force gives ground everywhere
    mediate_shift: int = 3
    riot_chance: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.06, 0.12])  # by tier, gangs on top
    riot_cooldown_h: float = 6
    tax_pct: list[int] = Field(default_factory=lambda: [0, 0, 3, 6])  # by tier, the Order on top: of the cash a press
    fail_chance: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.08, 0.15])  # by tier, the Agency weakest


class DialogsBalance(Strict):
    """Owner request 26.10: the outcomes of NPC talks judged by a scheduled Claude Code task (services/dialogs.py)."""

    enabled: bool = True
    idle_min: int = 20            # a talk quiet this long is ready to be judged
    min_chars: int = 30           # the player's lines together, at least
    keep_days: int = 7
    menu_size: int = 12
    max_per_file: int = 60
    summary_max: int = 600
    rules: list[str] = Field(default_factory=list)
    limits: dict[str, list[int]] = Field(default_factory=dict)  # effect base -> [min, max]; relation too
    deeds: bool = True              # owner request 27.10: the character's deeds (event_log) are judged too
    deeds_idle_min: int = 60        # a pause this long closes one batch of deeds
    deeds_min: int = 2              # a batch with fewer deeds is not judged
    deeds_max: int = 40             # deeds in one batch, at most
    deeds_skip: list[str] = Field(default_factory=list)  # event_log kinds that are not the character's own deeds
    bot_judge: bool = True          # owner request 27.10 «не scheduled Claude»: the bot asks Claude itself
    judge_per_run: int = 4          # records judged per job run
    judge_tries: int = 3            # Claude failed this many times: the record is closed without consequences
    file_dump: bool = False         # data/dialogs/pending.json for the old scheduled task


class DmRelayBalance(Strict):
    """Owner request 26.10: the group's posts in private for players outside the group (services/dm_relay.py), and
    play in the bot's private chat."""

    enabled: bool = True
    private_play: bool = True  # the game window works in the bot's private chat too
    member_cache_h: float = 6  # how long a membership check is trusted
    retry_h: float = 24  # a private chat the bot cannot write to is tried again after this


class ChromeRank(Strict):
    """Owner request 26.10: the rank of a unique ability = the version of its implant line (1..5)."""

    pay_step: float = 0.25  # the chrome choice pays eddies and xp ×(1 + pay_step × (rank − 1))
    cd_cut_at: int = 4  # from this rank the cooldown is a day shorter (never below one day)


class ChromeSetBonus(Strict):
    text: str
    mods: dict[str, int] = Field(default_factory=dict)
    specials: dict[str, int] = Field(default_factory=dict)  # combat specials (engine/perks.specials keys)
    abilities: list[Key] = Field(default_factory=list)


class ChromeSet(Strict):
    name: str
    icon: str = "🧩"
    text: str
    bonuses: dict[int, ChromeSetBonus] = Field(default_factory=dict)  # pieces installed -> bonus


class PBossBalance(Strict):
    """Owner request 27.10: a player's character as the raid boss with a bounty (services/pboss.py). Every attack is a
    real turn-based fight to the death with the boss's own fighter on the boss's own health."""

    enabled: bool = True
    bounty: int = 20000000
    daily_add: int = 1000000        # owner request 27.10: + this for every real 24 hours the boss lives
    ap: int = 1
    hit_cooldown_h: float = 3
    foe_danger: int = 5
    hit_cred: int = 1               # a hunter who wounded the boss
    flee_chance: float = 0.5        # owner request 27.10: the boss played in person tries to get away


class LootBalance(Strict):
    """Owner request 26.10: searching the bodies after a won fight (services/loot.py)."""

    enabled: bool = True
    per_body: dict[int, tuple[int, int]] = Field(default_factory=lambda: {1: (40, 120), 2: (100, 300), 3: (250, 700),
                                                                           4: (600, 1500), 5: (1200, 3000)})
    max_bodies: int = 6
    elite_mult: float = 2.5
    empty_chance: float = 0.12
    item_chance: float = 0.15
    item_price_per_danger: int = 2500
    heat_chance: float = 0.15
    ambush_base: float = 0.1
    ambush_per_danger: float = 0.03
    ambush_per_chain: float = 0.1
    ambush_cap: float = 0.5
    exclude: list[str] = Field(default_factory=list)
    found_lines: list[str] = Field(default_factory=lambda: ["Карманы, разгрузки, кредитные чипы."])
    empty_lines: list[str] = Field(default_factory=lambda: ["Пусто: кто-то обчистил их раньше."])
    ambush_lines: list[str] = Field(default_factory=lambda: ["Шаги за углом: их друзья пришли на выстрелы."])


class PossessionTier(Strict):
    at: int
    name: str
    mods: dict[str, int] = Field(default_factory=dict)


class PossessionSource(Strict):
    name: str
    bio: str
    start_line: str


class PossessionBalance(Strict):
    """Owner request 26.10: an AI in the head (engine/possession.py, services/possession.py)."""

    start_sync: int = 10
    sync_month: int = 6            # every lived month
    trust_bonus: int = 2           # + this sync when her trust is 70 or more
    event_chance: float = 0.5      # a lived month brings one of her wishes
    events: list[str] = Field(default_factory=list)
    final_at: int = 100
    final_event: str = "pos_one_body"
    suppress_cost: int = 3000      # blockers from a ripper: −sync, she is quiet for a month
    suppress_sync: int = 20
    remove_cost: int = 15000       # a ripper cuts her out
    tiers: list[PossessionTier] = Field(default_factory=list)
    sources: dict[str, PossessionSource] = Field(default_factory=dict)
    voice: list[str] = Field(default_factory=list)  # lines of the head, by the grammar (#rule#)


class StickersBalance(Strict):
    """Owner request 26.10: stickers of the legends (services/stickers.py)."""

    enabled: bool = True
    set_prefix: str = "nc_legends"
    set_title: str = "Легенды Найт-Сити"
    colors: dict[str, str] = Field(default_factory=dict)  # by ending key; "" = an ordinary legend


class BirthCardBalance(Strict):
    """Owner request 26.10: the birth registration card at character creation (handlers/onboarding.birth_card)."""

    quintile: dict[str, int] = Field(default_factory=dict)  # origin key -> the family's income quintile 1..5
    jobs: dict[int, list[str]] = Field(default_factory=dict)  # by quintile
    orders: list[str] = Field(default_factory=lambda: ["Первый ребёнок в семье"])


class DirectorBalance(Strict):
    """Owner request 26.10: the city director (services/director.py)."""

    enabled: bool = True
    hour: int = 9                  # the bot writes the day's input after this hour (Kyiv)
    categories: list[str] = Field(default_factory=lambda: ["street", "money", "relationships", "ncpd", "work", "rare"])
    menu_size: int = 12
    deeds_shown: int = 30
    max_hooks: int = 3
    max_moves: int = 3
    move_step: int = 5
    max_rumors: int = 3
    story_max: int = 1500
    rules: str = ""
    # owner request 26.10 «больше изменений»: memory, new scenes, effects on the characters, the Blackwall, the corps
    memory_days: int = 7           # the past stories the director reads back
    memory_max: int = 8000         # the size of the director's own notes (JSON characters)
    max_scenes: int = 4            # new or extended scenes a day
    scenes_kept: int = 60          # the director's scenes in the library (the oldest go)
    max_fx: int = 8                # effect packs on the characters a day (one per character)
    fx_keys: int = 8               # effects in one pack
    fx_caps: dict[str, int] = Field(default_factory=dict)        # plain effect -> the largest change
    fx_param_caps: dict[str, int] = Field(default_factory=dict)  # param effect (skill_xp, faction_rep...) -> cap
    fx_grants: list[str] = Field(default_factory=list)           # true-valued param effects allowed (item_grant...)
    fx_eddies_pct: float = 0.2     # eddies change: at most this share of the money on hand ...
    fx_eddies_floor: int = 5000    # ... but at least this much
    max_bw: int = 3                # Blackwall city taint changes a day
    bw_step: int = 2
    max_corps: int = 3             # corp stand changes a day
    corp_step: int = 10
    max_crews: int = 3             # player crews power changes a day
    crew_step: int = 10
    fallback_hour: int = 14        # no answer by this hour: the bot posts a quiet-day line itself
    quiet_lines: list[str] = Field(default_factory=list)


class NpcActsBalance(Strict):
    """Owner request 26.10: what a person does in a talk (services/npcact.py)."""

    warm_rel: int = 4
    hurt_rel: int = 6
    comfort_mood: int = 6
    money: tuple[int, int] = (300, 1500)
    tip_clues: int = 1
    discount_pct: int = 15
    cover_heat: int = 1
    cooldown_days: dict[str, int] = Field(default_factory=dict)


class DeedEcho(Strict):
    lines: list[str] = Field(min_length=1)
    effects: dict[str, Any] = Field(default_factory=dict)


class DeedsBalance(Strict):
    """Owner request 26.10 «действия юзеров отражались на мире» (services/deeds.py)."""

    talk_days: int = 14            # the street talk is about the deeds of the last N days
    talk_chance: float = 0.6       # a lived month brings a line of the street talk
    talk_heads: list[str] = Field(default_factory=lambda: ["На улицах говорят"])
    echo: dict[str, DeedEcho] = Field(default_factory=dict)  # by kind: the city comes back to the doer


class EliteAutoBalance(Strict):
    """Owner request 25.10 «все мои бои заканчиваются за один удар»: an ordinary fight of a strong character is elite
    too (services/combat.begin)."""

    enabled: bool = True
    min_danger: int = 3            # weaker fights stay quick
    ticks: int = 3                 # how long a named lead (a lair boss, an event's foe) lasts against the character
    threat_tick: float = 0.12      # the room's damage a tick (the contract fights: 0.2)
    room: dict[int, int] = Field(default_factory=lambda: {3: 3, 4: 4, 5: 4})  # the fewest enemies by danger
    exclude: list[str] = Field(default_factory=list)  # fight sources that set up their own elite or must stay as they are


class RomanceBalance(Strict):
    """Owner request 25.10 «больше жизни в отношениях» (services/romance.py): time with the partner, scandals."""

    grace_months: int = 1          # lived months without time together before the partner starts to miss you
    seen_rel: int = 3              # a month with time together: relation and mood
    seen_mood: int = 2
    neglect_rel: int = 4           # a month without: relation −this × the neglected months in a row (up to ×3)
    neglect_mood: int = 3
    scandal_base: float = 0.12     # a scandal chance even when attentive
    scandal_neglect: float = 0.35  # the chance on the first neglected month, +scandal_step per more
    scandal_step: float = 0.25
    scandal_cap: float = 0.9
    sweet_chance: float = 0.25     # a warm surprise after a month with time together
    ultimatum_rel: int = 15        # the relation at or below this: the partner sets an ultimatum
    scandals: list[str] = Field(default_factory=list)
    sweet: list[str] = Field(default_factory=list)
    ultimatum: str = "rom_ultimatum"


class SeasonBalance(Strict):
    """Owner request 25.10: seasons of the city (services/season.py)."""

    days: int = 21
    prizes: list[int] = Field(default_factory=lambda: [150000, 75000, 40000])
    titles: list[str] = Field(default_factory=lambda: ["Чемпион", "Серебро", "Бронза"])
    pts: dict[str, int] = Field(default_factory=dict)
    finale_hours: int = 24         # owner request 26.10: the last night starts this long before the end
    finale_key: str = "season_night"
    finale_shift: int = 8          # the next season's start: gangs and corps of every district −/+ this


class LegendBlessing(Strict):
    key: Key
    name: str
    text: str
    mods: dict[str, int]


class LegendBalance(Strict):
    """Owner request 25.10: leaving as a legend and the dynasty (services/legend.py)."""

    challenge_level: int = 3       # past this challenge level a character may leave as a legend
    heir_eddies: int = 50000       # per legend of the line
    heir_points: int = 1           # attribute and talent points per legend of the line
    pressure_pts: int = 25         # the city presses the heir harder: pressure points per legend of the line
    # docs/PLAN_ENDGAME2.md part 3: legends in the city (services/lgcity.py)
    duel_ap: int = 2
    duel_hp_mult: float = 1.5      # the legend fights as a boss
    duel_eddies: int = 25000       # the first win over a legend
    duel_fame: int = 3
    duel_cred: int = 5
    duel_repeat_eddies: int = 2000
    bless_days: int = 7
    bless_cap: int = 2             # the blessing grows with the legends of the line, up to this many times
    blessings: list[LegendBlessing] = Field(default_factory=list)


class EnemiesBalance(Strict):
    """Owner request 25.10: ways to get rid of enemies, by money and by hand (services/enemies.py)."""

    hostile_rep: int = -30         # a faction at or below this standing counts as an enemy
    contract_base: int = 25000     # a contract on the nemesis, × its level
    contract_chance: float = 0.75
    settle_per_level: int = 4000   # settling the grudges, × the enmity
    settle_levels: int = 3
    hook_price: int = 12000        # buying the hook out, × its level
    bounty_pct: int = 150          # taking the bounty off: this % of the pot
    truce_per_point: int = 300     # a truce with a faction, × the missing standing
    truce_gain: int = 30
    hunt_ap: int = 2
    hunt_dv: int = 13              # + 2 per nemesis level
    vendetta_ap: int = 2
    vendetta_danger: int = 4
    vendetta_enmity: int = 2
    vendetta_rep: int = 15
    lean_ap: int = 1
    lean_dv: int = 16


class TurfLaw(Strict):
    """A law the owner of a district sets (services/turf.py)."""

    key: str
    name: str
    desc: str = ""
    gang_per_day: float = 0.0
    corp_per_day: float = 0.0
    heat_mult: float = 1.0          # the wanted level of everyone who lives in the district
    mood_per_press: int = 0         # the mood of every resident, each lived month
    tax_pct: int = 0                # the owner's tax income, ± this %


class TurfBalance(Strict):
    """Owner request 25.10, docs/PLAN_ENDGAME2.md part 1: districts held by players (services/turf.py)."""

    need_army: int = 15
    max_meter: int = 40             # a free district: gangs and corps both at most this
    war_ap: int = 3
    war_extra: int = 4
    defenders_per_extra: int = 10   # the owner's army: one more defender per this many fighters
    hold_per_day: float = 1.0       # the owner's people push both meters down every day
    tax_names: list[str] = Field(default_factory=lambda: ["Без налога", "Низкий", "Средний", "Высокий"])
    tax_income: list[int] = Field(default_factory=lambda: [0, 300, 700, 1200])  # × (6 − district danger)
    tax_gang: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.5, 1.5])  # the gangs grow on the anger
    take_gang: int = -15
    take_corp: int = -10
    laws: list[TurfLaw] = Field(default_factory=list)


class ControlBalance(Strict):
    """Owner request 25.10: gangs against megacorps, two meters per Night City district (engine/control.py)."""

    hour: int = 15                     # Kyiv hour of the daily shift (with the new gangs and corps)
    start_gang: int = 15
    start_gang_per_danger: int = 7
    start_corp: int = 15
    start_corp_per_office: int = 8     # per canon corp of the district
    gang_growth: float = 0.5
    gang_per_crew: float = 1.5         # per new gang in the district
    gang_per_crew_city: float = 0.1    # per new gang anywhere in the city
    corp_growth: float = 0.5
    corp_per_crew: float = 2.0         # per new corp office in the district
    corp_per_crew_city: float = 0.1
    corp_per_office: float = 0.3       # per canon corp of the district
    suppress: float = 0.1             # the stronger side cuts this share of its lead from the weaker one
    gang_marks: list[int] = Field(default_factory=lambda: [50, 75, 90])
    corp_marks: list[int] = Field(default_factory=lambda: [50, 75, 90])
    tight_from: int = 50
    heat_k: float = 1.0                # wanted level ×(1 + (corp − tight_from)/100 × heat_k): surveillance
    regime_from: int = 75
    raid_k: float = 1.0                # a corporate sweep on a press: (corp − regime_from)% × raid_k
    ambush_from: int = 30
    ambush_k: float = 0.5              # a gang attack on a press: (gang − ambush_from)% × ambush_k
    danger_step: int = 20
    extra_from: int = 60
    extra_step: int = 15
    event_share: float = 0.5           # of the attacks: a scene instead of a straight fight
    spawn_per_point: float = 0.005     # spawn chance + city average × this (gangs by the gang meter, corps by corp)
    cap_step: int = 25                 # one more new gang (corp) may live per this much of the city average
    crew_born: int = 8                 # a new gang or corp office adds this to its meter in the district
    per_day: int = 2                   # cleansings and strikes a day
    cleanse_ap: int = 2
    cleanse_gang: int = 10
    cleanse_corp: int = 5              # the corps move in where the gangs are cleaned out
    cleanse_loot: tuple[int, int] = (500, 1500)
    strike_ap: int = 2
    strike_corp: int = 10              # a hack of the corp's systems
    strike_fight_corp: int = 14        # a fight with the corp's security
    strike_gang: int = 4               # the gangs move in where the corps are pushed out
    strike_loot: tuple[int, int] = (800, 2500)
    hack_dv: int = 12
    raid_gang: int = -6                # a solo raid on a gang of the district
    raid_corp: int = 3
    corpraid_corp: int = -8            # a raid on a corp of the district
    corpraid_gang: int = 3
    lair_gang: int = -20               # a lair of a new gang taken
    lair_corp: int = 8
    office_corp: int = -20             # an office of a new corp taken
    office_gang: int = 8
    ambush_won: int = -3
    ambush_lost: int = 2


class PressureTier(Strict):
    key: Key
    name: str
    min: int = 0


class PressureKrysha(Strict):
    name: str
    text: str
    fee_pct: int = 3              # of the wealth, every month
    fee_min: int = 400
    guard: int = 40               # cuts the chance of the base threats, %


class PressureBalance(Strict):
    """Owner request 24.10: a constant challenge. The threat director of every «Прожить месяц» (services/pressure.py)."""

    wealth_root_div: int = 100        # points: sqrt(wealth / this)
    per_level: int = 3
    per_fame: int = 2
    cred_div: int = 2
    month_cap: int = 60               # one point per lived month, up to this
    per_business: int = 10
    megacorp_pts: int = 80
    corp_asset_pts: int = 40
    gang_pts: int = 10
    tiers: list[PressureTier] = Field(default_factory=list)
    corp_tier: str = "corp"           # a megacorp or a corp asset puts the character here at once
    legend_tier: str = "legend"       # owner request 25.10: a character who outgrew the city (challenge power) lands here
    legend_chance: float = 0.8        # the base event chance of a legend, at least
    base_chance: float = 0.45
    chance_per_pt: float = 0.0025
    max_chance: float = 0.85
    second_from: int = 120
    second_chance: float = 0.3
    lower_tier_chance: float = 0.3
    guard_cap: int = 60
    hook_chance_per: float = 0.15
    enmity_chance_per: float = 0.15
    max_level: int = 10               # hook and enmity
    decay_months: int = 3
    history: int = 8
    krysha: dict[str, PressureKrysha] = Field(default_factory=dict)
    racket_events: list[str] = Field(default_factory=list)  # owner request 25.10: never drawn while a krysha holds
    nem_return_chance: float = 0.4
    nem_danger_base: int = 2
    nem_danger_max: int = 5
    nem_cred_win: int = 3
    nem_names: list[str] = Field(default_factory=list)
    nem_titles: dict[str, str] = Field(default_factory=dict)   # faction -> who he is
    pools: dict[str, dict[str, list[str]]] = Field(default_factory=dict)   # tier -> base / demand / revenge -> event ids


class PurgeRaider(Strict):
    """Owner request 24.10: NPC crews of the night. They come in waves at the players and can be hunted."""

    key: Key
    name: str = Field(max_length=22)
    faction: Key
    danger: int = Field(default=2, ge=1, le=5)
    power: int = 12                   # against a shelter: d10 + this
    pay: int = 1000                   # the price to make them go away
    loot: tuple[int, int] = (300, 1500)
    text: str
    targets: list[str] = ["hide", "hunt", "out"]


class PurgeMark(Strict):
    """Owner request 24.10: an NPC shelter a hunter may storm."""

    key: Key
    name: str = Field(max_length=22)
    defense: int = 10
    loot: tuple[int, int] = (1000, 4000)
    fail_hp: tuple[int, int] = (6, 15)
    text: str
    win: str
    lose: str


class PurgeBalance(Strict):
    """Owner request: «Судная ночь» (The Purge). Once a week of real time all laws are off for a few hours: hide at home
    behind what you prepared, or go out and hunt the city and the other players."""

    weekday: int = Field(default=5, ge=0, le=6)   # Monday = 0; Saturday by default
    start_hour: int = Field(default=21, ge=0, le=23)
    hours: int = Field(default=2, ge=1, le=6)
    announce_hours: int = Field(default=9, ge=1, le=24)  # the warning and the preparation open this long before
    base_defense: int = 4
    defenses: list[PurgeDefense] = Field(default_factory=list)
    medkit_price: int = 300
    medkit_hp: int = 15
    hunts: int = 6                    # hunter deeds per night
    street_dv: int = 13
    street_loot: tuple[int, int] = (200, 1200)
    street_fail_hp: tuple[int, int] = (4, 12)
    storm_bonus: int = 0              # added to the hunter's roll against a shelter
    storm_take_pct: int = 15
    storm_take_cap: int = 20000
    storm_hp: int = 15
    storm_defense_loss: int = 3
    storm_fail_hp: tuple[int, int] = (6, 15)
    duel_take_pct: int = 10
    duel_hp: int = 15
    shoot_back: int = 2               # a hiding player's «Отстреливаться»: +defense for the night
    shoot_back_times: int = 3
    siege_power: int = 12             # the gangs that test every shelter at dawn: d10 + this
    siege_take_pct: int = 10
    siege_hp: int = 10
    street_caught_chance: float = 0.5  # nobody prepared: caught outside at dawn
    street_caught_take_pct: int = 10
    street_caught_hp: int = 10
    winner_eddies: int = 3000         # the hunter of the night
    winner_fame: int = 2
    fortress_eddies: int = 1500       # the shelter nobody broke with the most attempts
    # owner request 24.10: the city itself hunts, not only the players
    wave_minutes: int = 15            # an NPC wave every this many minutes of the night
    wave_chance: float = 0.7          # a character who chose a mode gets a visit
    wave_chance_out: float = 0.35     # a character who chose nothing
    raiders: list[PurgeRaider] = Field(default_factory=list)
    marks: list[PurgeMark] = Field(default_factory=list)
    street_weights: dict[str, int] = Field(default_factory=lambda: {"npc": 35, "fight": 30, "event": 35})
    street_events: list[str] = Field(default_factory=list)
    threat_hp: tuple[int, int] = (6, 14)
    threat_take_pct: int = 8
    threat_def_loss: int = 2
    escape_dv: int = 13


class SeasonDef(Strict):
    key: Key
    name: str
    weeks: tuple[int, int]            # first and last week of the year (0-based), may wrap over the new year
    mods: Mods = {}
    text: str = ""


class WeatherKind(Strict):
    key: Key
    name: str
    icon: str = Field(min_length=1, max_length=8)
    weights: dict[str, int]           # season key -> weight of this weather in that season
    districts: list[Key] = Field(default_factory=list)  # only here (a dust storm of the Badlands); empty = anywhere
    mods: Mods = {}
    weekly: Effects = {}              # what a week of it does
    text: str = ""


class WeatherBalance(Strict):
    """Owner request: the season of the character's own date and the weather of the week change the mods and the events."""

    seasons: list[SeasonDef] = Field(default_factory=list)
    kinds: list[WeatherKind] = Field(default_factory=list)


class ChronoJob(Strict):
    key: Key
    name: str = Field(max_length=24)
    ap: int = Field(ge=1, le=6)
    stat: Literal["BODY", "REF", "TECH", "INT", "COOL", "EMP"]
    skill: str | None = None
    dv: int = Field(ge=6, le=25)
    weeks: tuple[int, int]              # paid in time on success
    fail_hp: int = 0
    text: str


class ChronoBalance(Strict):
    """Owner request: the «In Time» crossover. Aeon, a city-state of the eternal life where time is the money."""

    implant_price: int = 60000
    implant_weeks: int = 26             # the time on the clock right after the install
    buy_week: int = 1500                # eddies for one week of time at the exchange
    sell_week: int = 1000               # eddies paid for one week of time
    warn_weeks: int = 8                 # fewer weeks than this: a warning every press
    gift_max: int = 52
    jobs: list[ChronoJob] = Field(default_factory=list)
    poker_stakes: list[int] = Field(default_factory=lambda: [1, 4, 12])
    poker_dv: int = 15
    rob_ap: int = 2
    rob_dv: int = 17
    rob_weeks: tuple[int, int] = (4, 12)
    rob_fail_hp: int = 10
    rob_fail_heat: int = 2


class ElysiumBalance(Strict):
    """Owner request: the «Elysium» crossover. A station in orbit for the rich, Med-Bays that cure everything."""

    citizen_price: int = 250000
    citizen_mods: Mods = {}
    medbay_cooldown_weeks: int = 4      # a citizen's free Med-Bay
    medbay_humanity: int = 0
    medbay_mood: int = 5
    shuttle_price: int = 15000
    shuttle_dv: int = 16
    shuttle_crash_hp: int = 30
    shuttle_caught_heat: int = 2
    plant_ap: int = 2
    plant_dv: int = 13
    plant_pay: tuple[int, int] = (1200, 2500)
    plant_rad_chance: float = Field(ge=0, le=1, default=0.15)
    rad_weeks: int = 8                  # a lethal dose: weeks to live without a Med-Bay
    exo_key: str = "exo_frame"
    exo_price: int = 40000
    heist_ap: int = 3
    heist_dv: int = 20
    heist_fail_hp: int = 25
    heist_fail_heat: int = 3
    deport_heat: int = 4                # a citizen this wanted may lose the citizenship
    deport_chance: float = Field(ge=0, le=1, default=0.3)


class LibriaBalance(Strict):
    """Owner request 23.10: the «Equilibrium» crossover. Libria, Prozium, the Grammaton Clerics, the Underground."""

    dose_price: int = 600
    course_weeks: int = 4
    dose_mods: Mods = {}
    flat_mood: int = 40             # Prozium pulls the mood to this grey middle
    withdraw_mood: int = -8         # the first week without Prozium: everything at once
    withdraw_humanity: int = 2
    cleric_dv: int = 15
    cleric_mods: Mods = {}
    raid_ap: int = 2
    raid_dv: int = 14
    raid_pay: tuple[int, int] = (800, 1800)
    raid_fail_hp: int = 8
    burn_bonus: int = 500
    burn_humanity: int = -2
    keep_humanity: int = 2
    found_chance_per_art: float = Field(ge=0, le=1, default=0.06)  # a press: the hidden art is found
    found_heat: int = 3
    found_hp: int = 15
    smuggle_ap: int = 2
    smuggle_dv: int = 15
    smuggle_pay: tuple[int, int] = (1500, 3500)
    smuggle_fail_hp: int = 10
    smuggle_fail_heat: int = 2
    skip_dv: int = 17
    skip_win: dict[str, int] = Field(default_factory=dict)
    skip_fail_hp: int = 20


class GymBalance(Strict):
    """Owner request 23.10: health grows with sport. Fitness points (flag `fitness`) give hp_max and, later, BODY."""

    ap: int = 1
    price: int = 150
    coach_price: int = 600
    coach_points: int = 3
    dv: int = 13                  # BODY check for a second point
    mood: int = 2
    cap: int = 40
    hp_per_points: int = 2        # +1 hp_max per this many points
    body_at: list[int] = Field(default_factory=lambda: [20, 40])   # BODY +1 at each of these


class HqTier(Strict):
    key: Key
    name: str
    kind: Literal["gang", "corp"]
    weekly: int = Field(ge=0)        # the leader pays it every press (x weeks_per_press), then the crew bank
    base_def: int = Field(ge=0)      # the Purge defense of the shelter before any fortification
    biz_pct: int = Field(default=0, ge=0)  # the crew business pays this much more
    description: str = ""


class HubsBalance(Strict):
    """Owner request 23.10: shared homes, crew headquarters, the megacorp tower; fortified together for the Purge."""

    capacity: dict[int, int] = Field(default_factory=lambda: {1: 1, 2: 2, 3: 4, 4: 6})  # people by housing tier
    move_in_ap: int = 1
    fort_price: list[int] = Field(default_factory=lambda: [4000, 10000, 22000, 45000, 80000])
    fort_def: int = 4                # Purge defense per fortification level
    together_def: int = 2            # per other member hiding in the same shelter
    together_cap: int = 8
    cohab_mods: Mods = {}
    tower_base_def: int = 6
    tower_def_per_security: int = 4
    hq: list[HqTier] = Field(default_factory=list)


class LivingBalance(Strict):
    """Owner request 23.10: where and how you live matters. A capsule and noodles from a bag grind body and mood,
    a normal flat is neutral, a good home and good food give an edge. Cheap chrome lags."""

    tier_mods: dict[int, Mods] = Field(default_factory=dict)     # housing tier (0 = no home) -> weekly mods
    sick_chance: dict[int, float] = Field(default_factory=dict)  # housing tier -> weekly chance to fall ill
    starving_sick: float = 0.08     # added to the chance on the «Впроголодь» lifestyle
    lifestyle_sick_cut: dict[str, float] = Field(default_factory=dict)  # a good lifestyle lowers the chance
    sick_weeks: int = 4
    sick_mods: Mods = {}
    sick_hp: int = 2                # a week while ill
    fast_cure_tier: int = 3         # this housing tier and above: the illness goes twice as fast
    cheap_tier: str = "street"      # implants of this tier may lag
    cheap_lag_chance: float = 0.04  # a week, per cheap implant
    cheap_lag_hp: int = 2
    cheap_lag_mood: int = 2


class DynFactionsBalance(Strict):
    """Owner request 23.10: new gangs and corps appear, fight, fall apart; players can hunt their lairs."""

    hour: int = 15                    # Kyiv time: the day of the crews (scheduler/jobs.dyn_job)
    max_gangs: int = 4
    max_corps: int = 2
    gang_spawn_chance: float = 0.35   # a real day
    corp_spawn_chance: float = 0.15
    start_power: tuple[int, int] = (40, 70)
    daily_drift: tuple[int, int] = (-12, 10)
    war_chance: float = 0.3           # two crews in one district clash a day
    war_loss: tuple[int, int] = (10, 30)
    ambush_chance: float = 0.12       # a press, for a character in the crew's district
    ambush_danger: tuple[int, int] = (2, 4)
    ambush_power_loss: int = 15       # the crew loses this when its ambush is beaten
    search_ap: int = 1
    search_dv: int = 15
    search_need: int = 3              # clues to find the lair (an ambush gives one)
    assault_ap: int = 3
    assault_danger: int = 5
    loot: tuple[int, int] = (6000, 18000)
    corp_loot_mult: float = 2.0
    cred: int = 5
    gang_bases: list[str] = Field(default_factory=lambda: ["maelstrom", "tyger_claws", "valentinos", "sixth_street",
                                                            "scavengers", "animals", "wraiths"])
    corp_bases: list[str] = Field(default_factory=lambda: ["arasaka", "militech", "kang_tao", "biotechnica"])
    gang_first: list[str] = Field(default_factory=list)
    gang_second: list[str] = Field(default_factory=list)
    corp_first: list[str] = Field(default_factory=list)
    corp_second: list[str] = Field(default_factory=list)


class NetIceBalance(Strict):
    hp_base: int = 10
    hp_per_tier: int = 6
    black_hp: int = 15            # the ICE at the Blackwall
    dmg: tuple[int, int] = (2, 4)  # + tier
    black_dmg: int = 3
    harden: int = 4               # an attack into hardened ICE loses this much
    counter: int = 2              # a shield against a strike hits back
    probe_trace: int = 10
    rounds: int = 8
    brain_base: int = 10
    brain_per_int: int = 2
    see_base_pct: int = 45        # the chance to see the ICE's intent ...
    see_per_power_pct: int = 3    # ... + per point of INT + netrunning + deck bonus
    see_max_pct: int = 95
    hp_loss_div: int = 2          # brain damage taken / this = real hp lost (never below 1 hp)


class NetBalance(Strict):
    """Owner request 23.10: the Net of the netrunners (app/engine/net.py, app/services/net.py)."""

    grid: int = 5
    codes: list[str] = Field(default_factory=lambda: ["1C", "55", "BD", "E9", "7A"])
    buffer_base: int = 4
    buffer_per_ram: int = 4       # +1 buffer per this much RAM of the deck
    buffer_max: int = 8
    ap: dict[str, int] = Field(default_factory=dict)
    trace: dict[str, int] = Field(default_factory=dict)    # trace an action adds (on top of a failure)
    trace_fail: int = 10
    trace_limit: int = 100
    trace_after_hunt: int = 30
    trace_decay_per_day: int = 40   # old records without a time stamp
    trace_decay_per_hour: int = 0   # owner 24.10: no cooling in real time ...
    trace_decay_per_month: int = 15  # ... the trace cools once a lived month («Прожить месяц»)
    clean_ap: int = 1               # «Замести следы»: INT + netrunning against clean_dv
    clean_dv: int = 13
    clean_cut: int = 40
    clean_fail_cut: int = 10
    cleaner_price: int = 1500       # a fixer's cleaner wipes the logs for eddies
    cleaner_cut: int = 50
    role_trace_cut_pct: int = 5   # per netrunner role rank ...
    role_trace_cut_max_pct: int = 40
    hunt_danger: int = 4
    gig_pay_pct: list[int] = Field(default_factory=lambda: [30, 60, 100])   # of the tier's top gig pay, per daemon
    corp_loot_mult: float = 2.5
    corp_cred: int = 1
    dossier_dv: int = 13          # + the target's netrunning
    dossier_notice_pct: int = 25  # a clean dossier is still noticed this often
    blackwall_from: int = 2023
    blackwall_pay_mult: float = 4.0
    blackwall_humanity: int = 4
    blackwall_glitch_pct: int = 40
    blackwall_contact_pct: int = 35
    ice: NetIceBalance = Field(default_factory=NetIceBalance)


class SortieBalance(Strict):
    """Owner request 24.10: a sortie through a piece of the district, a fresh map every time (app/engine/sortie.py)."""

    ap: int = 2
    size: int = 5
    steps: int = 16
    pois: dict[str, int] = Field(default_factory=lambda: {"gang": 3, "harass": 2, "stash": 3, "trap": 2, "trader": 1,
                                                          "loot": 3})
    stash_eddies: tuple[int, int] = (300, 1200)
    loot_eddies: tuple[int, int] = (100, 400)
    fight_eddies: tuple[int, int] = (200, 700)      # x danger, from a beaten gang
    clear_eddies: tuple[int, int] = (800, 2000)     # every hostile point dealt with
    clear_cred: int = 2
    early_keep_pct: int = 50                        # leaving away from the exit or out of steps keeps this share
    stim_price: int = 300
    stim_hp: int = 15
    map_price: int = 200
    trap_hp: int = 6
    stash_trap_hp: int = 4


class DgameBalance(Strict):
    """Owner request 24.10: the deadly games of the rich (app/engine/dgame.py)."""

    players: int = 120
    per_head: int = 5000            # every dead player adds this to the prize
    dv: dict[str, int] = Field(default_factory=lambda: {"light": 13, "honey": 12, "rope": 14, "marbles": 12,
                                                        "glass": 13, "night": 12})
    near_miss: int = 2               # a failed roll by this much or less: wounded, alive
    near_miss_hp: int = 12
    vote_quit_chance: float = 0.35   # the vote to stop the game passes
    final_danger: int = 3
    closed_days: int = 30            # after the island is stormed the games stop for this long
    # owner request 24.10: rich players and corp owners run their own games
    host_min_eddies: int = 300000    # or the owner of a player's megacorp
    host_price: int = 100000         # the island, the guards, the dolls
    host_days: int = 7               # real days the game runs
    host_heat: int = 3
    host_dead_pct: int = 25          # of the pot when a player dies in your game
    host_win_pct: int = 10           # of the pot, from the guests' bets, when a player wins
    invite_advance: int = 3000       # the stranger in the suit pays an advance to the invited player


class CorpPolicy(Strict):
    name: str
    text: str
    income_pct: int = 0      # the monthly income of the corp
    power: int = 0           # war power and the defence of the districts
    morale: int = 0          # per press
    raid_pct: int = 0        # the chance of gang raids on the districts
    heat: int = 0            # the owner's heat per press


class CorpBuilding(Strict):
    name: str
    text: str
    prices: list[int]        # level 1, 2, 3 ...
    upkeep: int = 0          # per level, per press


class TroopTier(Strict):
    name: str
    power: int
    upkeep: int
    upgrade_price: int = 0   # from the tier below
    armory: int = 0          # the arsenal level needed


class CorpWarBalance(Strict):
    """Owner request 24.10: storming a corp and taking its vault, corp wars (app/services/corpwar.py)."""

    storm_ap: int = 3
    storm_cooldown_h: int = 12          # one attacker, one target
    npc_vault_start: int = 250000       # a canon megacorp's vault in this city
    npc_vault_cap: int = 400000
    npc_vault_regen_day: int = 40000
    npc_danger: int = 5
    npc_dv: int = 18                    # the quiet way in and the vault lock of a canon megacorp
    npc_rep_hit: int = 25
    player_danger_base: int = 2         # + the security level of a player's tower, up to 5
    vault_dv_base: int = 13             # + 2 per security level
    quiet_dv_base: int = 14             # + 2 per security level + the staff
    grab_pct: int = 30                  # the alarm went off: what can be grabbed on the run
    blast_pct: int = 60                 # a charge on the vault door: no check, part of the cash burns
    storm_heat: int = 3
    self_heat: int = 2                  # the cash kept for yourself, not laundered through a corp or a crew
    storm_cred: int = 3
    storm_fame: int = 2
    vault_income: tuple[int, int] = (5000, 12000)   # a player's corp earns into its vault every press
    vault_income_per_staff: int = 1500
    war_declare_price: int = 50000
    war_round_h: int = 12               # a skirmish of every war this often
    war_win_score: int = 3
    war_damage: tuple[int, int] = (8, 18)
    war_loot_pct: int = 15              # the loser of a skirmish loses this share of the vault to the winner
    war_final_pct: int = 40             # the side that loses the war
    war_npc_power: int = 10
    war_auto_chance: float = 0.12       # per day for a player corp at peace: a canon megacorp or a rival attacks
    war_min_age_days: int = 2
    war_push_ap: int = 2
    war_push_dv: int = 14
    war_push_power: int = 2
    war_truce_price: int = 80000        # paying off a canon megacorp
    war_surrender_pct: int = 25
    # corp management (owner request 24.10: resources and strategic decisions)
    troop_price: int = 3000
    troop_upkeep: int = 700             # per fighter, per press
    troop_max: int = 30
    troop_start: int = 6
    intel_price: int = 15000
    intel_max: int = 10
    morale_start: int = 70
    district_income: tuple[int, int] = (6000, 14000)
    district_upkeep: int = 3000
    district_need: int = 4              # fighters left to hold a taken district
    district_power_base: int = 6
    district_power_per_danger: int = 5
    security_upkeep: list[int] = Field(default_factory=lambda: [0, 2000, 5000, 10000, 20000])
    ally_tribute: int = 15000           # per gang, per press
    ally_power: int = 6
    merc_price: int = 40000             # a player crew hired for a war, into its bank
    merc_power_per_member: int = 3
    unpaid_morale: int = -15
    unpaid_desert_pct: int = 20
    unpaid_integrity: int = -5
    low_morale: int = 30                # below: the income is halved
    raid_chance: float = 0.25           # per district per press, before the policy
    crisis_chance: float = 0.55         # per press, x pressure
    pressure_per_million: float = 0.6   # wealth (vault + owner's eddies) raises crises, raids, enemy power
    pressure_cap: float = 3.0
    sabotage_intel: int = 2
    spy_intel: int = 1
    policies: dict[str, CorpPolicy] = Field(default_factory=dict)
    buildings: dict[str, CorpBuilding] = Field(default_factory=dict)
    tiers: dict[str, TroopTier] = Field(default_factory=dict)   # rec, vet, elite
    barracks_per_level: int = 20        # troop_max is the base, each level of barracks adds this
    walls_power: int = 3
    clinic_save_pct: int = 25           # per level: fallen fighters who survive
    intel_per_level: int = 1            # per press
    pr_morale: int = 3                  # per level per press
    pr_crisis_pct: int = 10             # per level: fewer crises
    vault_dv_per_level: int = 2         # the armoured vault
    vault_keep_pct: int = 15            # per level: cash the stormers cannot reach
    vet_promote_pct: int = 20           # recruits who become veterans after a won skirmish
    # the storm of a tower as a dungeon (app/services/tstorm.py)
    floor_dv: int = 13                  # + the level of the building on that floor
    floor_hp: int = 8                   # turrets and black ICE hurt
    office_eddies: tuple[int, int] = (2000, 8000)
    fight_eddies: tuple[int, int] = (1000, 4000)
    bribe_price: int = 8000             # per security level
    alarm_danger: int = 1               # every failed floor raises the danger of the next fights
    defenders_per_win: tuple[int, int] = (1, 3)


class VampireFeed(Strict):
    name: str
    text: str
    fail_text: str = ""
    ap: int = 1
    blood: int
    eddies: int = 0
    humanity: int = 0
    heat: int = 0
    stat: str | None = None
    skill: str | None = None
    dv: int = 12
    fail_heat: int = 0
    fail_hp: int = 0


class VampireBalance(Strict):
    """Owner request 24.10: the night-taxi vampires (engine/vampire.py)."""
    blood_max: int = 40          # drained blood: hp max +1 per point
    regen_per: int = 5           # hp regen +1 per this many points
    decay: int = 6               # per week the body burns the blood
    hunger_mods: dict[str, int] = Field(default_factory=dict)   # with no blood left
    sun_hp: int = 10             # a day outside
    feeds: dict[str, VampireFeed] = Field(default_factory=dict)


def _level_curve() -> list[int]:
    """Levels 1..100: the first 20 as before (gaps 40, 50, ... 220), then the gap grows by 15 a level, and from 50
    (owner report 26.10 «потолок низкий») by 20."""
    out, xp, gap = [0], 0, 40
    for lv in range(2, 101):
        xp += gap
        out.append(xp)
        gap += 10 if lv < 20 else (15 if lv < 50 else 20)
    return out


class PerksBalance(Strict):
    """Owner request 24.10: character levels, attribute and perk points (engine/perks.py, content/perks.yaml).
    Experience comes from skill experience, from every deed on the game-event bus and from every action point spent."""
    level_xp: list[int] = Field(default_factory=_level_curve)
    # owner request 26.10 «уровень очень быстро поднимается … базово x2»: every threshold of level_xp times this;
    # a record made under another multiplier has its experience rescaled once (engine/perks.rec), the level stays
    xp_curve_mult: float = 2.0
    action_xp: dict[str, int] = Field(default_factory=dict)   # bus event -> character experience
    default_action_xp: int = 3                                  # any other bus event
    ap_xp: int = 2                                              # per action point spent
    skill_level_xp: int = 20                                    # a skill level-up is worth this much on top
    skill_points_per_level: int = 2                             # character level -> skill points (+1 skill level each)
    skill_levels_per_talent: int = 3                            # every this many skill level-ups -> a perk point
    stat_cap: int = 10               # one attribute point per +1 up to this
    # owner report 26.10 «на 32 уровне все атрибуты на максимуме, потолок низкий»: above stat_cap up to the legend cap,
    # each point dearer: from stat_cap to stat_cap+1 costs over_cost_start points, then one more for every next one
    stat_legend_cap: int = 15
    over_cost_start: int = 2
    start_per_skill_levels: int = 4  # existing characters: one level per this many skill levels they already have
    start_cap: int = 12


class BwStep(Strict):
    """A step of the Avatar chain: a forced event and what the character needs to be let in."""
    event: Key
    title: str
    rot: int = 0       # the taint at least
    city: int = 0      # the taint of the whole city (sum of district levels)
    power: int = 0     # the Blackwall power (engine/blackwall.power)
    arms: int = 0      # Blackwall scripts and weapons together


class BwCorrupt(Strict):
    """A way to taint a district: by the Net, by the word or by force."""
    name: str
    stat: Stat
    skill: Skill
    ap: int = 2
    needs: Literal["deck", "cult"] | None = None
    text: str
    fail_text: str


class BwShopItem(Strict):
    kind: Literal["cyberware", "weapon"]
    key: Key
    price: int
    rank: int = 1
    rot: int = 5


class BlackwallBalance(Strict):
    """Owner request 24.10: the Blackwall path (engine/blackwall.py, services/blackwall.py)."""
    rot_max: int = 100
    tiers: list[int] = Field(default_factory=lambda: [15, 35, 60, 85])
    tier_names: list[str] = Field(default_factory=lambda: ["чисто", "шёпот", "эхо", "трещина", "прорыв"])
    tier_mods: list[Mods] = Field(default_factory=list)       # index = tier 1..4 (0 is clean)
    avatar_mods: Mods = {}
    weekly_humanity: list[int] = Field(default_factory=lambda: [0, 0, -1, -1, -2])
    avatar_weekly_humanity: int = -2
    power_script: int = 3
    power_weapon: int = 2
    power_rank: int = 2
    power_rot_div: int = 10
    power_avatar: int = 6
    steps: list[BwStep] = Field(default_factory=list)
    favor_ranks: list[int] = Field(default_factory=lambda: [0, 10, 25, 50])
    rank_names: list[str] = Field(default_factory=lambda: ["Слушающий", "Носитель шума", "Глашатай", "Архиерей помех"])
    vigil_ap: int = 1
    follow_ap: int = 2                # owner request 25.10: a step of the Avatar chain costs AP, one per lived month
    vigil_rot: int = 4
    vigil_favor: int = 2
    vigil_fx: dict[str, int] = Field(default_factory=lambda: {"humanity": -1, "mood": -2})
    tithe_eddies: int = 2500
    tithe_favor: int = 3
    corrupt: dict[str, BwCorrupt] = Field(default_factory=dict)
    corrupt_dv: int = 12
    corrupt_dv_per_level: int = 2
    corrupt_rot: int = 3
    corrupt_favor: int = 2
    corrupt_heat: int = 1
    corrupt_fail_hp: int = 6
    level_max: int = 5
    surge_ap: int = 3
    surge_levels: int = 2
    cleanse_eddies: int = 6000
    cleanse_rot: int = 20
    cleanse_humanity: int = 6
    decay_chance: float = 0.3
    breach_from: int = 3
    breach_pct_per_level: int = 8
    breaches: list[str] = Field(default_factory=list)
    whispers: list[str] = Field(default_factory=list)
    whisper_base: float = 0.02
    whisper_per_rot: float = 0.004
    shop: dict[str, BwShopItem] = Field(default_factory=dict)
    fight_rot: int = 2
    dive_rot: int = 6
    dive_fail_rot: int = 3
    ai_power_per_script: int = 2


class LoansBalance(Strict):
    fractions: list[int] = Field(default_factory=lambda: [25, 50, 100])   # buttons: % of the sum on offer
    lenders: dict[str, LoanLender] = Field(default_factory=dict)


class BalanceModel(Strict):
    time: TimeBalance
    character: CharacterBalance
    skills: SkillsBalance
    luck: LuckBalance
    checks: ChecksBalance
    gigs: GigsBalance
    debt: DebtBalance
    housing: HousingBalance
    activities: ActivitiesBalance
    property: PropertyBalance
    cyber: CyberBalance
    trauma_team: dict[TraumaTier, TraumaTierBalance]
    death: DeathBalance
    legacy: LegacyBalance
    events: EventsBalance
    crime: CrimeBalance
    jail: JailBalance
    combat: CombatBalance
    healing: HealingBalance
    relations: RelationsBalance
    careers: CareersBalance
    world: WorldBalance
    narrator: NarratorBalance
    ui: UiBalance
    mods_caps: ModsCapsBalance
    enemy_ai: EnemyAiBalance
    netrun: NetrunBalance
    chrome_free: ChromeFreeBalance
    daily: DailyBalance
    caliber: CaliberBalance
    dirt: DirtBalance
    reputation: ReputationBalance
    reminders: RemindersBalance
    world_effects: WorldEffectsBalance
    roles: RolesBalance
    exits: ExitsBalance
    car: CarBalance
    cat: CatBalance
    coin: CoinBalance
    cyberpsychosis: CyberpsychosisBalance
    ripper: RipperBalance
    backup: BackupBalance = Field(default_factory=BackupBalance)
    retention: RetentionBalance = Field(default_factory=RetentionBalance)
    vehicles: VehicleBalance = Field(default_factory=VehicleBalance)
    upgrades: UpgradesBalance = Field(default_factory=UpgradesBalance)
    players: PlayersBalance = Field(default_factory=PlayersBalance)
    chat_games: ChatGamesBalance = Field(default_factory=ChatGamesBalance)
    arcade: ArcadeBalance  # owner request 29.10
    economy: EconomyBalance = Field(default_factory=EconomyBalance)
    paths: PathsBalance = Field(default_factory=PathsBalance)
    freedom: FreedomBalance = Field(default_factory=FreedomBalance)
    chrono: ChronoBalance = Field(default_factory=ChronoBalance)
    elysium: ElysiumBalance = Field(default_factory=ElysiumBalance)
    living: LivingBalance = Field(default_factory=LivingBalance)
    dyn: DynFactionsBalance = Field(default_factory=DynFactionsBalance)
    sortie: SortieBalance = Field(default_factory=SortieBalance)
    dgame: DgameBalance = Field(default_factory=DgameBalance)
    corpwar: CorpWarBalance = Field(default_factory=CorpWarBalance)
    loans: LoansBalance = Field(default_factory=LoansBalance)
    vampire: VampireBalance = Field(default_factory=VampireBalance)
    perks: PerksBalance = Field(default_factory=PerksBalance)
    blackwall: BlackwallBalance = Field(default_factory=BlackwallBalance)
    net: NetBalance
    libria: LibriaBalance
    hubs: HubsBalance
    gym: GymBalance = Field(default_factory=GymBalance)
    purge: PurgeBalance = Field(default_factory=PurgeBalance)
    pressure: PressureBalance = Field(default_factory=PressureBalance)  # owner request 24.10
    mortality: MortalityBalance = Field(default_factory=MortalityBalance)  # owner request 24.10
    challenge: ChallengeBalance = Field(default_factory=ChallengeBalance)  # owner request 25.10
    arena: ArenaBalance = Field(default_factory=ArenaBalance)  # owner request 30.10: the fighting arena
    fight3d: Fight3dBalance = Field(default_factory=Fight3dBalance)  # owner request 31.10: the fight in first person
    city3d: City3dBalance = Field(default_factory=City3dBalance)  # owner request 30.10: the district in 3D
    corp_raid: CorpRaidBalance = Field(default_factory=CorpRaidBalance)  # owner request 25.10
    corp_stand: CorpStandBalance = Field(default_factory=CorpStandBalance)  # owner request 25.10
    control: ControlBalance = Field(default_factory=ControlBalance)  # owner request 25.10
    turf: TurfBalance = Field(default_factory=TurfBalance)  # owner request 25.10
    army: ArmyBalance = Field(default_factory=ArmyBalance)  # owner request 25.10
    enemies: EnemiesBalance = Field(default_factory=EnemiesBalance)  # owner request 25.10
    ambitions: AmbitionsBalance = Field(default_factory=AmbitionsBalance)  # owner request 25.10
    endings: EndingsBalance = Field(default_factory=EndingsBalance)  # owner request 25.10
    expeditions: ExpeditionsBalance = Field(default_factory=ExpeditionsBalance)  # owner request 25.10
    wear: WearBalance = Field(default_factory=WearBalance)  # owner request 25.10
    relics: RelicsBalance = Field(default_factory=RelicsBalance)  # owner request 25.10
    hariton: HaritonBalance = Field(default_factory=HaritonBalance)  # owner request 25.10
    bw_tree: BwTreeBalance = Field(default_factory=BwTreeBalance)  # owner request 25.10
    escort: EscortBalance = Field(default_factory=EscortBalance)  # owner request 25.10
    pmc: PmcBalance = Field(default_factory=PmcBalance)  # owner request 25.10
    dispatch: DispatchBalance = Field(default_factory=DispatchBalance)  # owner request 25.10
    romance: RomanceBalance = Field(default_factory=RomanceBalance)  # owner request 25.10
    elite_auto: EliteAutoBalance = Field(default_factory=EliteAutoBalance)  # owner request 25.10
    deeds: DeedsBalance = Field(default_factory=DeedsBalance)  # owner request 26.10
    npc_acts: NpcActsBalance = Field(default_factory=NpcActsBalance)  # owner request 26.10
    director: DirectorBalance = Field(default_factory=DirectorBalance)  # owner request 26.10
    birth_card: BirthCardBalance = Field(default_factory=BirthCardBalance)  # owner request 26.10
    stickers: StickersBalance = Field(default_factory=StickersBalance)  # owner request 26.10
    possession: PossessionBalance = Field(default_factory=PossessionBalance)  # owner request 26.10
    loot: LootBalance = Field(default_factory=LootBalance)  # owner request 26.10
    pboss: PBossBalance = Field(default_factory=PBossBalance)  # owner request 27.10
    chrome_abilities: dict[str, ChromeAbility] = Field(default_factory=dict)  # owner request 26.10: PLAN_CHROME
    chrome_rank: ChromeRank = Field(default_factory=ChromeRank)
    dm_relay: DmRelayBalance = Field(default_factory=DmRelayBalance)
    dialogs: DialogsBalance = Field(default_factory=DialogsBalance)
    wanted: WantedBalance = Field(default_factory=WantedBalance)
    equilib: EquilibBalance = Field(default_factory=EquilibBalance)
    injury: InjuryBalance = Field(default_factory=InjuryBalance)
    clones: ClonesBalance = Field(default_factory=ClonesBalance)
    capri: CapriBalance = Field(default_factory=CapriBalance)
    event_text: EventTextBalance = Field(default_factory=EventTextBalance)
    look: LookBalance = Field(default_factory=LookBalance)
    chrome_power: ChromePowerBalance = Field(default_factory=ChromePowerBalance)
    pally: PallyBalance = Field(default_factory=PallyBalance)
    blacktears: BlackTearsBalance = Field(default_factory=BlackTearsBalance)
    corpforce: CorpForceBalance = Field(default_factory=CorpForceBalance)
    tower_dungeon: TowerDungeonBalance = Field(default_factory=TowerDungeonBalance)
    chrome_sets: dict[str, ChromeSet] = Field(default_factory=dict)  # owner request 26.10: PLAN_CHROME
    grammar: dict[str, list[str]] = Field(default_factory=dict)  # owner request 26.10: engine/grammar.py
    legend: LegendBalance = Field(default_factory=LegendBalance)  # owner request 25.10
    season: SeasonBalance = Field(default_factory=SeasonBalance)  # owner request 25.10
    weather: WeatherBalance = Field(default_factory=WeatherBalance)

    @model_validator(mode="after")
    def _reputation_matches(self) -> "BalanceModel":
        if self.reputation.street_cred_max != self.character.street_cred_max:
            raise ValueError(
                "reputation.street_cred_max must equal character.street_cred_max "
                f"({self.reputation.street_cred_max} != {self.character.street_cred_max})"
            )
        return self

    @field_validator("trauma_team")
    @classmethod
    def _all_tiers(cls, v: dict[str, Any]) -> dict[str, Any]:
        if set(v) != {"silver", "gold", "platinum"}:
            raise ValueError("trauma_team must define silver, gold, platinum")
        return v


# ---------------------------------------------------------------------------
# Character building blocks
# ---------------------------------------------------------------------------


class Gear(Strict):
    weapons: list[Key] = []  # weapons.yaml keys
    armor: Key | None = None  # armor.yaml key
    cyberware: list[Key] = []  # cyberware.yaml keys
    vehicle: Key | None = None  # vehicles.yaml key: a starting ride (owner request)


class Preset(Timed):
    """A starting kit (DESIGN.md 5): stat skew, a couple of skills, some gear, sometimes a career.

    A preset is NOT a role. Roles are earned through play, see `Role` below.
    """

    key: Key
    group: Literal["street", "crime", "wheels", "order", "tech", "stage"] = "street"  # the tab at character creation
    name: str  # neutral name, no role word: "Уличный стрелок", not "Соло"
    description: str  # one line shown at preset choice
    stat_template: dict[Stat, int]  # main stats 6-7, rest lower
    skills: dict[Skill, int] = {}  # starting skill levels 0-6
    gear: Gear = Gear()
    starting_career: Key | None = None  # careers.yaml key, starts at rank 1
    origin_hint: Key | None = None  # origins.yaml key the generator prefers for this preset; null = any
    chrome_free_start: bool = False  # the character starts with the chrome_free flag (DESIGN.md 7a)
    # True = never drawn among the random preset offers, always shown as the extra last option
    # (the civilian). The generator draws the random offers from presets with always_offered false.
    always_offered: bool = False

    @field_validator("stat_template")
    @classmethod
    def _all_stats(cls, v: dict[str, int]) -> dict[str, int]:
        if set(v) != set(STATS):
            raise ValueError(f"stat_template must define exactly {STATS}")
        if any(not 2 <= x <= 8 for x in v.values()):
            raise ValueError("template stats must be 2..8")
        return v

    @field_validator("skills")
    @classmethod
    def _skill_range(cls, v: dict[str, int]) -> dict[str, int]:
        if any(not 0 <= x <= 6 for x in v.values()):
            raise ValueError("starting skills must be 0..6")
        return v


class RolePerk(Strict):
    """A role ability opened at a rank. Perks of every role work at that role's own rank."""

    rank: int = Field(ge=1, le=10)
    name: str
    description: str  # one line for the player
    mods: Mods  # by the usual mod grammar; must not be empty

    @field_validator("mods")
    @classmethod
    def _not_empty(cls, v: dict[str, int]) -> dict[str, int]:
        if not v:
            raise ValueError("a perk needs mods")
        return v


class Role(Timed):
    """An emergent role (DESIGN.md 5b): earned from play through affinity, not chosen.

    A record with only key, name and description (no affinity, decay_pct, rank_thresholds, perks)
    is a PLACEHOLDER: it loads, and the loader prints a `fill` warning until it is filled like `solo`.
    A half-filled record is an error.
    """

    key: Key
    name: str
    description: str  # one line: who this is
    # `bus event[:param] -> weight`, see "Сродство" in CONTENT_GUIDE.md and `parse_bus_key`.
    affinity: Annotated[dict[str, int], BeforeValidator(lambda v: validate_affinity(v))] = {}
    decay_pct: int | None = Field(default=None, ge=1, le=50)  # percent of affinity lost per week
    rank_thresholds: list[int] = []  # affinity needed for rank 1, 2, ... (max_rank values, ascending)
    perks: list[RolePerk] = []

    @property
    def is_placeholder(self) -> bool:
        return not (self.affinity or self.decay_pct is not None or self.rank_thresholds or self.perks)

    @field_validator("rank_thresholds")
    @classmethod
    def _thresholds(cls, v: list[int]) -> list[int]:
        if v and (v[0] < 1 or v != sorted(set(v))):
            raise ValueError("rank_thresholds must be ascending whole numbers >= 1, no repeats")
        return v

    @field_validator("perks")
    @classmethod
    def _perk_ranks(cls, v: list[RolePerk]) -> list[RolePerk]:
        ranks = [p.rank for p in v]
        if ranks != sorted(set(ranks)):
            raise ValueError("perks must be ascending by rank, one perk per rank")
        return v

    @model_validator(mode="after")
    def _all_or_nothing(self) -> "Role":
        if self.is_placeholder:
            return self
        missing = [
            name for name, ok in (
                ("affinity", bool(self.affinity)),
                ("decay_pct", self.decay_pct is not None),
                ("rank_thresholds", bool(self.rank_thresholds)),
                ("perks", bool(self.perks)),
            ) if not ok
        ]
        if missing:
            raise ValueError(
                f"role is half filled, missing: {', '.join(missing)}; fill all four like 'solo', "
                "or leave all of them out to keep a placeholder"
            )
        return self


class Origin(Timed):
    key: Key
    name: str
    description: str
    start_district: Key  # districts.yaml key
    skill_bonus: dict[Skill, int] = {}
    start_street_cred: int = Field(default=0, ge=0)
    enemy_gang: Key | None = None  # gangs.yaml key; the character starts with this enemy
    chrome_free_start: bool = False  # the character starts with the chrome_free flag (DESIGN.md 7a)
    start_vow: Key | None = None  # vows.yaml key; the character starts holding this vow


class Debt(Timed):
    key: Key
    name: str  # short label for tables
    creditor: str  # display name of the creditor
    creditor_kind: CreditorKind
    creditor_ref: Key | None = None  # gang / corp / ripperdoc key, by creditor_kind
    principal_min: int = Field(ge=1)
    principal_max: int = Field(ge=1)
    weekly_rate: float | None = Field(default=None, gt=0, le=1)  # None = balance default
    description: str

    @model_validator(mode="after")
    def _range(self) -> "Debt":
        if self.principal_min > self.principal_max:
            raise ValueError("principal_min > principal_max")
        if self.creditor_kind in ("gang", "corp", "ripper") and not self.creditor_ref:
            raise ValueError(f"creditor_kind {self.creditor_kind!r} needs creditor_ref")
        return self


class TextEntry(Strict):
    key: Key
    name: str
    description: str  # narrative hint for the narrator
    mods: Mods = {}  # optional mechanics, see DESIGN.md section 8


class TraitsFile(Strict):
    traits: list[TextEntry]
    marks: list[TextEntry]
    obsessions: list[TextEntry]


Gender = Literal["m", "f"]
GENDERS: tuple[str, ...] = ("m", "f")
NameGender = Literal["m", "f", "n"]  # n = unisex: the gender of the bearer is rolled


class FirstName(Strict):
    name: str = Field(min_length=1)
    gender: NameGender


class Surname(Strict):
    """One surname with its two gender forms. A surname that does not inflect has male == female."""

    male: str = Field(min_length=1)
    female: str = Field(min_length=1)

    @property
    def inflects(self) -> bool:
        return self.male != self.female

    def form(self, gender: str) -> str:
        return self.female if gender == "f" else self.male


def _surname_from_str(v: Any) -> Any:
    """A plain string in names.yaml is a surname that does not inflect."""
    if isinstance(v, str):
        return {"male": v, "female": v}
    return v


class NamesFile(Strict):
    first_names: list[FirstName] = Field(min_length=1)
    last_names: list[Annotated[Surname, BeforeValidator(_surname_from_str)]] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> "NamesFile":
        names = [n.name for n in self.first_names]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise ValueError(f"first_names: duplicates {dup}")
        genders = {n.gender for n in self.first_names}
        if not {"m", "f"} <= genders:
            raise ValueError("first_names: need both m and f names")
        forms = [f for s in self.last_names for f in {s.male, s.female}]
        dup = sorted({f for f in forms if forms.count(f) > 1})
        if dup:
            raise ValueError(f"last_names: duplicate surname forms {dup}")
        return self


# ---------------------------------------------------------------------------
# World
# ---------------------------------------------------------------------------


class District(Timed):
    key: Key
    name: str
    description: str
    danger: int = Field(ge=1, le=5)
    fixer: Key  # fixers.yaml key, the main fixer of the district
    gangs: list[Key] = []  # gangs.yaml keys
    corps: list[Key] = []  # corps.yaml keys present in the district
    places: list[str] = []
    heat_mult: float = Field(default=1.0, gt=0)  # City Center doubles wanted growth
    image_tags: list[str] = []
    city: Key = "nc"  # owner request 25.10: the city of the district (a country key of content/world_*.yaml, or nc)


class Fixer(Timed):
    key: Key
    name: str
    districts: list[Key]  # districts.yaml keys
    description: str
    voice: str  # speech style hint for the narrator
    min_street_cred: int = Field(default=0, ge=0)  # Rogue: 35
    legendary: bool = False


NAME_CASES: tuple[str, ...] = ("gen", "dat", "acc", "ins", "pre")  # Russian cases of a faction name (gig titles)


class Gang(Timed):
    key: Key
    name: str
    # Case forms for gig titles ({target_gen} ...): a gang whose Russian name declines sets them;
    # a missing one defaults to `name` (an indeclinable name such as «Валентинос»).
    name_gen: str = ""
    name_dat: str = ""
    name_acc: str = ""
    name_ins: str = ""
    name_pre: str = ""
    description: str
    style: str  # narrator hint
    disposition: Disposition  # bribe, talk, flee and surrender behaviour (DESIGN.md 6 "Выходы без боя")
    fights_as: Key | None = None  # owner request 25.10: a gang abroad fights with these archetypes, in its own name

    @model_validator(mode="before")
    @classmethod
    def _case_defaults(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("name"):
            data = dict(data)
            for case in NAME_CASES:
                if not data.get(f"name_{case}"):
                    data[f"name_{case}"] = data["name"]
        return data


class Corp(Timed):
    key: Key
    name: str
    description: str
    kind: Literal["megacorp", "service", "law"] = "megacorp"
    disposition: Disposition  # corp security / service behaviour (DESIGN.md 6 "Выходы без боя")


class Ripperdoc(Timed):
    key: Key
    name: str
    district: Key | None = None  # None = an underground ripper in every district
    description: str
    price_mod: float = Field(gt=0)  # 1.0 normal, 0.7 Fingers, 0.5 underground
    complication_chance: Pct
    infection_chance: Pct = 0.0
    scan_lie_chance: Pct = 0.0
    underground: bool = False
    sells_software: bool = False  # also sells quickhacks ("Софт")
    # owner request 25.10: every ripper has its own shelf. The specialty is always in stock and cheaper; the rest of the
    # catalog is a share that changes every lived month. Empty specialty = two categories picked by the ripper's key.
    specialty: list[str] = []
    stock_pct: int = Field(default=40, ge=0, le=100)


# ---------------------------------------------------------------------------
# Equipment
# ---------------------------------------------------------------------------


class ActiveEffects(Strict):
    extra_actions: int = 0          # extra attacks per tick while active
    check_bonus: int = 0            # bonus to all combat checks while active
    melee_bonus: int = 0
    damage_reduction: int = 0       # incoming damage reduced while active
    enemy_attack_penalty: int = 0   # enemy attack rolls reduced while active
    dodge: bool = False             # reactive: an incoming hit becomes a miss
    next_attack_bonus: int = 0      # reactive: bonus to the next own attack
    damage: Dice | None = None      # shot: damage of the shot
    targets: int = Field(default=1, ge=1)


class ActiveSpec(Strict):
    """Charge is fuel. See DESIGN.md section 8 "Активные импланты"."""
    mode: ActiveMode
    button: str                                   # Russian button label
    min_charge: int = Field(default=100, ge=1, le=100)  # charge needed to activate / trigger / fire
    duration_ticks: int = Field(default=0, ge=0)  # duration mode: ticks a FULL charge lasts; drain = ceil(100 / duration_ticks) per tick
    can_cancel: bool = False                      # duration mode: may be switched off early, the rest of the charge stays
    cost: int = Field(default=100, ge=1, le=100)  # reactive: per trigger; shot: per target
    trigger_toggle: bool = False                  # reactive: player can switch auto-trigger on/off
    recharge_per_tick: int = Field(ge=0, le=100)  # in combat, only while not active
    recharge_on_kill: int = Field(default=0, ge=0, le=100)      # applies always, also while active (extends it)
    recharge_on_takedown: int = Field(default=0, ge=0, le=100)
    effects: ActiveEffects

    @model_validator(mode="after")
    def _mode_fields(self) -> "ActiveSpec":
        if self.mode == "duration" and self.duration_ticks < 1:
            raise ValueError("duration mode needs duration_ticks >= 1")
        if self.mode == "shot" and self.effects.damage is None:
            raise ValueError("shot mode needs effects.damage")
        if self.mode == "reactive" and not (self.effects.dodge or self.effects.next_attack_bonus):
            raise ValueError("reactive mode needs dodge or next_attack_bonus")
        if self.mode != "duration" and self.cost > self.min_charge:
            raise ValueError("cost cannot exceed min_charge")
        if self.mode == "shot" and self.cost * self.effects.targets > 100:
            raise ValueError("cost per target x targets cannot exceed 100")
        return self


class DeckSpec(Strict):
    """Cyberdeck numbers, see DESIGN.md section 8 "Кибердека и быстрые взломы"."""

    ram_max: int = Field(ge=1, le=30)  # RAM pool
    ram_recharge: int = Field(ge=0, le=30)  # RAM restored per tick
    hack_bonus: int = Field(default=0, ge=0, le=10)  # added to quickhack rolls (not to skill.netrunning)


class Cyberware(Timed):
    key: Key
    name: str
    slot: CyberSlot
    price: int = Field(ge=0)
    humanity_cost: int = Field(ge=0)  # PCh, before the tolerance multiplier
    description: str
    tier: CyberTier  # street < 3500 eddies, mid 3500-9999, military 10000+
    category: CyberCategory
    requires: list[Key] = []  # cyberware keys that must already be installed
    exclusive_group: str | None = None  # items in one group replace the same organ: only one installed
    mods: Mods = {}  # everything that changes numbers outside `active`: skill.*, armor, check.* ...
    weapon_damage: Dice | None = None
    weapon_targets: int = Field(default=1, ge=1)
    active: ActiveSpec | None = None  # charge-based combat implant, see DESIGN.md section 8
    deck: DeckSpec | None = None  # required for exclusive_group cyberdeck, forbidden otherwise
    resurrection: bool = False  # Second Heart
    grants_hacks: list[Key] = Field(default_factory=list)  # deck programs the implant brings (a Blackwall script)
    smasher_only: bool = False  # Full Conversion
    borg_risk: bool = False     # owner request 27.10: the underground full conversion, anyone may die on the table
    # owner request 26.10 (docs/PLAN_CHROME.md): what the implant lets you do outside a fight (balance.chrome_abilities)
    # and the build line it belongs to (balance.chrome_sets): pieces of one line give a set bonus at 2 / 4 / 6
    abilities: list[Key] = []
    build: Key | None = None
    # True = never in a ripperdoc catalog (maxtac_inhibitor): content may still grant it via careers,
    # events (`cyberware_grant:<key>`) or enemy gear; presets / roles may not start with it.
    not_for_sale: bool = False

    @model_validator(mode="after")
    def _deck_and_category(self) -> "Cyberware":
        is_deck = self.exclusive_group == "cyberdeck"
        if is_deck and self.deck is None:
            raise ValueError("exclusive_group 'cyberdeck' needs a 'deck' block: {ram_max, ram_recharge, hack_bonus}")
        if not is_deck and self.deck is not None:
            raise ValueError("'deck' is only allowed on implants with exclusive_group: cyberdeck")
        if self.category == "booster":
            if not self.mods:
                raise ValueError("category 'booster' needs mods (usually active.<group|all>.<field>)")
            if self.active is not None or self.weapon_damage is not None or self.deck is not None:
                raise ValueError("category 'booster' changes other implants: no active, weapon_damage or deck")
        return self


WeaponType = Literal["pistol", "smg", "shotgun", "rifle", "heavy", "melee", "exotic"]
WEAPON_TYPES: tuple[str, ...] = ("pistol", "smg", "shotgun", "rifle", "heavy", "melee", "exotic")


class WeaponVoice(Strict):
    """Owner request 24.10: a smart weapon with an AI inside talks in fights, in the monthly report and in a chat."""

    persona: str  # who speaks: for the chat with Claude and the handbook
    chatty: bool = False  # talks after every shot (owner request 24.10: the rapping gun), else every other one
    hit: list[str] = Field(default_factory=list)
    miss: list[str] = Field(default_factory=list)
    kill: list[str] = Field(default_factory=list)
    idle: list[str] = Field(default_factory=list)


class WeaponSpecial(Strict):
    """Owner request 25.10: what a special weapon really does in a fight (shown on its card)."""

    text: str                       # for the player: the effect in words
    stun_chance: float = Field(default=0.0, ge=0, le=1)   # on a hit: the target loses its next turn
    blind: int = Field(default=0, ge=0, le=6)             # on a hit: the target's attacks −N for 2 turns
    shock_chrome: str = ""          # on a hit: extra damage dice against a target with chrome
    pierce: bool = False            # ignores armor
    drain: int = Field(default=0, ge=0, le=20)            # heals the wielder this much per hit
    splash: str = ""                # on a hit: these dice to one more enemy


class Weapon(Timed):
    key: Key
    name: str
    description: str
    damage: Dice
    skill: Literal["firearms", "melee"]
    type: WeaponType | None = None  # the shop tab; None = pistol for firearms, melee for melee
    price: int = Field(ge=0)
    close_range_only: bool = False  # shotgun: full damage only up close
    extra_attempts: int = Field(default=0, ge=0)  # SMG: +1 attempt
    min_street_cred: int = Field(default=0, ge=0)
    mods: Mods = {}
    not_for_sale: bool = False  # never on a shop shelf: granted by events (`item_grant`), e.g. Blackwall weapons
    voice: WeaponVoice | None = None  # a talking smart weapon (owner request 24.10)
    special: WeaponSpecial | None = None  # owner request 25.10: a real combat effect


VehicleKind = Literal["bike", "car", "truck", "aero", "joke"]
VEHICLE_KINDS: tuple[str, ...] = ("bike", "car", "truck", "aero", "joke")


class Vehicle(Timed):
    """A ride (owner request): bought at the dealer or stolen. Speed helps escapes and races, armor soaks crashes,
    style helps with people. The owned one adds `mods` plus escape = speed // 3 and social = style // 4."""

    key: Key
    name: str
    description: str
    kind: VehicleKind
    price: int = Field(ge=0)
    speed: int = Field(ge=1, le=12)
    armor: int = Field(default=0, ge=0, le=10)
    style: int = Field(default=0, ge=0, le=10)
    min_street_cred: int = Field(default=0, ge=0)
    stealable: bool = True     # street models can be stolen for yourself
    not_for_sale: bool = False
    mods: Mods = {}


class Armor(Timed):
    key: Key
    name: str
    description: str
    defense: int = Field(ge=0)  # subtracted from incoming damage
    price: int = Field(ge=0)
    mods: Mods = {}
    not_for_sale: bool = False  # never on a shelf: legendary armor from events


class QuickhackEffects(Strict):
    damage: Dice | None = None
    stun_ticks: int = Field(default=0, ge=0, le=5)  # target skips its actions
    attack_penalty: int = Field(default=0, ge=0, le=10)  # penalty to target attacks, needs penalty_ticks
    penalty_ticks: int = Field(default=0, ge=0, le=5)
    drain_charge: int = Field(default=0, ge=0, le=100)  # charge points removed from the target's implant
    disable_ticks: int = Field(default=0, ge=0, le=5)  # a running implant of the target is switched off
    spread: bool = False  # also hits a second target

    @model_validator(mode="after")
    def _shape(self) -> "QuickhackEffects":
        if (self.attack_penalty > 0) != (self.penalty_ticks > 0):
            raise ValueError("attack_penalty and penalty_ticks must be set together")
        if not (self.damage or self.stun_ticks or self.attack_penalty or self.drain_charge
                or self.disable_ticks):
            raise ValueError(
                "a quickhack needs at least one effect: damage, stun_ticks, attack_penalty+penalty_ticks, "
                "drain_charge or disable_ticks (spread alone is not an effect)"
            )
        return self


class Quickhack(Timed):
    """A program bought from a netrunner fixer or ripper. See DESIGN.md section 8."""

    key: Key
    name: str
    description: str
    price: int = Field(ge=0)
    ram_cost: int = Field(ge=1, le=20)
    dv_base: int = Field(ge=5, le=30)  # dv against a target = dv_base + dv_per_danger * target danger
    dv_per_danger: int = Field(ge=0, le=5)
    tier: CyberTier
    effects: QuickhackEffects
    # True = works through the surroundings (cameras, lights, vending machines), so it also
    # works against chrome-free targets, with the balance.chrome_free.quickhack_penalty.
    environmental: bool = False
    not_for_sale: bool = False  # comes only with an implant (cyberware.grants_hacks), not from a shop


class Enemy(Timed):
    """Combat archetype. Plays by the same rules as the player. See DESIGN.md section 6."""

    key: Key
    name: str
    factions: list[Key] = Field(min_length=1)  # gangs.yaml / corps.yaml keys, or 'cyberpsycho'
    danger: int = Field(ge=1, le=5)
    hp: int | None = Field(default=None, ge=1)  # None = balance.combat formula 6 + 4 * danger
    attack: int = Field(ge=5, le=30)  # attack roll = attack + d10 against the player's defense
    defense: int = Field(ge=5, le=30)  # dv for the player to hit this enemy
    weapon: Key | None = None  # weapons.yaml key; None = unarmed or implant weapons
    armor: Key | None = None  # armor.yaml key
    cyberware: list[Key] = []  # cyberware keys, checked like role gear
    quickhacks: list[Key] = []  # quickhacks.yaml keys, need a cyberdeck in cyberware
    ai: EnemyAi
    leader: bool = False

    @field_validator("factions")
    @classmethod
    def _unique_factions(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("duplicate faction in factions")
        return v


class Housing(Strict):
    key: Key
    name: str
    description: str
    district: Key  # districts.yaml key; housing is bought in this district
    tier: int = Field(ge=1, le=4)
    rent: int = Field(ge=0)  # eddies per week
    food: int = Field(ge=0)  # eddies per week
    min_status: int = Field(default=0, ge=0)  # status needed to rent it (the doorman wants to know you)


class CareerRank(Strict):
    name: str
    salary: int = Field(ge=0)  # eddies per week
    promotion_dv: int | None = Field(default=None, ge=5, le=30)  # None on the last rank
    min_weeks: int = Field(ge=0)  # weeks of service before a promotion check
    min_humanity_lost: int = Field(default=0, ge=0)  # MaxTac: implants worth 20+ PCh


class CareerPrereq(Strict):
    """Having served in another career (MaxTac: NCPD detective)."""

    career: Key  # careers.yaml key; the loader checks it
    min_rank: int = Field(ge=1, le=5)


class CareerRequirements(Strict):
    stats: dict[Stat, int] = {}
    skills: dict[Skill, int] = {}
    from_career: CareerPrereq | None = None  # normal route: currently in this career at min_rank or above
    min_humanity_lost: int = Field(default=0, ge=0)  # total PCh of installed implants (before tolerance)


class Career(Timed):
    key: Key
    name: str
    description: str
    requirements: CareerRequirements = CareerRequirements()
    promotion_check: CheckSpec
    corps: list[Key] | None = None  # corps.yaml keys of possible employers
    ranks: list[CareerRank]
    # Contract career (MaxTac): weeks a recruited character must serve; leaving earlier = desertion.
    service_weeks: int | None = Field(default=None, ge=1)
    # True = the recruitment flow (balance.cyberpsychosis) may put a character here at rank 1,
    # ignoring `requirements`. The normal route still uses `requirements`.
    grantable_by_recruitment: bool = False
    requires_flag: Key | None = None   # owner request 24.10: only characters with this flag see and take the career
    bound_flag: Key | None = None      # ... and cannot quit while the flag is set

    @model_validator(mode="after")
    def _contract(self) -> "Career":
        if self.grantable_by_recruitment and self.service_weeks is None:
            raise ValueError("grantable_by_recruitment needs service_weeks (the contract term)")
        return self

    @field_validator("ranks")
    @classmethod
    def _five_ranks(cls, v: list[CareerRank]) -> list[CareerRank]:
        if len(v) != 5:
            raise ValueError("a career has exactly 5 ranks")
        for i, r in enumerate(v):
            if i < 4 and r.promotion_dv is None:
                raise ValueError(f"rank {i + 1} needs promotion_dv")
            if i == 4 and r.promotion_dv is not None:
                raise ValueError("the last rank must not have promotion_dv")
        return v


# ---------------------------------------------------------------------------
# Gigs and events
# ---------------------------------------------------------------------------


# Gig title placeholders: the target faction in the nominative and in each case (corps use `name` in all).
GIG_TITLE_FIELDS: frozenset[str] = frozenset({"target", *(f"target_{c}" for c in NAME_CASES)})


class GigTemplate(Timed):
    key: Key
    type: GigType
    titles: list[str] = Field(min_length=1)  # may use {target} and its cases {target_gen} ... (GIG_TITLE_FIELDS)
    targets: list[GigTarget] = Field(min_length=1)
    approaches: dict[Approach, list[CheckSpec]]
    tiers: list[int] = [1, 2, 3, 4, 5]  # gig tiers this template may appear at

    @field_validator("approaches")
    @classmethod
    def _approaches(cls, v: dict[str, list[CheckSpec]]) -> dict[str, list[CheckSpec]]:
        if set(v) != set(APPROACHES):
            raise ValueError(f"approaches must define exactly {APPROACHES}")
        for name, checks in v.items():
            if not 2 <= len(checks) <= 3:
                raise ValueError(f"approach {name!r} needs 2-3 checks")
        return v

    @field_validator("titles")
    @classmethod
    def _title_fields(cls, v: list[str]) -> list[str]:
        for t in v:
            bad = {f for _, f, _, _ in Formatter().parse(t) if f is not None and f not in GIG_TITLE_FIELDS}
            if bad:
                raise ValueError(f"title {t!r}: only {sorted(GIG_TITLE_FIELDS)} are allowed, found {sorted(bad)}")
        return v

    @field_validator("tiers")
    @classmethod
    def _tiers(cls, v: list[int]) -> list[int]:
        if not v or any(t not in GIG_TIERS for t in v):
            raise ValueError("tiers must be a non-empty subset of 1..9")
        return v


class EventConditions(Strict):
    districts: list[Key] | None = None  # null = any
    roles: list[Key] | None = None  # the MAIN role of the character (roles.yaml)
    min_role_rank: dict[Key, int] | None = None  # role key -> minimum rank of that role, 1..10
    careers: list[Key] | None = None
    min_heat: int | None = None
    max_heat: int | None = None
    max_humanity: int | None = None
    min_street_cred: int | None = None
    min_status: int | None = None
    min_fame: int | None = None
    max_mood: int | None = None
    requires: list[EventFlag] = []
    forbids: list[EventFlag] = []
    abilities: list[Key] = []  # owner request 26.10: chrome abilities the character must all have
    # cyberware keys the character must NOT have installed (an event that sells or grants that implant)
    lacks_cyberware: list[Key] = []
    # character flags (set by the effect flag_set:<key>) that must all be set / must all be unset: story arcs
    flags_all: list[Key] = []
    flags_none: list[Key] = []
    employers: list[Key] | None = None  # corps.yaml keys: the character works for one of these (career employer)
    in_gang: bool | None = None         # true = a member of a city gang (flag gang), false = not a member
    has_business: bool | None = None    # true = owns a business (flag business), false = owns none
    seasons: list[Key] | None = None    # owner request: the season of the character's date (balance.weather.seasons)
    weather: list[Key] | None = None    # the weather of the current week (balance.weather.kinds)

    @field_validator("requires", "forbids", mode="before")
    @classmethod
    def _none_is_empty(cls, v: Any) -> Any:
        return [] if v is None else v

    @field_validator("min_role_rank")
    @classmethod
    def _rank_range(cls, v: dict[str, int] | None) -> dict[str, int] | None:
        if v is not None:
            if not v:
                raise ValueError("min_role_rank: use null instead of an empty mapping")
            bad = {k: n for k, n in v.items() if not 1 <= n <= 10}
            if bad:
                raise ValueError(f"min_role_rank: ranks must be 1..10, got {bad}")
        return v


class ChoiceOutcome(Strict):
    text: str
    effects: Effects = {}


class EventChoice(Strict):
    label: str
    # With a check: success + fail. Without: text + effects.
    check: Check | None = None
    success: ChoiceOutcome | None = None
    fail: ChoiceOutcome | None = None
    text: str | None = None
    effects: Effects | None = None
    # True marks a violent choice even without start_combat (a beating, a shot, a stabbing).
    # See Event violent rule: an event with a violent choice needs a non-violent one too.
    violent: bool = False
    # owner request 26.10: a choice only a character with this chrome ability sees (balance.chrome_abilities)
    ability: Key | None = None
    # Deeds this choice stands for, as bus keys ('perform', 'deal:trade', 'work_hard:career=corpo'). They are
    # published on the game-event bus when the choice is made and its check (if any) does not fail, so
    # quests, role affinity and reputation can count them (DESIGN.md 5b, 12a).
    bus: list[str] = []

    @field_validator("bus")
    @classmethod
    def _bus_keys(cls, v: list[str]) -> list[str]:
        for key in v:
            parse_bus_key(key)
        return v

    @property
    def is_violent(self) -> bool:
        """Violent = flagged `violent`, or any outcome of the choice starts a combat."""
        if self.violent:
            return True
        outcomes = [self.effects] + [o.effects for o in (self.success, self.fail) if o is not None]
        return any("start_combat" in (fx or {}) for fx in outcomes)

    @model_validator(mode="after")
    def _shape(self) -> "EventChoice":
        if self.check is not None:
            if self.success is None or self.fail is None:
                raise ValueError("a choice with a check needs both success and fail")
            if self.text is not None or self.effects is not None:
                raise ValueError("a choice with a check must not have top-level text/effects")
        else:
            if self.success is not None or self.fail is not None:
                raise ValueError("success/fail require a check")
            if self.text is None or self.effects is None:
                raise ValueError("a choice without a check needs text and effects")
        return self


class Event(Timed):
    id: Key
    title: str
    category: EventCategory
    weight: int = Field(ge=1)
    conditions: EventConditions = EventConditions()
    text: str
    choices: list[EventChoice] = Field(min_length=2, max_length=5)

    @model_validator(mode="after")
    def _plain_and_ability(self) -> "Event":
        """2-3 choices for everyone, up to 2 more for the owners of a chrome ability (owner request 26.10). A scene
        only owners of an ability see (conditions.abilities) may have 1 plain choice."""
        plain = [c for c in self.choices if not c.ability]
        lo = 1 if self.conditions.abilities else 2
        if not lo <= len(plain) <= 3:
            raise ValueError(f"choices: an event needs {lo}-3 choices without an ability, has {len(plain)}")
        if len(self.choices) - len(plain) > 2:
            raise ValueError("choices: at most 2 ability choices")
        return self
    # Gang or corp key (or `cyberpsycho`) the character meets here. With it, a fight started by a
    # violent choice first offers the exits without a fight (pay, talk, flee, surrender) against
    # this faction's disposition. Without it there are no exits and the fight just happens.
    faction: Key | None = None
    public: bool = False  # goes into the public city feed
    # True = never drawn at random; only a service puts it in the week's queue (a child is born, a firing).
    forced_only: bool = False
    # Owner request: a story of an owned business of these kinds ([] = any kind). Never drawn at random: the weekly
    # business hook queues it for one business; "{biz}" in the texts becomes that business's name.
    business: list[str] | None = None
    # Owner report 23.10 (a bounty hunter in a parking lot became an NCPD fight): a named lone opponent. The fight
    # takes the strongest archetype of `foe_faction` (default: a gang of the district) and gives it this name.
    foe: str | None = None
    foe_faction: Key | None = None
    image_tags: list[str] = []


# ---------------------------------------------------------------------------
# Actions: a catalog of small deeds (owner request: leisure, more crime, gang tasks)
# ---------------------------------------------------------------------------

ActionSection = Literal["leisure", "crime", "gang"]
ACTION_SECTIONS: tuple[str, ...] = ("leisure", "crime", "gang")


class ActionOutcome(Strict):
    weight: int = Field(default=1, ge=1)
    text: str
    effects: Effects = {}
    event: Key | None = None  # an event (usually forced_only) that plays right after this outcome
    loud: str | None = None   # a line for the group feed at once, "{name}" = the character (a jackpot, a bank job)


class Action(Timed):
    """One deed from a menu: a price, an optional check and weighted outcomes (often funny). In `effects` the
    parameter `my_gang` stands for the character's gang (`faction_rep:my_gang: 2`)."""

    key: Key
    section: ActionSection
    name: str
    button: str = Field(max_length=22)
    description: str
    ap: int = Field(default=1, ge=0, le=3)
    cost: int = Field(default=0, ge=0)
    conditions: EventConditions = EventConditions()
    check: Check | None = None
    success: list[ActionOutcome] = Field(min_length=1)  # the outcomes of a pass (or of the deed with no check)
    fail: list[ActionOutcome] = []                      # the outcomes of a miss (needed with a check)
    bus: list[str] = []
    daily: bool = False                                 # at most once per real day

    @field_validator("bus")
    @classmethod
    def _bus_keys(cls, v: list[str]) -> list[str]:
        for key in v:
            parse_bus_key(key)
        return v

    @model_validator(mode="after")
    def _fail_with_check(self) -> "Action":
        if self.check is not None and not self.fail:
            raise ValueError("an action with a check needs fail outcomes")
        return self


# ---------------------------------------------------------------------------
# Eras, timeline, vows, tarot, quests, Afterlife shop (PLAN.md 1.6)
# ---------------------------------------------------------------------------

ERA_BRIEF_MAX = 600


class Era(Strict):
    """An era of the world (DESIGN.md 9a). Eras are contiguous: each starts the year after the previous ends."""

    key: Key
    name: str
    from_year: Year
    to_year: Year  # inclusive; the last era should run far ahead (2200) so the world never runs out of eras
    brief: str = Field(max_length=ERA_BRIEF_MAX)  # for Claude, up to 600 characters
    player_text: str  # what the player reads when the era begins
    chrome_price_mult: float = Field(gt=0, le=10)  # multiplier of implant prices
    chrome_humanity_mult: float = Field(gt=0, le=10)  # multiplier of humanity loss from implants
    news_tone: str  # tone hint for the news narrator

    @model_validator(mode="after")
    def _years(self) -> "Era":
        if self.from_year > self.to_year:
            raise ValueError(f"from_year ({self.from_year}) is later than to_year ({self.to_year})")
        return self


class TimelineEvent(Strict):
    """A canon (or generated-style) world event fired when the world reaches year/week."""

    id: Key
    year: Year
    week: int = Field(ge=1, le=52)
    title: str
    description: str  # context for Claude, not shown to players as is
    announcement: str = Field(max_length=600)  # the post in the city feed
    effects: WorldEffects = {}
    canon: bool = True  # False = a sample or an invented event; canon needs a real source
    source_note: str = Field(min_length=1)  # where the date comes from (wiki page, book)


VowForbid = Literal["cyberware", "violence", "braindance", "crime", "lies"]
VOW_FORBIDS: tuple[str, ...] = ("cyberware", "violence", "braindance", "crime", "lies")


class VowBonus(Strict):
    weeks: int = Field(ge=1)  # reached when the streak is at least this many weeks
    mods: Mods  # while the vow holds and this streak is reached

    @field_validator("mods")
    @classmethod
    def _not_empty(cls, v: dict[str, int]) -> dict[str, int]:
        if not v:
            raise ValueError("a streak bonus needs mods")
        return v


class Heritage(Strict):
    """Owner request 23.10: family roots in the character editor. Cosmetic: names and the profile line only."""

    key: Key
    name: str
    description: str
    male: list[str] = Field(min_length=1)
    female: list[str] = Field(min_length=1)
    surnames: list[Surname] = Field(min_length=1)

    @field_validator("surnames", mode="before")
    @classmethod
    def _plain_surnames(cls, v: Any) -> Any:
        return [{"male": x, "female": x} if isinstance(x, str) else x for x in (v or [])]


class CountryWork(Strict):
    name: str
    button: str = Field(max_length=22)
    ap: int = Field(default=1, ge=0, le=3)
    stat: Stat
    skill: Skill | None = None
    dv: int = Field(ge=5, le=30)
    pay: tuple[int, int]
    text_ok: str
    text_fail: str
    fail_effects: Effects = {}


class CountryDeed(Strict):
    key: Key
    name: str
    button: str = Field(max_length=22)
    ap: int = Field(default=1, ge=0, le=3)
    cost: int = Field(default=0, ge=0)
    check: Check | None = None
    success: ChoiceOutcome
    fail: ChoiceOutcome | None = None


class Country(Timed):
    """Owner request 23.10: a country of the world to fly to and live in (content/countries.yaml)."""

    key: Key
    name: str
    city: str
    region: Literal["america", "europe", "asia", "africa", "oceania", "crossover"] = "america"
    description: str
    flight: int = Field(ge=0)
    visa: int = Field(default=0, ge=0)
    weekly_cost: int = Field(ge=0)
    max_heat: int = Field(default=3, ge=0, le=5)   # above it the airport is closed: only the smuggler
    smuggle_price: int = Field(ge=0)
    mods: Mods = {}
    work: CountryWork
    deeds: list[CountryDeed] = []
    # owner request 25.10: the city of the country (filled by the loader from content/world_*.yaml)
    police: str = ""               # the local police instead of the NCPD (a world pack sets it)
    districts: list[Key] = []
    story_title: str = ""
    story: list[Key] = []          # forced events, one step per day (services/abroad.story_next)


class WorldPack(Strict):
    """Owner request 25.10: the city of a country, an alternative to Night City (content/world_*.yaml)."""

    country: Key
    police: str = ""
    districts: list[dict[str, Any]]
    fixers: list[dict[str, Any]] = []
    gangs: list[dict[str, Any]] = []
    ripperdocs: list[dict[str, Any]] = []
    housing: list[dict[str, Any]] = []
    story_title: str = ""
    story: list[Key] = []


class Vow(Strict):
    key: Key
    name: str
    description: str
    forbids: list[VowForbid] = Field(min_length=1)  # what breaks the vow
    streak_bonuses: list[VowBonus] = Field(min_length=1)  # ascending by weeks; bonuses stack
    break_effects: Effects  # applied once when the vow is broken (the streak resets separately)

    @field_validator("forbids")
    @classmethod
    def _unique_forbids(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("duplicate value in forbids")
        return v

    @field_validator("streak_bonuses")
    @classmethod
    def _ascending(cls, v: list[VowBonus]) -> list[VowBonus]:
        weeks = [b.weeks for b in v]
        if weeks != sorted(set(weeks)):
            raise ValueError("streak_bonuses must be ascending by weeks, no repeats")
        return v


class Icon(Strict):
    """One icon of content/icons.yaml (app/bot/ui/style.py). Buttons always show `emoji`; message text shows the
    premium custom emoji when `custom_emoji_id` is set, with `emoji` as its fallback."""

    key: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)?$")
    emoji: str = Field(min_length=1, max_length=16)
    custom_emoji_id: str = Field(default="", pattern=r"^(\d{1,32})?$")


class Tarot(Strict):
    """One of the 22 Major Arcana: the Misty card of the day."""

    key: Key
    name: str
    text: str  # Misty's line
    mods: Mods = {}  # temporary, last until Kyiv midnight; negative values are fine
    eddies: int = 0  # signed
    weight: int = Field(ge=1)  # draw weight

    @model_validator(mode="after")
    def _has_effect(self) -> "Tarot":
        if not self.mods and not self.eddies:
            raise ValueError("a card needs mods or eddies")
        return self


QuestPath = Literal["combat", "noncombat", "chrome_free", "any"]
QUEST_PATHS: tuple[str, ...] = ("combat", "noncombat", "chrome_free", "any")
QuestPeriod = Literal["daily", "weekly"]

CRIME_KINDS: tuple[str, ...] = ("pickpocket", "car_theft", "store_robbery", "assault")
HEAL_TARGETS: tuple[str, ...] = ("self", "other")
DEAL_KINDS: tuple[str, ...] = ("broker", "trade")  # broker = mediation and contacts, trade = business and resale

# Events of the game-event bus (services/game_events.py). ONE shared enum: quest goals count them
# (quests.yaml `goal`) and role affinity grows from them (roles.yaml `affinity`). The parameters each
# one accepts: ("choice", values), ("int", lo, hi) (matches when the event value is >= the given one),
# ("bool",) or ("ref", catalog) (a snake_case key checked against that catalog by the loader;
# catalog is an attribute of Content: careers, districts).
_GoalParam = tuple[Any, ...]
QUEST_GOAL_EVENTS: dict[str, dict[str, _GoalParam]] = {
    "gig_done": {
        "approach": ("choice", APPROACHES),
        "type": ("choice", GIG_TYPES),
        "min_tier": ("int", 1, 5),
        "outcome": ("choice", OUTCOME_TIERS),
    },
    "crime_done": {"kind": ("choice", CRIME_KINDS), "success": ("bool",)},
    "fight_won": {"min_danger": ("int", 1, 5)},
    "takedown": {},
    "family_time": {},
    "social_time": {},
    "meditate": {},
    "martial_arts": {},
    "service": {},
    "craft": {},  # repair, build, modify gear (Techie)
    "heal": {"target": ("choice", HEAL_TARGETS)},  # first aid, surgery (Medtech)
    "deal": {"kind": ("choice", DEAL_KINDS)},  # brokering a deal, buying and reselling (Fixer, Trader)
    "perform": {},  # a gig on stage (Rockerboy)
    "report": {},  # a story, filming (Media)
    "drive": {"district": ("ref", "districts")},  # a road trip (Nomad)
    "hack": {},  # a hack outside a fight (Netrunner)
    "quickhack_used": {},  # a quickhack in a fight (Netrunner)
    "vow_week": {"vow": ("ref", "vows")},  # a week lived while holding a vow (Monk); one event per vow
    "lie": {},  # a choice built on a lie (breaks the vow of honest words)
    "train": {"skill": ("choice", SKILLS)},
    "study": {"skill": ("choice", SKILLS)},
    "rest": {},
    "bar": {},
    "braindance": {},
    "therapy": {},
    "work_hard": {"career": ("ref", "careers")},
    "promotion": {"career": ("ref", "careers")},
    "cyberware_installed": {},
    "debt_paid": {},
    "event_resolved": {"category": ("choice", EVENT_CATEGORIES), "nonviolent": ("bool",)},
    "week_lived": {"max_heat": ("int", 0, 5), "min_mood": ("int", 0, 100)},
    # interaction between players (docs/EPIC_PLAYERS.md)
    "player_gift": {},
    "player_drink": {},
    "player_vouch": {},
    "player_note": {},
    "player_hookup": {},
    "player_date": {},
    "player_duel": {"won": ("bool",)},
    "player_together": {},
    "player_attack": {"won": ("bool",)},
    "player_job": {"outcome": ("choice", ("full", "half", "fail"))},
    "item_bought": {},  # a weapon or armor from the shop
    # garage and workshop (owner request): a weapon mod put on, a thing for home, tuning, a ride bought or stolen
    "casino_spin": {"won": ("bool",)},  # owner request: a spin of the chat slot machine
    "fish_caught": {"rare": ("bool",)},  # owner request: a cast of the fishing that brought something
    "upgrade_done": {"kind": ("choice", ("weapon_mod", "home_item", "tune", "ride_bought", "ride_stolen", "repair"))},
    # a small deed (leisure, more crime, gang tasks); `action` names one deed, `ok` = its check passed
    "action_done": {"section": ("choice", ACTION_SECTIONS), "action": ("ref", "actions"), "ok": ("bool",)},
}


BUS_EVENTS = QUEST_GOAL_EVENTS  # the same dict under its general name


def _goal_help() -> str:
    parts = []
    for ev, params in QUEST_GOAL_EVENTS.items():
        parts.append(f"{ev}({', '.join(params)})" if params else ev)
    return "; ".join(parts)


def _param_error(spec: _GoalParam, event: str, name: str, val: object) -> str | None:
    """Why `val` is not a valid value of parameter `name`, or None when it is fine."""
    if spec[0] == "choice" and val not in spec[1]:
        return f"{event}.{name}: {val!r} is not one of {list(spec[1])}"
    if spec[0] == "int" and (not _is_int(val) or not spec[1] <= val <= spec[2]):
        return f"{event}.{name}: expected integer {spec[1]}..{spec[2]}, got {val!r}"
    if spec[0] == "bool" and not isinstance(val, bool):
        return f"{event}.{name}: expected true or false, got {val!r}"
    if spec[0] == "ref" and not (isinstance(val, str) and KEY_RE.match(val)):
        return f"{event}.{name}: expected a {spec[1]} key (snake_case), got {val!r}"
    return None


def parse_bus_key(key: str) -> tuple[str, dict[str, Any]]:
    """Parse an affinity key: `event`, `event:value` or `event:param=value`.

    `gig_done:loud` is short for `gig_done:approach=loud`; the short form works only when the value
    belongs to exactly one choice parameter of the event (`hack` is both an approach and a gig type,
    so write `gig_done:approach=hack`). Integer and bool parameters need the long form
    (`fight_won:min_danger=3`). Returns (event, {param: typed value}). Raises ValueError.
    """
    event, sep, rest = key.partition(":")
    if event not in QUEST_GOAL_EVENTS:
        raise ValueError(f"affinity key {key!r}: unknown bus event {event!r}; allowed events (with parameters): {_goal_help()}")
    params = QUEST_GOAL_EVENTS[event]
    if not sep:
        return event, {}
    if not rest:
        raise ValueError(f"affinity key {key!r}: nothing after ':'; write '{event}' or '{event}:<value>'")
    if "=" in rest:
        name, _, raw = rest.partition("=")
        spec = params.get(name)
        if spec is None:
            raise ValueError(
                f"affinity key {key!r}: event {event!r} has no parameter {name!r}; "
                f"allowed: {list(params) if params else 'none'}"
            )
        val: Any = raw
        if spec[0] == "int":
            try:
                val = int(raw)
            except ValueError:
                raise ValueError(f"affinity key {key!r}: {name} must be an integer") from None
        elif spec[0] == "bool":
            if raw not in ("true", "false"):
                raise ValueError(f"affinity key {key!r}: {name} must be true or false")
            val = raw == "true"
        err = _param_error(spec, event, name, val)
        if err:
            raise ValueError(f"affinity key {key!r}: {err}")
        return event, {name: val}
    matches = [n for n, sp in params.items() if sp[0] == "choice" and rest in sp[1]]
    if not matches:
        raise ValueError(
            f"affinity key {key!r}: {rest!r} is not a value of any choice parameter of {event!r} "
            f"(parameters: {list(params) if params else 'none'}); use '{event}:param=value' for "
            "integer, bool and key parameters"
        )
    if len(matches) > 1:
        raise ValueError(
            f"affinity key {key!r}: {rest!r} fits several parameters {matches}; "
            f"write '{event}:<param>={rest}'"
        )
    return event, {matches[0]: rest}


AFFINITY_WEIGHT_MAX = 20


def validate_affinity(v: Any) -> dict[str, int]:
    """Affinity keys parse as bus events, weights are whole numbers 1..AFFINITY_WEIGHT_MAX."""
    if v is None:
        return {}
    if not isinstance(v, dict):
        raise ValueError("affinity must be a mapping like {gig_done:loud: 3, fight_won: 2}")
    errors: list[str] = []
    for key, val in v.items():
        if not isinstance(key, str):
            errors.append(f"affinity key {key!r} must be a string")
            continue
        try:
            parse_bus_key(key)
        except ValueError as e:
            errors.append(str(e))
            continue
        if not _is_int(val) or not 1 <= val <= AFFINITY_WEIGHT_MAX:
            errors.append(f"affinity {key!r}: weight must be a whole number 1..{AFFINITY_WEIGHT_MAX}, got {val!r}")
    if errors:
        raise ValueError(" | ".join(errors))
    return v


def validate_quest_goal(v: Any) -> dict[str, Any]:
    if not isinstance(v, dict) or "event" not in v:
        raise ValueError("goal must be a mapping with 'event', like {event: gig_done, approach: quiet}")
    event = v["event"]
    if event not in QUEST_GOAL_EVENTS:
        raise ValueError(f"unknown goal event {event!r}; allowed events (with parameters): {_goal_help()}")
    allowed = QUEST_GOAL_EVENTS[event]
    errors: list[str] = []
    for name, val in v.items():
        if name == "event":
            continue
        spec = allowed.get(name)
        if spec is None:
            errors.append(
                f"goal event {event!r} has no parameter {name!r}; "
                f"allowed: {list(allowed) if allowed else 'none'}"
            )
            continue
        err = _param_error(spec, event, name, val)
        if err:
            errors.append(f"goal {err}")
    if errors:
        raise ValueError(" | ".join(errors))
    return v


QuestGoal = Annotated[dict[str, Any], BeforeValidator(validate_quest_goal)]


class QuestRewards(Strict):
    eddies: int = Field(default=0, ge=0)
    xp: int = Field(default=0, ge=0)  # skill xp, the engine picks the skill
    tokens: int = Field(default=0, ge=0)  # Afterlife tokens on top of balance.daily.quest_tokens
    street_cred: int = Field(default=0, ge=0)
    status: int = Field(default=0, ge=0)
    fame: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _not_empty(self) -> "QuestRewards":
        if not any(self.model_dump().values()):
            raise ValueError("a quest needs at least one reward")
        return self


class QuestWorld(Strict):
    """Owner request 26.10: a claimed quest moves the character's district (services/control.adjust)."""

    gang: int = 0
    corp: int = 0
    text: str


class Quest(Strict):
    key: Key
    text: str  # shown to the player, Russian, one short line
    period: QuestPeriod
    paths: list[QuestPath] = Field(min_length=1)  # who gets it; 'any' = every path
    goal: QuestGoal
    target: int = Field(ge=1)  # how many times the goal event must happen
    rewards: QuestRewards
    world: QuestWorld | None = None

    @field_validator("paths")
    @classmethod
    def _paths(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("duplicate path")
        if "any" in v and len(v) > 1:
            raise ValueError("'any' already means every path, do not combine it with others")
        return v


SHOP_EFFECTS: tuple[str, ...] = ("stim", "recharge_all", "streak_freeze", "tarot_reroll")
SHOP_TITLE_MAX = 40


def validate_shop_effect(v: str) -> str:
    if v in SHOP_EFFECTS:
        return v
    if v.startswith("title:"):
        text = v[len("title:"):].strip()
        if not text or len(text) > SHOP_TITLE_MAX:
            raise ValueError(f"effect {v!r}: title text must be 1..{SHOP_TITLE_MAX} characters")
        return v
    if v.startswith("cat_item:"):
        item = v[len("cat_item:"):]
        if not KEY_RE.match(item):
            raise ValueError(f"effect {v!r}: cat_item needs a snake_case key from balance.cat.chrome")
        return v
    raise ValueError(
        f"unknown shop effect {v!r}; allowed: {list(SHOP_EFFECTS)}, 'title:<text in Russian>' "
        "or 'cat_item:<key from balance.cat.chrome>'"
    )


class ShopItem(Strict):
    key: Key
    name: str
    description: str
    price_tokens: int = Field(ge=1)
    effect: Annotated[str, AfterValidator(validate_shop_effect)]


# ---------------------------------------------------------------------------
# fallback_texts.yaml
# ---------------------------------------------------------------------------

# Placeholders each fallback kind may use. They must be fields of the payload
# the narrator receives for that kind; str.format_map is used with a safe dict.
FALLBACK_PAYLOAD_FIELDS: dict[str, frozenset[str]] = {
    "gig_brief": frozenset(
        {"name", "role", "district", "fixer", "gig_title", "gig_type", "tier", "pay", "target"}
    ),
    "gig_result": frozenset(
        {"name", "fixer", "gig_title", "approach", "eddies", "hp", "heat", "street_cred"}
    ),
    "event_text": frozenset({"name", "district", "event_title"}),
    "event_outcome": frozenset({"name", "district", "event_title", "choice_label"}),
    "obituary": frozenset({"name", "role", "age", "cause", "street_cred", "achievement"}),
    "news": frozenset({"district", "count", "names"}),
    "news_quiet": frozenset(),
    "npc_chat": frozenset({"name", "npc_name", "npc_role"}),
    "unique_event": frozenset({"name", "district", "title"}),
}


def placeholder_names(text: str) -> set[str]:
    """Field names used in a format string. Raises ValueError on a broken template."""
    names: set[str] = set()
    for _, field, _, _ in Formatter().parse(text):
        if field is None:
            continue
        if field == "" or not field.isidentifier():
            raise ValueError(f"unsupported placeholder {{{field}}} in {text!r}")
        names.add(field)
    return names


TextList = Annotated[list[str], Field(min_length=1)]


class FallbackTexts(Strict):
    gig_brief: TextList
    gig_result: dict[OutcomeTier, TextList]
    event_text: TextList
    event_outcome: TextList
    obituary: TextList
    news: TextList
    news_quiet: TextList  # "quiet night" digest when nothing public happened
    npc_chat: TextList
    unique_event: TextList

    @field_validator("gig_result")
    @classmethod
    def _tiers(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        if set(v) != set(OUTCOME_TIERS):
            raise ValueError(f"gig_result must define exactly {OUTCOME_TIERS}")
        return v

    @model_validator(mode="after")
    def _placeholders(self) -> "FallbackTexts":
        for kind, allowed in FALLBACK_PAYLOAD_FIELDS.items():
            value = getattr(self, kind)
            texts = [t for lst in value.values() for t in lst] if isinstance(value, dict) else value
            for text in texts:
                try:
                    used = placeholder_names(text)
                except ValueError as e:
                    raise ValueError(f"{kind}: {e}") from e
                extra = used - allowed
                if extra:
                    raise ValueError(
                        f"{kind}: unknown placeholders {sorted(extra)} in {text!r}; "
                        f"payload fields are {sorted(allowed)}"
                    )
        return self
