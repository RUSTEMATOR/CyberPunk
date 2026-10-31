"""A sortie through a piece of the district (owner request 24.10). Pure: randomness only through the passed rng.

A fresh size x size map every sortie: the start (the exit) on an edge, the character's own goal far from it, points of
interest scattered (a gang on a corner, someone harassed, a stash, a trap, a trader, a dropped bag). The character sees
the cells next to the visited ones; every move costs a step; night falls when the steps run out.

The run lives in the character flag `sortie`:
  {district, size, pos [x, y], start [x, y], prev [x, y], steps, seen ["x,y"], pois {"x,y": {k, done}},
   goal {k, pos [x, y], done}, eddies, log [lines], fight "x,y" | None, flavor {"x,y": index}}
"""

from __future__ import annotations

import random
from typing import Any

FLAG = "sortie"
HOSTILE = ("gang", "harass")
DIRS = {"n": (0, -1), "s": (0, 1), "w": (-1, 0), "e": (1, 0)}

# the goal of the sortie by the preset group of the character (presets.yaml `group`)
GOALS = {"street": "hideout", "crime": "cash", "wheels": "wreck", "order": "shard", "tech": "node", "stage": "wall"}

FLAVORS = (
    "переулок за лапшичной", "крыша гаража", "двор мегабашни", "закрытый рынок", "пустая парковка",
    "лестница пожарного выхода", "подземный переход", "задний двор клуба", "стройка без рабочих", "автомойка",
    "проулок с граффити", "остановка без автобусов",
)


def key(x: int, y: int) -> str:
    return f"{x},{y}"


def neighbours(x: int, y: int, size: int) -> list[tuple[int, int]]:
    return [(x + dx, y + dy) for dx, dy in DIRS.values() if 0 <= x + dx < size and 0 <= y + dy < size]


def generate(rng: random.Random, size: int, steps: int, pois: dict[str, int], district: str, goal: str) -> dict[str, Any]:
    cells = [(x, y) for x in range(size) for y in range(size)]
    edge = [c for c in cells if c[0] in (0, size - 1) or c[1] in (0, size - 1)]
    start = rng.choice(edge)
    far = [c for c in cells if abs(c[0] - start[0]) + abs(c[1] - start[1]) >= size - 1] or [c for c in cells if c != start]
    goal_pos = rng.choice(far)
    free = [c for c in cells if c not in (start, goal_pos)]
    rng.shuffle(free)
    placed: dict[str, dict[str, Any]] = {}
    for kind, n in pois.items():
        for _ in range(n):
            if not free:
                break
            x, y = free.pop()
            placed[key(x, y)] = {"k": kind, "done": False}
    run: dict[str, Any] = {
        "district": district, "size": size, "pos": list(start), "start": list(start), "prev": list(start),
        "steps": steps, "seen": [], "pois": placed, "goal": {"k": goal, "pos": list(goal_pos), "done": False},
        "eddies": 0, "log": [], "fight": None,
        "flavor": {key(x, y): rng.randrange(len(FLAVORS)) for x, y in cells},
    }
    reveal(run)
    return run


def reveal(run: dict[str, Any]) -> None:
    x, y = run["pos"]
    seen = set(run["seen"])
    seen.add(key(x, y))
    for nx, ny in neighbours(x, y, run["size"]):
        seen.add(key(nx, ny))
    run["seen"] = sorted(seen)


def reveal_all(run: dict[str, Any]) -> None:
    run["seen"] = [key(x, y) for x in range(run["size"]) for y in range(run["size"])]


def move(run: dict[str, Any], d: str) -> bool:
    if d not in DIRS or run["steps"] <= 0:
        return False
    dx, dy = DIRS[d]
    x, y = run["pos"][0] + dx, run["pos"][1] + dy
    if not (0 <= x < run["size"] and 0 <= y < run["size"]):
        return False
    run["prev"] = list(run["pos"])
    run["pos"] = [x, y]
    run["steps"] -= 1
    reveal(run)
    return True


def step_back(run: dict[str, Any]) -> None:
    run["pos"] = list(run["prev"])
    reveal(run)


def here(run: dict[str, Any]) -> dict[str, Any] | None:
    """The open point of interest under the character (the goal counts), None when the cell is quiet."""
    x, y = run["pos"]
    g = run["goal"]
    if list(g["pos"]) == [x, y] and not g["done"]:
        return {"k": "goal", "goal": g["k"]}
    p = run["pois"].get(key(x, y))
    if p is not None and not p["done"]:
        return p
    return None


def resolve_here(run: dict[str, Any]) -> None:
    x, y = run["pos"]
    if list(run["goal"]["pos"]) == [x, y]:
        run["goal"]["done"] = True
        return
    p = run["pois"].get(key(x, y))
    if p is not None:
        p["done"] = True


def at_exit(run: dict[str, Any]) -> bool:
    return list(run["pos"]) == list(run["start"])


def cleared(run: dict[str, Any]) -> bool:
    return all(p["done"] for p in run["pois"].values() if p["k"] in HOSTILE)


def hostile_left(run: dict[str, Any]) -> int:
    return sum(1 for p in run["pois"].values() if p["k"] in HOSTILE and not p["done"])


ICONS = {"gang": "☠️", "harass": "❗", "stash": "📦", "trap": "⚠️", "trader": "🛒", "loot": "💰"}


def grid_lines(run: dict[str, Any]) -> list[str]:
    """The map: ⬛ unseen, ▫️ seen and quiet, 🟢 you, 🚪 the exit, 🎯 the goal, icons for open points, ✖️ dealt with."""
    seen = set(run["seen"])
    out = []
    for y in range(run["size"]):
        row = []
        for x in range(run["size"]):
            k = key(x, y)
            if [x, y] == list(run["pos"]):
                row.append("🟢")
            elif k not in seen:
                row.append("⬛")
            elif [x, y] == list(run["start"]):
                row.append("🚪")
            elif list(run["goal"]["pos"]) == [x, y]:
                row.append("✖️" if run["goal"]["done"] else "🎯")
            elif k in run["pois"]:
                p = run["pois"][k]
                row.append("✖️" if p["done"] else ICONS.get(p["k"], "❔"))
            else:
                row.append("▫️")
        out.append("".join(row))
    return out
