"""The arcade (owner request 29.10, docs/PLAN_ARCADE.md): the rules of five small games taken from the most popular
HTML5 games. Pure: no clock, no DB, randomness only through the `rng`.

Wordle, a snake (Slither.io), the rhythm sequence (Friday Night Funkin'), the town builder (Townscaper) and the
district guessing (GeoGuessr) live as plain dicts, so the service can keep them in `world_state` as JSON.
"""

from __future__ import annotations

import hashlib
import random
from typing import Any

# -- Wordle ------------------------------------------------------------------------------------------------------


def norm(word: str) -> str:
    """A typed word the way the list keeps it: lower case, `ё` as `е`."""
    return word.strip().lower().replace("ё", "е")


def valid_word(word: str, size: int = 5) -> bool:
    w = norm(word)
    return len(w) == size and all("а" <= c <= "я" for c in w)


def word_of_day(words: list[str], day: str) -> str:
    """The word of a Kyiv day: the same for everybody, spread by a hash of the date."""
    n = int(hashlib.sha256(day.encode()).hexdigest(), 16)
    return norm(words[n % len(words)])


def evaluate(guess: str, answer: str) -> str:
    """Tile colours of a guess: `g` the letter is in place, `y` it is elsewhere, `b` it is not there (or already used up).
    Repeated letters are counted the way the original game does: greens first, then yellows left to right."""
    g, a = norm(guess), norm(answer)
    res = ["b"] * len(g)
    left: dict[str, int] = {}
    for i, ch in enumerate(a):
        if i < len(g) and g[i] == ch:
            res[i] = "g"
        else:
            left[ch] = left.get(ch, 0) + 1
    for i, ch in enumerate(g):
        if res[i] == "b" and left.get(ch, 0) > 0:
            res[i] = "y"
            left[ch] -= 1
    return "".join(res)


# -- the snake ----------------------------------------------------------------------------------------------------

DIRS: dict[str, tuple[int, int]] = {"u": (-1, 0), "d": (1, 0), "l": (0, -1), "r": (0, 1)}
OPPOSITE = {"u": "d", "d": "u", "l": "r", "r": "l"}


def _free_cell(snake: list[list[int]], size: int, rng: random.Random) -> list[int] | None:
    taken = {(r, c) for r, c in snake}
    cells = [[r, c] for r in range(size) for c in range(size) if (r, c) not in taken]
    return rng.choice(cells) if cells else None


def new_snake(size: int, rng: random.Random) -> dict[str, Any]:
    mid = size // 2
    snake = [[mid, 2], [mid, 1], [mid, 0]]
    return {"n": size, "snake": snake, "dir": "r", "food": _free_cell(snake, size, rng), "score": 0, "alive": True,
            "won": False}


def snake_step(st: dict[str, Any], d: str, rng: random.Random) -> str:
    """One move of the head. Returns `ok`, `ate`, `dead` or `won`; a turn straight back is ignored (the snake goes on)."""
    if not st["alive"]:
        return "dead"
    if d not in DIRS or (OPPOSITE[d] == st["dir"] and len(st["snake"]) > 1):
        d = st["dir"]
    st["dir"] = d
    dr, dc = DIRS[d]
    hr, hc = st["snake"][0]
    nr, nc = hr + dr, hc + dc
    size = int(st["n"])
    eat = st["food"] is not None and [nr, nc] == st["food"]
    body = st["snake"] if eat else st["snake"][:-1]  # the tail leaves its cell unless the snake grows
    if not (0 <= nr < size and 0 <= nc < size) or [nr, nc] in body:
        st["alive"] = False
        return "dead"
    st["snake"].insert(0, [nr, nc])
    if not eat:
        st["snake"].pop()
        return "ok"
    st["score"] = int(st["score"]) + 1
    st["food"] = _free_cell(st["snake"], size, rng)
    if st["food"] is None:
        st["alive"], st["won"] = False, True
        return "won"
    return "ate"


# -- the rhythm sequence --------------------------------------------------------------------------------------------

ARROWS: dict[str, str] = {"l": "←", "u": "↑", "d": "↓", "r": "→"}


def new_beat(start_len: int, rng: random.Random) -> dict[str, Any]:
    return {"seq": [rng.choice("ludr") for _ in range(start_len)], "pos": 0, "round": 1, "phase": "show", "alive": True}


def beat_press(st: dict[str, Any], d: str, rng: random.Random) -> str:
    """A pressed arrow. Returns `ok`, `round` (the sequence is repeated: the next one is one arrow longer) or `miss`."""
    if not st["alive"] or st["phase"] != "play":
        return "miss"
    if d != st["seq"][st["pos"]]:
        st["alive"] = False
        return "miss"
    st["pos"] += 1
    if st["pos"] < len(st["seq"]):
        return "ok"
    st["seq"].append(rng.choice("ludr"))
    st["pos"], st["phase"] = 0, "show"
    st["round"] += 1
    return "round"


# -- the town builder ------------------------------------------------------------------------------------------------

TOWN_GLYPHS: tuple[str, ...] = ("🟦", "🏠", "🏘", "🏢", "🏰", "🌳")  # water, then houses, then a tree
TOWN_BUILDINGS = 4  # glyphs 1..4 count as buildings


def new_town(size: int) -> list[list[int]]:
    return [[0] * size for _ in range(size)]


def town_tap(grid: list[list[int]], r: int, c: int) -> None:
    grid[r][c] = (grid[r][c] + 1) % len(TOWN_GLYPHS)


def town_buildings(grid: list[list[int]]) -> int:
    return sum(1 for row in grid for v in row if 1 <= v <= TOWN_BUILDINGS)


# -- the district guessing ---------------------------------------------------------------------------------------------


def geo_points(clues_shown: int) -> int:
    """3 points for the first clue, 2 for the second, 1 for the third and later."""
    return max(1, 4 - max(1, clues_shown))
