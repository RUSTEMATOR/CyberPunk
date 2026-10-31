"""Ambitions (owner request 25.10, docs/PLAN_ENDGAME.md part 2): one big goal at a time, a chain of stages over weeks.

flag `amb` = {"key", "stage", "since" (weeks_lived when the stage opened), "target" (a megacorp), "c": {raids, wars, tower}}
flag `amb_done` = [{"key", "title"}] — the legend titles of the character.
Counters: a won raid on the target (services/corpraid.py), a won war of the own gang (services/army.py).
"""

from __future__ import annotations

import random

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.engine import checks
from app.engine.effects import apply_effects
from app.engine.state import CharacterState
from app.services.game import ActiveCharacter, save_active

FLAG, DONE = "amb", "amb_done"


def cfg(content: Content):  # noqa: ANN201
    return content.balance.ambitions


def get(content: Content, key: str):  # noqa: ANN201
    return next((a for a in cfg(content).items if a.key == key), None)


def current(st: CharacterState) -> dict | None:
    r = st.flags.get(FLAG)
    return dict(r) if isinstance(r, dict) and r.get("key") else None


def done(st: CharacterState) -> list[dict]:
    return [dict(x) for x in (st.flags.get(DONE) or []) if isinstance(x, dict)]


def titles(st: CharacterState) -> list[str]:
    return [str(x.get("title")) for x in done(st) if x.get("title")]


def count(st: CharacterState, what: str, target: str = "") -> None:
    """A counter of the current ambition: a won raid on its target, a won war, the target's tower."""
    r = current(st)
    if r is None or (target and r.get("target") != target):
        return
    c = dict(r.get("c") or {})
    c[what] = int(c.get(what, 0) or 0) + 1
    r["c"] = c
    st.flags[FLAG] = r


async def _world(session: AsyncSession, content: Content, st: CharacterState) -> dict:
    from app.engine import control as ce
    from app.services import control

    state = await control.load(session, content)
    here = state.get(st.district) or {}
    return {"here_gang": here.get(ce.GANG, 100), "here_corp": here.get(ce.CORP, 100),
            "city_gang": ce.city_avg(state, ce.GANG), "city_corp": ce.city_avg(state, ce.CORP)}


def stage_status(content: Content, st: CharacterState, r: dict, world: dict) -> tuple[bool, str]:
    """(satisfied, what the player sees: the progress)."""
    a = get(content, r["key"])
    return kind_status(content, st, a.stages[int(r.get("stage", 0))], r, world)


def kind_status(content: Content, st: CharacterState, s, r: dict, world: dict) -> tuple[bool, str]:  # noqa: ANN001
    """A stage of an ambition or an ending: (satisfied, the progress the player sees)."""
    from app.engine import army

    c = r.get("c") or {}
    k, v = s.kind, s.value
    if k == "pay":
        return st.eddies >= v, f"нужно {v} эдди"
    if k == "check":
        from app.bot.views import SKILL_LABELS, STAT_LABELS

        skill = f" + {SKILL_LABELS.get(s.skill, s.skill).lower()}" if s.skill else ""
        return True, f"проверка {STAT_LABELS.get(s.stat or '', s.stat)}{skill}, сложность {s.dv}, {cfg(content).check_ap} ОД"
    if k == "wait":
        left = v - (st.weeks_lived - int(r.get("since", st.weeks_lived)))
        return left <= 0, ("время прошло" if left <= 0 else f"ещё {left} нед")
    if k == "army":
        n = len(army.soldiers(st))
        return n >= v, f"бойцов {n}/{v}"
    if k == "free_district":
        g, p = world.get("here_gang", 100), world.get("here_corp", 100)
        return g <= v and p <= v, f"твой район: банды {g}%, корпорации {p}% (нужно ≤{v}%)"
    if k == "city_gang":
        return world.get("city_gang", 100) <= v, f"банды по городу {world.get('city_gang')}% (нужно ≤{v}%)"
    if k == "city_corp":
        return world.get("city_corp", 100) <= v, f"корпорации по городу {world.get('city_corp')}% (нужно ≤{v}%)"
    if k == "fame":
        return st.fame >= v, f"слава {st.fame}/{v}"
    if k == "humanity":
        return st.humanity >= v, f"человечность {st.humanity}/{v}"
    if k == "cred":
        return st.street_cred >= v, f"репутация {st.street_cred}/{v}"
    if k == "child":
        ok = bool(st.npcs_of("child"))
        return ok, "ребёнок есть" if ok else "нужен ребёнок"
    if k == "raids":
        n = int(c.get("raids", 0) or 0)
        return n >= v, f"налётов на цель {n}/{v}"
    if k == "wars":
        n = int(c.get("wars", 0) or 0)
        return n >= v, f"выигранных войн {n}/{v}"
    if k == "tower":
        n = int(c.get("tower", 0) or 0)
        return n >= 1, "башня цели взята" if n else "штурм башни цели ещё впереди"
    return False, ""


