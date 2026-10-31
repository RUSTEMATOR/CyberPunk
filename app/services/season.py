"""Seasons of the city (owner request 25.10, docs/PLAN_ENDGAME.md part 4).

A season lasts `balance.season.days` real days. Deeds that change the city earn season points: challenges, ambitions,
legends, gang wars, raids on corps and gangs, cleansings and strikes, lairs taken. At the end the top three get titles
and money by mail, the group gets the table, the map of the districts goes back to its start, a new season begins.

world_state `season` = {"n", "start", "end", "scores": {tg: {"name", "pts", "parts": {kind: pts}}}, "history": [...]}
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.content.loader import Content
from app.db import repo
from app.db.models import PlayerMail

log = logging.getLogger(__name__)
KEY = "season"
KIND_RU = {"challenge": "вызовы", "ambition": "амбиции", "legend": "легенды", "war": "войны банд", "craid": "налёты на корпорации",
           "graid": "налёты на банды", "street": "зачистки и удары", "lair": "логова и офисы"}


def cfg(content: Content):  # noqa: ANN201
    return content.balance.season


async def state(session: AsyncSession, content: Content) -> dict:
    s = dict(await repo.ws_get(session, KEY, {}) or {})
    if not s.get("end"):
        now = utcnow()
        s = {"n": int(s.get("n", 0) or 0) + 1, "start": now.isoformat(), "end": (now + timedelta(days=cfg(content).days)).isoformat(),
             "scores": {}, "history": list(s.get("history") or [])}
        await repo.ws_set(session, KEY, s)
    return s


async def add(session: AsyncSession, content: Content, tg: int, name: str, kind: str, pts: int) -> None:
    """Season points for a deed. Never breaks the deed."""
    if pts <= 0:
        return
    try:
        s = await state(session, content)
        scores = dict(s.get("scores") or {})
        rec = dict(scores.get(str(tg)) or {"name": name, "pts": 0, "parts": {}})
        rec["name"] = name or rec.get("name", "")
        rec["pts"] = int(rec.get("pts", 0)) + pts
        parts = dict(rec.get("parts") or {})
        parts[kind] = int(parts.get(kind, 0)) + pts
        rec["parts"] = parts
        scores[str(tg)] = rec
        s["scores"] = scores
        await repo.ws_set(session, KEY, s)
    except Exception:  # noqa: BLE001
        log.exception("season: add failed")


async def add_ch(session: AsyncSession, content: Content, ch, kind: str, pts: int) -> None:  # noqa: ANN001
    await add(session, content, ch.player_tg_id, f"{ch.state.name} {ch.state.surname}".strip(), kind, pts)


def table(s: dict) -> list[tuple[str, dict]]:
    return sorted((s.get("scores") or {}).items(), key=lambda kv: -int(kv[1].get("pts", 0)))


def left_text(s: dict) -> str:
    try:
        left = datetime.fromisoformat(s["end"]) - utcnow()
    except (KeyError, ValueError):
        return ""
    d = max(0, left.days)
    h = max(0, left.seconds // 3600)
    return f"{d} дн. {h} ч."


async def tick(session: AsyncSession, content: Content) -> list[str]:
    """Close the season when its time is up. Returns posts for the group."""
    from app.engine import control as ce
    from app.services import control

    c = cfg(content)
    s = await state(session, content)
    end = datetime.fromisoformat(s["end"])
    if end > utcnow():
        if end - utcnow() <= timedelta(hours=c.finale_hours) and s.get("finale") != s["n"]:
            import random

            from app.services import crisis  # owner request 26.10: the last night of the season, for everyone

            s["finale"] = s["n"]
            await repo.ws_set(session, KEY, s)
            await repo.ws_set(session, "season_finale", None)
            return await crisis.start_forced(session, content, c.finale_key, end, random.Random())
        return []
    pre: list[str] = []
    from app.services import crisis

    cur = await crisis.current(session)
    if cur is not None and cur.get("key") == c.finale_key:  # the last night ends with the season, before the reset
        import random

        pre.append(await crisis._finish(session, content, cur, random.Random()))  # noqa: SLF001
    top = table(s)
    lines = [f"🏁 <b>Сезон {s['n']} закрыт.</b>"]
    if not top:
        lines.append("Никто не заработал ни очка. Город спал.")
    for i, (tg, rec) in enumerate(top[:10]):
        prize = c.prizes[i] if i < len(c.prizes) else 0
        mark = ("🥇", "🥈", "🥉")[i] if i < 3 else f"{i + 1}."
        lines.append(f"{mark} {rec.get('name')} — {rec.get('pts')} очк." + (f" · приз {prize} эдди" if prize else ""))
        if prize:
            title = f"«{c.titles[i]} сезона {s['n']}»" if i < len(c.titles) else ""
            session.add(PlayerMail(to_tg_id=int(tg), from_tg_id=0, from_name="Город", kind="city",
                                   payload={"text": f"Сезон {s['n']}: {i + 1}-е место. {title} Приз.", "eddies": prize,
                                            "fame": max(0, 3 - i)}))
    fresh = ce.normalize(content, {})  # the districts start over
    fin = await repo.ws_get(session, "season_finale", None)
    if isinstance(fin, dict):  # owner request 26.10: the last night shapes the start
        k = -c.finale_shift if fin.get("win") else c.finale_shift
        for dk in list(fresh):
            ce.shift(fresh, dk, gang=k, corp=k)
        await repo.ws_set(session, "season_finale", None)
    await control.save(session, fresh)
    lines.append("Карта районов сброшена к началу: банды и корпорации снова на старте. Новый сезон уже идёт.")
    if isinstance(fin, dict):
        lines.append("Последняя ночь " + ("осталась за городом: банды и корпорации начинают сезон слабее."
                                          if fin.get("win") else
                                          "осталась за бандами и корпорациями: они начинают сезон сильнее."))
    hist = (list(s.get("history") or []) + [{"n": s["n"], "top": [(rec.get("name"), rec.get("pts")) for _, rec in top[:3]]}])[-20:]
    now = utcnow()
    await repo.ws_set(session, KEY, {"n": int(s["n"]) + 1, "start": now.isoformat(),
                                     "end": (now + timedelta(days=c.days)).isoformat(), "scores": {}, "history": hist})
    return pre + ["\n".join(lines)]
