"""The Net of the netrunners (owner request 23.10): the breach protocol and the ICE duel. Pure, randomness through `rng`.

Breach: a grid of codes. The first pick is in the top row, then the column of that cell, then its row, and so on. The
picked codes fill the buffer; a daemon (a short chain of codes) is uploaded when the buffer holds it as a run. The
daemons are cut from one walkable path, so all of them can be uploaded in one buffer.

ICE duel: the ICE telegraphs what it does (the netrunner sees it with a chance); the runner answers with an attack,
a shield or a bypass. Attack beats a probe, a shield beats a strike, a bypass beats hardening.
"""

from __future__ import annotations

import random
from typing import Any

from app.content.loader import Content
from app.engine import derived
from app.engine.state import CharacterState

ROW, COL = "row", "col"
OLD_NET_UNTIL = 2022  # the DataKrash: after it the Old Net is gone, there are only the local subnets


def cfg(content: Content):  # noqa: ANN201
    return content.balance.net


def deck_of(state: CharacterState, content: Content):  # noqa: ANN201
    deck = None
    for item in state.cyberware:
        cw = content.cyberware.get(item.key)
        if cw is not None and cw.deck is not None:
            deck = cw.deck
    return deck


def has_deck(state: CharacterState, content: Content) -> bool:
    return deck_of(state, content) is not None


def old_net(year: int) -> bool:
    return year <= OLD_NET_UNTIL


def runner_rank(state: CharacterState, content: Content) -> int:
    return derived.role_ranks(state, content).get("netrunner", 0)


def power(state: CharacterState, content: Content) -> int:
    """INT + netrunning + the deck's hack bonus: the runner's strength in the Net."""
    deck = deck_of(state, content)
    return (derived.stat_total(state, content, "INT") + derived.skill_total(state, content, "netrunning")
            + (deck.hack_bonus if deck else 0) + derived.mod(state, content, "check.gig_hack"))


