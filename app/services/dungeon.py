"""The tower of a megacorp as a dungeon (owner request 26.10 «штурм корпорации как MMORPG-данж, даже соло»,
docs/PLAN_DUNGEON.md). Five floors: rooms to choose from, a mid boss, the final boss in two waves. A party of up to
`party_max` players shares the floor, the alarm and the bosses' health; solo is a party of one.

world_state `tower_runs` = {id: run}; run = {"id", "corp", "leader", "lv", "counter", "floor", "alarm", "weaken",
  "boss": {floor index: share of health left}, "members": {tg: {"cid", "name", "out", "pocket"}}, "status"
  (open | done | failed), "created" (iso utc), "log": [lines]}.
Character flags: `tower_run` = run id while inside, `tower_day` = the Kyiv date of the last run started or joined.
Fights: services/combat.begin with src `tdun`, ctx {run, floor, room | boss}; services/combat calls `fight_over`.
A lost fight never kills (ctx `nonlethal`): the guards throw the character out with 1 hp.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.db import repo
from app.engine import challenge as chal
from app.engine import checks
from app.engine import combat as cb
from app.engine.effects import apply_effects
from app.services.game import ActiveCharacter, save_active

KEY = "tower_runs"
FLAG, FLAG_DAY = "tower_run", "tower_day"
SRC = "tdun"
OPEN, DONE, FAILED = "open", "done", "failed"


def cfg(content: Content):  # noqa: ANN201
    return content.balance.tower_dungeon


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def book(session: AsyncSession) -> dict[str, dict]:
    return dict(await repo.ws_get(session, KEY, {}) or {})


async def _save_run(session: AsyncSession, run: dict) -> None:
    b = await book(session)
    b[str(run["id"])] = run
    cutoff = (_now() - timedelta(days=3)).isoformat()  # finished runs are kept a few days for the screens
    b = {k: v for k, v in b.items() if v.get("status") == OPEN or str(v.get("created", "")) >= cutoff}
    await repo.ws_set(session, KEY, b)


def floor_of(content: Content, run: dict):  # noqa: ANN201
    fl = cfg(content).floors
    i = int(run.get("floor", 0))
    return fl[i] if 0 <= i < len(fl) else None


def alive(run: dict) -> list[str]:
    return [tg for tg, m in (run.get("members") or {}).items() if not m.get("out")]


def expired(content: Content, run: dict) -> bool:
    try:
        born = datetime.fromisoformat(str(run.get("created")))
    except ValueError:
        return True
    return _now() - born > timedelta(hours=cfg(content).ttl_hours)


def corp_name(content: Content, run: dict) -> str:
    c = content.corps.get(str(run.get("corp")))
    return c.name if c else str(run.get("corp"))


def boss_name(content: Content, run: dict, floor) -> str:  # noqa: ANN001
    return floor.boss.replace("{corp}", corp_name(content, run))


async def current(session: AsyncSession, content: Content, ch: ActiveCharacter) -> dict | None:
    """The run the character is in (an expired one is closed on the way, the loot paid at the expire share)."""
    rid = ch.state.flags.get(FLAG)
    if not rid:
        return None
    run = (await book(session)).get(str(rid))
    if run is None or run.get("status") != OPEN or str(ch.player_tg_id) not in (run.get("members") or {}):
        ch.state.flags.pop(FLAG, None)
        return run if run is not None and str(ch.player_tg_id) in (run.get("members") or {}) else None
    if expired(content, run):
        run["status"] = FAILED
        run.setdefault("log", []).append("Башня закрылась на карантин: штурм окончен.")
        await _save_run(session, run)
    return run


async def open_runs(session: AsyncSession, content: Content, corp: str) -> list[dict]:
    c = cfg(content)
    out = []
    for run in (await book(session)).values():
        if (run.get("corp") == corp and run.get("status") == OPEN and not expired(content, run)
                and len(run.get("members") or {}) < c.party_max and int(run.get("floor", 0)) < len(c.floors)):
            out.append(run)
    return out


def _can_enter(content: Content, ch: ActiveCharacter, tz: str) -> str | None:
    from app.clock import kyiv_date
    from app.engine import jail as jail_eng
    from app.services import ap as ap_services
    from app.services import events as ev_service

    if not cfg(content).enabled:
        return "off"
    if jail_eng.in_jail(ch.state):
        return "jail"
    if ev_service.has_pending(ch):
        return "busy"
    if ch.state.flags.get(FLAG):
        return "inside"
    if ch.state.flags.get(FLAG_DAY) == kyiv_date(tz) and cfg(content).per_day <= 1:
        return "today"
    if not ap_services.can_spend(ch, cfg(content).ap_enter):
        return "ap"
    return None


def _member(ch: ActiveCharacter) -> dict:
    return {"cid": ch.id, "name": ch.state.name, "out": False, "pocket": 0}


async def start(session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, tz: str, corp: str,
                rng: random.Random) -> dict | str:
    from app.clock import kyiv_date
    from app.services import ap as ap_services
    from app.services import corpraid, corpstand

    if await corpraid.allowed(session, content, ch) is None:
        return "member"
    if corp not in corpraid.targets(content, ch.state, year):
        return "gone"
    if corpstand.down(corpstand.rec_of(content, await corpstand.book(session), corp)):
        return "down"
    lv = await corpraid.ladder(session, ch)
    if lv < 2:
        return "tower"
    why = _can_enter(content, ch, tz)
    if why:
        return why
    ap_services.spend(ch, content, cfg(content).ap_enter)
    b = await book(session)
    rid = max([int(k) for k in b] + [0]) + 1
    run = {"id": rid, "corp": corp, "leader": str(ch.player_tg_id), "lv": max(2, lv), "counter": rng.choice(chal.COUNTERS),
           "floor": 0, "alarm": 0, "weaken": 0, "boss": {}, "members": {str(ch.player_tg_id): _member(ch)},
           "status": OPEN, "created": _now().isoformat(),
           "log": [f"{ch.state.name} начинает штурм башни {corp_name(content, {'corp': corp})}."]}
    ch.state.flags[FLAG] = rid
    ch.state.flags[FLAG_DAY] = kyiv_date(tz)
    await _save_run(session, run)
    await save_active(session, ch, tz)
    return run


async def join(session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, tz: str, rid: int) -> dict | str:
    from app.clock import kyiv_date
    from app.services import ap as ap_services
    from app.services import corpraid

    if await corpraid.allowed(session, content, ch) is None:
        return "member"
    run = (await book(session)).get(str(rid))
    if run is None or run.get("status") != OPEN or expired(content, run):
        return "gone"
    if len(run.get("members") or {}) >= cfg(content).party_max:
        return "full"
    if run.get("corp") not in corpraid.targets(content, ch.state, year):
        return "gone"
    why = _can_enter(content, ch, tz)
    if why:
        return why
    ap_services.spend(ch, content, cfg(content).ap_enter)
    run["members"][str(ch.player_tg_id)] = _member(ch)
    run.setdefault("log", []).append(f"{ch.state.name} присоединяется к штурму.")
    ch.state.flags[FLAG] = int(run["id"])
    ch.state.flags[FLAG_DAY] = kyiv_date(tz)
    await _save_run(session, run)
    await save_active(session, ch, tz)
    return run


def _loot(content: Content, run: dict, lohi: list[int], rng: random.Random) -> int:
    lo, hi = (lohi + [0, 0])[:2]
    if hi <= 0:
        return 0
    n = rng.randint(int(lo), int(hi))
    if cfg(content).loot_ladder:
        n *= max(1, chal.mult(int(run.get("lv", 2))) // 2)
    return min(n, cfg(content).loot_cap)


def _bump_alarm(content: Content, run: dict, n: int) -> None:
    run["alarm"] = max(0, min(cfg(content).alarm_max, int(run.get("alarm", 0)) + n))


def _advance(content: Content, run: dict) -> None:
    run["floor"] = int(run.get("floor", 0)) + 1


def _alarm_line(n: int) -> str:
    if n > 0:
        return f"Тревога +{n}."
    if n < 0:
        return f"Тревога −{-n}."
    return ""


async def act(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
              room_key: str) -> tuple[list[str], Any]:
    """Take a room of the current floor. (lines, a combat Step or None)."""
    from app.services import ap as ap_services

    run = await current(session, content, ch)
    if run is None or run.get("status") != OPEN:
        return ["Штурм уже окончен."], None
    me = run["members"][str(ch.player_tg_id)]
    if me.get("out"):
        return ["Ты уже вне башни."], None
    floor = floor_of(content, run)
    if floor is None or floor.boss:
        return ["Здесь нет такой комнаты."], None
    room = next((r for r in floor.rooms if r.key == room_key), None)
    if room is None:
        return ["Здесь нет такой комнаты."], None
    c = cfg(content)
    if not ap_services.can_spend(ch, c.ap_room):
        return ["Не хватает ОД."], None
    ap_services.spend(ch, content, c.ap_room)
    lines = [f"<b>{room.name}</b>. {room.text}"]
    if room.kind == "fight":
        return lines, await _room_fight(session, content, ch, rng, year, tz, run, room)
    if room.kind == "check" and room.check is not None:
        chk = checks.roll_check(ch.state, content, rng, room.check.stat, room.check.skill, room.check.dv, ())
        if not chk.ok:
            _bump_alarm(content, run, room.alarm_fail)
            lines.append(room.fail_text + " " + _alarm_line(room.alarm_fail))
            if room.fail_fight:
                await _save_run(session, run)
                return lines, await _room_fight(session, content, ch, rng, year, tz, run, room)
            await _save_run(session, run)
            await save_active(session, ch, tz)
            return lines, None
        lines.append(room.ok_text)
    lines += await _room_done(session, content, ch, rng, year, tz, run, room)
    return lines, None


async def _room_done(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                     tz: str, run: dict, room) -> list[str]:  # noqa: ANN001
    """A room taken without a fight, or its fight won: loot, alarm, weaken, heal; the floor is passed."""
    lines: list[str] = []
    me = run["members"][str(ch.player_tg_id)]
    loot = _loot(content, run, list(room.loot), rng)
    if loot:
        me["pocket"] = int(me.get("pocket", 0)) + loot
        lines.append(f"Добыча: +{loot} эдди в карман штурма.")
    if room.heal_pct:
        hp_max = _hp_max(content, ch)
        add = max(1, hp_max * room.heal_pct // 100)
        apply_effects(ch.state, {"hp": add}, content, rng, year)
        lines.append(f"Здоровье +{add}.")
    if room.fx_ok:
        apply_effects(ch.state, dict(room.fx_ok), content, rng, year)
        if room.fx_ok.get("humanity"):
            lines.append(f"Человечность {room.fx_ok['humanity']:+d}.".replace("-", "−"))
    if room.weaken:
        run["weaken"] = min(60, int(run.get("weaken", 0)) + room.weaken)
        lines.append(f"Финальный босс ослаблен: −{room.weaken}% здоровья.")
    if room.alarm_ok:
        _bump_alarm(content, run, room.alarm_ok)
        lines.append(_alarm_line(room.alarm_ok))
    _advance(content, run)
    run.setdefault("log", []).append(f"{ch.state.name}: {room.name}.")
    nxt = floor_of(content, run)
    if nxt is not None:
        lines.append(f"Путь открыт: {nxt.name}.")
    await _save_run(session, run)
    await save_active(session, ch, tz)
    return [x for x in lines if x]


def _hp_max(content: Content, ch: ActiveCharacter) -> int:
    from app.engine import derived

    return int(derived.max_hp(ch.state, content))


def _scale_alarm(content: Content, run: dict, cs: cb.CombatState) -> None:
    pct = int(run.get("alarm", 0)) * cfg(content).alarm_hp_pct
    if pct <= 0:
        return
    for e in cs.enemies:
        e.max_hp = int(e.max_hp * (100 + pct) / 100)
        e.hp = int(e.hp * (100 + pct) / 100)


async def _room_fight(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                      tz: str, run: dict, room):  # noqa: ANN001, ANN202
    from app.services import combat
    from app.services.combat import _store

    c = cfg(content)
    danger = int(room.danger) + (1 if int(run.get("alarm", 0)) >= c.alarm_wave_at else 0)
    step = await combat.begin(session, content, ch, rng, year, tz, faction=str(run["corp"]), danger=min(5, danger),
                              src={"src": SRC, "run": int(run["id"]), "floor": int(run["floor"]), "room": room.key,
                                   "nonlethal": True, "solo": True})
    if int(run.get("alarm", 0)) >= c.alarm_wave_at and not isinstance(step.cs.ctx.get("elite"), dict):
        for en in cb.build_pack(step.cs.faction, step.cs.danger, rng, content, year)[:1]:  # reinforcements
            step.cs.enemies.append(cb.build_enemy(en, len(step.cs.enemies), content))
        step.cs.pack_size = len(step.cs.enemies)
    _scale_alarm(content, run, step.cs)
    _store(ch, step.cs)
    await save_active(session, ch, tz)
    return step


def boss_left(run: dict, floor_i: int) -> float:
    return float((run.get("boss") or {}).get(str(floor_i), 1.0))


async def fight_boss(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                     tz: str) -> tuple[list[str], Any]:
    from app.engine import elite
    from app.services import ap as ap_services
    from app.services import combat
    from app.services.combat import _store

    run = await current(session, content, ch)
    if run is None or run.get("status") != OPEN or run["members"][str(ch.player_tg_id)].get("out"):
        return ["Штурм уже окончен."], None
    floor = floor_of(content, run)
    if floor is None or not floor.boss:
        return ["Босса здесь нет."], None
    c = cfg(content)
    if not ap_services.can_spend(ch, c.ap_boss):
        return ["Не хватает ОД."], None
    ap_services.spend(ch, content, c.ap_boss)
    fi = int(run["floor"])
    name = boss_name(content, run, floor)
    waves = int(floor.waves) + (1 if int(run.get("alarm", 0)) >= c.alarm_wave_at else 0)
    step = await combat.begin(session, content, ch, rng, year, tz, faction=str(run["corp"]), danger=5,
                              src={"src": SRC, "run": int(run["id"]), "floor": fi, "boss": True, "nonlethal": True,
                                   "solo": True, "dg_boss": name})
    cs = step.cs
    lv = int(run.get("lv", 2)) if floor.boss_final else max(0, int(run.get("lv", 2)) - 1)
    if lv >= 2 and not isinstance(cs.ctx.get("elite"), dict):
        chal.scale(cs, content, lv, str(run.get("counter", "")) if floor.boss_final else "", rng, year, name)
    cs.ctx.pop("elite", None)  # the boss room is built here, not by the auto elite of an ordinary fight
    elite.setup(cs, content, rng, year, boss=name, waves=waves)
    n = max(1, len(run.get("members") or {}))
    mult = 1 + c.boss_party_mult * (n - 1)
    weaken = int(run.get("weaken", 0)) if floor.boss_final else 0
    left = boss_left(run, fi)
    _set_boss_hp(cs, mult * (100 - weaken) / 100, left)
    _scale_alarm(content, run, cs)
    _store(ch, cs)
    await save_active(session, ch, tz)
    lines = [f"<b>{name}</b>. Запас здоровья босса: {round(left * 100)}%."]
    return lines, step


def _set_boss_hp(cs: cb.CombatState, mult: float, left: float) -> None:
    """The boss: health × party/weaken multiplier, then the share the party has left of it."""
    for e in cs.enemies:
        if e.role == "boss":
            e.max_hp = max(1, int(e.max_hp * mult))
            e.hp = max(1, int(e.max_hp * left))
            return
    st = cs.ctx.get("elite") or {}
    for wave in st.get("waves") or []:
        for d in wave:
            if d.get("role") == "boss":
                d["max_hp"] = max(1, int(int(d["max_hp"]) * mult))
                d["hp"] = max(1, int(d["max_hp"] * left))
                return


def boss_share(cs: cb.CombatState) -> float | None:
    """The share of the boss's health after the fight (None when the boss was never reached)."""
    for e in cs.enemies:
        if e.role == "boss":
            return 0.0 if e.hp <= 0 or e.out else max(0.0, e.hp / max(1, e.max_hp))
    return None


