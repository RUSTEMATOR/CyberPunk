"""Chrome abilities that are felt (owner request 27.10: «сделай, чтобы регенерация и все остальные способности
ощущались по-настоящему»; docs/PLAN_ABILITIES_REAL.md). Pure functions, randomness only through `rng`.

Every ability belongs to a family (balance.chrome_abilities[key].family, else the key itself); a family has its spec
in `balance.chrome_power.families`:
- in checks: +1 / +2 by rank to `check.<ctx>` of its contexts, all abilities together capped per context;
- in a fight: a free action (one a tick, its own cooldown in ticks) — heal, strike, stun, buffs, escape...;
- the mend family also heals a little at the end of every tick of a fight.
"""

from __future__ import annotations

import html
import random
from typing import Any, Iterator

from app.content.loader import Content
from app.engine import abilities as ab_eng
from app.engine import combat as cb
from app.engine.state import CharacterState

CTX_READY = "ab_ready"   # family -> the tick it is ready again
CTX_TICK = "ab_tick"     # the tick an ability was used (one a tick)
CTX_BUFF = "ab_buff"     # {"def": [n, until tick], "dr": [n, until tick]}
CTX_FLEE = "ab_flee"     # DV off the flee check
CTX_TALK = "ab_talk"     # DV off the talk and bribe checks
CTX_REGEN = "ab_regen"   # hp back at the end of every tick

FIGHT_WORDS = {
    "heal": "лечит", "stim": "лечит и бодрит", "strike": "бьёт", "double": "ещё одна атака", "aoe": "бьёт по всем",
    "stun": "цель пропускает ход", "weaken": "штраф цели", "weaken_all": "штраф всем врагам", "fear": "пугает",
    "guard": "по тебе труднее попасть", "shield": "урон по тебе меньше", "aim": "следующий удар точнее",
    "hack": "ОЗУ и скан", "escape": "легче сбежать", "talk": "легче договориться", "repair": "перезаряжает хром",
}
CTX_WORDS = {
    "combat": "бой", "social": "разговоры", "escape": "побег", "gig_quiet": "тихие заказы", "career": "работа",
    "gig_hack": "взлом", "talk_exit": "договориться в бою", "heal": "лечение", "crime": "кражи и криминал",
    "bribe": "взятки", "gig_loud": "громкие заказы", "gig_talk": "заказы-переговоры",
}


def _n(name: str) -> str:
    return html.escape(str(name), quote=False)


def cfg(content: Content):  # noqa: ANN201
    return content.balance.chrome_power


def spec(content: Content, fam: str):  # noqa: ANN201
    return cfg(content).families.get(fam)


def size(sp: Any, rank: int) -> int:
    return int(sp.n) + int(sp.per_rank) * max(0, rank - 1)


def families(st: CharacterState, content: Content) -> dict[str, tuple[int, str]]:
    """Family -> (best rank, the ability key that gives it) for the character's chrome."""
    rk = ab_eng.ranks(st, content)
    out: dict[str, tuple[int, str]] = {}
    for key in ab_eng.owned(st, content):
        fam = ab_eng.family(content, key)
        if fam not in cfg(content).families:
            continue
        r = max(1, int(rk.get(key, 1)))
        if fam not in out or r > out[fam][0]:
            out[fam] = (r, key)
    return out


def mod_sources(st: CharacterState, content: Content) -> Iterator[tuple[str, dict[str, int]]]:
    """The passive check bonuses of the abilities (owner request 27.10)."""
    c = cfg(content)
    if not c.enabled or not st.cyberware:
        return
    table = list(c.check_by_rank)
    total: dict[str, int] = {}
    for fam, (rank, _) in families(st, content).items():
        bonus = table[min(rank, len(table) - 1)] if table else 0
        for ctx in spec(content, fam).ctx:
            total[f"check.{ctx}"] = min(c.check_cap, total.get(f"check.{ctx}", 0) + bonus)
    total = {k: v for k, v in total.items() if v}
    if total:
        yield "chrome_power", total


def checks_line(content: Content, fam: str, rank: int) -> str:
    sp = spec(content, fam)
    table = list(cfg(content).check_by_rank)
    bonus = table[min(rank, len(table) - 1)] if table else 0
    words = [CTX_WORDS.get(x, x) for x in (sp.ctx if sp else [])]
    return f"+{bonus} к проверкам: {', '.join(words)}" if words and bonus else ""


