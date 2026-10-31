"""Gigs: the fixer's board, the checks of an approach, the payout of an outcome (PLAN 5.1-5.5).

Pure: no clock, no DB, randomness only through the `rng`. The service (services/gigs.py) stores the
board in the `gigs` table, plays the checks one by one (with luck and glitch offers) and applies
the settlement through `apply_effects`.

Availability by year (architecture rule 12): templates, gangs, corps and fixers come from
`content.available(kind, year)` only.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from app.content.loader import Content
from app.content.schemas import APPROACHES, GIG_TITLE_FIELDS, NAME_CASES, GigTemplate
from app.engine import checks, derived
from app.engine.checks import CheckResult
from app.engine.dice import clamp, half_up, weighted_choice
from app.engine.state import CharacterState

# Modifier contexts (`check.<context>`) of each approach's checks, plus the type's own.
APPROACH_CONTEXTS: dict[str, tuple[str, ...]] = {
    "loud": ("gig_loud", "combat"),
    "quiet": ("gig_quiet", "escape"),
    "talk": ("gig_talk", "social"),
    "hack": ("gig_hack",),
}
CRIME_TYPES: tuple[str, ...] = ("theft", "elimination")  # these gigs count as a crime (heat does not decay)

# Targets that can be a gig target: megacorps only (services and the law are not employers' enemies).
TARGET_CORP_KIND = "megacorp"


@dataclass(frozen=True)
class GigDraft:
    """A gig before it is stored (the `gigs` row without id)."""

    district: str
    fixer: str
    type: str
    tier: int
    pay: int
    target: str  # a gangs.yaml or corps.yaml key
    template_key: str


@dataclass(frozen=True)
class CheckPlan:
    stat: str
    skill: str
    dv: int


# -- the board ---------------------------------------------------------------------------------


RANK_FLAG = "gig_rank"  # contract rank points of the character (no cap), owner request 29.10


def rank_of(state: CharacterState) -> int:
    """Contract rank points of a character (a flag, so a new life starts from zero)."""
    return max(0, int(state.flags.get(RANK_FLAG, 0) or 0))


def tier_open(content: Content, cred: float, rank: int, tier: int) -> bool:
    """A tier is open when both ladders are climbed: the street cred and the contract rank."""
    g = content.balance.gigs
    return cred >= g.unlock_cred_by_tier[tier] and rank >= g.unlock_rank_by_tier[tier]


def max_open_tier(content: Content, avg_cred: float | None, rank: int = 0) -> int:
    """Highest tier the district's players have unlocked on average; 'nobody lives here' is a fixed cap."""
    g = content.balance.gigs
    if avg_cred is None:
        return g.empty_district_max_tier
    best = 1
    for tier in sorted(g.unlock_cred_by_tier):
        if tier_open(content, avg_cred, rank, tier):
            best = tier
    return best


def tier_weights(content: Content, avg_cred: float | None, rank: int = 0) -> dict[int, float]:
    """Weights of the tiers a board draws from: the top open tier counts twice, every tier below it falls by
    `tier_weight_decay` per step (a rich player is not buried in street pennies), and (when players live here)
    one tier above is the closed teaser."""
    g = content.balance.gigs
    top = max_open_tier(content, avg_cred, rank)
    weights = {t: g.tier_weight_decay ** (top - t) for t in range(1, top + 1)}
    weights[top] = 2.0
    if avg_cred is not None and top < max(g.unlock_cred_by_tier):
        weights[top + 1] = 1.0
    return weights


def district_targets(content: Content, district_key: str, year: int) -> tuple[list[str], list[str]]:
    """(gang keys, corp keys) of the district that exist in `year`."""
    district = content.districts.get(district_key)
    if district is None:
        return [], []
    gangs = content.available("gangs", year)
    corps = content.available("corps", year)
    return (
        [g for g in district.gangs if g in gangs],
        [c for c in district.corps if c in corps and corps[c].kind == TARGET_CORP_KIND],
    )


