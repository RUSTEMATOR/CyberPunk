"""The daily loop as pure functions (PLAN 4.7, DESIGN.md 12a): streak, the Misty card, quest picking.

No clock (dates come in as 'YYYY-MM-DD' strings), no DB, randomness only through `rng`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.content.loader import Content
from app.content.schemas import Quest, QuestRewards, Tarot, parse_bus_key
from app.engine.dice import weighted_choice

PATH_COMBAT = "combat"
PATH_NONCOMBAT = "noncombat"
PATH_CHROME_FREE = "chrome_free"
PATH_ANY = "any"

# -- dates -----------------------------------------------------------------------------------------------


def _d(day: str) -> date:
    return date.fromisoformat(day)


def days_between(earlier: str, later: str) -> int:
    return (_d(later) - _d(earlier)).days


def yesterday(day: str) -> str:
    return (_d(day) - timedelta(days=1)).isoformat()


def week_start(day: str) -> str:
    """The Monday of the week that holds `day`."""
    d = _d(day)
    return (d - timedelta(days=d.weekday())).isoformat()


def period_start(period: str, day: str) -> str:
    """Start of a quest period: the day itself, or the Monday of its week."""
    return day if period == "daily" else week_start(day)


# -- streak ----------------------------------------------------------------------------------------------


@dataclass
class StreakResult:
    streak: int  # the streak after today's claim
    freezes_used: int = 0
    broken: bool = False  # the old streak burned (a gap with too few freezes)
    already: bool = False  # today was claimed before: nothing changes


def advance_streak(last_daily: str | None, today: str, streak: int, freezes: int) -> StreakResult:
    """The streak after claiming on `today`.

    Claimed yesterday: +1. A gap of N missed days is bridged when the player holds N freezes (they are
    spent); otherwise the streak burns and starts again from 1.
    """
    if last_daily == today:
        return StreakResult(streak, already=True)
    if last_daily is None or streak <= 0:
        return StreakResult(1)
    missed = days_between(last_daily, today) - 1
    if missed <= 0:
        return StreakResult(streak + 1)
    if freezes >= missed:
        return StreakResult(streak + 1, freezes_used=missed)
    return StreakResult(1, broken=True)


def streak_at_risk(last_daily: str | None, today: str, streak: int, freezes: int) -> bool:
    """True when skipping today would burn the streak at tomorrow's claim (the 21:00 reminder)."""
    if not last_daily or streak <= 0 or last_daily == today:
        return False
    return days_between(last_daily, today) > freezes  # days missed if the claim waits for tomorrow


# -- the card --------------------------------------------------------------------------------------------


def draw_card(content: Content, rng: random.Random, exclude: str | None = None) -> Tarot:
    """A card of the 22 by weight; `exclude` (a reroll) is never drawn again."""
    cards = [c for c in content.tarot.values() if c.key != exclude] or list(content.tarot.values())
    return weighted_choice(rng, cards, [c.weight for c in cards])


def streak_eddies(content: Content, streak: int) -> int:
    d = content.balance.daily
    return min(d.eddies_streak_cap, d.eddies_per_streak_day * max(0, streak - 1))


def card_payout(content: Content, card: Tarot, streak: int) -> int:
    """Eddies of one claim: base + streak extra + the card's own (signed)."""
    return content.balance.daily.base_eddies + streak_eddies(content, streak) + card.eddies


def streak_chests(content: Content, streak: int) -> list[tuple[int, Any]]:
    """Streak rewards reached today: a reward of day D comes every D days of streak (7 = chest, 30 = rare)."""
    return [(day, r) for day, r in sorted(content.balance.daily.streak_rewards.items())
            if streak > 0 and streak % day == 0]


# -- paths and quests ------------------------------------------------------------------------------------

# bus event -> which path counter it feeds. Positive actions only; an event with params is judged by
# `action_path`.
_COMBAT_EVENTS = frozenset({"fight_won", "takedown", "quickhack_used"})
_NONCOMBAT_EVENTS = frozenset({
    "family_time", "social_time", "meditate", "martial_arts", "service", "craft", "heal", "deal",
    "perform", "report", "drive", "work_hard", "promotion", "study", "therapy", "bar",
})


def action_path(event: str, params: dict[str, Any]) -> str | None:
    """'combat' / 'noncombat' for a bus event that says something about the way a player plays, else None."""
    if event in _COMBAT_EVENTS:
        return PATH_COMBAT
    if event == "gig_done":
        return PATH_COMBAT if params.get("approach") == "loud" else PATH_NONCOMBAT
    if event == "crime_done":
        return PATH_COMBAT if params.get("kind") == "assault" else PATH_NONCOMBAT
    if event == "event_resolved":
        return PATH_NONCOMBAT if params.get("nonviolent") else PATH_COMBAT
    if event in _NONCOMBAT_EVENTS:
        return PATH_NONCOMBAT
    return None


