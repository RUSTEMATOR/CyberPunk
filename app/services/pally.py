"""Hired players in their employer's fights (owner request 26.10, docs/PLAN_CORPFORCE.md part 1). Rules of the tick:
engine/pally.py.

Who stands at the side: the bodyguards (services/pguard.py) and the staff of the employer's megacorp
(services/pcorp.py), when they are free, alive, out of jail and in the same city. The hired player's own character is
not touched: health, ammo and charge stay as they were (a copy fights).

world_state:
- `pally_live` = {hired tg: {emp, aid, emp_name, foe, at, tick, snap, skills}} — the employer's fight right now, for the
  hired player's window «Бой нанимателя»;
- `pally_orders` = {employer tg: {ally id: skill}} — the hired player's own choice for the next tick;
- `pally_ping` = [[tg, text]] — the pings the job sends (the group for members, private for the rest).
"""

from __future__ import annotations

import random
import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.db import repo
from app.engine import combat as cb
from app.engine import jail as jail_eng
from app.engine import pally as eng
from app.engine.effects import apply_effects
from app.engine.state import FLAG_COMBAT
from app.services.game import ActiveCharacter

LIVE = "pally_live"
ORDERS = "pally_orders"
PINGS = "pally_ping"
OVER = "pally_over"  # {hired tg: {text, at}} — how the fight ended, for the joined player's window


def cfg(content: Content):  # noqa: ANN201
    return content.balance.pally


def _name(st) -> str:  # noqa: ANN001
    return f"{st.name} {st.surname}".strip()


def _same_city(content: Content, a: str, b: str) -> bool:
    da, db_ = content.districts.get(a), content.districts.get(b)
    return da is not None and db_ is not None and da.city == db_.city


def _free(content: Content, g: ActiveCharacter, ch: ActiveCharacter) -> bool:
    st = g.state
    return not (jail_eng.in_jail(st) or st.flags.get(FLAG_COMBAT) or st.hp <= 0
                or not _same_city(content, st.district, ch.state.district))


async def staff_for_fight(session: AsyncSession, content: Content, ch: ActiveCharacter,
                          skip: set[int]) -> list[ActiveCharacter]:
    """The employer's megacorp staff who can stand in the fight now."""
    from app.services import bigmoney, game, pcorp

    corp = (await bigmoney.corps(session)).get(str(ch.player_tg_id))
    if corp is None:
        return []
    out: list[ActiveCharacter] = []
    for tg, entry in pcorp.staff_of(corp).items():
        if int(tg) in skip or int(tg) == ch.player_tg_id:
            continue
        g = await game.get_active_character(session, int(tg))
        if g is None or g.id != int(entry.get("char", g.id)) or not _free(content, g, ch):
            continue
        out.append(g)
        if len(out) >= cfg(content).max_staff:
            break
    return out


def join(content: Content, cs: cb.CombatState, members: list[tuple[ActiveCharacter, str]]) -> list[str]:
    """Put the hired players into the fight as allies that act on their own skills."""
    pallies = dict(cs.ctx.get("pallies") or {})
    names: list[str] = []
    for g, role in members:
        c = cb.build_player(g.state, content)
        idx = len(cs.allies)
        icon = "🛡" if role == "guard" else "🏢"
        c.id, c.side, c.ai, c.name = f"a{idx}", "ally", "", f"{icon} {_name(g.state)}"
        cs.allies.append(c)
        from app.services import look as look_svc

        cs.ctx.setdefault("looks", {})[c.id] = look_svc.snapshot(content, g)  # owner request 26.10: the figure in the fight
        mode = str(g.state.flags.get(eng.FLAG_MODE) or eng.PLAY)
        pallies[c.id] = {"tg": int(g.player_tg_id), "name": _name(g.state), "role": role,
                         "mode": mode if mode in eng.MODES else eng.PLAY,
                         "style": dict(g.state.flags.get(eng.FLAG_STYLE) or {}), "seq": 0}
        names.append(_name(g.state))
        cb._emit(cs, k="reinf", name=f"{'охрана' if role == 'guard' else 'сотрудник'}, {_name(g.state)}", n=1)  # noqa: SLF001
    if pallies:
        cs.ctx["pallies"] = pallies
    return names


