"""Expeditions to other worlds (owner request 25.10, docs/PLAN_ENDGAME2.md part 4): orbit, the NUSA front, the
Badlands, the Old Net. A chain of stages with its own rules: each world takes away a different part of the build (guns,
chrome, programs, armor, health), so the strongest build of the city has to fight differently. At the end: a big
reward and the expedition's relic, if no one in the city holds it (services/relic.py).

flag `exped` = {"key", "stage", "since" (weeks_lived)}; flag `exped_done` = {key: weeks_lived when finished}.
A lost fight ends the expedition: back home, the stages start over.
"""

from __future__ import annotations

import dataclasses
import random

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.engine import checks
from app.engine import combat as cb
from app.engine.effects import apply_effects
from app.engine.state import CharacterState
from app.services.game import ActiveCharacter, save_active

FLAG, DONE = "exped", "exped_done"
SRC = "exped"


def cfg(content: Content):  # noqa: ANN201
    return content.balance.expeditions


def get(content: Content, key: str):  # noqa: ANN201
    return next((e for e in cfg(content).items if e.key == key), None)


def current(st: CharacterState) -> dict | None:
    r = st.flags.get(FLAG)
    return dict(r) if isinstance(r, dict) and r.get("key") else None


def again_in(content: Content, st: CharacterState, key: str) -> int:
    """Lived weeks until the expedition can be taken again (0 = now)."""
    at = (st.flags.get(DONE) or {}).get(key)
    if at is None:
        return 0
    return max(0, cfg(content).again_weeks - (st.weeks_lived - int(at)))


def status(content: Content, st: CharacterState, r: dict) -> tuple[bool, str]:
    e = get(content, r["key"])
    s = e.stages[int(r.get("stage", 0))]
    if s.kind == "pay":
        return st.eddies >= s.value, f"нужно {s.value} эдди"
    if s.kind == "check":
        from app.bot.texts import SKILL_LABELS, STAT_LABELS

        skill = f" + {SKILL_LABELS.get(s.skill, s.skill).lower()}" if s.skill else ""
        return True, f"проверка {STAT_LABELS.get(s.stat or '', s.stat)}{skill}, сложность {s.dv}, {cfg(content).check_ap} ОД"
    if s.kind == "wait":
        left = s.value - (st.weeks_lived - int(r.get("since", st.weeks_lived)))
        return left <= 0, ("время прошло" if left <= 0 else f"ещё {left} нед")
    return True, f"бой: {s.boss or 'враги'}, {cfg(content).fight_ap} ОД"


async def start(session: AsyncSession, content: Content, ch: ActiveCharacter, tz: str, key: str) -> str | None:
    e = get(content, key)
    if e is None:
        return "gone"
    if current(ch.state) is not None:
        return "busy_exp"
    if again_in(content, ch.state, key):
        return "again"
    ch.state.flags[FLAG] = {"key": key, "stage": 0, "since": ch.state.weeks_lived}
    await save_active(session, ch, tz)
    return None


async def drop(session: AsyncSession, ch: ActiveCharacter, tz: str) -> None:
    ch.state.flags.pop(FLAG, None)
    await save_active(session, ch, tz)


async def _next(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                r: dict, e) -> list[str]:  # noqa: ANN001
    st = ch.state
    s = e.stages[int(r.get("stage", 0))]
    r["stage"] = int(r.get("stage", 0)) + 1
    r["since"] = st.weeks_lived
    lines = [f"✅ {s.name}."]
    if r["stage"] < len(e.stages):
        st.flags[FLAG] = r
        lines.append(f"Дальше: «{e.stages[r['stage']].name}».")
        return lines
    st.flags.pop(FLAG, None)
    done = dict(st.flags.get(DONE) or {})
    done[e.key] = st.weeks_lived
    st.flags[DONE] = done
    if e.reward:
        apply_effects(st, dict(e.reward), content, rng, year)
    lines.append(f"🚀 Экспедиция «{e.name}» пройдена. Награда: {int(e.reward.get('eddies', 0) or 0)} эдди.")
    from app.services import loud, relic, season

    await season.add_ch(session, content, ch, "expedition", content.balance.season.pts.get("expedition", 0))
    if e.relic:
        lines.extend(await relic.grant(session, content, ch, e.relic, f"экспедиция «{e.name}»"))
    await loud.post(session, ch.id, f"🚀 {loud.who(st.name, st.surname)} возвращается из экспедиции «{e.name}».", kind="exped", w=2)
    return lines