def board_fixer(content: Content, district_key: str, year: int, tier: int) -> str | None:
    """The fixer who offers a gig of this tier: a legendary one of the district for the top tier,
    otherwise the district's main fixer (or any ordinary fixer who works it in `year`)."""
    fixers = content.available("fixers", year)
    if tier >= content.balance.gigs.legendary_fixer_min_tier:
        for f in fixers.values():
            if f.legendary and district_key in f.districts:
                return f.key
    district = content.districts.get(district_key)
    if district is not None and district.fixer in fixers:
        return district.fixer
    for f in fixers.values():
        if district_key in f.districts and not f.legendary:
            return f.key
    return None


def _template_targets(template: GigTemplate, gangs: list[str], corps: list[str]) -> list[str]:
    out: list[str] = []
    if "gang" in template.targets:
        out.extend(gangs)
    if "corp" in template.targets:
        out.extend(corps)
    return out


def roll_pay(content: Content, tier: int, rng: random.Random, war_bonus: bool = False, pay_mult: float = 1.0,
             cred: float | None = None, rank: int = 0) -> int:
    g = content.balance.gigs
    lo, hi = g.pay_by_tier[tier]
    pay = rng.randint(lo, hi)
    if cred is not None and g.cred_pay_pct:  # a name on the street raises the price, slowly
        above = max(0.0, cred - g.unlock_cred_by_tier.get(tier, 0))
        pay = half_up(pay * (1 + min(g.cred_pay_cap_pct, above * g.cred_pay_pct) / 100))
    if rank and g.rank_pay_pct and g.unlock_rank_by_tier[tier]:  # a name among the contractors raises it too
        over = max(0, rank - g.unlock_rank_by_tier[tier])
        pay = half_up(pay * (1 + min(g.rank_pay_cap_pct, over * g.rank_pay_pct) / 100))
    if war_bonus:
        pay = half_up(pay * (1 + g.gang_war_pay_bonus_pct / 100))
    pay = half_up(pay * pay_mult)  # the world's gig pay effect
    step = g.pay_round
    return max(step, half_up(pay / step) * step)


def generate_board(
    district_key: str, rng: random.Random, content: Content, year: int,
    avg_cred: float | None = None, gang_wars: dict[str, object] | None = None, pay_mult: float = 1.0,
    exclude: set[str] | None = None, rank: int = 0,
) -> list[GigDraft]:
    """3-5 gigs for a district (PLAN 5.1). The first one is always tier 1, so a newcomer has work.

    `avg_cred` is the average street cred of the players living in the district (None = nobody).
    Gigs against gangs at war (`gang_wars`) pay more. Returns fewer gigs only when the district has
    no usable template or target in `year`.
    """
    g = content.balance.gigs
    gangs, corps = district_targets(content, district_key, year)
    if exclude:  # owner report 23.10: fixers do not offer jobs against the character's own employer
        gangs = [g_ for g_ in gangs if g_ not in exclude]
        corps = [c_ for c_ in corps if c_ not in exclude]
    templates = list(content.available("gig_templates", year).values())
    weights = tier_weights(content, avg_cred, rank)
    top = max_open_tier(content, avg_cred, rank)
    count = rng.randint(g.board_min, g.board_max)
    wars = gang_wars or {}
    drafts: list[GigDraft] = []
    seen: set[tuple[str, str]] = set()
    for i in range(count):
        if i == 0:  # the first gig is below the top, so a newcomer has work and a veteran is not offered only pennies
            tier = max(1, top - g.first_gig_below_top)
        elif i == 1 and top >= g.elite_min_tier:  # a contract is always on the board of those who reached them
            tier = top if rng.random() < 0.6 else top - 1
        else:
            tier = weighted_choice(rng, list(weights), list(weights.values()))
        fixer = board_fixer(content, district_key, year, tier)
        if fixer is None:
            break
        pool = [
            (t, target) for t in templates if tier in t.tiers
            for target in _template_targets(t, gangs, corps) if (t.key, target) not in seen
        ]
        if not pool:
            continue
        template, target = pool[rng.randrange(len(pool))]
        seen.add((template.key, target))
        pay = roll_pay(content, tier, rng, war_bonus=target in wars, pay_mult=pay_mult, cred=avg_cred, rank=rank)
        drafts.append(GigDraft(district_key, fixer, template.type, tier, pay, target, template.key))
    return drafts