def tier(state: CharacterState, content: Content) -> int:
    return max(1, min(5, power(state, content) // 4))


def buffer_size(state: CharacterState, content: Content) -> int:
    c = cfg(content)
    deck = deck_of(state, content)
    ram = (deck.ram_max + derived.mod(state, content, "ram_max")) if deck else 0
    return max(c.buffer_base, min(c.buffer_max, c.buffer_base + ram // c.buffer_per_ram + runner_rank(state, content) // 3))


def trace_cost(state: CharacterState, content: Content, base: int) -> int:
    c = cfg(content)
    cut = min(c.role_trace_cut_max_pct, runner_rank(state, content) * c.role_trace_cut_pct)
    return max(1, round(base * (100 - cut) / 100))


# -- the trace -----------------------------------------------------------------------------------------------------------


def trace_now(state: CharacterState, content: Content, today: str, now: "datetime | None" = None) -> int:
    """Owner report 24.10: the trace cools by `trace_decay_per_hour` every hour since it was last set
    (an old record without a time stamp counts from the midnight of its day)."""
    from datetime import datetime, timezone

    rec = state.flags.get("net_trace") or {}
    v = int(rec.get("v", 0) or 0)
    if not v:
        return 0
    now = now or datetime.now(timezone.utc)
    ts = rec.get("ts") or (str(rec.get("day")) + "T00:00:00+00:00" if rec.get("day") else None)
    if not ts:
        return v
    try:
        hours = int((now - datetime.fromisoformat(str(ts))).total_seconds() // 3600)
    except (ValueError, TypeError):
        hours = 0
    return max(0, v - cfg(content).trace_decay_per_hour * max(0, hours))


def trace_set(state: CharacterState, v: int, today: str, now: "datetime | None" = None) -> None:
    """Keeps the time stamp of the last whole cooled hour, so the next hour is not lost."""
    from datetime import datetime, timedelta, timezone

    now = now or datetime.now(timezone.utc)
    rec = state.flags.get("net_trace") or {}
    ts = now
    old = rec.get("ts") or (str(rec.get("day")) + "T00:00:00+00:00" if rec.get("day") else None)
    if old and int(rec.get("v", 0) or 0) > 0:
        try:
            prev = datetime.fromisoformat(str(old))
            ts = prev + timedelta(hours=int((now - prev).total_seconds() // 3600))
        except (ValueError, TypeError):
            ts = now
    state.flags["net_trace"] = {"v": max(0, v), "day": today, "ts": ts.isoformat()}


# -- breach ------------------------------------------------------------------------------------------------------------------


def new_breach(rng: random.Random, content: Content, buf: int, daemons: int) -> dict[str, Any]:
    c = cfg(content)
    n = c.grid
    grid = [[rng.choice(c.codes) for _ in range(n)] for _ in range(n)]
    # one walkable path of `buf` cells: row 0, then alternate column / row, never the same cell twice
    path: list[tuple[int, int]] = []
    line, mode = 0, ROW
    for _ in range(buf):
        free = [(line, j) if mode == ROW else (j, line) for j in range(n)]
        free = [p for p in free if p not in path]
        if not free:
            break
        cell = rng.choice(free)
        path.append(cell)
        line, mode = (cell[1], COL) if mode == ROW else (cell[0], ROW)
    codes = [grid[r][k] for r, k in path]
    lens = [2, 3, 4][:daemons]
    found: list[list[str]] = []
    for ln in lens:
        ln = min(ln, len(codes))
        start = rng.randint(0, len(codes) - ln)
        found.append(codes[start:start + ln])
    return {"g": grid, "used": [], "buf": [], "max": buf, "line": 0, "mode": ROW, "d": found, "ok": [False] * len(found),
            "over": False}


def _holds(buf: list[str], seq: list[str]) -> bool:
    n = len(seq)
    return any(buf[i:i + n] == seq for i in range(len(buf) - n + 1))


def line_cells(b: dict[str, Any]) -> list[tuple[int, int]]:
    n = len(b["g"])
    used = {tuple(x) for x in b["used"]}
    cells = [(b["line"], j) if b["mode"] == ROW else (j, b["line"]) for j in range(n)]
    return [p for p in cells if p not in used]


def pick(b: dict[str, Any], r: int, k: int) -> str:
    """Pick a cell. Returns "" when done, or why not: "line", "used", "over"."""
    if b["over"]:
        return "over"
    if [r, k] in b["used"]:
        return "used"
    if (r, k) not in line_cells(b):
        return "line"
    b["used"].append([r, k])
    b["buf"].append(b["g"][r][k])
    b["line"], b["mode"] = (k, COL) if b["mode"] == ROW else (r, ROW)
    for i, seq in enumerate(b["d"]):
        if not b["ok"][i] and _holds(b["buf"], seq):
            b["ok"][i] = True
    if len(b["buf"]) >= b["max"] or all(b["ok"]) or not line_cells(b):
        b["over"] = True
    return ""


def solved(b: dict[str, Any]) -> list[int]:
    return [i for i, ok in enumerate(b["ok"]) if ok]


# -- the ICE duel ----------------------------------------------------------------------------------------------------------

STRIKE, PROBE, HARDEN = "strike", "probe", "harden"
ATTACK, SHIELD, BYPASS = "attack", "shield", "bypass"
INTENTS = (STRIKE, PROBE, HARDEN)


def new_duel(state: CharacterState, content: Content, rng: random.Random, t: int, black: bool = False) -> dict[str, Any]:
    c = cfg(content).ice
    hp = c.hp_base + c.hp_per_tier * t + (c.black_hp if black else 0)
    brain = c.brain_base + c.brain_per_int * derived.stat_total(state, content, "INT")
    d = {"t": t, "ice": hp, "ice_max": hp, "brain": brain, "brain_max": brain, "round": 1, "shield": 0, "trace": 0,
         "taken": 0, "over": "", "black": black, "log": []}
    _next_intent(d, state, content, rng)
    return d


def _next_intent(d: dict[str, Any], state: CharacterState, content: Content, rng: random.Random) -> None:
    c = cfg(content).ice
    d["intent"] = rng.choice(INTENTS)
    seen = min(c.see_max_pct, c.see_base_pct + c.see_per_power_pct * power(state, content))
    d["seen"] = rng.randint(1, 100) <= seen


def _dmg(state: CharacterState, content: Content, rng: random.Random) -> int:
    return rng.randint(1, 6) + power(state, content) // 3


def duel_move(d: dict[str, Any], move: str, state: CharacterState, content: Content, rng: random.Random) -> list[str]:
    """One round. Returns the lines of what happened."""
    c = cfg(content).ice
    if d["over"]:
        return []
    intent = d["intent"]
    lo, hi = c.dmg
    ice_hit = rng.randint(lo, hi) + d["t"] + (c.black_dmg if d["black"] else 0)
    out: list[str] = []
    hit_ice = 0
    hit_me = 0
    if move == ATTACK:
        base = _dmg(state, content, rng)
        if intent == PROBE:
            hit_ice = base
            out.append(f"ЛЕД прощупывал тебя и раскрылся: удар на {base}.")
        elif intent == STRIKE:
            hit_ice, hit_me = base, ice_hit
            out.append(f"Размен ударами: ты {base}, ЛЕД тебе {ice_hit}.")
        else:
            hit_ice = max(0, base - c.harden)
            out.append(f"ЛЕД укрепился: прошло только {hit_ice}.")
    elif move == SHIELD:
        if intent == STRIKE:
            hit_ice = c.counter
            out.append(f"Щит держит удар ЛЕДа. Отражённый импульс: {c.counter}.")
        elif intent == PROBE:
            d["trace"] += c.probe_trace
            out.append(f"Пока ты закрывался, ЛЕД прощупал тебя. След +{c.probe_trace}.")
        else:
            out.append("Оба стоите в обороне. Ход уходит впустую.")
    else:  # bypass
        base = _dmg(state, content, rng)
        if intent == HARDEN:
            hit_ice = base * 2
            out.append(f"Обход под укреплённым слоем: удар на {hit_ice}.")
        elif intent == STRIKE:
            hit_me = ice_hit + ice_hit // 2
            out.append(f"Обход открыл тебя под удар: ЛЕД бьёт на {hit_me}.")
        else:
            hit_ice = base
            d["trace"] += c.probe_trace // 2
            out.append(f"Проскальзываешь мимо щупа: удар на {base}, след +{c.probe_trace // 2}.")
    d["ice"] = max(0, d["ice"] - hit_ice)
    d["brain"] = max(0, d["brain"] - hit_me)
    d["taken"] += hit_me
    if d["ice"] <= 0:
        d["over"] = "won"
    elif d["brain"] <= 0:
        d["over"] = "flat"
    elif d["round"] >= c.rounds:
        d["over"] = "kicked"
    else:
        d["round"] += 1
        _next_intent(d, state, content, rng)
    d["log"] = (d["log"] + out)[-3:]
    return out
