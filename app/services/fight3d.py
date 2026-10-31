"""The fight in first person, the service (owner request 31.10, docs/PLAN_FIGHT3D.md phase 2).

The fight is the character's own `flags["combat"]`, the one the text window plays. The Unity client asks for its state
and reports hits; every hit is one strike of `engine/realtime.py` (which calls `engine/combat._strike`). Under the
player's lock, like a button press. The end of the fight is `services/combat._finish` and the window of the player shows
the same closing screen as after a text fight.

    GET  /api/fight3d/state?t=        enemies, the player, the weapons, the pace
    POST /api/fight3d/hit  {t,i,w,m}   the player's projectile or blade reached enemy i (w weapon key, m melee)
    POST /api/fight3d/hurt {t,i,m}     enemy i's projectile or blow reached the player
    POST /api/fight3d/leave {t}        the client closes: the window shows the fight at its current tick
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any

from app.content.loader import Content
from app.engine import combat as cb
from app.engine import realtime as rt

log = logging.getLogger(__name__)

GAME = "city3d"  # the same pass as the district


def _tg(ui: Any, token: str) -> int | None:
    from app.services import arcade

    data = arcade.read_web_token(ui.settings.bot_token, token, arcade.cfg(ui.content).web.token_ttl_h)
    return int(data["u"]) if data is not None and data.get("g") == GAME else None


def _tick_s(content: Content) -> float:
    return float(content.balance.fight3d.tick_s)


def _weapons(content: Content, cs: cb.CombatState) -> list[dict[str, Any]]:
    p = cs.player
    out: list[dict[str, Any]] = []
    for key, a in p.arsenal.items():
        w = content.weapons.get(key)
        out.append({"key": key, "name": w.name if w else key, "melee": a.kind != cb.RANGED, "attempts": a.attempts,
                    "dice": a.dice, "type": (w.type if w else "melee" if a.kind != cb.RANGED else "pistol")})
    if not any(not x["melee"] for x in out) and cb.RANGED in p.attacks:
        a = p.attacks[cb.RANGED]
        out.insert(0, {"key": "", "name": "Ствол", "melee": False, "attempts": a.attempts, "dice": a.dice, "type": "pistol"})
    if not any(x["melee"] for x in out):
        a = p.attacks.get(cb.MELEE) or p.attacks.get(cb.MELEE_NL)
        if a is not None:
            out.append({"key": "", "name": "Кулаки", "melee": True, "attempts": a.attempts, "dice": a.dice, "type": "fists"})
    return out


def view(content: Content, cs: cb.CombatState, now: float) -> dict[str, Any]:
    """What the client draws: the enemies (with their slot index), the player, the pace."""
    enemies = []
    for i, e in enumerate(cs.enemies):
        enemies.append({"i": i, "id": e.id, "name": e.name, "hp": max(0, e.hp), "max_hp": e.max_hp, "active": e.active,
                        "out": e.out, "ranged": cb.RANGED in e.attacks, "melee": cb.MELEE in e.attacks or cb.MELEE_NL in e.attacks,
                        "danger": e.danger, "role": getattr(e, "role", "") or "", "stun": e.stun,
                        "attempts": max((a.attempts for a in e.attacks.values()), default=1)})
    p = cs.player
    return {"ok": True, "status": "fight", "tick": cs.tick, "tick_s": _tick_s(content), "tick_left": rt.tick_left(cs, now, _tick_s(content)),
            "faction": cs.faction, "over": bool(cs.outcome), "outcome": cs.outcome or "", "reinforced": cs.reinforced,
            "player": {"hp": max(0, p.hp), "max_hp": p.max_hp, "name": p.name, "defense": p.defense},
            "weapons": _weapons(content, cs), "enemies": enemies, "nonlethal": cs.nonlethal}


async def _with_fight(ui: Any, token: str, act):  # noqa: ANN001
    """Load the player's fight under the lock, run `act(session, ch, cs, rng, year, now)`, save or finish."""
    from app.db import session as db
    from app.services import combat as svc
    from app.services import game, world as world_service

    tg = _tg(ui, token)
    if tg is None:
        return {"ok": False, "status": "auth"}
    content: Content = ui.content
    tz = ui.settings.timezone
    end: tuple[Any, Any] | None = None
    async with ui.lock_for(tg):
        actor = world_service.set_actor(tg)
        try:
            async with db.session_scope() as s:
                ch = await game.get_active_character(s, tg)
                if ch is None:
                    return {"ok": False, "status": "nochar"}
                cs = svc.state_of(ch)
                if cs is None:
                    return {"ok": True, "status": "none"}
                if rt.text_only(cs):
                    return {"ok": True, "status": "text_only"}
                year = await game.current_year(s, content)
                rng = random.Random()
                now = time.time()
                try:
                    res = act(cs, rng, now)
                except cb.CombatError as e:
                    await svc._persist(s, content, ch, cs, rng, year, tz)
                    return {**view(content, cs, now), "status": e.reason, "left": round(float(e.info.get("left", 0) or 0), 2)}
                if cs.outcome:
                    step = await svc._finish(s, content, ch, rng, year, tz, cs)
                    out = {**view(content, cs, now), **(res or {}), "over": True, "outcome": cs.outcome,
                           "died": bool(step.end and step.end.died)}
                    end = (ch, step)
                else:
                    await svc._persist(s, content, ch, cs, rng, year, tz)
                    out = {**view(content, cs, now), **(res or {})}
            if end is not None:  # after the session: the window is written by its own session
                await _show_end(ui, tg, content, end[0], end[1])
            return out
        finally:
            world_service.reset_actor(actor)