def player_paths(content: Content, counters: dict[str, int], chrome_free: bool) -> set[str]:
    """The paths a player gets quests for. With few actions on record: both combat and non-combat."""
    d = content.balance.daily
    c, n = int(counters.get(PATH_COMBAT, 0)), int(counters.get(PATH_NONCOMBAT, 0))
    total = c + n
    paths = {PATH_ANY}
    if total < d.path_min_actions:
        paths |= {PATH_COMBAT, PATH_NONCOMBAT}
    else:
        if c / total >= d.path_share_min:
            paths.add(PATH_COMBAT)
        if n / total >= d.path_share_min:
            paths.add(PATH_NONCOMBAT)
    if chrome_free:
        paths.add(PATH_CHROME_FREE)
    return paths


def eligible_quests(content: Content, period: str, paths: set[str]) -> list[Quest]:
    out = []
    for q in content.quests.values():
        if q.period != period:
            continue
        if PATH_ANY in q.paths or paths & set(q.paths):
            out.append(q)
    return out


def pick_quests(content: Content, rng: random.Random, period: str, paths: set[str], exclude: frozenset[str] = frozenset()) -> list[Quest]:
    """The quests of a new period: `daily_quests` / `weekly_quests` of them, no repeats.

    Path-specific quests come first in the draw (a chrome-free monk gets the monk quests before the
    generic ones), the rest fills up from the general pool.
    """
    want = content.balance.daily.daily_quests if period == "daily" else content.balance.daily.weekly_quests
    pool = [q for q in eligible_quests(content, period, paths) if q.key not in exclude]
    specific = [q for q in pool if PATH_ANY not in q.paths]
    general = [q for q in pool if PATH_ANY in q.paths]
    rng.shuffle(specific)
    rng.shuffle(general)
    # about two of three from the player's own paths, the rest generic
    own = min(len(specific), max(0, want - 1) if general else want)
    picked = specific[:own] + general[: max(0, want - own)]
    if len(picked) < want:
        picked += [q for q in specific[own:] if q not in picked][: want - len(picked)]
    return picked[:want]


def goal_matches(goal: dict[str, Any], event: str, params: dict[str, Any]) -> bool:
    """Does a bus event count for a quest goal (or an affinity entry written as a goal)?

    Choice and ref parameters must be equal, bool parameters equal, int parameters reach the given
    value (`min_danger`). `week_lived` is the exception: `max_heat` is a ceiling, `min_mood` a floor,
    judged against the heat and mood the week published.
    """
    if goal.get("event") != event:
        return False
    for name, want in goal.items():
        if name == "event":
            continue
        if event == "week_lived":
            if name == "max_heat" and int(params.get("heat", 99)) > int(want):
                return False
            if name == "min_mood" and int(params.get("mood", -1)) < int(want):
                return False
            continue
        have = params.get(name)
        if isinstance(want, bool) or isinstance(want, str):
            if have != want:
                return False
        elif have is None or int(have) < int(want):
            return False
    return True


def key_matches(key: str, event: str, params: dict[str, Any]) -> int | None:
    """Specificity of a role-affinity / reputation key for a bus event: the number of parameters it
    fixes, or None when it does not fit. `gig_done:loud` fits a loud gig with specificity 1."""
    ev, want = parse_bus_key(key)
    if ev != event:
        return None
    if not goal_matches({"event": ev, **want}, event, params):
        return None
    return len(want)


def best_key_weight(table: dict[str, int], event: str, params: dict[str, Any]) -> int:
    """Weight of the most specific key that fits (ties: the larger weight), 0 when none fits.

    Same rule as roles.yaml: 'When several keys fit one event, only the most specific one counts'.
    """
    best: tuple[int, int] | None = None
    for key, weight in table.items():
        spec = key_matches(key, event, params)
        if spec is None:
            continue
        cand = (spec, weight)
        if best is None or cand > best:
            best = cand
    return 0 if best is None else best[1]


def quest_effects(rewards: QuestRewards, skill: str | None) -> dict[str, int]:
    """The `apply_effects` dict for the character part of a quest reward (tokens go to the player)."""
    fx: dict[str, int] = {}
    if rewards.eddies:
        fx["eddies"] = rewards.eddies
    if rewards.xp and skill:
        fx[f"skill_xp:{skill}"] = rewards.xp
    if rewards.street_cred:
        fx["street_cred"] = rewards.street_cred
    if rewards.status:
        fx["status"] = rewards.status
    if rewards.fame:
        fx["fame"] = rewards.fame
    return fx
