"""The fight in first person (owner request 31.10, docs/PLAN_FIGHT3D.md phase 2): the same fight, in real time.

«Вся логика должна быть такой же, как в текстовой части, просто срабатывать каждый раз, когда происходит атака».
Nothing here rolls on its own terms: every hit of a projectile is one `combat._strike` (the attack roll, dodge, perks,
the damage dice of the weapon, armor, the weapon's special) on the same `CombatState` the text fight keeps.

The pace is the text's pace. A tick lasts `tick_s` seconds of real time (the server's clock). In one tick the player
lands at most `attempts` attacks of the weapon in hand (as one press of «Стрелять» in the text), every enemy at most its
own `attempts`. When the time of a tick runs out, `combat._end_of_tick` does what it does after a text tick (implant
charge, statuses, the reinforcement timer). Morale runs after the player's first attack of a tick, as after the player
phase of a text tick.

Pure: the clock comes in as `now`, randomness through `rng`. The service (services/fight3d.py) loads and saves.
"""

from __future__ import annotations

import random
from typing import Any

from app.content.loader import Content
from app.engine import combat as cb

RT = "rt3d"           # cs.ctx key: {"at": the start of the current tick, "p": the player's attacks in it, "e": {enemy id: n}}
MAX_CATCHUP = 30      # ticks played at most in one call (a phone asleep for an hour does not run the fight to the end)
TEXT_ONLY = ("elite", "boss_ctl", "pallies")  # fights whose other side is played by people or by scripts of their own


def text_only(cs: cb.CombatState) -> bool:
    """Fights that stay in the text: the elite fights with waves and intents, a raid boss played by a player, hired players."""
    return any(cs.ctx.get(k) for k in TEXT_ONLY)


def _rt(cs: cb.CombatState, now: float) -> dict[str, Any]:
    rt = cs.ctx.get(RT)
    if not isinstance(rt, dict):
        rt = {"at": now, "p": 0, "e": {}}
        cs.ctx[RT] = rt
    return rt


def advance(cs: cb.CombatState, content: Content, rng: random.Random, now: float, tick_s: float) -> int:
    """Close the ticks whose time ran out. Returns how many closed."""
    rt = _rt(cs, now)
    at = float(rt.get("at", now))
    n = 0
    while not cs.outcome and now - at >= tick_s and n < MAX_CATCHUP:
        if not rt.get("p") and not rt.get("e"):
            # nobody attacked in this tick: it does not count (in the text a tick passes only by a press), so walking
            # around the street does not bring the reinforcements closer
            at = now
            break
        at += tick_s
        n += 1
        for e in cs.enemies:  # a stunned enemy loses the tick it would have acted in
            if e.active and e.stun > 0:
                e.stun -= 1
        if cs.player.stun > 0:
            cs.player.stun -= 1
        cb._end_of_tick(cs, content)
        cb.check_end(cs)
        rt["p"], rt["e"] = 0, {}
    if now - at >= tick_s:
        at = now
    rt["at"] = at
    return n


def tick_left(cs: cb.CombatState, now: float, tick_s: float) -> float:
    rt = _rt(cs, now)
    return max(0.0, tick_s - (now - float(rt.get("at", now))))


def _choose(cs: cb.CombatState, content: Content, weapon: str, melee: bool) -> tuple[cb.Attack, int]:
    """The attack in hand, the way the text picks it for «Стрелять» / «Ударить» with a weapon button."""
    p = cs.player
    kind = cb.RANGED if not melee else (cb.MELEE_NL if cs.nonlethal else cb.MELEE)
    chosen = p.arsenal.get(weapon) if weapon else None
    if chosen is not None and (chosen.kind == cb.RANGED) == (not melee) and kind != cb.MELEE_NL:
        return chosen, chosen.attempts + cb._fx(p, content)["extra_actions"]
    if kind not in p.attacks:
        raise cb.CombatError("no_weapon")
    return cb._attacks_of(cs, p, kind, content)