async def fight_over(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                     tz: str, result: str, ctx: dict) -> list[str]:
    b = await book(session)
    run = b.get(str(ctx.get("run")))
    tg = str(ch.player_tg_id)
    if run is None or tg not in (run.get("members") or {}):
        ch.state.flags.pop(FLAG, None)
        return []
    if run.get("status") != OPEN:
        return ["Штурм уже окончен."]
    fi = int(ctx.get("floor", -1))
    if ctx.get("boss"):
        share = ctx.get("dg_share")
        if share is not None:
            before = boss_left(run, fi)
            run.setdefault("boss", {})[str(fi)] = min(before, float(share))
    if result != cb.WON:
        if ctx.get("boss") and boss_left(run, fi) <= 0:
            return await _boss_down(session, content, ch, rng, year, tz, run, fi)
        return await _wipe(session, content, ch, rng, year, tz, run)
    if ctx.get("boss"):
        run.setdefault("boss", {})[str(fi)] = 0.0
        return await _boss_down(session, content, ch, rng, year, tz, run, fi)
    floor = cfg(content).floors[fi] if 0 <= fi < len(cfg(content).floors) else None
    room = next((r for r in (floor.rooms if floor else []) if r.key == ctx.get("room")), None)
    if room is None or int(run.get("floor", 0)) != fi:  # a party member took the floor first: the loot stays
        me = run["members"][tg]
        loot = _loot(content, run, list(room.loot) if room else [0, 0], rng)
        me["pocket"] = int(me.get("pocket", 0)) + loot
        await _save_run(session, run)
        return [f"Этаж уже взят отрядом. Добыча: +{loot} эдди в карман штурма."]
    return await _room_done(session, content, ch, rng, year, tz, run, room)


