"""City crises (owner request 24.10; engine/crisis.py, content/crises.yaml).

world_state:
- `crisis` = {id, key, started, ends, score {a, b} | {goal}, by {tg: {side, pts}}, bulletin} while one lasts;
- `crisis_after` = {key, text, mods, until}: the aftermath for the whole city;
- `crisis_next` = when the next crisis may start; `crisis_hist` = the last keys (no repeats in a row).
The tick (scheduler, every few minutes) starts, posts, finishes and rewards. Characters feel it through the modifiers
(engine/crisis.py), through `press` at every «Прожить месяц» (an effect and a forced crisis event) and through the window.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.content.loader import Content
from app.db import repo
from app.db.models import PlayerMail
from app.engine import checks
from app.engine import combat as cb
from app.engine import crisis as eng
from app.engine import events as engine_events
from app.engine import jail as jail_eng
from app.engine.effects import AppliedEffects, apply_effects
from app.services import ap as ap_services
from app.services.game import ActiveCharacter, save_active

KEY, AFTER, NEXT, HIST = "crisis", "crisis_after", "crisis_next", "crisis_hist"
SRC = "crisis"
COMMON_FOES = ("scavengers", "animals", "maelstrom", "tyger_claws", "valentinos", "sixth_street")


def _iso(d: datetime) -> str:
    return d.replace(microsecond=0).isoformat()


def _dt(s: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(s)) if s else None
    except ValueError:
        return None


async def current(session: AsyncSession) -> dict | None:
    st = await repo.ws_get(session, KEY, None)
    return dict(st) if isinstance(st, dict) and st.get("key") else None


async def after(session: AsyncSession) -> dict | None:
    a = await repo.ws_get(session, AFTER, None)
    if isinstance(a, dict) and (_dt(a.get("until")) or utcnow()) > utcnow():
        return dict(a)
    return None


async def refresh(session: AsyncSession) -> None:
    """Push the live modifiers into the engine."""
    st = await current(session)
    c = eng.get(st["key"]) if st else None
    a = await after(session)
    eng.set_live(st["key"] if st else None, (c or {}).get("mods") if c else None, (a or {}).get("mods") if a else None)


def day_of(st: dict) -> tuple[int, int]:
    s = eng.settings()
    start = _dt(st.get("started")) or utcnow()
    total = max(1, int(s.get("duration_hours", 72)) // 24)
    return min(total, (utcnow() - start).days + 1), total


def score_line(st: dict) -> str:
    c = eng.get(st.get("key")) or {}
    sc = st.get("score") or {}
    if c.get("kind") == eng.WAR:
        return f"{eng.side_name(c, 'a')} {int(sc.get('a', 0))} — {eng.side_name(c, 'b')} {int(sc.get('b', 0))}"
    return f"сдерживание {int(sc.get('goal', 0))}/{int(c.get('target', 1))}"


def title(st: dict) -> str:
    c = eng.get(st.get("key")) or {}
    return f"{c.get('icon', '🔥')} {c.get('name', '')}"


async def menu_line(session: AsyncSession, ch: ActiveCharacter | None = None) -> str | None:
    st = await current(session)
    if st is None:
        a = await after(session)
        return f"🕯 Последствия: {a.get('text', '')}" if a and a.get("text") else None
    day, total = day_of(st)
    line = f"{title(st)}: день {day} из {total} · {score_line(st)}"
    if ch is not None:
        c = eng.get(st["key"]) or {}
        side = eng.sync_side(ch.state, st.get("id"))
        if c.get("kind") == eng.WAR:
            line += f" · ты за: {eng.side_name(c, side)}" if side else " · ты ни за кого"
    return line


# -- points ------------------------------------------------------------------------------------------------------------


async def _add(session: AsyncSession, st: dict, tg: int, side: str | None, pts: int) -> None:
    c = eng.get(st.get("key")) or {}
    by = dict(st.get("by") or {})
    rec = dict(by.get(str(tg)) or {})
    if side and not rec.get("side"):
        rec["side"] = side
    rec["pts"] = int(rec.get("pts", 0) or 0) + pts
    by[str(tg)] = rec
    st["by"] = by
    sc = dict(st.get("score") or {})
    if c.get("kind") == eng.WAR:
        s = rec.get("side")
        if s in ("a", "b"):
            sc[s] = int(sc.get(s, 0)) + pts
    else:
        sc["goal"] = int(sc.get("goal", 0)) + pts
    st["score"] = sc
    await repo.ws_set(session, KEY, st)


async def harvest(session: AsyncSession, content: Content, ch: ActiveCharacter, tz: str) -> int:
    """Move a character's pending crisis points (from events) and side pick into the crisis."""
    st = await current(session)
    before = dict(ch.state.flags)
    side = eng.sync_side(ch.state, st.get("id") if st else None)
    pts = eng.take_points(ch.state)
    if st is not None and (pts or side):
        c = eng.get(st["key"]) or {}
        if c.get("kind") == eng.WAR and not side:
            pts = 0  # a war counts only for a side
        rec = (st.get("by") or {}).get(str(ch.player_tg_id)) or {}
        if pts or (side and rec.get("side") != side):
            await _add(session, st, ch.player_tg_id, side, max(0, pts))
    if ch.state.flags != before:
        await save_active(session, ch, tz)
    return pts