def avg_street_cred(creds: list[int]) -> float | None:
    return sum(creds) / len(creds) if creds else None


def unlocked(content: Content, street_cred: int, tier: int, rank: int = 0) -> bool:
    return tier_open(content, street_cred, rank, tier)


def visible_gigs(content: Content, street_cred: int, tiers: list[int], rank: int = 0) -> tuple[list[int], list[int]]:
    """Indexes of the gigs a player can take and of the closed ones he is shown (lowest tiers first).

    `tiers` are the tiers of the open gigs of the board in board order.
    """
    open_idx = [i for i, t in enumerate(tiers) if unlocked(content, street_cred, t, rank)]
    closed = sorted((i for i, t in enumerate(tiers) if not unlocked(content, street_cred, t, rank)), key=lambda i: tiers[i])
    return open_idx, closed[: content.balance.gigs.locked_shown]


def lock_reason(content: Content, street_cred: int, tier: int, rank: int = 0) -> tuple[int, int]:
    """What a closed tier still asks for: (street cred needed, rank points needed); 0 = already enough."""
    g = content.balance.gigs
    return (max(0, g.unlock_cred_by_tier[tier] - street_cred), max(0, g.unlock_rank_by_tier[tier] - rank))


def deposit(content: Content, tier: int, pay: int) -> int:
    """The deposit a contract takes at the start (0 below `deposit_min_tier`)."""
    g = content.balance.gigs
    if tier < g.deposit_min_tier or not g.deposit_pct:
        return 0
    return max(1, half_up(pay * g.deposit_pct / 100))