def _result(cs: cb.CombatState, since: int, who: str) -> dict[str, Any]:
    """What the strike did, from the engine's own events: the roll against the defense, the damage, a down."""
    out: dict[str, Any] = {"hit": False, "rolled": False}
    for ev in cs.cur_events[since:]:
        k = ev.get("k")
        if k == "roll" and ev.get("by") == who and not out["rolled"]:
            out.update(rolled=True, total=ev.get("total"), dv=ev.get("dv"), crit=ev.get("crit") or "", hit=bool(ev.get("hit")))
        elif k == "hit" and ev.get("by") == who and "dmg" not in out:
            out.update(dmg=int(ev.get("dmg", 0) or 0), down=ev.get("down") or "", to=ev.get("to"))
        elif k in ("dodge", "ghost", "deflect") and "dmg" not in out:
            out["hit"], out["dodged"] = False, k
    out["events"] = [dict(ev) for ev in cs.cur_events[since:]]
    return out


def player_strike(cs: cb.CombatState, content: Content, rng: random.Random, idx: int, weapon: str, melee: bool,
                  now: float, tick_s: float) -> dict[str, Any]:
    """A projectile (or a blade) of the player reached enemy `idx`: one attack, as in the text."""
    cb._require_open(cs)
    if cs.reinforced:
        raise cb.CombatError("reinforced")
    if not 0 <= idx < len(cs.enemies) or not cs.enemies[idx].active:
        raise cb.CombatError("no_target")
    advance(cs, content, rng, now, tick_s)
    cb._require_open(cs)
    rt = _rt(cs, now)
    atk, attempts = _choose(cs, content, weapon, melee)
    used = int(rt.get("p", 0))
    if used >= max(1, attempts):
        raise cb.CombatError("cooldown", left=tick_left(cs, now, tick_s))
    p = cs.player
    first = used == 0
    if first:
        cb._begin_actions(cs, p, content)
    rt["p"] = used + 1
    fx = cb._fx(p, content)
    since = len(cs.cur_events)
    target = cs.enemies[idx]
    before = target.hp
    cb._strike(cs, content, rng, p, target, atk, fx, first)
    if target.hp < before:
        cb._blackwall(cs, content, rng, p, idx, atk, before - target.hp)
    cs.used[cb.RANGED if atk.kind == cb.RANGED else cb.MELEE] += 1
    cb._end_actions(cs, p, content)
    cb.check_end(cs)
    if first and not cs.outcome:
        cb._morale(cs, content, rng)
        cb.check_end(cs)
    out = _result(cs, since, p.id)
    out.update(left=max(0, attempts - rt["p"]), attempts=attempts, weapon=atk.source, kind=atk.kind)
    return out


def enemy_strike(cs: cb.CombatState, content: Content, rng: random.Random, idx: int, melee: bool,
                 now: float, tick_s: float) -> dict[str, Any]:
    """A projectile (or a blow) of enemy `idx` reached the player: one attack of that enemy, as in its text turn."""
    cb._require_open(cs)
    advance(cs, content, rng, now, tick_s)
    cb._require_open(cs)
    if not 0 <= idx < len(cs.enemies) or not cs.enemies[idx].active:
        raise cb.CombatError("no_target")
    e = cs.enemies[idx]
    if e.stun > 0:
        raise cb.CombatError("stunned")
    kind = cb.MELEE if melee else cb.RANGED
    if kind not in e.attacks:
        kind = cb.RANGED if cb.RANGED in e.attacks else cb.MELEE
    atk, attempts = cb._attacks_of(cs, e, kind, content)
    rt = _rt(cs, now)
    used = int((rt.get("e") or {}).get(e.id, 0))
    if used >= max(1, attempts):
        raise cb.CombatError("cooldown", left=tick_left(cs, now, tick_s))
    first = used == 0
    if first:
        cb._begin_actions(cs, e, content)
    rt.setdefault("e", {})[e.id] = used + 1
    target = cs.player
    guard = cb.cover_guard(cs)  # a hired player covering the employer takes the blow, as in the text
    if guard is not None:
        target = guard
    since = len(cs.cur_events)
    cb._strike(cs, content, rng, e, target, atk, cb._fx(e, content), first)
    cb._end_actions(cs, e, content)
    cb.check_end(cs)
    out = _result(cs, since, e.id)
    out.update(kind=atk.kind, attempts=attempts)
    return out