async def _pay(session: AsyncSession, content: Content, run: dict, tg: str, ch: ActiveCharacter | None,
               rng: random.Random, year: int, fx: dict, text: str) -> list[str]:
    """The actor gets it now, the rest of the party by mail."""
    fx = {k: v for k, v in fx.items() if v}
    if ch is not None and str(ch.player_tg_id) == tg:
        if fx:
            apply_effects(ch.state, fx, content, rng, year)
        return []
    from app.db.models import PlayerMail

    m = run["members"][tg]
    session.add(PlayerMail(to_tg_id=int(tg), from_tg_id=0, from_char_id=None, to_char_id=int(m.get("cid", 0)) or None,
                           from_name="Штурм башни", from_year=0, kind="city",
                           payload={"text": text, "eddies": int(fx.get("eddies", 0)), "cred": int(fx.get("street_cred", 0)),
                                    "fame": int(fx.get("fame", 0))}))
    await session.flush()
    return []


async def _boss_down(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                     tz: str, run: dict, fi: int) -> list[str]:
    from app.services import corpraid, corpstand, loud

    c = cfg(content)
    floor = c.floors[fi]
    name = boss_name(content, run, floor)
    corp = corp_name(content, run)
    if int(run.get("floor", 0)) != fi:
        await _save_run(session, run)
        return [f"{name} уже повержен отрядом."]
    lines = [f"💀 <b>{name}</b> повержен."]
    party = alive(run)
    for tg in party:
        loot = _loot(content, run, list(floor.loot), rng)
        run["members"][tg]["pocket"] = int(run["members"][tg].get("pocket", 0)) + loot
        if tg == str(ch.player_tg_id):
            lines.append(f"Добыча босса: +{loot} эдди в карман штурма.")
    run.setdefault("log", []).append(f"{ch.state.name} добивает: {name}.")
    _advance(content, run)
    if not floor.boss_final:
        lines += await corpstand.hit(session, content, ch, str(run["corp"]), "lab", int(run.get("lv", 2)) - 1, rng, year)
        nxt = floor_of(content, run)
        if nxt is not None:
            lines.append(f"Путь открыт: {nxt.name}.")
        await _save_run(session, run)
        await save_active(session, ch, tz)
        return lines
    # the final boss: the tower falls; the old tower reward for the one who finished it, the loot for everyone
    run["status"] = DONE
    lines += await corpraid.raid_over(session, content, ch, rng, year, tz, cb.WON,
                                      {"target": run["corp"], "site": "tower", "lv": int(run.get("lv", 2))})
    for tg in party:
        m = run["members"][tg]
        pocket = int(m.get("pocket", 0))
        fx = {"eddies": pocket, "street_cred": c.final_cred, "fame": c.final_fame}
        text = (f"Башня {corp} взята. Твоя доля штурма: {pocket} эдди, уличная репутация +{c.final_cred}, "
                f"слава +{c.final_fame}.")
        await _pay(session, content, run, tg, ch, rng, year, fx, text)
        m["pocket"] = 0
        if tg == str(ch.player_tg_id):
            lines.append(f"Твоя доля штурма: +{pocket} эдди, уличная репутация +{c.final_cred}, слава +{c.final_fame}.")
    ch.state.flags.pop(FLAG, None)
    names = ", ".join(str(run["members"][tg].get("name", "")) for tg in party)
    await loud.post(session, ch.id, f"🏢 Штурм башни {corp} пройден до пентхауса: {names}. Глава СБ лежит, "
                                    f"тревога {run.get('alarm', 0)}/{c.alarm_max}.", kind="tower", w=3)
    await _save_run(session, run)
    await save_active(session, ch, tz)
    return lines


