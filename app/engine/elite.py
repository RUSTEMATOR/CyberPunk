"""The elite fight (owner request 25.10 «все мои бои заканчиваются за один удар», docs/PLAN_ELITE.md part 2). Pure.

A layer over engine/combat.py, switched on by `cs.ctx["elite"]`:
- intents: every elite enemy shows what it does next tick (after Slay the Spire), so the player decides;
- roles: shield (the others stand behind it: their damage taken ×0.6), sniper (aims a tick, then fires ×3; cover saves),
  medic (heals the most wounded), boss (a barrier eats damage first; phases at 2/3 and 1/3 of health: the barrier is
  back and help comes, then rage ×1.5);
- a resistance: the boss takes half damage of one kind (ranged, melee or hacks), the window says which;
- the health floor: no elite enemy dies to one blow of the character (scaled to the character's damage per tick);
- objectives: protect a person (an ally `vip`: enemies strike at it, «Прикрыть» takes the blows), waves (a cleared
  room brings the next one, prebuilt in ctx).
"""

from __future__ import annotations

import random
from dataclasses import asdict
from typing import Any

from app.content.loader import Content
from app.engine import combat as cb
from app.engine import dice

ROLES = ("shield", "sniper", "medic")
RESIST_KINDS = {"ranged": (cb.RANGED, "rocket"), "melee": (cb.MELEE, cb.MELEE_NL), "hack": ("hack", "blackwall")}
RESIST_RU = {"ranged": "огнестрел", "melee": "ближний бой", "hack": "взломы"}
SHIELD_CUT = 0.6
SNIPE_PCT = 0.18               # an aimed shot: a quarter of the target's max health, no dodge, half in cover
HEAVY_PCT = 0.12               # the boss's heavy blow: no dodge
RAGE_MULT = 1.5
VIP_CHANCE = 0.2
HEAL_PCT = 25
MAX_ROOM = 5
MIN_ROOM = 4
REGULAR_FLOOR = 1.3            # an elite fighter survives one tick of the character's damage
THREAT_TICK = 0.2             # the room's damage a tick, a share of the character's max health after armor


def on(cs: cb.CombatState) -> dict | None:
    e = cs.ctx.get("elite")
    return e if isinstance(e, dict) else None


def armor_for(p: cb.Combatant) -> int:
    best = 0.0
    for a in list(p.attacks.values()) + list(p.arsenal.values()):
        if a.lethal:
            best = max(best, dice.dice_average(a.dice) + (p.ranged_damage if a.kind == cb.RANGED else p.melee_damage))
    return int(best * 0.2)


def _hit_chance(base: int, dv: int) -> float:
    """base + d10 against dv (a roll of 1..10)."""
    return max(0.1, min(1.0, (11 - (dv - base)) / 10))


def player_dpt(p: cb.Combatant, cut: int = 0, dv: int = 0, content: Content | None = None,
               perks: dict | None = None) -> int:
    """What the character really deals in one tick with the best weapon: (average dice + bonus − the enemies'
    armor) × attempts × the chance to hit their defense, plus the second weapon in the other hand."""

    def one(a: cb.Attack, pen: int = 0, attempts: int | None = None) -> float:
        dmg = max(1, dice.dice_average(a.dice) + (p.ranged_damage if a.kind == cb.RANGED else p.melee_damage) - cut)
        hit = _hit_chance(a.base - pen + p.check_bonus, dv) if dv else 0.75
        return dmg * max(1, a.attempts if attempts is None else attempts) * hit

    best = 0.0
    for a in list(p.attacks.values()) + list(p.arsenal.values()):
        if not a.lethal:
            continue
        total = one(a)
        off = cb.offhand(content, p, a) if content is not None and a.source else None
        if off is not None:  # owner request 25.10: two blades or two pistols strike twice a tick
            skill = int((perks or {}).get("dual_guns" if off.kind == cb.RANGED else "dual_blades", 0))
            pen = 0 if skill else content.balance.combat.dual_penalty
            total += one(off, pen, 1) * (2 if skill >= 2 else 1)
        best = max(best, total)
    return max(6, int(best))


