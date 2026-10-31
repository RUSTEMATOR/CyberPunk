"""The meme mini-games as pure functions (PLAN 4.7, DESIGN.md 12a): caliber, car, cat, coin, dirt.

State lives on the player (or in world_state for the shared coin rate); the services load it, call
these functions and save the result. Dates are 'YYYY-MM-DD' Kyiv days. Randomness only through `rng`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from app.content.loader import Content
from app.engine.daily import days_between, yesterday
from app.engine.dice import clamp, half_up, roll

# -- caliber ---------------------------------------------------------------------------------------------


@dataclass
class CaliberResult:
    before: int
    change: int
    after: int
    title: str
    title_changed: bool


def caliber_title(content: Content, mm: int) -> str:
    """Title of the highest threshold reached."""
    title = content.balance.caliber.titles[0].title
    for t in content.balance.caliber.titles:
        if mm >= t.mm:
            title = t.title
    return title


def roll_caliber(content: Content, rng: random.Random, current: int | None, change: int | None = None) -> CaliberResult:
    """One day of caliber. `current` None = the first time: the gun starts at `start_mm` and then changes.
    `change` is a roll already made (a Telegram dice face mapped by `dice.roll_from_face`); None rolls here."""
    cfg = content.balance.caliber
    before = cfg.start_mm if current is None else current
    if change is None:
        change = roll(cfg.change_dice, rng)
    after = max(cfg.min_mm, before + change)
    return CaliberResult(
        before, after - before, after, caliber_title(content, after),
        caliber_title(content, before) != caliber_title(content, after),
    )


# -- car -------------------------------------------------------------------------------------------------


@dataclass
class TuneResult:
    before: int
    after: int
    change: int
    lost_part: str | None = None
    extra_loss: int = 0


def tune_car(content: Content, rng: random.Random, hp: int, missing: list[str]) -> TuneResult:
    """A day of tuning: horsepower moves by `tune_dice`; sometimes a part falls off and costs more."""
    cfg = content.balance.car
    change = roll(cfg.tune_dice, rng)
    lost: str | None = None
    extra = 0
    left = [p.key for p in cfg.parts if p.key not in missing]
    if left and rng.random() < cfg.break_chance:
        lost = rng.choice(left)
        extra = roll(cfg.break_hp_loss, rng)
    after = clamp(hp + change - extra, cfg.min_hp, cfg.max_hp)
    return TuneResult(hp, after, after - hp, lost, extra)


@dataclass
class RaceResult:
    scores: tuple[int, int]
    rolls: tuple[int, int]
    winner: int | None  # 0 = the challenger, 1 = the opponent, None = a draw


def race_score(
    content: Content, rng: random.Random, hp: int, missing: list[str], fixed: int | None = None,
) -> tuple[int, int]:
    cfg = content.balance.car
    r = roll(cfg.race_roll, rng) if fixed is None else fixed
    return hp - cfg.missing_part_penalty * len(missing) + r, r


def run_race(
    content: Content, rng: random.Random, a: tuple[int, list[str]], b: tuple[int, list[str]],
    rolls: tuple[int, int] | None = None,
) -> RaceResult:
    """`rolls` are the two race rolls already made (Telegram dice); None rolls here."""
    sa, ra = race_score(content, rng, *a, fixed=rolls[0] if rolls else None)
    sb, rb = race_score(content, rng, *b, fixed=rolls[1] if rolls else None)
    winner = 0 if sa > sb else 1 if sb > sa else None
    return RaceResult((sa, sb), (ra, rb), winner)


# -- cat -------------------------------------------------------------------------------------------------

CAT_OK = "ok"
CAT_SULK = "sulk"
CAT_GONE = "gone"


def cat_level_of(content: Content, feedings: int) -> int:
    level = 1
    for lv in content.balance.cat.levels:
        if feedings >= lv.feedings:
            level = lv.level
    return level


def cat_level_name(content: Content, level: int) -> str:
    for lv in content.balance.cat.levels:
        if lv.level == level:
            return lv.name
    return content.balance.cat.levels[-1].name


def new_cat(content: Content, today: str) -> dict[str, Any]:
    """A fresh cat. `fed` is yesterday, so the first feeding is possible today and it does not sulk."""
    return {
        "name": content.balance.cat.default_name, "level": 1, "feedings": 0, "fed": yesterday(today),
        "chrome": [], "gone": False, "fights": 0, "fight_day": None,
    }


def cat_state(content: Content, cat: dict[str, Any], today: str) -> str:
    """ok / sulk / gone by days since the last feeding (balance.cat.sulk_after_days / leave_after_days)."""
    if cat.get("gone"):
        return CAT_GONE
    days = days_between(str(cat.get("fed")), today)
    cfg = content.balance.cat
    if days >= cfg.leave_after_days:
        return CAT_GONE
    if days >= cfg.sulk_after_days:
        return CAT_SULK
    return CAT_OK


def refresh_cat(content: Content, cat: dict[str, Any], today: str) -> dict[str, Any]:
    """Mark a cat that waited too long as gone (a stored fact: the return costs tokens)."""
    out = dict(cat)
    if cat_state(content, out, today) == CAT_GONE:
        out["gone"] = True
    return out


def can_feed(cat: dict[str, Any], today: str) -> bool:
    return not cat.get("gone") and cat.get("fed") != today


def feed_cat(content: Content, cat: dict[str, Any], today: str) -> tuple[dict[str, Any], int | None]:
    """Feed once a day. Returns the cat and the new level when the feeding raised it."""
    out = dict(cat)
    out["feedings"] = int(cat.get("feedings", 0)) + 1
    out["fed"] = today
    new_level = cat_level_of(content, out["feedings"])
    leveled = new_level if new_level > int(cat.get("level", 1)) else None
    out["level"] = new_level
    return out, leveled


def bring_back(cat: dict[str, Any], today: str) -> dict[str, Any]:
    out = dict(cat)
    out["gone"] = False
    out["fed"] = yesterday(today)
    return out


def chrome_bonus(content: Content, cat: dict[str, Any]) -> int:
    have = set(cat.get("chrome") or [])
    return sum(c.fight_bonus for c in content.balance.cat.chrome if c.key in have)


def fights_left(content: Content, cat: dict[str, Any], today: str) -> int:
    used = int(cat.get("fights", 0)) if cat.get("fight_day") == today else 0
    return max(0, content.balance.cat.fights_per_day - used)


def cat_fight_score(
    content: Content, rng: random.Random, cat: dict[str, Any], today: str, fixed: int | None = None,
) -> tuple[int, int]:
    cfg = content.balance.cat
    r = roll(cfg.fight_roll, rng) if fixed is None else fixed
    score = int(cat.get("level", 1)) * cfg.level_power + chrome_bonus(content, cat) + r
    if cat_state(content, cat, today) == CAT_SULK:
        score -= cfg.sulk_fight_penalty
    return score, r


def mark_fight(cat: dict[str, Any], today: str) -> dict[str, Any]:
    out = dict(cat)
    out["fights"] = (int(cat.get("fights", 0)) if cat.get("fight_day") == today else 0) + 1
    out["fight_day"] = today
    return out


# -- coin ------------------------------------------------------------------------------------------------


def next_rate(content: Content, rng: random.Random, rate: float, shock: bool = False) -> float:
    """The rate after one day: drift plus or minus the volatility, uniformly; a shock doubles the swing."""
    cfg = content.balance.coin
    vol = cfg.daily_volatility * (cfg.shock_volatility_mult if shock else 1.0)
    change = cfg.daily_drift + rng.uniform(-vol, vol)
    return round(clamp_f(rate * (1.0 + change), cfg.min_rate, cfg.max_rate), 2)


def clamp_f(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def coins_for(eddies: int, rate: float) -> float:
    return round(eddies / rate, 4)


def sale(content: Content, coins: float, rate: float) -> tuple[int, int, int]:
    """(gross, fee, net) eddies for selling `coins` at `rate`; the fee is the balance commission."""
    gross = half_up(coins * rate)
    fee = half_up(gross * content.balance.coin.commission_pct)
    return gross, fee, gross - fee


# -- dirt ------------------------------------------------------------------------------------------------


def dirt_gain(content: Content, rng: random.Random) -> int:
    return roll(content.balance.dirt.gain_dice, rng)