def _snap(content: Content, cs: cb.CombatState) -> str:
    foes = [f"{e.name} {max(0, e.hp)}/{e.max_hp}" for _, e in cs.active_enemies()][:6]
    p = cs.player
    return (f"Наниматель {max(0, p.hp)}/{p.max_hp} · ход {cs.tick}\nПротив: " + (", ".join(foes) if foes else "никого"))


async def go_live(session: AsyncSession, content: Content, ch: ActiveCharacter, cs: cb.CombatState, foe: str) -> None:
    """The fight started: every hired player at the side gets an invitation with a button and `invite_s` seconds to
    join in person (owner request 26.10: «как мультиплеер … 30 секунд на ответ»); otherwise the skills act by
    themselves."""
    pallies = cs.ctx.get("pallies") or {}
    if not pallies:
        return
    live = dict(await repo.ws_get(session, LIVE, {}) or {})
    pings = list(await repo.ws_get(session, PINGS, []) or [])
    emp = _name(ch.state)
    now = time.time()
    c = cfg(content)
    for aid, rec in pallies.items():
        a = next((x for x in cs.allies if x.id == aid), None)
        live[str(rec["tg"])] = {"emp": int(ch.player_tg_id), "aid": aid, "emp_name": emp, "foe": foe, "at": now,
                                "name": rec["name"],
                                "tick": cs.tick, "snap": _snap(content, cs), "joined": False,
                                "invite_until": now + c.invite_s, "turn_at": now,
                                "skills": eng.all_skills(a, content) if a is not None else []}
        tag = f'<a href="tg://user?id={int(rec["tg"])}">{rec["name"]}</a>'
        text = (f"⚔️ {tag}, {emp} вступает в бой ({foe}), ты в отряде. {c.invite_s} секунд, чтобы вступить "
                f"самому и выбирать ходы. Не успеешь — твои навыки сработают сами.")
        if UI is not None:
            UI.spawn(invite(UI, int(rec["tg"]), int(ch.player_tg_id), text))
        else:
            pings.append([int(rec["tg"]), text])
    await repo.ws_set(session, LIVE, live)
    await repo.ws_set(session, PINGS, pings[-30:])


UI = None  # the bot's UiKit, set at the start (app/main.py): the invitations and the windows of the hired players
JOIN_PREFIX = "pj"


async def invite(ui, tg: int, emp: int, text: str) -> None:  # noqa: ANN001
    """A message with the button «Вступить в бой»: in the group for a member, in private for the rest."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    from app.services import dm_relay

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="⚔️ Вступить в бой", callback_data=f"{JOIN_PREFIX}:{emp}:{tg}")]])
    ttl = 10 * 60
    try:
        member = ui.group_id is not None and (not dm_relay.cfg(ui).enabled or await dm_relay.in_group(ui, tg))
    except Exception:  # noqa: BLE001 - a failed check sends it in private
        member = False
    await ui._send_temp(ui.group_id if member else tg, text, ttl, "news", kb)  # noqa: SLF001


async def join_live(session: AsyncSession, tg: int, emp: int, chat_id: int) -> str | None:
    """The hired player joins the employer's fight in person. None when it worked, else why not."""
    live = dict(await repo.ws_get(session, LIVE, {}) or {})
    e = live.get(str(tg))
    if not e or int(e.get("emp", 0)) != int(emp):
        return "Этот бой уже кончился."
    if e.get("down"):
        return "Тебя уже вывели из этого боя."
    e.update({"joined": True, "chat": int(chat_id), "turn_at": time.time()})
    live[str(tg)] = e
    await repo.ws_set(session, LIVE, live)
    return None


def refresh(tg: int) -> None:
    """Redraw the hired player's window with the fight (only for those who joined in person)."""
    if UI is None:
        return

    async def _go() -> None:
        from app.bot.handlers.pally import push_screen

        await push_screen(UI, tg)

    UI.spawn(_go())