def _floor(e: cb.Combatant, hp: int) -> None:
    """The elite's health is set by the character's damage (not by the archetype or the challenge scaling)."""
    e.max_hp = e.hp = max(1, hp)


def setup(cs: cb.CombatState, content: Content, rng: random.Random, year: int, *, boss: str = "",
          vip: str = "", vip_hp: int = 0, waves: int = 1, ticks: int = 5, room: int = MIN_ROOM,
          threat_tick: float = THREAT_TICK) -> None:
    """Make a fresh fight elite. `ticks`: how long the boss should last against the character's damage; `room`: the
    fewest enemies in the room; `threat_tick`: the room's damage a tick, a share of the character's max health."""
    tries = 0
    room = min(MAX_ROOM, max(1, room))
    while len(cs.enemies) < room and tries < 5:  # owner request 25.10: an elite room has a team, not one guard
        tries += 1
        for en in cb.build_pack(cs.faction, cs.danger, rng, content, year)[: room - len(cs.enemies)]:
            cs.enemies.append(cb.build_enemy(en, len(cs.enemies), content))
    if len(cs.enemies) > MAX_ROOM:  # owner request 25.10: an elite room is a few strong ones, not a crowd
        keep = sorted(cs.enemies, key=lambda e: -e.max_hp)[:MAX_ROOM]
        cs.enemies = [e for e in cs.enemies if e in keep]
        for i, e in enumerate(cs.enemies):
            e.id = f"e{i}"
        cs.pack_size = len(cs.enemies)
    foes = cs.enemies or []
    p = cs.player
    top = max((a.base for a in list(p.attacks.values()) + list(p.arsenal.values())), default=10) + p.check_bonus
    for e in foes:  # nobody misses an elite all fight long: the defense is capped by the character's best attack
        e.defense = min(e.defense, top + 4)
    soak = armor_for(p)
    for e in foes:  # the elite's armor eats about a fifth of the character's hit, whatever the build
        e.armor, e.damage_reduction = 0, soak
    cut = soak
    dv = sum(e.defense for e in foes) // max(1, len(foes))
    dpt = player_dpt(p, cut, dv, content, cs.ctx.get("perks"))
    state: dict[str, Any] = {"year": year, "dpt": dpt, "wave": 1, "waves": [], "rage": False, "threat": threat_tick}
    _make_elite(cs, rng, dpt, boss if waves <= 1 else "", ticks)
    for w in range(2, max(1, waves) + 1):  # the next rooms are built now and wait in ctx
        pack = cb.build_pack(cs.faction, cs.danger, rng, content, year)[:MAX_ROOM]
        start = len(cs.enemies)
        built = [cb.build_enemy(en, start + i, content) for i, en in enumerate(pack)]
        mult = 1 + 0.25 * (w - 1)
        for c in built:
            c.max_hp = c.hp = int(dpt * REGULAR_FLOOR * mult)
        _assign_roles(built, rng, boss_name="" if w < waves else (boss or ""), dpt=dpt, ticks=ticks)
        state["waves"].append([asdict(c) for c in built])
    threat(cs, content, cs.enemies, threat_tick)
    for wave in state["waves"]:
        built = [cb._load_combatant(d) for d in wave]  # noqa: SLF001
        for c in built:
            c.armor, c.damage_reduction = 0, soak
        threat(cs, content, built, threat_tick)
        wave[:] = [asdict(c) for c in built]
    if vip:
        idx = len(cs.allies)
        v = cb.build_ally(content, year, 2, idx, vip, rng)
        v.max_hp = v.hp = max(vip_hp, int(cs.player.max_hp * 1.5))  # the person wears the best armor money buys
        v.armor = max(v.armor, cb.armor_effect(cs.player, content))
        v.damage_reduction = max(v.damage_reduction, cs.player.damage_reduction)
        v.name = f"🛡 {vip}"
        cs.allies.append(v)
        state["vip"] = v.id
    cs.ctx["elite"] = state
    plan_intents(cs)