async def _wipe(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
                run: dict) -> list[str]:
    c = cfg(content)
    tg = str(ch.player_tg_id)
    me = run["members"][tg]
    keep = int(me.get("pocket", 0)) * c.wipe_keep_pct // 100
    me["out"], me["pocket"] = True, 0
    ch.state.hp = max(1, ch.state.hp)
    if keep:
        apply_effects(ch.state, {"eddies": keep}, content, rng, year)
    ch.state.flags.pop(FLAG, None)
    run.setdefault("log", []).append(f"{ch.state.name} выбит из башни.")
    lines = [f"Охрана выбрасывает тебя на улицу с последним ОЗ. Из добычи штурма остаётся {keep} эдди."]
    if not alive(run):
        run["status"] = FAILED
        lines.append("Отряда в башне больше нет: штурм провален.")
    else:
        lines.append("Отряд продолжает без тебя.")
    await _save_run(session, run)
    await save_active(session, ch, tz)
    return lines


async def leave(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                tz: str) -> list[str]:
    """Retreat: the pocket is kept whole; an expired or finished run pays its share."""
    run = await current(session, content, ch)
    tg = str(ch.player_tg_id)
    if run is None or tg not in (run.get("members") or {}):
        ch.state.flags.pop(FLAG, None)
        await save_active(session, ch, tz)
        return []
    me = run["members"][tg]
    if me.get("out"):
        ch.state.flags.pop(FLAG, None)
        await save_active(session, ch, tz)
        return []
    pct = 100 if run.get("status") == OPEN else cfg(content).expire_keep_pct
    keep = int(me.get("pocket", 0)) * pct // 100
    me["out"], me["pocket"] = True, 0
    if keep:
        apply_effects(ch.state, {"eddies": keep}, content, rng, year)
    ch.state.flags.pop(FLAG, None)
    run.setdefault("log", []).append(f"{ch.state.name} уходит из башни.")
    if run.get("status") == OPEN and not alive(run):
        run["status"] = FAILED
    await _save_run(session, run)
    await save_active(session, ch, tz)
    return [f"Ты уходишь из башни с добычей: +{keep} эдди."]