async def waiting_for(session: AsyncSession, content: Content, emp: int) -> list[tuple[str, float]]:
    """Whom the employer's tick waits for now: (name, seconds left). An invited player who has not answered yet, a
    joined one who has not chosen the move of this tick yet."""
    live = await repo.ws_get(session, LIVE, {}) or {}
    orders = (await repo.ws_get(session, ORDERS, {}) or {}).get(str(emp)) or {}
    c = cfg(content)
    now = time.time()
    out: list[tuple[str, float]] = []
    for e in live.values():
        if int(e.get("emp", 0)) != int(emp) or e.get("down"):
            continue
        if not e.get("joined"):
            left = float(e.get("invite_until", 0) or 0) - now
        elif e.get("aid") not in orders:
            left = float(e.get("turn_at", 0) or 0) + c.turn_s - now
        else:
            continue
        if left > 0:
            out.append((str(e.get("name") or e.get("aid")), left))
    return out


async def pull_orders(session: AsyncSession, ch: ActiveCharacter, cs: cb.CombatState) -> None:
    """Before the employer's tick: the hired players' own choices go into the fight."""
    if not cs.ctx.get("pallies"):
        return
    allo = dict(await repo.ws_get(session, ORDERS, {}) or {})
    mine = allo.pop(str(ch.player_tg_id), None)
    if mine:
        orders = dict(cs.ctx.get("orders") or {})
        for aid, skill in mine.items():
            if aid not in cs.ctx["pallies"]:
                continue
            if skill in GUARD_MOVES:  # owner request 29.10: cover the employer
                apply_guard(cs, aid, skill)
                continue
            if aid not in orders:  # the employer's own order comes first
                orders[aid] = skill
        cs.ctx["orders"] = orders
        await repo.ws_set(session, ORDERS, allo)


async def after_tick(session: AsyncSession, content: Content, ch: ActiveCharacter, cs: cb.CombatState) -> None:
    """After the employer's tick: the hired players' windows see the new state."""
    pallies = cs.ctx.get("pallies") or {}
    if not pallies:
        return
    live = dict(await repo.ws_get(session, LIVE, {}) or {})
    changed = False
    refreshed: list[int] = []
    for aid, rec in pallies.items():
        e = live.get(str(rec["tg"]))
        if not e or int(e.get("emp", 0)) != ch.player_tg_id:
            continue
        a = next((x for x in cs.allies if x.id == aid), None)
        e.update({"tick": cs.tick, "snap": _snap(content, cs), "at": time.time(), "last": rec.get("last", ""),
                  "hold": (cs.ctx.get("cover") or {}).get(aid) == "hold",
                  "down": bool(a is None or not a.active), "turn_at": time.time(),
                  "skills": eng.all_skills(a, content) if a is not None else []})
        changed = True
        if e.get("joined"):
            refreshed.append(int(rec["tg"]))
    if changed:
        await repo.ws_set(session, LIVE, live)
    for tg in refreshed:
        refresh(tg)


async def end_live(session: AsyncSession, content: Content, ch: ActiveCharacter, cs: cb.CombatState,
                   result: str) -> list[str]:
    """The fight ended: the windows close; a won fight pays the corp staff from the employer's pocket."""
    pallies = cs.ctx.get("pallies") or {}
    if not pallies:
        return []
    live = dict(await repo.ws_get(session, LIVE, {}) or {})
    over = dict(await repo.ws_get(session, OVER, {}) or {})
    word = {cb.WON: "победа", cb.DOWNED: "наниматель лёг"}.get(result, "бой окончен")
    joined: list[int] = []
    for rec in pallies.values():
        e = live.get(str(rec["tg"]))
        if e and int(e.get("emp", 0)) == ch.player_tg_id:
            live.pop(str(rec["tg"]), None)
            if e.get("joined"):
                over[str(rec["tg"])] = {"text": f"Бой {_name(ch.state)} против {e.get('foe')}: {word}.", "at": time.time()}
                joined.append(int(rec["tg"]))
    await repo.ws_set(session, LIVE, live)
    await repo.ws_set(session, OVER, over)
    for tg in joined:
        refresh(tg)
    allo = dict(await repo.ws_get(session, ORDERS, {}) or {})
    if allo.pop(str(ch.player_tg_id), None) is not None:
        await repo.ws_set(session, ORDERS, allo)
    lines: list[str] = []
    if result == cb.WON:
        pay = cfg(content).win_pay_per_danger * max(1, cs.danger)
        staff = [r for r in pallies.values() if r.get("role") == "staff"]
        from app.services import pcorp

        paid = []
        for r in staff:
            if ch.state.eddies < pay:
                break
            apply_effects(ch.state, {"eddies": -pay}, content, random.Random(), ch.state.current_year)
            pcorp._mail(session, int(r["tg"]), f"⚔️ Боевые от {_name(ch.state)}: бой выигран, ты был в отряде.", pay)  # noqa: SLF001
            paid.append(r["name"])
        if paid:
            lines.append(f"🏢 Боевые сотрудникам ({', '.join(paid)}): по {pay} эдди.")
    return lines