async def join(session: AsyncSession, content: Content, ch: ActiveCharacter, tz: str, side: str) -> str | None:
    st = await current(session)
    if st is None:
        return "none"
    c = eng.get(st["key"]) or {}
    if c.get("kind") != eng.WAR or side not in ("a", "b"):
        return "none"
    if eng.sync_side(ch.state, st["id"]):
        return "side"
    ch.state.flags[eng.FLAG_SIDE] = side
    ch.state.flags[eng.FLAG_ID] = st["id"]
    await save_active(session, ch, tz)
    await _add(session, st, ch.player_tg_id, side, 0)
    return None


# -- the press --------------------------------------------------------------------------------------------------------


async def press(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int) -> list[str]:
    """Every «Прожить месяц» during a crisis: its weight on the character and one of its events."""
    st = await current(session)
    if st is None:
        return []
    c = eng.get(st["key"]) or {}
    out = []
    fx = dict(c.get("press_fx") or {})
    if fx and not jail_eng.in_jail(ch.state):
        apply_effects(ch.state, fx, content, rng, year)
        if ch.state.hp <= 0:
            ch.state.hp = 1
        out.append(f"{title(st)}: {c.get('press_text', '')}")
    eng.sync_side(ch.state, st.get("id"))
    if not jail_eng.in_jail(ch.state) and not ch.state.flags.get("abroad"):
        from app.engine import derived
        from app.services.week import append_capped

        flags = derived.event_flags(ch.state, content)
        pool = [e for e in c.get("events") or [] if e in content.events
                and engine_events.matches(content.events[e], ch.state, content, flags)]
        if pool:
            append_capped(ch, rng.choice(pool))
        for e in c.get("always") or []:  # owner request 24.10: the draft notice comes to everyone once per crisis
            mark = f"cr_always:{st.get('id')}:{e}"
            if e in content.events and not ch.state.flags.get(mark):
                ch.state.flags[mark] = True
                append_capped(ch, e)
    return out


# -- actions of the window --------------------------------------------------------------------------------------------


@dataclass
class Result:
    blocked: str | None = None
    text: str = ""
    check: object | None = None
    applied: AppliedEffects | None = None
    pts: int = 0
    fight: object | None = None
    lines: list[str] = field(default_factory=list)


def _fight_faction(content: Content, c: dict, act: dict, side: str | None, year: int, rng: random.Random) -> str:
    wanted: list[str] = []
    if act.get("faction"):
        wanted.append(str(act["faction"]))
    if c.get("kind") == eng.WAR and side:
        wanted.append(str(((c.get("sides") or {}).get(side) or {}).get("fight", "")))
    pool = list(COMMON_FOES)
    rng.shuffle(pool)
    for f in [w for w in wanted if w] + pool:
        if cb._faction_pool(content, f, year):  # noqa: SLF001
            return f
    return "ncpd"


