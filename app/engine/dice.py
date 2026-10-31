"""Dice for the engine (PLAN 2.1). Randomness comes only from the `rng` passed in.

d10 check rules are the Cyberpunk RED ones: a natural top face adds another die,
a natural 1 subtracts another die. The faces and the crit triggers are read from
`balance.checks` when a config is passed; without one the plain d10 rules apply.
"""

from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass
from typing import Literal, NamedTuple, Sequence, TypeVar

from app.content.schemas import ChecksBalance

T = TypeVar("T")

Crit = Literal["success", "fail"] | None

_DICE_RE = re.compile(r"^(\d*)d(\d+)([+-]\d+)?$")

# Obvious dice faces that are not balance numbers.
_D10 = 10
_CRIT_HIGH = 10
_CRIT_LOW = 1
_D100 = 100


def d(sides: int, rng: random.Random) -> int:
    """One die with `sides` faces: 1..sides."""
    if sides < 1:
        raise ValueError(f"a die needs at least one face, got {sides}")
    return rng.randint(1, sides)


@dataclass(frozen=True)
class DiceSpec:
    count: int
    sides: int
    bonus: int


def parse_dice(expr: str) -> DiceSpec:
    """Parse '2d6', 'd10', '2d6+1' or '1d10-4'. Raises ValueError on anything else."""
    m = _DICE_RE.match(expr.strip())
    if not m:
        raise ValueError(f"bad dice expression {expr!r}, expected like '2d6' or '2d6+2'")
    count = int(m.group(1)) if m.group(1) else 1
    sides = int(m.group(2))
    if count < 1 or sides < 1:
        raise ValueError(f"bad dice expression {expr!r}: count and faces must be positive")
    return DiceSpec(count, sides, int(m.group(3)) if m.group(3) else 0)


class DiceRoll(NamedTuple):
    total: int
    dice: tuple[int, ...]
    bonus: int


def roll_detail(expr: str, rng: random.Random) -> DiceRoll:
    spec = parse_dice(expr)
    dice = tuple(d(spec.sides, rng) for _ in range(spec.count))
    return DiceRoll(sum(dice) + spec.bonus, dice, spec.bonus)


def roll(expr: str, rng: random.Random) -> int:
    """Total of a dice expression such as '2d6+1'."""
    return roll_detail(expr, rng).total


def dice_range(expr: str) -> tuple[int, int]:
    """(minimum, maximum) of an expression, for corridors and UI."""
    spec = parse_dice(expr)
    return spec.count + spec.bonus, spec.count * spec.sides + spec.bonus


def dice_average(expr: str) -> float:
    spec = parse_dice(expr)
    return spec.count * (spec.sides + 1) / 2 + spec.bonus


def d100(rng: random.Random) -> int:
    """1..100, where the roll 00 counts as 100 (the cyber tolerance table)."""
    return d(_D100, rng)


class D10(NamedTuple):
    value: int  # the result of the die including the crit die
    crit: Crit
    natural: int  # the first die
    extra: int  # the crit die, 0 when there was no crit


def d10_detail(rng: random.Random, cfg: ChecksBalance | None = None) -> D10:
    sides = cfg.die_sides if cfg else _D10
    high = cfg.crit_high if cfg else _CRIT_HIGH
    low = cfg.crit_low if cfg else _CRIT_LOW
    natural = d(sides, rng)
    if natural >= high:
        extra = d(sides, rng)
        return D10(natural + extra, "success", natural, extra)
    if natural <= low:
        extra = d(sides, rng)
        return D10(natural - extra, "fail", natural, extra)
    return D10(natural, None, natural, 0)


def d10_check(rng: random.Random, cfg: ChecksBalance | None = None) -> tuple[int, Crit]:
    """RED d10: a 10 adds another d10, a 1 subtracts one. Returns (value, crit)."""
    r = d10_detail(rng, cfg)
    return r.value, r.crit


# ---------------------------------------------------------------------------
# Telegram dice (🎲 1..6, 🎰 1..64): a face picks the roll by quantile, so the balance keeps its meaning.
# Face k of n stands for the middle of its slice, q = (k - 0.5) / n, and gives the smallest outcome whose cumulative
# probability reaches q. The mean and the shape of the original roll are kept as well as n slices allow, and the same
# face always gives the same roll.
# ---------------------------------------------------------------------------


