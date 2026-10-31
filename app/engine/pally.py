"""Hired players in their employer's fight (owner request 26.10: «если меня нанял юзер корпорат, то у него должна быть
возможность прям мной воевать. Меня автоматически должно подключать в бой нанявшего меня. Если я афк, то мои скиллы
применяются в зависимости от моего плейстайла или по очереди»). Pure functions.

A hired player stands in the fight as an ally built from the character itself (engine/combat.build_player). Its
record in `cs.ctx["pallies"][ally id]` = {"tg", "name", "mode" ("play" | "turns"), "style" (counts of the player's
own fights), "seq" (the next skill in turns)}. Every tick the ally does one of its skills:
1. an order of the employer or of the hired player (`cs.ctx["orders"][ally id]`), if it can be done now;
2. otherwise by its mode: «play» weighs the skills by how the player fights in their own fights, «turns» goes round.
A skill: "imp:<implant>" switches a duration implant on (then the ally still strikes), "hack:<program>" runs a
quickhack, "ranged" / "melee" strike with the best weapon of the kind.
"""

from __future__ import annotations

import random
from typing import Any

from app.content.loader import Content
from app.engine import combat as cb

PLAY, TURNS = "play", "turns"
MODES = (PLAY, TURNS)
FLAG_STYLE = "fight_style"   # the player's own counts: {"imp": {key: n}, "hack": {key: n}, "ranged": n, "melee": n}
FLAG_MODE = "ally_mode"      # how the character fights in an employer's fight when nobody gives orders


def skills(cs: cb.CombatState, a: cb.Combatant, content: Content) -> list[str]:
    """What the ally can do right now."""
    out: list[str] = []
    for imp in a.implants:
        if cb.spec_of(a, imp, content).mode == "duration" and not cb.activation_block(a, imp, content):
            out.append(f"imp:{imp.key}")
    foes = cs.active_enemies()
    target = foes[0][1] if foes else None
    for key in a.quickhacks:
        if target is not None and not cb.hack_block(a, key, target, content):
            out.append(f"hack:{key}")
    for kind in (cb.RANGED, cb.MELEE):
        if kind in a.attacks:
            out.append(kind)
    return out


def all_skills(a: cb.Combatant, content: Content) -> list[str]:
    """Everything the ally has, for the order buttons (some may be not ready this tick)."""
    out = [f"imp:{i.key}" for i in a.implants if cb.spec_of(a, i, content).mode == "duration"]
    out += [f"hack:{k}" for k in a.quickhacks]
    out += [k for k in (cb.RANGED, cb.MELEE) if k in a.attacks]
    return out


def label(content: Content, skill: str) -> str:
    kind, _, key = skill.partition(":")
    if kind == "imp":
        cw = content.cyberware.get(key)
        return cw.name if cw else key
    if kind == "hack":
        qh = content.quickhacks.get(key)
        return qh.name if qh else key
    return {"ranged": "Стрелять", "melee": "Ближний бой"}.get(skill, skill)


def _weight(style: dict[str, Any], skill: str) -> float:
    kind, _, key = skill.partition(":")
    if kind in ("imp", "hack"):
        return 1.0 + float((style.get(kind) or {}).get(key, 0) or 0)
    return 1.0 + float(style.get(skill, 0) or 0)


def choose(cs: cb.CombatState, a: cb.Combatant, rec: dict[str, Any], content: Content, rng: random.Random) -> str | None:
    """The skill of this tick (the order is taken out of ctx once used)."""
    can = skills(cs, a, content)
    if not can:
        return None
    orders = cs.ctx.setdefault("orders", {})
    order = orders.pop(a.id, None)
    if order and order in can:
        return str(order)
    if rec.get("mode") == TURNS:
        mine = all_skills(a, content)
        if not mine:
            return can[0]
        i = int(rec.get("seq", 0) or 0)
        for step in range(len(mine)):
            s = mine[(i + step) % len(mine)]
            if s in can:
                rec["seq"] = (i + step + 1) % len(mine)
                return s
        return can[0]
    style = rec.get("style") or {}
    if not any(style.values()):  # no history yet: a bit of everything, the chrome first
        style = {"imp": {s[4:]: 2 for s in can if s.startswith("imp:")}, "hack": {s[5:]: 1 for s in can if s.startswith("hack:")}}
    weights = [_weight(style, s) for s in can]
    return rng.choices(can, weights=weights, k=1)[0]


def act(cs: cb.CombatState, a: cb.Combatant, rec: dict[str, Any], content: Content, rng: random.Random) -> None:
    """One tick of a hired player at the side."""
    foes = cs.active_enemies()
    if not foes:
        return
    skill = choose(cs, a, rec, content, rng)
    rec["last"] = skill or ""
    kind, _, key = (skill or "").partition(":")
    cb._begin_actions(cs, a, content)  # noqa: SLF001
    if kind == "imp":
        try:
            cb.activate(cs, a, key, content)
        except cb.CombatError:
            pass
        skill = cb.RANGED if cb.RANGED in a.attacks else cb.MELEE
    elif kind == "hack":
        idx, e = rng.choice(foes)
        qh = content.quickhacks.get(key)
        try:
            cb.hack(cs, content, rng, a, key, cb.hack_targets(cs, a, e, qh) if qh else [e])
            skill = ""
        except cb.CombatError:
            skill = cb.RANGED if cb.RANGED in a.attacks else cb.MELEE
    if skill in (cb.RANGED, cb.MELEE) and skill in a.attacks:
        idx, e = rng.choice(cs.active_enemies() or foes)
        atk, attempts = cb._attacks_of(cs, a, skill, content)  # noqa: SLF001
        fx = cb._fx(a, content)  # noqa: SLF001
        for n in range(attempts):
            if not e.active:
                break
            cb._strike(cs, content, rng, a, e, atk, fx, n == 0)  # noqa: SLF001
    cb._end_actions(cs, a, content)  # noqa: SLF001


def style_from(cs: cb.CombatState, old: dict[str, Any] | None) -> dict[str, Any]:
    """The player's own fight added to the counts (the newest fights weigh a little more)."""
    st: dict[str, Any] = {"imp": dict((old or {}).get("imp") or {}), "hack": dict((old or {}).get("hack") or {}),
                          "ranged": float((old or {}).get("ranged", 0) or 0), "melee": float((old or {}).get("melee", 0) or 0)}
    for k in ("ranged", "melee"):
        st[k] = round(st[k] * 0.9 + int(cs.used.get(k, 0) or 0), 2)
    for group in ("imp", "hack"):
        st[group] = {k: round(v * 0.9, 2) for k, v in st[group].items() if v * 0.9 >= 0.2}
    for tick in cs.log:
        for ev in tick.get("ev") or []:
            if ev.get("by") != cb.PLAYER_ID:
                continue
            if ev.get("k") == "activate" and ev.get("imp"):
                st["imp"][ev["imp"]] = round(st["imp"].get(ev["imp"], 0) + 1, 2)
            elif ev.get("k") == "hack" and ev.get("prog"):
                st["hack"][ev["prog"]] = round(st["hack"].get(ev["prog"], 0) + 1, 2)
    return st