def fight_line(content: Content, fam: str, rank: int) -> str:
    """What the button does in a fight, in a few words with numbers."""
    sp = spec(content, fam)
    if sp is None or not sp.fight:
        return ""
    n = size(sp, rank)
    k = sp.fight
    if k == "heal":
        return f"лечит {n}% здоровья" + (", союзникам половину" if sp.allies else "")
    if k == "stim":
        return f"лечит {n}% здоровья, следующий удар +{sp.aim}"
    if k == "strike":
        extra = [f"урон {sp.dice}+{sp.per_rank * max(0, rank - 1)}" if sp.per_rank and rank > 1 else f"урон {sp.dice}"]
        if sp.pierce:
            extra.append("сквозь броню")
        if sp.stun:
            extra.append("оглушает")
        if sp.n and sp.ticks:
            extra.append(f"штраф цели −{sp.n} на {sp.ticks} х.")
        return ("выстрел дрона: " if sp.ranged else "удар: ") + ", ".join(extra)
    if k == "double":
        return "ещё одна атака твоим оружием в этот же ход"
    if k == "aoe":
        plus = sp.per_rank * max(0, rank - 1)
        bits = [f"урон {sp.dice}{f'+{plus}' if plus else ''} каждому"]
        if sp.pierce:
            bits.append("сквозь броню")
        if sp.stun:
            bits.append("все теряют ход")
        if sp.n:
            bits.append(f"штраф −{sp.n} на {sp.ticks} х.")
        return ", ".join(bits)
    if k == "stun":
        return f"цель пропускает {stun_turns(sp, rank)} х."
    if k == "weaken":
        return f"цель бьёт хуже: −{n} на {sp.ticks} х." + (f", твой удар +{sp.aim}" if sp.aim else "")
    if k == "weaken_all":
        return f"все враги бьют хуже: −{n} на {sp.ticks} х."
    if k == "fear":
        return f"все враги −{n} на {sp.ticks} х., раненые могут сбежать"
    if k == "guard":
        return f"по тебе труднее попасть: +{n} на {sp.ticks} х." + (f", удар +{sp.aim}" if sp.aim else "")
    if k == "shield":
        return f"урон по тебе −{n} на {sp.ticks} х." + (f", удар +{sp.aim}" if sp.aim else "")
    if k == "aim":
        return f"следующий удар +{n}"
    if k == "hack":
        return f"ОЗУ +{n}% и скан хрома врагов" + (f", удар +{sp.aim}" if sp.aim else "")
    if k == "escape":
        return f"побег легче на {n} до конца боя"
    if k == "talk":
        return f"договориться или откупиться легче на {n}"
    if k == "repair":
        return f"импланты снова заряжены, урон по тебе −{n} на {sp.ticks} х."
    return FIGHT_WORDS.get(k, k)


def stun_turns(sp: Any, rank: int) -> int:
    return max(1, int(sp.n or 1) + (1 if rank >= 4 else 0))


def cooldown(content: Content, fam: str, rank: int) -> int:
    sp = spec(content, fam)
    cd = int(sp.cd if sp else 4)
    if cd >= 99:
        return 999
    return max(1, cd - (1 if rank >= cfg(content).cd_cut_at else 0))


def ready_in(cs: cb.CombatState, fam: str) -> int:
    """Ticks until the family can be used again (0 = now)."""
    at = int((cs.ctx.get(CTX_READY) or {}).get(fam, 0) or 0)
    return max(0, at - cs.tick)


def used_this_tick(cs: cb.CombatState) -> bool:
    return int(cs.ctx.get(CTX_TICK, -1) or -1) == cs.tick


# -- hooks of the fight engine -------------------------------------------------------------------------------------


def _buff(cs: cb.CombatState, key: str) -> int:
    rec = (cs.ctx.get(CTX_BUFF) or {}).get(key)
    if not rec:
        return 0
    n, until = int(rec[0]), int(rec[1])
    return n if cs.tick <= until else 0


def def_bonus(cs: cb.CombatState) -> int:
    return _buff(cs, "def")


def dr_bonus(cs: cb.CombatState) -> int:
    return _buff(cs, "dr")


def prime(cs: cb.CombatState, st: CharacterState, content: Content) -> None:
    """Passive parts that work all fight long (the mend family heals every tick)."""
    if CTX_REGEN in cs.ctx or not cfg(content).enabled:
        return
    fams = families(st, content)
    cs.ctx[CTX_REGEN] = int(cfg(content).regen_per_rank) * fams["mend"][0] if "mend" in fams else 0


def end_of_tick(cs: cb.CombatState) -> None:
    n = int(cs.ctx.get(CTX_REGEN, 0) or 0)
    p = cs.player
    if n > 0 and 0 < p.hp < p.max_hp and not p.out:
        heal = min(n, p.max_hp - p.hp)
        p.hp += heal
        cb._emit(cs, k="ability", by=p.id, txt=f"🧬 Наноботы затягивают раны: здоровье +{heal}.")  # noqa: SLF001