def _assign_roles(enemies: list[cb.Combatant], rng: random.Random, boss_name: str, dpt: int, ticks: int) -> None:
    if not enemies:
        return
    lead = max(enemies, key=lambda e: e.max_hp)
    if boss_name:
        lead.role = "boss"
        lead.name = boss_name
        lead.leader = True
        _floor(lead, int(dpt * ticks * 0.35))
        lead.barrier = lead.barrier_max = int(dpt * ticks * 0.15)
        lead.resist = rng.choice(tuple(RESIST_KINDS))
    others = [e for e in enemies if e is not lead or not boss_name]
    roles = list(ROLES)
    rng.shuffle(roles)
    for e, r in zip(others, roles):
        e.role = r
    for e in enemies:
        if e.role != "boss":
            _floor(e, int(dpt * REGULAR_FLOOR))


def _make_elite(cs: cb.CombatState, rng: random.Random, dpt: int, boss: str, ticks: int) -> None:
    _assign_roles(cs.enemies, rng, boss, dpt, ticks)


def _add_bonus(a: cb.Attack, n: int) -> None:
    if n <= 0:
        return
    if "+" in a.dice:
        head, tail = a.dice.split("+", 1)
        a.dice = f"{head}+{int(tail) + n}" if tail.isdigit() else f"{a.dice}+{n}"
    else:
        a.dice = f"{a.dice}+{n}"


def threat(cs: cb.CombatState, content: Content, enemies: list[cb.Combatant], tick: float = THREAT_TICK) -> None:
    """The elite hits hard enough to matter: the whole room takes about THREAT_TICK of the character's max health
    a tick after armor (the boss a bigger share), so a fight is lost in ~8 ticks without cover and healing."""
    p = cs.player
    cut = cb.armor_effect(p, content) + p.damage_reduction
    n = max(1, len(enemies))
    best = max((a.base for a in list(p.attacks.values()) + list(p.arsenal.values())), default=10) + p.check_bonus
    for e in enemies:
        e.defense = min(e.defense, best + 4)
        share = tick / n * (1.5 if e.role == "boss" else 1.0)
        for a in e.attacks.values():  # the elite's damage is set by the character, not by the archetype or a level
            want = int(p.max_hp * share / max(1, a.attempts)) + cut
            a.dice = f"1d6+{max(0, want - 4)}"
            a.base = p.defense - 4  # about two hits of three land against the character's defense


def plan_intents(cs: cb.CombatState) -> None:
    """What every elite enemy will do on the next tick (shown in the window)."""
    st = on(cs)
    if st is None:
        return
    for i, e in cs.active_enemies():
        if e.role == "sniper":
            e.intent = "fire" if e.aim else "aim"
        elif e.role == "medic":
            hurt = [x for _, x in cs.active_enemies() if x.hp < x.max_hp * 0.7]
            e.intent = "heal" if hurt else "attack"
        elif e.role == "shield":
            others = [x for _, x in cs.active_enemies() if x is not e]
            e.intent = "guard" if others else "attack"  # the last one standing drops the shield and fights
        elif e.role == "boss":
            e.intent = "heavy" if cs.tick % 3 == 2 else "attack"
        else:
            e.intent = ""
    _utility(cs)


# -- utility AI (owner request 26.10, docs/PLAN_LIVING.md stage 3): the rank and file weigh their options ------------

COVER_HP = 0.35        # wounded, a medic alive: wait for the heal behind cover
LAST_COVER_HP = 0.2    # badly wounded, no medic: still hide sometimes
PRESS_HP = 0.35        # the character is bleeding: press harder
PRESS_MULT = 1.25
FOCUS_VIP_HP = 0.6     # the protected person is weak: go for them
FOCUS_ALLY_HP = 0.4    # one of the character's people is weak: finish them


def _focus_target(cs: cb.CombatState) -> cb.Combatant | None:
    st = on(cs) or {}
    vid = st.get("vip")
    allies = cs.active_allies()
    v = next((a for a in allies if a.id == vid), None) if vid else None
    if v is not None:
        return v if v.hp < v.max_hp * FOCUS_VIP_HP else None
    weak = [a for a in allies if a.hp < a.max_hp * FOCUS_ALLY_HP]
    return min(weak, key=lambda a: a.hp / max(1, a.max_hp)) if weak else None