TG_DIE_FACES = 6  # Telegram 🎲
TG_SLOTS_FACES = 64  # Telegram 🎰: 1 = three bars, 64 = three sevens


def _quantile(face: int, faces: int) -> float:
    if faces < 1 or not 1 <= face <= faces:
        raise ValueError(f"face {face} is outside 1..{faces}")
    return (face - 0.5) / faces


def dice_distribution(expr: str) -> list[tuple[int, float]]:
    """[(total, probability)] of a dice expression, totals ascending."""
    spec = parse_dice(expr)
    dist: dict[int, float] = {0: 1.0}
    for _ in range(spec.count):
        nxt: dict[int, float] = {}
        for total, p in dist.items():
            for face in range(1, spec.sides + 1):
                nxt[total + face] = nxt.get(total + face, 0.0) + p / spec.sides
        dist = nxt
    return sorted((t + spec.bonus, p) for t, p in dist.items())


def roll_from_face(expr: str, face: int, faces: int) -> int:
    """The total of `expr` that a Telegram dice face stands for (see the block comment above)."""
    q = _quantile(face, faces)
    acc = 0.0
    dist = dice_distribution(expr)
    for total, p in dist:
        acc += p
        if acc >= q - 1e-12:
            return total
    return dist[-1][0]


def d10_from_face(face: int, faces: int, cfg: ChecksBalance | None = None) -> D10:
    """The RED check die (with its crit die) that a Telegram dice face stands for: every outcome of `d10_detail` is
    ordered by its value and picked by quantile, so a top face is a critical success and face 1 a critical failure."""
    sides = cfg.die_sides if cfg else _D10
    high = cfg.crit_high if cfg else _CRIT_HIGH
    low = cfg.crit_low if cfg else _CRIT_LOW
    outcomes: list[tuple[int, int, int, Crit, float]] = []  # value, natural, extra, crit, probability
    for natural in range(1, sides + 1):
        if natural >= high or natural <= low:
            for extra in range(1, sides + 1):
                if natural >= high:
                    outcomes.append((natural + extra, natural, extra, "success", 1 / sides ** 2))
                else:
                    outcomes.append((natural - extra, natural, extra, "fail", 1 / sides ** 2))
        else:
            outcomes.append((natural, natural, 0, None, 1 / sides))
    outcomes.sort(key=lambda o: (o[0], o[1]))
    q = _quantile(face, faces)
    acc = 0.0
    for value, natural, extra, crit, p in outcomes:
        acc += p
        if acc >= q - 1e-12:
            return D10(value, crit, natural, extra)
    value, natural, extra, crit, _ = outcomes[-1]
    return D10(value, crit, natural, extra)


# ---------------------------------------------------------------------------
# Small helpers every engine module needs (all take the rng).
# ---------------------------------------------------------------------------


def half_up(x: float) -> int:
    """Round half up (not the banker's rounding of round())."""
    return int(math.floor(x + 0.5))


def stochastic_round(x: float, rng: random.Random) -> int:
    """Round so that the expected value stays x: 1.25 gives 1 (75%) or 2 (25%)."""
    base = math.floor(x)
    return int(base) + (1 if rng.random() < x - base else 0)


def choice(rng: random.Random, items: Sequence[T]) -> T:
    if not items:
        raise ValueError("cannot choose from an empty sequence")
    return items[rng.randrange(len(items))]


def weighted_choice(rng: random.Random, items: Sequence[T], weights: Sequence[float]) -> T:
    """One item with probability proportional to its weight (weights >= 0, at least one > 0)."""
    if len(items) != len(weights) or not items:
        raise ValueError("items and weights must be non-empty and of equal length")
    total = float(sum(weights))
    if total <= 0:
        raise ValueError("at least one weight must be positive")
    pick = rng.random() * total
    acc = 0.0
    for item, w in zip(items, weights):
        acc += w
        if pick < acc:
            return item
    return items[-1]


def clamp(x: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, x))
