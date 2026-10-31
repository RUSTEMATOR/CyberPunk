"""RED checks, outcome tiers, luck and glitch helpers (PLAN 2.2, DESIGN.md section 6).

`check` is a pure function of numbers. The helpers that spend luck or glitches never change
a character: they return the new result plus an effects dict for `effects.apply_effects`
(rule 3: every change of a character goes through apply_effects).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Iterable, Sequence

from app.content.schemas import ChecksBalance, LuckBalance, OutcomeTier
from app.engine.dice import D10, Crit, d10_detail

if TYPE_CHECKING:
    from app.content.loader import Content
    from app.engine.state import CharacterState

Outcome = OutcomeTier  # clean | complication | fail | disaster


@dataclass(frozen=True)
class CheckResult:
    """One check: stat + skill + d10 + modifiers (+ spent luck) against a dv."""

    total: int
    base_roll: int  # the die including the crit die (can be negative after a crit fail)
    natural: int  # the first die
    crit: Crit
    ok: bool
    margin: int  # total - dv; negative = missed by that much
    dv: int
    stat_val: int
    skill_val: int
    bonus: int  # modifiers (check.*, wounds, situational), without luck
    luck: int = 0  # bonus bought with luck points
    rerolled: bool = False  # the die was rolled again with a glitch
    stat: str | None = None  # labels for the narrator and the UI
    skill: str | None = None

    def as_dict(self) -> dict[str, object]:
        """Compact form for the narrator payload (DESIGN.md section 14)."""
        return {
            "stat": self.stat, "skill": self.skill, "dv": self.dv, "total": self.total,
            "ok": self.ok, "crit": self.crit, "margin": self.margin,
        }


def _build(
    *, base_roll: int, natural: int, crit: Crit, stat_val: int, skill_val: int, bonus: int,
    luck: int, dv: int, rerolled: bool, stat: str | None, skill: str | None,
) -> CheckResult:
    total = stat_val + skill_val + base_roll + bonus + luck
    return CheckResult(
        total=total, base_roll=base_roll, natural=natural, crit=crit, ok=total >= dv,
        margin=total - dv, dv=dv, stat_val=stat_val, skill_val=skill_val, bonus=bonus,
        luck=luck, rerolled=rerolled, stat=stat, skill=skill,
    )


def check(
    stat_val: int,
    skill_val: int,
    mods: int,
    dv: int,
    rng: random.Random,
    cfg: ChecksBalance | None = None,
    *,
    stat: str | None = None,
    skill: str | None = None,
    die: D10 | None = None,
) -> CheckResult:
    """stat + skill + d10 + mods >= dv. A natural 10 adds a d10, a natural 1 subtracts one.
    `die` is a die already rolled (a Telegram dice face, `dice.d10_from_face`); None rolls here."""
    roll = die if die is not None else d10_detail(rng, cfg)
    return _build(
        base_roll=roll.value, natural=roll.natural, crit=roll.crit, stat_val=stat_val,
        skill_val=skill_val, bonus=mods, luck=0, dv=dv, rerolled=False, stat=stat, skill=skill,
    )


def outcome_tier(
    results: Sequence[CheckResult], cfg: ChecksBalance, key_index: int = 0
) -> Outcome:
    """Four-tier outcome of a set of checks (DESIGN.md section 6).

    - disaster: a critical failure on the key check (and the check really failed);
    - fail: more than `fail_ratio` of the checks failed;
    - complication: one check failed (not enough for a fail), or a check passed by less
      than `complication_margin`;
    - clean: everything passed with room to spare.
    """
    if not results:
        return "clean"
    if not 0 <= key_index < len(results):
        raise IndexError(f"key_index {key_index} is outside the {len(results)} checks")
    key = results[key_index]
    if key.crit == "fail" and not key.ok:
        return "disaster"
    failed = sum(1 for r in results if not r.ok)
    if failed / len(results) > cfg.fail_ratio:
        return "fail"
    if failed or any(r.margin < cfg.complication_margin for r in results):
        return "complication"
    return "clean"


# ---------------------------------------------------------------------------
# Luck pool: +luck_per_point to a check per spent point.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LuckSpend:
    result: CheckResult
    spent: int
    effects: dict[str, int]  # {"luck_pool": -spent}, give it to apply_effects


def luck_points_to_pass(result: CheckResult, pool: int, luck: LuckBalance) -> int | None:
    """Points of luck needed to turn a failed check into a pass.

    0 = it already passed. None = the pool is too small. A crit fail still counts: luck adds to the total.
    """
    if result.ok:
        return 0
    need = math.ceil(-result.margin / luck.luck_per_point)
    return need if need <= pool else None


def spend_luck(result: CheckResult, points: int, pool: int, luck: LuckBalance) -> LuckSpend:
    """Add `points` of luck (capped by the pool) to a check. Nothing is changed on the character."""
    spent = max(0, min(points, pool))
    if spent == 0:
        return LuckSpend(result, 0, {})
    bonus = spent * luck.luck_per_point
    total = result.total + bonus
    new = replace(
        result, total=total, luck=result.luck + bonus, ok=total >= result.dv,
        margin=total - result.dv,
    )
    return LuckSpend(new, spent, {"luck_pool": -spent})


# ---------------------------------------------------------------------------
# Glitch: one reroll of a check.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GlitchReroll:
    result: CheckResult
    effects: dict[str, int]  # {"glitches": -1}


def can_glitch_reroll(result: CheckResult, glitches: int) -> bool:
    return glitches >= 1 and not result.rerolled


def glitch_reroll(
    result: CheckResult, glitches: int, rng: random.Random, cfg: ChecksBalance | None = None, die: D10 | None = None,
) -> GlitchReroll:
    """Roll the die of a check again. Stat, skill, modifiers and already spent luck stay.

    A check can be rerolled once. Raises ValueError without a glitch or on a second reroll.
    """
    if not can_glitch_reroll(result, glitches):
        raise ValueError("no glitch to spend, or this check was already rerolled")
    roll = die if die is not None else d10_detail(rng, cfg)
    new = _build(
        base_roll=roll.value, natural=roll.natural, crit=roll.crit, stat_val=result.stat_val,
        skill_val=result.skill_val, bonus=result.bonus, luck=result.luck, dv=result.dv,
        rerolled=True, stat=result.stat, skill=result.skill,
    )
    return GlitchReroll(new, {"glitches": -1})


# ---------------------------------------------------------------------------
# A check for a character: totals and bonuses come from derived.py.
# ---------------------------------------------------------------------------


def roll_check(
    state: "CharacterState",
    content: "Content",
    rng: random.Random,
    stat: str,
    skill: str | None,
    dv: int,
    contexts: str | Iterable[str] = (),
    extra: int = 0,
    die: D10 | None = None,
) -> CheckResult:
    """Check of a character: stat_total + skill_total + d10 + check_bonus(contexts) + extra.

    `contexts` are the mod contexts that apply (for a loud gig: ("gig_loud", "combat")); the heavy
    wound penalty is part of check_bonus. `extra` is a situational bonus.
    """
    from app.engine import derived  # local import: derived does not import checks

    from app.engine.state import FLAG_STIM

    stim = int(state.flags.pop(FLAG_STIM, 0) or 0)  # the Afterlife stim is spent by the next check
    bonus = derived.check_bonus(state, content, contexts) + extra + stim
    return check(
        derived.stat_total(state, content, stat), derived.skill_total(state, content, skill),
        bonus, dv, rng, content.balance.checks, stat=stat, skill=skill, die=die,
    )


def pass_chance(base: int, dv: int, sides: int = 10) -> float:
    """Chance that `base + d10 >= dv`, with the crits of `check`: a natural `sides` adds another die, a natural 1
    subtracts one (owner report 24.10: the exit screen shows the odds)."""
    total = 0
    for nat in range(1, sides + 1):
        if nat == sides:
            total += sum(1 for extra in range(1, sides + 1) if base + nat + extra >= dv)
        elif nat == 1:
            total += sum(1 for extra in range(1, sides + 1) if base + nat - extra >= dv)
        else:
            total += sides if base + nat >= dv else 0
    return total / (sides * sides)
