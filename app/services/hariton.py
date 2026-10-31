"""The event of the week with the cat Hariton (owner request 25.10).

A ginger exotic with a naturally gloomy face who «talks» with sound buttons and asks things like «Почему я грустный?».
Every week the city gets one question about him; players bet «Да» or «Нет». On Monday at `hour` the week closes: the
answer is rolled by the question's chance, the winners take their stakes back plus the losers' pot (and the bank carried
over) in proportion to their stakes. Nobody on the winning side: the whole pot carries over to the next week.

world_state `hariton` = {"week", "q", "bets": {tg: {"side": "yes"|"no", "amount", "name", "char"}}, "bank", "recent": [keys]}
"""

from __future__ import annotations

import random
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.db import repo
from app.db.models import PlayerMail
from app.engine.effects import apply_effects
from app.services.game import ActiveCharacter, save_active

KEY = "hariton"
SIDES = {"yes": "Да", "no": "Нет"}


def cfg(content: Content):  # noqa: ANN201
    return content.balance.hariton


def week_key(now: datetime) -> str:
    y, w, _ = now.date().isocalendar()
    return f"{y}-W{w:02d}"


MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября",
          "декабря")


def closes(content: Content, week: str) -> str:
    """'понедельник, 2 ноября, 12:00': when the question of `week` closes (the Monday of the next week)."""
    from datetime import date, timedelta

    try:
        y, w = week.split("-W")
        d = date.fromisocalendar(int(y), int(w), 1) + timedelta(days=7)
    except ValueError:
        return f"понедельник, {cfg(content).hour}:00"
    return f"понедельник, {d.day} {MONTHS[d.month - 1]}, {cfg(content).hour}:00"


def question(content: Content, key: str):  # noqa: ANN201
    return next((q for q in cfg(content).questions if q.key == key), None)


async def load(session: AsyncSession) -> dict:
    r = await repo.ws_get(session, KEY, None)
    return dict(r) if isinstance(r, dict) else {}


async def _save(session: AsyncSession, r: dict) -> None:
    await repo.ws_set(session, KEY, r)


def _pick(content: Content, recent: list[str], rng: random.Random) -> str:
    pool = [q.key for q in cfg(content).questions if q.key not in recent] or [q.key for q in cfg(content).questions]
    return rng.choice(pool)


def totals(r: dict) -> tuple[int, int]:
    bets = r.get("bets") or {}
    yes = sum(int(b.get("amount", 0) or 0) for b in bets.values() if b.get("side") == "yes")
    no = sum(int(b.get("amount", 0) or 0) for b in bets.values() if b.get("side") == "no")
    return yes, no


def payout_per(r: dict, side: str) -> float:
    """What one eddy on `side` returns if it wins (the stake included)."""
    yes, no = totals(r)
    mine, theirs = (yes, no) if side == "yes" else (no, yes)
    bank = int(r.get("bank", 0) or 0)
    return 1 + (theirs + bank) / mine if mine else 0.0


def _announce(content: Content, r: dict) -> str:
    q = question(content, r["q"])
    bank = int(r.get("bank", 0) or 0)
    return ("🐱 <b>Харитон недели</b>\n"
            "Рыжий экзот с вечно угрюмой мордочкой снова у своих звуковых кнопок. Вопрос недели:\n"
            f"<b>{q.text}</b>\n"
            + (f"В банке с прошлой недели: {bank} эдди.\n" if bank else "")
            + f"Ставки «Да» или «Нет» до: {closes(content, str(r.get('week', '')))}. /play → «Город» → «🐱 Харитон недели».")


def _resolve(content: Content, r: dict, rng: random.Random) -> tuple[str, list[tuple[int, int, int, str]], int]:
    """(answer side, [(tg, char, payout, name)], bank carried over)."""
    q = question(content, r["q"])
    side = "yes" if q is not None and rng.random() < q.p_yes else "no"
    bets = r.get("bets") or {}
    yes, no = totals(r)
    win_pot, lose_pot = (yes, no) if side == "yes" else (no, yes)
    bank = int(r.get("bank", 0) or 0)
    if not win_pot:
        return side, [], bank + lose_pot
    prize = lose_pot + bank
    pays = []
    for tg, b in bets.items():
        if b.get("side") != side:
            continue
        amt = int(b.get("amount", 0) or 0)
        pays.append((int(tg), int(b.get("char", 0) or 0), amt + prize * amt // win_pot, str(b.get("name", ""))))
    return side, pays, 0


async def tick(session: AsyncSession, content: Content, tz: str, rng: random.Random) -> list[str]:
    """Called every few minutes: opens the first week, closes a finished week on Monday after `hour`."""
    now = datetime.now(ZoneInfo(tz))
    wk = week_key(now)
    r = await load(session)
    if not r:  # the first question: opened late in the week, it runs through the next week too
        from datetime import timedelta

        first = week_key(now + timedelta(days=7)) if now.weekday() >= 4 else wk
        r = {"week": first, "q": _pick(content, [], rng), "bets": {}, "bank": 0, "recent": []}
        await _save(session, r)
        return [_announce(content, r)]
    if str(r.get("week", "")) >= wk or (now.weekday() == 0 and now.hour < cfg(content).hour):  # "YYYY-Www" sorts by time
        return []
    posts: list[str] = []
    q = question(content, r.get("q", ""))
    side, pays, bank = _resolve(content, r, rng)
    for tg, char, amt, name in pays:
        session.add(PlayerMail(to_tg_id=tg, from_tg_id=0, from_name="Харитон", kind="city",
                               payload={"text": f"Ставка на Харитона сыграла: +{amt} эдди.", "eddies": amt}))
    if q is not None:
        lines = [f"🐱 <b>Харитон: итоги недели</b>\n{q.text}\nОтвет: <b>{SIDES[side]}</b>. "
                 + (q.yes_text if side == "yes" else q.no_text)]
        if pays:
            lines.append("Выиграли: " + ", ".join(f"{name} +{amt}" for _, _, amt, name in pays) + ".")
        elif r.get("bets"):
            lines.append(f"На «{SIDES[side]}» никто не ставил. Весь банк, {bank} эдди, переходит на следующую неделю.")
        posts.append("\n".join(lines))
    recent = (list(r.get("recent") or []) + [r.get("q", "")])[-cfg(content).recent:]
    r = {"week": wk, "q": _pick(content, recent, rng), "bets": {}, "bank": bank, "recent": recent}
    await _save(session, r)
    posts.append(_announce(content, r))
    return posts


async def bet(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
              side: str, amount: int) -> str | None:
    c = cfg(content)
    r = await load(session)
    if not r or side not in SIDES:
        return "closed"
    bets = dict(r.get("bets") or {})
    mine = dict(bets.get(str(ch.player_tg_id)) or {})
    if mine and mine.get("side") != side:
        return "other_side"
    have = int(mine.get("amount", 0) or 0)
    if amount < c.min_bet:
        return "min"
    if have + amount > c.max_bet:
        return "max"
    if ch.state.eddies < amount:
        return "eddies"
    apply_effects(ch.state, {"eddies": -amount}, content, rng, year)
    bets[str(ch.player_tg_id)] = {"side": side, "amount": have + amount, "char": ch.id,
                                  "name": f"{ch.state.name} {ch.state.surname}".strip()}
    r["bets"] = bets
    await _save(session, r)
    await save_active(session, ch, tz)
    return None
