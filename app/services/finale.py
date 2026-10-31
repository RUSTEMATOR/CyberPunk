"""The endings of a life (owner request 25.10, docs/PLAN_ENDGAME2.md part 2): long chains on the ambition engine with
fight stages, for those the city can no longer hold. The last stage opens the finale: the life closes with its own
cause, the ending goes into the Hall of Legends, and the ending leaves a gift to every next heir of the player.

flag `finale` = {"key", "stage", "since" (weeks_lived), "c": {}, "ready": bool}
flag `finale_done` = [keys] (a life ends with one, but the flag keeps the shape of the ambitions)
world_state `dynasty`[tg]["gifts"] = [ending keys] — the gifts of the line (services/legend.apply_heir).
"""

from __future__ import annotations

import random

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.engine import checks
from app.engine.effects import apply_effects
from app.engine.state import CharacterState
from app.services.game import ActiveCharacter, save_active

FLAG = "finale"
SRC = "finale"


def cfg(content: Content):  # noqa: ANN201
    return content.balance.endings


def get(content: Content, key: str):  # noqa: ANN201
    return next((e for e in cfg(content).items if e.key == key), None)


def current(st: CharacterState) -> dict | None:
    r = st.flags.get(FLAG)
    return dict(r) if isinstance(r, dict) and r.get("key") else None


async def _extra(session: AsyncSession, ch: ActiveCharacter) -> dict:
    from app.services import paths, turf

    seat = await paths.mayor(session) or {}
    return {"mayor": int(seat.get("char", 0) or 0) == ch.id,
            "turf": len([d for d, r in (await turf.book(session)).items()
                         if int(r.get("owner", 0) or 0) == ch.player_tg_id and int(r.get("char", 0) or 0) == ch.id])}


async def status(session: AsyncSession, content: Content, ch: ActiveCharacter) -> tuple[dict | None, bool, str]:
    from app.services import ambition

    r = current(ch.state)
    if r is None:
        return None, False, ""
    e = get(content, r["key"])
    if e is None:
        return None, False, ""
    if r.get("ready"):
        return r, True, "все этапы пройдены: финал открыт"
    s = e.stages[int(r.get("stage", 0))]
    if s.kind == "fight":
        return r, True, f"бой: {s.boss or 'охрана'}, {cfg(content).fight_ap} ОД"
    if s.kind in ("mayor", "turf"):
        x = await _extra(session, ch)
        if s.kind == "mayor":
            return r, x["mayor"], "кресло мэра твоё" if x["mayor"] else "кресло мэра не твоё («Город» → выборы)"
        return r, x["turf"] >= s.value, f"твоих районов {x['turf']}/{s.value}"
    ok, txt = ambition.kind_status(content, ch.state, s, r, await ambition._world(session, content, ch.state))  # noqa: SLF001
    return r, ok, txt


async def start(session: AsyncSession, content: Content, ch: ActiveCharacter, tz: str, key: str) -> str | None:
    from app.services import legend

    if get(content, key) is None:
        return "gone"
    if current(ch.state) is not None:
        return "busy_end"
    if not await legend.eligible(session, content, ch):
        return "small"
    ch.state.flags[FLAG] = {"key": key, "stage": 0, "since": ch.state.weeks_lived, "c": {}}
    await save_active(session, ch, tz)
    return None


async def drop(session: AsyncSession, ch: ActiveCharacter, tz: str) -> None:
    ch.state.flags.pop(FLAG, None)
    await save_active(session, ch, tz)


def _next(st: CharacterState, r: dict, e) -> list[str]:  # noqa: ANN001
    s = e.stages[int(r.get("stage", 0))]
    r["stage"] = int(r.get("stage", 0)) + 1
    r["since"] = st.weeks_lived
    lines = [f"✅ {s.name}."]
    if r["stage"] >= len(e.stages):
        r["ready"] = True
        lines.append(f"🌅 Все этапы пройдены. Финал «{e.name}» открыт: он закроет эту жизнь.")
    else:
        lines.append(f"Дальше: «{e.stages[r['stage']].name}».")
    st.flags[FLAG] = r
    return lines


async def advance(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                  tz: str):  # noqa: ANN201
    """Do or confirm the current stage. (blocked, lines, check) or a combat Step for a fight stage."""
    from app.services import ap as ap_services

    st = ch.state
    r = current(st)
    if r is None:
        return "none", [], None
    e = get(content, r["key"])
    if e is None or r.get("ready"):
        return "none", [], None
    s = e.stages[int(r.get("stage", 0))]
    if s.kind == "fight":
        return await _fight(session, content, ch, rng, year, tz, r, s)
    _, ok, txt = await status(session, content, ch)
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
    lines = _next(st, r, e)
    await save_active(session, ch, tz)
    return None, lines, chk


async def _fight(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
                 r: dict, s):  # noqa: ANN001, ANN201
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
    step = await combat.begin(session, content, ch, rng, year, tz, faction=s.faction, danger=5,
                              src={"src": SRC, "ending": r["key"], "stage": int(r.get("stage", 0)), "army_war": True,
                                   "extra": 2})
    from app.engine import elite  # owner request 25.10: the elite fight: roles, intents, a boss with phases

    chal.scale(step.cs, content, lv, rng.choice(chal.COUNTERS), rng, year, s.boss or "Командир")
    elite.setup(step.cs, content, rng, year, boss=s.boss or "Командир", waves=1 + s.lv_plus)
    _store(ch, step.cs)
    await save_active(session, ch, tz)
    return step


async def fight_over(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                     tz: str, result: str, ctx: dict) -> list[str]:
    from app.engine import combat as cb

    r = current(ch.state)
    e = get(content, str(ctx.get("ending", "")))
    if r is None or e is None or r.get("key") != e.key or int(r.get("stage", -1)) != int(ctx.get("stage", -2)):
        return []
    s = e.stages[int(r["stage"])]
    if result != cb.WON:
        return [f"«{s.name}»: не вышло. Финал ждёт, можно вернуться с новыми силами."]
    lines = _next(ch.state, r, e)
    await save_active(session, ch, tz)
    return lines


async def close(session: AsyncSession, content: Content, ch: ActiveCharacter, tz: str, year: int) -> tuple[str, str] | None:
    """The finale: into the Hall, the gift to the line, the life closes. Returns (cause, the last words)."""
    from app.services import death, legend, loud

    r = current(ch.state)
    e = get(content, r["key"]) if r else None
    if r is None or e is None or not r.get("ready"):
        return None
    await legend.retire(session, content, ch, ending=e.key, ending_title=e.title)
    from app.services import season

    await season.add_ch(session, content, ch, "ending", content.balance.season.pts.get("ending", 0))
    await loud.post(session, ch.id, f"🌅 {loud.who(ch.state.name, ch.state.surname)}: финал «{e.name}». {e.final}", kind="ending", w=3)
    await death.kill(session, content, ch, e.cause, tz, year=year)
    return e.cause, e.final


def gift_text(content: Content, key: str) -> str:
    e = get(content, key)
    return e.gift.text if e is not None else ""