def record_style(ch: ActiveCharacter, cs: cb.CombatState) -> None:
    """The player's own fight teaches the auto mode how this player fights."""
    if cs.ctx.get("duel"):
        return
    ch.state.flags[eng.FLAG_STYLE] = eng.style_from(cs, ch.state.flags.get(eng.FLAG_STYLE))


async def live_for(session: AsyncSession, tg: int, content: Content) -> dict[str, Any] | None:
    e = (await repo.ws_get(session, LIVE, {}) or {}).get(str(tg))
    if not e or time.time() - float(e.get("at", 0)) > cfg(content).live_min * 60:
        return None
    return dict(e)


async def choose(session: AsyncSession, content: Content, tg: int, skill: str) -> str:
    """The hired player picks the skill for the next tick of the employer's fight."""
    e = await live_for(session, tg, content)
    if e is None:
        return "Бой уже кончился."
    if e.get("down"):
        return "Тебя вывели из этого боя."
    if skill not in (e.get("skills") or []) and skill not in GUARD_MOVES:
        return "Этого навыка у тебя сейчас нет."
    allo = dict(await repo.ws_get(session, ORDERS, {}) or {})
    mine = dict(allo.get(str(e["emp"])) or {})
    mine[str(e["aid"])] = skill
    allo[str(e["emp"])] = mine
    await repo.ws_set(session, ORDERS, allo)
    return f"Ход принят: {move_label(content, skill)}. Ждём нанимателя."


# owner request 29.10 «защитить нанимателя и принять урон на себя. Или вообще драться вместо нанимателя»
GUARD, HOLD, UNHOLD = "guard", "hold", "unhold"
GUARD_MOVES = (GUARD, HOLD, UNHOLD)
GUARD_LABELS = {GUARD: "🛡 Прикрыть", HOLD: "⚔️ Принять бой на себя", UNHOLD: "↩️ Отойти за спину"}


def move_label(content: Content, skill: str) -> str:
    return GUARD_LABELS.get(skill) or eng.label(content, skill)


def apply_guard(cs: cb.CombatState, aid: str, move: str) -> None:
    """A guard move of a hired player: «guard» this tick (no strike, half damage), «hold» until the end of the fight or
    «unhold». The blows meant for the employer go to the guard (engine/combat.cover_guard)."""
    cover = dict(cs.ctx.get("cover") or {})
    if move == GUARD:
        if cover.get(aid) != "hold":
            cover[aid] = "tick"
    elif move == HOLD:
        cover[aid] = "hold"
        cb._emit(cs, k="guard_hold", by=aid)  # noqa: SLF001
    elif move == UNHOLD and cover.pop(aid, None):
        cb._emit(cs, k="guard_off", by=aid)  # noqa: SLF001
    cs.ctx["cover"] = cover


def order(cs: cb.CombatState, aid: str, skill: str) -> bool:
    """The employer's order to one of the hired players (a free action, no tick passes)."""
    if aid not in (cs.ctx.get("pallies") or {}):
        return False
    orders = dict(cs.ctx.get("orders") or {})
    orders[aid] = skill
    cs.ctx["orders"] = orders
    return True


async def send_pings(session: AsyncSession, ui) -> int:  # noqa: ANN001
    pings = list(await repo.ws_get(session, PINGS, []) or [])
    if not pings:
        return 0
    await repo.ws_set(session, PINGS, [])
    from app.services import dm_relay

    for tg, text in pings:
        ui.spawn(dm_relay.ping(ui, int(tg), str(text), 15 * 60, "news"))
    return len(pings)