async def advance(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                  tz: str):  # noqa: ANN201
    from app.services import ap as ap_services

    st = ch.state
    r = current(st)
    if r is None:
        return "none", [], None
    e = get(content, r["key"])
    if e is None:
        return "none", [], None
    s = e.stages[int(r.get("stage", 0))]
    if s.kind == "fight":
        return await _fight(session, content, ch, rng, year, tz, r, e, s)
    ok, txt = status(content, st, r)
    chk = None
    if s.kind == "check":
        if not ap_services.can_spend(ch, cfg(content).check_ap):
            return "ap", [], None
        ap_services.spend(ch, content, cfg(content).check_ap)
        chk = checks.roll_check(st, content, rng, s.stat or "COOL", s.skill, s.dv, ())
        if not chk.ok:
            out = [f"«{s.name}»: не вышло. Можно пробовать снова."]
            if s.fail:
                fx = dict(s.fail)
                if "hp" in fx:  # a failed check hurts, never kills
                    fx["hp"] = -min(-int(fx["hp"]), max(0, st.hp - 1))
                apply_effects(st, fx, content, rng, year)
                ru = {"hp": "здоровье", "humanity": "человечность", "eddies": "эдди", "mood": "настроение", "heat": "розыск"}
                out.append("Это стоило: " + ", ".join(f"{ru.get(k, k)} {v:+}" for k, v in s.fail.items()) + ".")
            await save_active(session, ch, tz)
            return None, out, chk
    elif not ok:
        return "not_yet", [f"«{s.name}»: {txt}."], None
    elif s.kind == "pay":
        apply_effects(st, {"eddies": -s.value}, content, rng, year)
    lines = await _next(session, content, ch, rng, year, r, e)
    await save_active(session, ch, tz)
    return None, lines, chk


def apply_rules(content: Content, cs: cb.CombatState, st: CharacterState, e) -> None:  # noqa: ANN001
    """The world takes away a part of the build: the player's fighter is rebuilt without it."""
    rules = set(e.rules)
    if rules & {"no_guns", "no_chrome"}:
        inv = [i for i in st.inventory if not ("no_guns" in rules and i.kind == "weapon" and i.key in content.weapons
                                               and content.weapons[i.key].skill == "firearms")]
        tmp = dataclasses.replace(st, inventory=inv, cyberware=[] if "no_chrome" in rules else list(st.cyberware))
        p = cb.build_player(tmp, content)
        p.hp = min(cs.player.hp, p.max_hp)
        if (cs.ctx.get("challenge") or {}).get("counter") == "jammer":  # engine/challenge.scale's jammer stays
            p.check_bonus -= 3
        cs.player = p
    p = cs.player
    if "no_hacks" in rules:
        p.quickhacks, p.ram, p.ram_max, p.ram_recharge = [], 0, 0, 0
    if "half_armor" in rules:
        p.armor //= 2
    if "hp_cap" in rules:
        p.hp = max(1, min(p.hp, int(p.max_hp * e.hp_cap)))


async def _fight(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
                 r: dict, e, s):  # noqa: ANN001, ANN201
    from app.engine import challenge as chal
    from app.engine import jail as jail_eng
    from app.services import ap as ap_services
    from app.services import combat, corpraid
    from app.services import events as ev_service
    from app.services.combat import _store

    if jail_eng.in_jail(ch.state):
        return "jail", [], None
    if ev_service.has_pending(ch):
        return "busy", [], None
    if not ap_services.can_spend(ch, cfg(content).fight_ap):
        return "ap", [], None
    ap_services.spend(ch, content, cfg(content).fight_ap)
    await save_active(session, ch, tz)
    lv = max(2, await corpraid.ladder(session, ch)) + s.lv_plus
    src = {"src": SRC, "exped": e.key, "stage": int(r.get("stage", 0)), "extra": s.extra}
    if e.solo:
        src["solo"] = True
    else:
        src["army_war"] = True
    step = await combat.begin(session, content, ch, rng, year, tz, faction=s.faction, danger=5, src=src)
    chal.scale(step.cs, content, lv, rng.choice(chal.COUNTERS), rng, year, s.boss or "Командир")
    apply_rules(content, step.cs, ch.state, e)
    from app.engine import elite  # owner request 25.10: the elite fight

    elite.setup(step.cs, content, rng, year, boss=s.boss or "Командир", waves=1 + s.lv_plus)
    _store(ch, step.cs)
    await save_active(session, ch, tz)
    return step


async def fight_over(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                     tz: str, result: str, ctx: dict) -> list[str]:
    r = current(ch.state)
    e = get(content, str(ctx.get("exped", "")))
    if r is None or e is None or r.get("key") != e.key or int(r.get("stage", -1)) != int(ctx.get("stage", -2)):
        return []
    if result != cb.WON:
        ch.state.flags.pop(FLAG, None)
        await save_active(session, ch, tz)
        return [f"Экспедиция «{e.name}» сорвана. Дорога домой, всё придётся начинать заново."]
    lines = await _next(session, content, ch, rng, year, r, e)
    await save_active(session, ch, tz)
    return lines