async def act(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
              key: str) -> Result:
    st = await current(session)
    if st is None:
        return Result("none")
    c = eng.get(st["key"]) or {}
    a = (c.get("actions") or {}).get(key)
    if a is None:
        return Result("none")
    stt = ch.state
    if jail_eng.in_jail(stt):
        return Result("jail")
    if stt.flags.get("abroad") and a.get("kind") != "pay":  # owner request 25.10: from abroad only money helps
        return Result("abroad")
    side = eng.sync_side(stt, st["id"])
    if c.get("kind") == eng.WAR and not side:
        return Result("side")
    kind = a.get("kind")
    if kind == "pay":
        price = int(a.get("eddies", 0))
        rec = dict((st.get("by") or {}).get(str(ch.player_tg_id)) or {})
        if int(rec.get("paid", 0) or 0) >= int(eng.settings().get("pay_limit", 3)):
            return Result("paid")  # owner report 24.10: one rich player must not buy the whole crisis
        if stt.eddies < price:
            return Result("eddies")
        rec["paid"] = int(rec.get("paid", 0) or 0) + 1
        st.setdefault("by", {})[str(ch.player_tg_id)] = rec
        applied = apply_effects(stt, {"eddies": -price}, content, rng, year)
        pts = int(a.get("pts", 0))
        await save_active(session, ch, tz)
        await _add(session, st, ch.player_tg_id, side, pts)
        return Result(None, str(a.get("text", "")), None, applied, pts)
    ap = int(a.get("ap", 1))
    if not ap_services.can_spend(ch, ap):
        return Result("ap")
    from app.services import events as ev_service

    if ev_service.has_pending(ch):
        return Result("busy")
    ap_services.spend(ch, content, ap)
    if kind == "fight":
        from app.services import combat

        faction = _fight_faction(content, c, a, side, year, rng)
        await save_active(session, ch, tz)
        from app.engine import perks

        lv = int(perks.rec(stt, content).get("level", 1))  # the front matches the fighter: danger grows with the level
        danger = max(1, min(int(a.get("danger", 3)) + 1, 1 + lv // 4))
        step = await combat.begin(session, content, ch, rng, year, tz, faction=faction, danger=danger,
                                  src={"src": SRC, "crisis": st["id"]})
        return Result(None, str(a.get("text", "")), fight=step)
    chk = checks.roll_check(stt, content, rng, str(a.get("stat", "COOL")), a.get("skill"), int(a.get("dv", 13)))
    if chk.ok:
        pts = int(a.get("pts", 0))
        applied = None
        text = str(a.get("text", ""))
    else:
        pts = 0
        applied = apply_effects(stt, dict(a.get("fail_fx") or {}), content, rng, year)
        text = str(a.get("fail_text", ""))
    if stt.hp <= 0:
        stt.hp = 1
    await save_active(session, ch, tz)
    if pts:
        await _add(session, st, ch.player_tg_id, side, pts)
    return Result(None, text, chk, applied, pts)


async def fight_over(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                     tz: str, result: str, ctx: dict) -> list[str]:
    st = await current(session)
    if st is None or st.get("id") != ctx.get("crisis") or result != cb.WON:
        return []
    pts = int(eng.settings().get("fight_pts", 5))
    await _add(session, st, ch.player_tg_id, eng.sync_side(ch.state, st["id"]), pts)
    return [f"{title(st)}: твой вклад +{pts}. {score_line(await current(session) or st)}."]


# -- the clock --------------------------------------------------------------------------------------------------------


async def _start(session: AsyncSession, content: Content, rng: random.Random) -> str:
    hist = list(await repo.ws_get(session, HIST, []) or [])
    pool = [(k, int(c.get("weight", 1))) for k, c in eng.crises().items() if k not in hist[-2:]]
    from app.services import blackwall as bw_svc

    taint = await bw_svc.city_total(session)
    pool = [(k, w + (4 if k == "blackwall_breach" and taint >= 10 else 0)) for k, w in pool]
    if not pool:
        pool = [(k, 1) for k in eng.crises()]
    keys, weights = zip(*pool)
    key = rng.choices(keys, weights=weights)[0]
    c = eng.get(key) or {}
    now = utcnow()
    st = {"id": f"{key}-{now.strftime('%m%d%H%M')}", "key": key, "started": _iso(now),
          "ends": _iso(now + timedelta(hours=int(eng.settings().get("duration_hours", 72)))),
          "score": {"a": 0, "b": 0} if c.get("kind") == eng.WAR else {"goal": 0}, "by": {}, "bulletin": _iso(now)}
    await repo.ws_set(session, KEY, st)
    await repo.ws_set(session, HIST, (hist + [key])[-5:])
    how = ("Выбери сторону: события сами придут к тебе, а в меню есть окно кризиса. Победившая сторона заберёт "
           "награду, город заплатит за любой исход." if c.get("kind") == eng.WAR else
           f"Нужно {c.get('target')} очков вклада за {int(eng.settings().get('duration_hours', 72)) // 24} дня. "
           "Не успеем — заплатит весь город.")
    return f"{title(st)}\n{c.get('intro', '')}\n\n{how} Окно кризиса: /play → «{c.get('name', '')}»."


async def start_forced(session: AsyncSession, content: Content, key: str, ends: datetime,
                       rng: random.Random) -> list[str]:
    """Owner request 26.10: the season's last night starts on time; a crisis still running ends first."""
    posts: list[str] = []
    cur = await current(session)
    if cur is not None:
        posts.append(await _finish(session, content, cur, rng))
    c = eng.get(key) or {}
    now = utcnow()
    st = {"id": f"{key}-{now.strftime('%m%d%H%M')}", "key": key, "started": _iso(now), "ends": _iso(ends),
          "score": {"goal": 0}, "by": {}, "bulletin": _iso(now)}
    await repo.ws_set(session, KEY, st)
    hours = max(1, int((ends - now).total_seconds() // 3600))
    posts.append(f"{title(st)}\n{c.get('intro', '')}\n\nНужно {c.get('target')} очков вклада за {hours} ч. "
                 f"Итог ночи решит, с чем город начнёт новый сезон. Окно: /play → «{c.get('name', '')}».")
    await refresh(session)
    return posts


async def _names(session: AsyncSession, tgs: list[str]) -> list[str]:
    return [n for n in [await repo.display_name(session, int(t), "") for t in tgs] if n]


async def _finish(session: AsyncSession, content: Content, st: dict, rng: random.Random) -> str:
    s = eng.settings()
    c = eng.get(st["key"]) or {}
    sc = st.get("score") or {}
    by = st.get("by") or {}
    base, per, cap = int(s.get("reward_base", 1500)), int(s.get("reward_per_pt", 150)), int(s.get("reward_cap", 15000))
    lose_base = int(s.get("loser_base", 300))
    winners: list[str] = []
    if c.get("kind") == eng.WAR:
        a, b = int(sc.get("a", 0)), int(sc.get("b", 0))
        win = "a" if a > b else "b" if b > a else rng.choice(["a", "b"])
        text = str((c.get("win_text") or {}).get(win, ""))
        mods = (c.get("after") or {}).get(win) or {}
        head = f"{title(st)}: конец. Победа: <b>{eng.side_name(c, win)}</b> ({score_line(st)})."
    else:
        win = "win" if int(sc.get("goal", 0)) >= int(c.get("target", 1)) else "fail"
        text = str(c.get("win_text" if win == "win" else "fail_text", ""))
        mods = (c.get("after") or {}).get(win) or {}
        head = f"{title(st)}: конец. {'Город справился' if win == 'win' else 'Город не справился'} ({score_line(st)})."
    for tg, rec in by.items():
        pts = int((rec or {}).get("pts", 0) or 0)
        if pts <= 0:
            continue
        good = (rec.get("side") == win) if c.get("kind") == eng.WAR else win == "win"
        pay = min(cap, base + per * pts) if good else lose_base + 50 * pts
        if good:
            winners.append(tg)
        session.add(PlayerMail(to_tg_id=int(tg), from_tg_id=0, from_name="Город", kind="city",
                               payload={"eddies": pay, "text": f"{c.get('name', 'Кризис')}: {'награда' if good else 'компенсация'} "
                                                                f"за вклад {pts}: +{pay} эдди."}))
    fm = c.get("fail_mail")  # owner request 24.10: a lost external threat hits every living character
    if c.get("kind") == eng.WAR:
        fm = fm.get(win) if isinstance(fm, dict) and isinstance(fm.get(win), dict) else None
    elif win != "fail":
        fm = None
    if isinstance(fm, dict) and fm.get("text"):
        from sqlalchemy import select

        from app.db.models import Character

        res = await session.execute(select(Character).where(Character.alive.is_(True)))
        for row in res.scalars():
            if (row.flags or {}).get("abroad"):  # owner request 25.10: the lost city does not reach those abroad
                continue
            session.add(PlayerMail(to_tg_id=row.player_tg_id, from_tg_id=0, from_name="Город", kind="city", to_char_id=row.id,
                                   payload={"text": f"{c.get('name', 'Кризис')}: {fm['text']}",
                                            **{k: int(fm[k]) for k in ("pct", "hp", "mood") if fm.get(k)}}))
    if c.get("finale"):  # owner request 26.10: the season's last night decides the start of the next one
        await repo.ws_set(session, "season_finale", {"win": win == "win"})
    now = utcnow()
    hours = int(c.get("after_hours") or s.get("after_hours", 24))
    await repo.ws_set(session, AFTER, {"key": st["key"], "text": text, "mods": mods,
                                       "until": _iso(now + timedelta(hours=hours))})
    await repo.ws_set(session, NEXT, _iso(now + timedelta(hours=int(s.get("gap_hours", 18)))))
    await repo.ws_set(session, KEY, {})
    heroes = await _names(session, winners)
    return (f"{head}\n{text}\n\n" + (f"Награды: {', '.join(heroes)}. Эдди ждут в /play.\n" if heroes else "")
            + ("Город проиграл: каждый получит удар почтой. " if isinstance(fm, dict) and fm.get("text") else "")
            + f"Последствия держатся {hours} ч и касаются всех.")


async def tick(session: AsyncSession, content: Content, rng: random.Random, quiet: bool) -> list[str]:
    """Start, bulletin and finish. Posts wait for the end of quiet hours, except the finish."""
    posts: list[str] = []
    st = await current(session)
    now = utcnow()
    c_now = eng.get(st.get("key")) if st else None
    done_early = bool(c_now and c_now.get("kind") == eng.COMMON
                      and int((st.get("score") or {}).get("goal", 0)) >= int(c_now.get("target", 1)))
    if st is not None and (now >= (_dt(st.get("ends")) or now) or done_early):  # a reached goal ends it at once
        posts.append(await _finish(session, content, st, rng))
    elif st is not None:
        last = _dt(st.get("bulletin")) or now
        if not quiet and now - last >= timedelta(hours=int(eng.settings().get("bulletin_hours", 24))):
            st["bulletin"] = _iso(now)
            await repo.ws_set(session, KEY, st)
            day, total = day_of(st)
            by = st.get("by") or {}
            top = sorted(by.items(), key=lambda kv: -int((kv[1] or {}).get("pts", 0) or 0))[:3]
            names = await _names(session, [t for t, r in top if int((r or {}).get("pts", 0) or 0) > 0])
            posts.append(f"{title(st)}: день {day} из {total}. {score_line(st)}."
                         + (f" Больше всех сделали: {', '.join(names)}." if names else " Пока никто не вмешался.")
                         + " Окно кризиса в /play.")
    else:
        nxt = _dt(await repo.ws_get(session, NEXT, None))
        if not quiet and (nxt is None or now >= nxt):
            posts.append(await _start(session, content, rng))
    await refresh(session)
    return posts