def rank_gain(content: Content, tier: int, outcome: str) -> int:
    """Rank points of a settled gig: all for a clean one, `rank_partial_pct` (rounded up) for a complicated one."""
    g = content.balance.gigs
    pts = g.rank_pts_by_tier[tier]
    if outcome == "clean":
        return pts
    if outcome == "complication":
        return -(-pts * g.rank_partial_pct // 100)
    return 0


def next_rank_goal(content: Content, street_cred: int, rank: int) -> tuple[int, int] | None:
    """The nearest closed contract tier on the rank ladder: (tier, rank points needed), or None at the top."""
    g = content.balance.gigs
    for tier in sorted(g.unlock_rank_by_tier):
        need = g.unlock_rank_by_tier[tier]
        if need > rank:
            return tier, need
    return None


def target_forms(content: Content, key: str) -> dict[str, str]:
    """`{target}` and its cases `{target_gen}` ... for a gig title.

    A gang gives its gangs.yaml case forms; a corp (Latin, never declined) or an unknown key gives
    the same name in every slot.
    """
    gang = content.gangs.get(key)
    if gang is not None:
        return {"target": gang.name, **{f"target_{c}": getattr(gang, f"name_{c}") for c in NAME_CASES}}
    corp = content.corps.get(key)
    name = corp.name if corp is not None else key
    return {"target": name, **{f"target_{c}": name for c in NAME_CASES}}


def gig_title(template: GigTemplate, gig_id: int, target: str | dict[str, str]) -> str:
    """The title of a stored gig: the template title picked by the gig id (no column needed).

    `target` is `target_forms(...)`, or a plain name used in every case slot.
    """
    forms = target if isinstance(target, dict) else {f: target for f in GIG_TITLE_FIELDS}
    return template.titles[gig_id % len(template.titles)].format(**forms)


# -- checks of an approach ----------------------------------------------------------------------------


def gig_checks(content: Content, template: GigTemplate, approach: str, tier: int) -> list[CheckPlan]:
    """The 2-3 checks of an approach at the tier's difficulty."""
    if approach not in APPROACHES:
        raise ValueError(f"unknown approach {approach!r}")
    dv = content.balance.gigs.dv_by_tier[tier]
    return [CheckPlan(c.stat, c.skill, dv) for c in template.approaches[approach]]


def deck_hack_bonus(state: CharacterState, content: Content) -> int:
    """hack_bonus of the installed cyberdeck (0 without one)."""
    best = 0
    for item in state.cyberware:
        cw = content.cyberware.get(item.key)
        if cw is not None and cw.deck is not None:
            best = max(best, cw.deck.hack_bonus)
    return best


def roll_gig_check(
    state: CharacterState, content: Content, rng: random.Random, plan: CheckPlan, approach: str, gig_type: str,
    assist: int = 0,
) -> CheckResult:
    """One check of a gig: stat + skill + d10 + the approach's mod contexts (+ the deck on a hack)."""
    contexts = list(APPROACH_CONTEXTS[approach])
    if gig_type in CRIME_TYPES:
        contexts.append("crime")
    extra = (deck_hack_bonus(state, content) if approach == "hack" else 0) + assist  # assist: a hired merc
    return checks.roll_check(state, content, rng, plan.stat, plan.skill, plan.dv, contexts, extra=extra)


def gig_outcome(results: list[CheckResult], content: Content) -> str:
    """Four-tier outcome; the first check is the key one (a crit fail there is a disaster)."""
    return checks.outcome_tier(results, content.balance.checks, key_index=0)


# -- payout -------------------------------------------------------------------------------------------


@dataclass
class Settlement:
    outcome: str
    pay: int
    effects: dict[str, int | bool]  # for apply_effects
    fixer: str
    notes: list[str] = field(default_factory=list)


def fixer_pay_pct(state: CharacterState, content: Content, fixer: str) -> int:
    """+10% from a fixer who likes you, -10% from one who does not (PLAN 5.5)."""
    g = content.balance.gigs
    rel = state.fixer_rel.get(fixer, g.fixer_rel_start)
    if rel >= g.fixer_rel_high:
        return g.fixer_rel_pay_pct
    if rel <= g.fixer_rel_low:
        return -g.fixer_rel_pay_pct
    return 0


def settle(
    state: CharacterState, content: Content, *, outcome: str, tier: int, base_pay: int, fixer: str,
    target: str, approach: str, passed_skills: list[str], fought: bool,
) -> Settlement:
    """Turn an outcome into effects for `apply_effects` (PLAN 5.5).

    pay = base x pay_pct x (1 + fixer pay % + gig_pay_pct mod); hp loss only when no fight
    happened (the fight already took its hp); heat, street cred and the fixer's opinion from the
    outcome table; the target faction's standing moves after a clean or complicated gig.
    """
    g = content.balance.gigs
    o = g.outcomes[outcome]
    adj = fixer_pay_pct(state, content, fixer) + derived.mod(state, content, "gig_pay_pct")
    pay = max(0, half_up(base_pay * o.pay_pct / 100 * max(0.0, 1 + adj / 100)))
    effects: dict[str, int | bool] = {}
    if pay:
        effects["eddies"] = pay
    if o.hp_loss_per_tier and not fought:
        effects["hp"] = -o.hp_loss_per_tier * min(tier, g.hp_loss_tier_cap)
    heat = o.heat + (g.loud_extra_heat if approach == "loud" and outcome != "clean" else 0)
    if heat:
        effects["heat"] = heat
    if o.street_cred:
        effects["street_cred"] = o.street_cred
    if o.fixer_rel:
        effects[f"rel_fixer:{fixer}"] = o.fixer_rel
    if outcome in ("clean", "complication"):
        rep = g.faction_rep[approach] * tier
        if rep and (target in content.gangs or target in content.corps):
            effects[f"faction_rep:{target}"] = clamp(rep, -50, 50)
    for skill in passed_skills:
        key = f"skill_xp:{skill}"
        effects[key] = int(effects.get(key, 0)) + g_success_xp(content)
    return Settlement(outcome, pay, effects, fixer)


def g_success_xp(content: Content) -> int:
    return content.balance.skills.gig_success_xp


def worse_outcome(a: str, b: str) -> str:
    """The worse of two outcome tiers."""
    order = ("clean", "complication", "fail", "disaster")
    return a if order.index(a) >= order.index(b) else b