async def _show_end(ui: Any, tg: int, content: Content, ch: Any, step: Any) -> None:
    """The window of the player shows the closing screen of the fight, the one the text shows after the last press."""
    try:
        from app.bot.handlers.combat import step_screen
        from app.webapp.gateway import _window

        snap = await _window(tg)
        chat = snap.chat_id if snap is not None else ui.group_id
        if chat is not None:
            await ui.windows.show(tg, step_screen(content, ch, step), chat)
    except Exception:
        log.exception("fight3d: the closing screen was not shown")


async def state(ui: Any, token: str) -> dict[str, Any]:
    content: Content = ui.content
    return await _with_fight(ui, token, lambda cs, rng, now: (rt.advance(cs, content, rng, now, _tick_s(content)) and None) or {})


async def hit(ui: Any, token: str, idx: int, weapon: str, melee: bool) -> dict[str, Any]:
    content: Content = ui.content
    return await _with_fight(ui, token, lambda cs, rng, now: {
        "strike": rt.player_strike(cs, content, rng, idx, weapon, melee, now, _tick_s(content)), "status": "hit"})


async def hurt(ui: Any, token: str, idx: int, melee: bool) -> dict[str, Any]:
    content: Content = ui.content
    return await _with_fight(ui, token, lambda cs, rng, now: {
        "strike": rt.enemy_strike(cs, content, rng, idx, melee, now, _tick_s(content)), "status": "hurt"})


async def leave(ui: Any, token: str) -> dict[str, Any]:
    """The client closes in the middle of the fight: the window shows the fight at its current tick."""
    tg = _tg(ui, token)
    if tg is None:
        return {"ok": False, "status": "auth"}
    try:
        from app.bot.handlers.combat import combat_screen
        from app.db import session as db
        from app.services import combat as svc
        from app.services import game
        from app.webapp.gateway import _window

        async with ui.lock_for(tg):
            async with db.session_scope() as s:
                ch = await game.get_active_character(s, tg)
                cs = svc.state_of(ch) if ch is not None else None
            if cs is None:
                return {"ok": True, "status": "none"}
            snap = await _window(tg)
            chat = snap.chat_id if snap is not None else ui.group_id
            if chat is not None:
                await ui.windows.show(tg, combat_screen(ui.content, ch, cs, 0), chat)
    except Exception:
        log.exception("fight3d: leave failed")
    return {"ok": True, "status": "left"}


def url(content: Content, bot_token: str, tg_id: int) -> str | None:
    """The Unity client in the fight mode, or None before the first build."""
    from app.services import unity3d

    base = unity3d.url(content, bot_token, tg_id)
    return base + "&m=fight" if base else None