def _utility(cs: cb.CombatState) -> None:
    """Every enemy without a role scores attack, cover, press and focus and takes the best; one hides at a time,
    two focus at most, so a fight never stalls."""
    p = cs.player
    medic = any(x.role == "medic" for _, x in cs.active_enemies())
    focus = _focus_target(cs)
    covered, focusing = [], 0
    for _, e in cs.active_enemies():
        if e.role:
            continue
        hp = e.hp / max(1, e.max_hp)
        score = {"attack": 1.0,
                 "cover": 1.6 if hp < COVER_HP and medic else 1.2 if hp < LAST_COVER_HP else 0.0,
                 "press": 1.5 if p.hp < p.max_hp * PRESS_HP else 0.0,
                 "focus": 1.4 if focus is not None else 0.0}
        if covered:
            score["cover"] = 0.0
        if focusing >= 2:
            score["focus"] = 0.0
        pick = max(score, key=lambda k: (score[k], k == "attack"))
        e.intent = "" if pick == "attack" else pick
        if pick == "cover":
            covered.append(e.id)
        elif pick == "focus":
            focusing += 1
    cs.ctx["el_cover"] = covered


def focus_target(cs: cb.CombatState, e: cb.Combatant) -> cb.Combatant | None:
    if e.intent != "focus" or on(cs) is None:
        return None
    t = _focus_target(cs)
    if t is not None and t.id == (on(cs) or {}).get("vip") and cs.ctx.get("guarding"):
        cb._emit(cs, k="el_guard", by=cb.PLAYER_ID)  # noqa: SLF001
        return cs.player
    return t