# -- using an ability in a fight ------------------------------------------------------------------------------------


def _set_buff(cs: cb.CombatState, key: str, n: int, ticks: int) -> None:
    buffs = dict(cs.ctx.get(CTX_BUFF) or {})
    cur = buffs.get(key)
    until = cs.tick + max(1, ticks) - 1
    if cur and int(cur[1]) >= cs.tick:
        n = max(n, int(cur[0]))
        until = max(until, int(cur[1]))
    buffs[key] = [n, until]
    cs.ctx[CTX_BUFF] = buffs


def _weaken(e: cb.Combatant, n: int, ticks: int) -> None:
    e.penalty = max(e.penalty if e.penalty_ticks > 0 else 0, n)
    e.penalty_ticks = max(e.penalty_ticks, ticks)


def use(cs: cb.CombatState, content: Content, rng: random.Random, st: CharacterState, fam: str,
        target: int) -> list[str]:
    """Use the family's ability as a free action of this tick. Lines for the screen; CombatError on a refusal."""
    cb._require_open(cs)  # noqa: SLF001
    c = cfg(content)
    sp = spec(content, fam)
    fams = families(st, content)
    if not c.enabled or sp is None or not sp.fight or fam not in fams:
        raise cb.CombatError("ab_none")
    if cs.reinforced:
        raise cb.CombatError("reinforced")
    if used_this_tick(cs):
        raise cb.CombatError("ab_tick")
    if ready_in(cs, fam) > 0:
        raise cb.CombatError("ab_cd")
    rank, key = fams[fam]
    ab = content.balance.chrome_abilities[key]
    p = cs.player
    n = size(sp, rank)
    foes = cs.active_enemies()
    idx = cb._next_target(cs, target)  # noqa: SLF001
    e = cs.enemies[idx] if idx is not None else None
    head = f"{ab.icon} {ab.name}"
    lines: list[str] = []
    k = sp.fight
    if k in ("heal", "stim"):
        if p.hp >= p.max_hp and not (sp.allies and any(a.active and a.hp < a.max_hp for a in cs.allies)):
            raise cb.CombatError("full_hp")
        heal = min(p.max_hp - p.hp, max(1, p.max_hp * n // 100))
        p.hp += heal
        lines.append(f"{head}: здоровье +{heal}.")
        if sp.allies:
            for a in cs.allies:
                if a.active and a.hp < a.max_hp:
                    h = min(a.max_hp - a.hp, max(1, a.max_hp * n // 200))
                    a.hp += h
                    lines.append(f"{_n(a.name)}: +{h}.")
        if k == "stim" and sp.aim:
            p.next_bonus += int(sp.aim)
            lines.append(f"Стимулятор: следующий удар +{sp.aim}.")
    elif k == "strike":
        if e is None:
            raise cb.CombatError("no_target")
        bonus = int(sp.per_rank) * max(0, rank - 1)
        kind = cb.RANGED if sp.ranged else cb.MELEE
        lethal = not cs.nonlethal
        dmg = cb._hit(cs, content, rng, p, e, sp.dice or "2d6", bonus, lethal, kind=kind, pierce=bool(sp.pierce),  # noqa: SLF001
                      dodgeable=bool(sp.ranged))
        lines.append(f"{head}: {_n(e.name)} −{dmg}." if dmg else f"{head}: {_n(e.name)} уходит от удара.")
        if e.active and sp.stun:
            e.stun = max(e.stun, int(sp.stun))
            lines.append(f"{_n(e.name)}: оглушение, пропустит ход.")
        if e.active and sp.n and sp.ticks:
            _weaken(e, int(sp.n), int(sp.ticks))
            lines.append(f"{_n(e.name)} бьёт хуже: −{sp.n}.")
    elif k == "double":
        if e is None:
            raise cb.CombatError("no_target")
        kind = cb.RANGED if cb.RANGED in p.attacks else cb.MELEE
        if kind not in p.attacks:
            raise cb.CombatError("no_weapon")
        cb._begin_actions(cs, p, content)  # noqa: SLF001
        atk, _ = cb._attacks_of(cs, p, kind, content)  # noqa: SLF001
        before = e.hp
        cb._strike(cs, content, rng, p, e, atk, cb._fx(p, content), True)  # noqa: SLF001
        cb._end_actions(cs, p, content)  # noqa: SLF001
        lines.append(f"{head}: мир замедляется, лишняя атака — {_n(e.name)} −{before - e.hp}." if e.hp < before
                     else f"{head}: лишняя атака уходит мимо.")
    elif k == "aoe":
        if not foes:
            raise cb.CombatError("no_target")
        bonus = int(sp.per_rank) * max(0, rank - 1)
        bits = []
        for _, x in list(foes):
            dmg = cb._hit(cs, content, rng, p, x, sp.dice or "1d6", bonus, not cs.nonlethal, kind=cb.MELEE,  # noqa: SLF001
                          pierce=bool(sp.pierce))
            bits.append(f"{_n(x.name)} −{dmg}")
            if x.active and sp.stun:
                x.stun = max(x.stun, int(sp.stun))
            if x.active and sp.n:
                _weaken(x, int(sp.n), int(sp.ticks))
        lines.append(f"{head}: " + ", ".join(bits) + ".")
        if sp.stun:
            lines.append("Враги теряют ход.")
    elif k == "stun":
        if e is None:
            raise cb.CombatError("no_target")
        t = stun_turns(sp, rank)
        e.stun = max(e.stun, t)
        lines.append(f"{head}: {_n(e.name)} пропустит {t} х.")
    elif k == "weaken":
        if e is None:
            raise cb.CombatError("no_target")
        _weaken(e, n, int(sp.ticks))
        lines.append(f"{head}: {_n(e.name)} бьёт хуже, −{n} на {sp.ticks} х.")
        if sp.aim:
            p.next_bonus += int(sp.aim)
            lines.append(f"Ты видишь, куда бить: следующий удар +{sp.aim}.")
    elif k in ("weaken_all", "fear"):
        if not foes:
            raise cb.CombatError("no_target")
        for _, x in foes:
            _weaken(x, n, int(sp.ticks))
        lines.append(f"{head}: все враги бьют хуже, −{n} на {sp.ticks} х.")
        if k == "fear":
            chance = 0.2 + 0.1 * rank
            for _, x in foes:
                if x.active and not x.leader and x.hp * 2 <= x.max_hp and rng.random() < chance:
                    x.out = cb.OUT_FLED
                    cb._emit(cs, k="flee", by=x.id, why="fear")  # noqa: SLF001
                    lines.append(f"{_n(x.name)} не выдерживает и бежит.")
            cb.check_end(cs)
    elif k in ("guard", "shield", "repair"):
        if k == "repair":
            for imp in p.implants:
                imp.charge = 100
                imp.disabled_ticks = 0
            lines.append(f"{head}: импланты снова заряжены.")
        _set_buff(cs, "def" if k == "guard" else "dr", n, int(sp.ticks))
        if k == "guard":
            lines.append(f"{head}: по тебе труднее попасть, +{n} на {sp.ticks} х.")
        else:
            lines.append(f"{head}: урон по тебе −{n} на {sp.ticks} х." if k == "shield" else f"Урон по тебе −{n}.")
        if sp.aim:
            p.next_bonus += int(sp.aim)
            lines.append(f"Следующий удар +{sp.aim}.")
    elif k == "aim":
        p.next_bonus += n
        lines.append(f"{head}: следующий удар +{n}.")
    elif k == "hack":
        if p.ram_max > 0:
            got = min(p.ram_max - p.ram, max(1, p.ram_max * n // 100))
            p.ram += got
            lines.append(f"{head}: ОЗУ +{got}." if got else f"{head}: ОЗУ и так полная.")
        else:
            lines.append(f"{head}:")
        p.scan = True
        lines.append("Хром врагов как на ладони.")
        if sp.aim:
            p.next_bonus += int(sp.aim)
            lines.append(f"Следующий удар +{sp.aim}.")
    elif k == "escape":
        cs.ctx[CTX_FLEE] = max(int(cs.ctx.get(CTX_FLEE, 0) or 0), n)
        lines.append(f"{head}: путь отхода готов, сбежать легче на {n}.")
    elif k == "talk":
        cs.ctx[CTX_TALK] = max(int(cs.ctx.get(CTX_TALK, 0) or 0), n)
        lines.append(f"{head}: договориться или откупиться легче на {n}.")
    else:
        raise cb.CombatError("ab_none")
    ready = dict(cs.ctx.get(CTX_READY) or {})
    ready[fam] = cs.tick + cooldown(content, fam, rank)
    cs.ctx[CTX_READY] = ready
    cs.ctx[CTX_TICK] = cs.tick
    cb._emit(cs, k="ability", by=p.id, txt=" ".join(lines))  # noqa: SLF001
    cb.check_end(cs)
    return lines