async def status(session: AsyncSession, content: Content, ch: ActiveCharacter) -> tuple[dict | None, bool, str]:
    r = current(ch.state)
    if r is None:
        return None, False, ""
    ok, txt = stage_status(content, ch.state, r, await _world(session, content, ch.state))
    return r, ok, txt


async def start(session: AsyncSession, content: Content, ch: ActiveCharacter, tz: str, key: str,
                target: str = "") -> str | None:
    a = get(content, key)
    if a is None:
        return "gone"
    if current(ch.state) is not None:
        return "busy_amb"
    if any(x.get("key") == key for x in done(ch.state)):
        return "done"
    if a.target and target not in content.corps:
        return "target"
    ch.state.flags[FLAG] = {"key": key, "stage": 0, "since": ch.state.weeks_lived, "target": target, "c": {}}
    await save_active(session, ch, tz)
    return None


async def drop(session: AsyncSession, ch: ActiveCharacter, tz: str) -> None:
    ch.state.flags.pop(FLAG, None)
    await save_active(session, ch, tz)


async def advance(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                  tz: str) -> tuple[str | None, list[str], object | None]:
    """Do the current stage (pay, the check) or confirm it (the rest). Returns (blocked, lines, check)."""
    from app.services import ap as ap_services
    from app.services import loud

    st = ch.state
    r = current(st)
    if r is None:
        return "none", [], None
    a = get(content, r["key"])
    s = a.stages[int(r.get("stage", 0))]
    ok, txt = stage_status(content, st, r, await _world(session, content, st))
    chk = None
    if s.kind == "check":
        if not ap_services.can_spend(ch, cfg(content).check_ap):
            return "ap", [], None
        ap_services.spend(ch, content, cfg(content).check_ap)
        chk = checks.roll_check(st, content, rng, s.stat or "COOL", s.skill, s.dv, ())
        if not chk.ok:
            await save_active(session, ch, tz)
            return None, [f"«{s.name}»: не вышло. Можно пробовать снова."], chk
    elif not ok:
        return "not_yet", [f"«{s.name}»: {txt}."], None
    elif s.kind == "pay":
        apply_effects(st, {"eddies": -s.value}, content, rng, year)
    lines = [f"✅ {s.name}."]
    r["stage"] = int(r.get("stage", 0)) + 1
    r["since"] = st.weeks_lived
    if r["stage"] >= len(a.stages):
        st.flags.pop(FLAG, None)
        st.flags[DONE] = done(st) + [{"key": a.key, "title": a.title}]
        if a.reward:
            apply_effects(st, dict(a.reward), content, rng, year)
        lines.append(f"🏆 Амбиция исполнена: «{a.name}». Титул легенды: {a.title}.")
        from app.services import season  # owner request 25.10

        await season.add_ch(session, content, ch, "ambition", content.balance.season.pts.get("ambition", 0))
        await loud.post(session, ch.id, f"🏆 {loud.who(st.name, st.surname)} исполняет амбицию «{a.name}». "
                                        f"Город называет это имя иначе: {a.title}.", kind="ambition", w=3)
    else:
        st.flags[FLAG] = r
        lines.append(f"Дальше: «{a.stages[r['stage']].name}».")
    await save_active(session, ch, tz)
    return None, lines, chk