def enemy_turn(cs: cb.CombatState, idx: int, e: cb.Combatant, content: Content, rng: random.Random) -> float | None:
    """The elite part of an enemy's turn. None = the turn is spent; a number = attack with this damage multiplier."""
    st = on(cs)
    if st is None:
        return 1.0
    if not e.role:  # the utility AI's picks
        if e.intent == "cover":
            cb._emit(cs, k="el_cover", by=e.id)  # noqa: SLF001
            return None
        return PRESS_MULT if e.intent == "press" else 1.0
    rage = RAGE_MULT if st.get("rage") and e.role == "boss" else 1.0
    if e.role == "sniper":
        if not e.aim:
            e.aim = 1
            cb._emit(cs, k="el_aim", by=e.id)  # noqa: SLF001
            return None
        e.aim = 0
        cs.ctx["_pct"] = SNIPE_PCT * (0.5 if cs.player.cover else 1.0)
        cb._emit(cs, k="el_snipe", by=e.id, cover=cs.player.cover)  # noqa: SLF001
        return 1.0
    if e.role == "medic":
        hurt = sorted((x for _, x in cs.active_enemies() if x.hp < x.max_hp * 0.7), key=lambda x: x.hp / x.max_hp)
        if hurt:
            t = hurt[0]
            heal = max(1, t.max_hp * HEAL_PCT // 100)
            t.hp = min(t.max_hp, t.hp + heal)
            cb._emit(cs, k="el_heal", by=e.id, to=t.id, n=heal)  # noqa: SLF001
            return None
        return 1.0
    if e.role == "boss" and e.intent == "heavy":
        cs.ctx["_pct"] = HEAVY_PCT * rage
        cb._emit(cs, k="el_heavy", by=e.id)  # noqa: SLF001
        return 1.0
    return rage


def absorb(cs: cb.CombatState, defender: cb.Combatant, dmg: int, kind: str) -> int:
    """Damage to an elite enemy: the shield cover, the resistance, then the barrier."""
    if dmg <= 0 or defender.side != "enemy" or on(cs) is None:
        return dmg
    if defender.role != "shield" and any(x.role == "shield" for _, x in cs.active_enemies()):
        dmg = max(1, int(dmg * SHIELD_CUT))
    if defender.id in (cs.ctx.get("el_cover") or []):  # the utility AI: behind cover
        dmg = max(1, dmg // 2)
    if defender.resist and kind in RESIST_KINDS.get(defender.resist, ()):
        dmg = max(1, dmg // 2)
    if defender.barrier > 0:
        take = min(defender.barrier, dmg)
        defender.barrier -= take
        dmg -= take
        cb._emit(cs, k="el_barrier", to=defender.id, n=take, left=defender.barrier)  # noqa: SLF001
    return dmg


def after_damage(cs: cb.CombatState, defender: cb.Combatant, content: Content, rng: random.Random) -> None:
    """The phases of a boss: at 2/3 the barrier comes back and help arrives, at 1/3 the rage."""
    st = on(cs)
    if st is None or defender.role != "boss" or not defender.active:
        return
    if defender.phase == 0 and defender.hp * 3 <= defender.max_hp * 2:
        defender.phase = 1
        defender.barrier = defender.barrier_max
        pack = cb.build_pack(cs.faction, min(cs.danger, 4), rng, content, int(st.get("year", 2077)))[:2]
        for en in pack:
            c = cb.build_enemy(en, len(cs.enemies), content)
            c.max_hp = c.hp = max(1, int(int(st.get("dpt", 10)) * 0.6))
            c.armor, c.damage_reduction = 0, armor_for(cs.player)
            threat(cs, content, [c], float(st.get("threat", THREAT_TICK)))
            cs.enemies.append(c)
        cs.pack_size = len(cs.enemies)
        cb._emit(cs, k="el_phase", by=defender.id, n=1)  # noqa: SLF001
    elif defender.phase == 1 and defender.hp * 3 <= defender.max_hp:
        defender.phase = 2
        st["rage"] = True
        cb._emit(cs, k="el_phase", by=defender.id, n=2)  # noqa: SLF001


def vip_target(cs: cb.CombatState, rng: random.Random, e: cb.Combatant | None = None) -> cb.Combatant | None:
    """The person under protection draws the ordinary blows (an aimed shot and the boss's heavy blow are meant for the
    character); «Прикрыть» takes them on the character."""
    st = on(cs)
    vid = (st or {}).get("vip")
    if not vid or (e is not None and e.intent in ("fire", "heavy")):
        return None
    v = next((a for a in cs.allies if a.id == vid), None)
    if v is None or not v.active or rng.random() >= VIP_CHANCE:
        return None
    if cs.ctx.get("guarding"):
        cb._emit(cs, k="el_guard", by=cb.PLAYER_ID)  # noqa: SLF001
        return cs.player
    return v


def vip_alive(cs: cb.CombatState) -> bool:
    st = on(cs)
    vid = (st or {}).get("vip")
    if not vid:
        return True
    v = next((a for a in cs.allies if a.id == vid), None)
    return v is not None and v.active


def next_wave(cs: cb.CombatState) -> bool:
    """The room is clear: the next wave comes in. False when there is none."""
    st = on(cs)
    if st is None or not st.get("waves"):
        return False
    raw = st["waves"].pop(0)
    start = len(cs.enemies)
    for i, d in enumerate(raw):
        c = cb._load_combatant(d)  # noqa: SLF001
        c.id = f"e{start + i}"
        cs.enemies.append(c)
    cs.pack_size = len(cs.enemies)
    st["wave"] = int(st.get("wave", 1)) + 1
    cb._emit(cs, k="el_wave", n=st["wave"])  # noqa: SLF001
    plan_intents(cs)
    return True


INTENT_RU = {"aim": "🎯 целится: следующий ход точный выстрел (четверть здоровья, не увернуться) — укройся или убей",
             "fire": "💥 стреляет прицельно — укрытие вдвое снижает урон",
             "heal": "💉 будет лечить раненого", "guard": "🛡 держит щит: остальные за ним получают меньше",
             "heavy": "⚡ готовит тяжёлый удар (не увернуться) — укройся, или прикрой охраняемого", "attack": "",
             "cover": "🧱 уходит в укрытие: по нему вдвое слабее, сам не стреляет",
             "press": "🩸 чует кровь: пока ты ранен, бьёт сильнее",
             "focus": "☠ метит в слабого: охраняемого или раненого из твоих"}
ROLE_RU = {"shield": "щитоносец", "sniper": "снайпер", "medic": "медик", "boss": "босс"}
