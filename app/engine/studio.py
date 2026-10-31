"""Braindance studio (owner request): a director of the path `bd_director` shoots a braindance after a book of
Ozon671Games. Pure functions; the numbers are in balance.yaml `paths.studio`.

A shoot is two checks: the shoot itself (the book's stat + skill against its dv) and the edit (TECH + tech).
Stars: 1 for showing up, +1 for each passed check, +1 for a wide margin over both, +1 for a critical success.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from app.content.loader import Content
from app.engine import paths as pe
from app.engine.state import CharacterState

FLAG_WEEK = "bd_week"  # weeks_lived of the last shoot: one braindance a press

R_PATH, R_RANK, R_AP, R_EDDIES, R_JAIL, R_DONE, R_GONE = "path", "rank", "ap", "eddies", "jail", "done", "gone"


def cfg(content: Content):  # noqa: ANN201
    return content.balance.paths.studio


def book(content: Content, key: str):  # noqa: ANN201
    return next((b for b in cfg(content).books if b.key == key), None)


def is_director(state: CharacterState, content: Content) -> bool:
    return pe.record(state).get("key") == cfg(content).path


def price(state: CharacterState, content: Content) -> int:
    c = cfg(content)
    return c.price + c.price_per_rank * max(0, pe.rank(state, content) - 1)


def blocked(state: CharacterState, content: Content, *, jailed: bool = False) -> str | None:
    c = cfg(content)
    if not is_director(state, content):
        return R_PATH
    if jailed:
        return R_JAIL
    if pe.rank(state, content) < c.min_rank:
        return R_RANK
    if state.flags.get(FLAG_WEEK) == state.weeks_lived:
        return R_DONE
    if state.ap < c.ap:
        return R_AP
    if state.eddies < price(state, content):
        return R_EDDIES
    return None


def auto_title(content: Content, key: str, rng: random.Random) -> str:
    b = book(content, key)
    if b is None:
        return "Без названия"
    return f"{b.title}: {rng.choice(b.subtitles)}"


@dataclass
class Shoot:
    shot: object   # CheckResult of the shoot
    edit: object   # CheckResult of the edit
    stars: int
    pay: int
    xp: int


def shoot(state: CharacterState, content: Content, rng: random.Random, key: str) -> Shoot:
    from app.engine import checks

    c = cfg(content)
    b = book(content, key)
    assert b is not None
    a = checks.roll_check(state, content, rng, b.stat, b.skill, b.dv, ("social",))
    e = checks.roll_check(state, content, rng, "TECH", "tech", b.dv + c.edit_dv_bonus, ())
    stars = 1 + int(a.ok) + int(e.ok)
    if a.ok and e.ok and (a.total - a.dv) + (e.total - e.dv) >= 6:
        stars += 1
    if getattr(a, "crit", None) == "success" or getattr(e, "crit", None) == "success":
        stars += 1
    stars = max(1, min(5, stars))
    n = pe.rank(state, content)
    pay = c.pay_by_stars[stars - 1] * (100 + content.balance.paths.rank_pay_pct * max(0, n - 1)) // 100
    return Shoot(a, e, stars, pay, c.xp_by_stars[stars - 1])


def ticket(content: Content, stars: int) -> int:
    return cfg(content).watch_price_per_star * max(1, stars)


def royalty(content: Content, stars: int) -> int:
    return ticket(content, stars) * cfg(content).royalty_pct // 100


def stars_text(n: int) -> str:
    return "★" * n + "☆" * (5 - n)
