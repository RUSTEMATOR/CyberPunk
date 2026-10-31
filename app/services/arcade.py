"""The arcade as a service (owner request 29.10, docs/PLAN_ARCADE.md): the state of the games, the money, the records.

Everything of a player lives in one `world_state` row `arcade:<tg_id>` (a JSON dict), the records of all players in
`arcade:top`; no tables, so a closed window loses nothing. Money goes to the living character through `apply_effects`
and never above `balance.arcade.daily_cap` a Kyiv day.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import random
import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import kyiv_date
from app.content.loader import Content
from app.db import repo
from app.engine import arcade as eng
from app.engine.effects import apply_effects
from app.services.game import ActiveCharacter, save_active

log = logging.getLogger(__name__)

TOP_KEY = "arcade:top"
GAMES = ("wordle", "snake", "geo", "beat", "town")
GAME_NAMES = {"wordle": "Взлом пароля", "snake": "Змейка", "geo": "Угадай район", "beat": "Рэп-баттл", "town": "Застройка"}
GAME_UNITS = {"wordle": "побед", "snake": "длина", "geo": "очков за заход", "beat": "раундов", "town": "зданий"}


def cfg(content: Content):
    return content.balance.arcade


def _key(tg_id: int) -> str:
    return f"arcade:{tg_id}"


async def load(session: AsyncSession, tg_id: int) -> dict[str, Any]:
    return dict(await repo.ws_get(session, _key(tg_id), {}) or {})


async def store(session: AsyncSession, tg_id: int, st: dict[str, Any]) -> None:
    await repo.ws_set(session, _key(tg_id), st)


# -- the money and the records -----------------------------------------------------------------------------------


def earned_today(st: dict[str, Any], day: str) -> int:
    earn = st.get("earn") or {}
    return int(earn.get("sum", 0)) if earn.get("day") == day else 0


async def pay(
    session: AsyncSession, content: Content, ch: ActiveCharacter, st: dict[str, Any], rng: random.Random, year: int,
    tz: str, amount: int,
) -> int:
    """Eddies from a finished game, cut at the daily cap. `st` is the player's arcade dict (changed in place, the caller
    stores it). Returns what was really paid."""
    day = kyiv_date(tz)
    room = max(0, cfg(content).daily_cap - earned_today(st, day))
    paid = max(0, min(int(amount), room))
    if paid:
        apply_effects(ch.state, {"eddies": paid}, content, rng, year)
        await save_active(session, ch, tz)
    st["earn"] = {"day": day, "sum": earned_today(st, day) + paid}
    return paid


async def top_record(session: AsyncSession, game: str, tg_id: int, name: str, value: int) -> bool:
    """Keeps the best value of a player in a game; True when it is a new best."""
    top = dict(await repo.ws_get(session, TOP_KEY, {}) or {})
    board = dict(top.get(game) or {})
    mine = board.get(str(tg_id)) or {}
    if int(mine.get("best", 0)) >= value:
        return False
    board[str(tg_id)] = {"name": name, "best": int(value)}
    top[game] = board
    await repo.ws_set(session, TOP_KEY, top)
    return True


async def top_list(session: AsyncSession, game: str, n: int) -> list[tuple[str, int]]:
    board = (await repo.ws_get(session, TOP_KEY, {}) or {}).get(game) or {}
    rows = sorted(((str(v.get("name", "")), int(v.get("best", 0))) for v in board.values()), key=lambda x: -x[1])
    return rows[:n]


async def my_best(session: AsyncSession, game: str, tg_id: int) -> int:
    board = (await repo.ws_get(session, TOP_KEY, {}) or {}).get(game) or {}
    return int((board.get(str(tg_id)) or {}).get("best", 0))


# -- Wordle ---------------------------------------------------------------------------------------------------------

WORDLE_TRIES = 6
GEO_OPTIONS = 6  # districts offered as answers in a round


def wordle_state(content: Content, st: dict[str, Any], day: str) -> dict[str, Any]:
    w = st.get("wordle") or {}
    if w.get("day") != day:
        w = {"day": day, "guesses": [], "done": False, "win": False, "paid": 0}
        st["wordle"] = w
    return w


async def wordle_guess(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str, text: str,
) -> tuple[dict[str, Any], str]:
    """One typed guess. Returns the day's state and a note ('' when the guess was taken)."""
    tg = ch.player_tg_id
    day = kyiv_date(tz)
    st = await load(session, tg)
    w = wordle_state(content, st, day)
    if w["done"]:
        return w, "На сегодня пароль уже взломан или заблокирован. Новый придёт завтра."
    if not eng.valid_word(text):
        return w, "Нужно слово из пяти русских букв."
    guess = eng.norm(text)
    if guess in w["guesses"]:
        return w, "Это слово уже было."
    answer = eng.word_of_day(cfg(content).wordle.words, day)
    w["guesses"].append(guess)
    note = ""
    if guess == answer:
        w["done"], w["win"] = True, True
        rewards = cfg(content).wordle.reward_by_attempt
        paid = await pay(session, content, ch, st, rng, year, tz, rewards[min(len(w["guesses"]), len(rewards)) - 1])
        w["paid"] = paid
        st["wordle_wins"] = int(st.get("wordle_wins", 0)) + 1
        await top_record(session, "wordle", tg, ch.state.name, int(st["wordle_wins"]))
    elif len(w["guesses"]) >= WORDLE_TRIES:
        w["done"] = True
    await store(session, tg, st)
    return w, note


# -- the snake -------------------------------------------------------------------------------------------------------


async def snake_finish(
    session: AsyncSession, content: Content, ch: ActiveCharacter, st: dict[str, Any], rng: random.Random, year: int,
    tz: str,
) -> tuple[int, bool]:
    """Pays and records a finished snake once. Returns (paid, new record)."""
    sn = st["snake"]
    if sn.get("settled"):
        return int(sn.get("paid", 0)), False
    sn["settled"] = True
    score = int(sn["score"])
    paid = await pay(session, content, ch, st, rng, year, tz, score * cfg(content).snake.eddies_per_food)
    sn["paid"] = paid
    record = bool(score) and await top_record(session, "snake", ch.player_tg_id, ch.state.name, score + 3)
    return paid, record


# -- the rhythm sequence ---------------------------------------------------------------------------------------------


async def beat_finish(
    session: AsyncSession, content: Content, ch: ActiveCharacter, st: dict[str, Any], rng: random.Random, year: int,
    tz: str,
) -> tuple[int, bool]:
    b = st["beat"]
    if b.get("settled"):
        return int(b.get("paid", 0)), False
    b["settled"] = True
    done = int(b["round"]) - 1  # rounds repeated to the end
    c = cfg(content).beat
    paid = await pay(session, content, ch, st, rng, year, tz, max(0, done - c.free_rounds) * c.eddies_per_round)
    b["paid"] = paid
    record = bool(done) and await top_record(session, "beat", ch.player_tg_id, ch.state.name, done)
    return paid, record


# -- the district guessing --------------------------------------------------------------------------------------------


def geo_districts(content: Content, year: int) -> list[Any]:
    return [d for d in content.available("districts", year).values() if d.city == "nc"]


def _mask(text: str, name: str) -> str:
    return text.replace(name, "…").replace(name.lower(), "…")


def geo_clues(content: Content, district: Any, rng: random.Random) -> list[str]:
    """Three clues of a district, from the vaguest to the plainest."""
    out: list[str] = []
    if district.places:
        out.append("Рядом: " + _mask(rng.choice(district.places), district.name) + ".")
    out.append(_mask(district.description, district.name))
    gangs = [content.gangs[g].name for g in district.gangs if g in content.gangs]
    corps = [content.corps[c].name for c in district.corps if c in content.corps]
    fixer = content.fixers.get(district.fixer)
    third = ["Банды: " + (", ".join(gangs) if gangs else "нет") + ".",
             "Корпорации: " + (", ".join(corps) if corps else "нет") + "."]
    if fixer is not None:
        third.append(f"Фиксер района: {fixer.name}. Опасность {district.danger} из 5.")
    out.append(" ".join(third))
    return out


def geo_new_round(content: Content, year: int, rng: random.Random, geo: dict[str, Any] | None) -> dict[str, Any]:
    """The next round: a district that was not asked in this run yet (when there are enough districts)."""
    pool = geo_districts(content, year)
    used = list((geo or {}).get("used") or [])
    fresh = [d for d in pool if d.key not in used] or pool
    d = rng.choice(fresh)
    total = cfg(content).geo.rounds
    others = [x.key for x in pool if x.key != d.key]
    options = [d.key] + rng.sample(others, min(len(others), GEO_OPTIONS - 1))
    rng.shuffle(options)
    return {
        "round": len(used) + 1, "total": total, "district": d.key, "clues": 1, "points": int((geo or {}).get("points", 0)),
        "options": options, "used": used + [d.key], "text": geo_clues(content, d, rng), "done": False, "answered": "",
    }


async def geo_finish(
    session: AsyncSession, content: Content, ch: ActiveCharacter, st: dict[str, Any], rng: random.Random, year: int,
    tz: str,
) -> tuple[int, bool]:
    g = st["geo"]
    if g.get("settled"):
        return int(g.get("paid", 0)), False
    g["settled"] = True
    pts = int(g["points"])
    paid = await pay(session, content, ch, st, rng, year, tz, pts * cfg(content).geo.eddies_per_point)
    g["paid"] = paid
    record = bool(pts) and await top_record(session, "geo", ch.player_tg_id, ch.state.name, pts)
    return paid, record


# -- the town builder --------------------------------------------------------------------------------------------------


async def town_tap(session: AsyncSession, content: Content, ch: ActiveCharacter, r: int, c: int) -> list[list[int]]:
    tg = ch.player_tg_id
    st = await load(session, tg)
    size = cfg(content).town.size
    grid = st.get("town")
    if not grid or len(grid) != size:
        grid = eng.new_town(size)
    if 0 <= r < size and 0 <= c < size:
        eng.town_tap(grid, r, c)
    st["town"] = grid
    await store(session, tg, st)
    await top_record(session, "town", tg, ch.state.name, eng.town_buildings(grid))
    return grid


# -- the open-source HTML5 games (owner request 29.10, docs/PLAN_ARCADE_WEB.md) ------------------------------------------


def _web_key(bot_token: str) -> bytes:
    return hashlib.sha256(("arcade|" + bot_token).encode()).digest()


def web_token(bot_token: str, tg_id: int, game_key: str) -> str:
    """A signed pass for one player and one game: the page sends it back with the score."""
    body = base64.urlsafe_b64encode(json.dumps({"u": tg_id, "g": game_key, "t": int(time.time())},
                                               separators=(",", ":")).encode()).decode().rstrip("=")
    sig = hmac.new(_web_key(bot_token), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{sig}"


def read_web_token(bot_token: str, token: str, ttl_h: int) -> dict[str, Any] | None:
    try:
        body, sig = token.rsplit(".", 1)
        good = hmac.new(_web_key(bot_token), body.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, good):
            return None
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if time.time() - int(data.get("t", 0)) > ttl_h * 3600:
            return None
        return data
    except Exception:
        return None


def web_url(content: Content, bot_token: str, tg_id: int, game_key: str) -> str:
    base = cfg(content).web.base_url.rstrip("/")
    return f"{base}/{game_key}/index.html#t={web_token(bot_token, tg_id, game_key)}"


def web_games(content: Content) -> dict[str, Any]:
    return dict(cfg(content).web.games)


async def web_score(ui: Any, token: str, score: int) -> dict[str, Any]:
    """A finished run of an HTML5 game: the record and the money, under the player's lock and his own date (the same
    path as a button press in the Mini App)."""
    from app.db import session as db
    from app.services import game, world as world_service

    content: Content = ui.content
    w = cfg(content).web
    data = read_web_token(ui.settings.bot_token, token, w.token_ttl_h)
    if data is None or str(data.get("g")) not in w.games:
        return {"status": "auth"}
    tg, key = int(data["u"]), str(data["g"])
    gc = w.games[key]
    sc = max(0, min(int(score), gc.max_score))
    async with ui.lock_for(tg):
        actor = world_service.set_actor(tg)
        try:
            async with db.session_scope() as s:
                ch = await game.get_active_character(s, tg)
                if ch is None:
                    return {"status": "dead", "score": sc}
                st = await load(s, tg)
                record = bool(sc) and await top_record(s, "web:" + key, tg, ch.state.name, sc)
                now = int(time.time())
                last = dict(st.get("web_last") or {})
                amount = min(gc.max_per_run, int(sc * gc.rate))
                paid = 0
                soon = bool(amount) and now - int(last.get(key, 0)) < w.min_interval_s
                if amount and not soon:
                    year = await game.current_year(s, content)
                    paid = await pay(s, content, ch, st, random.Random(), year, ui.settings.timezone, amount)
                    last[key] = now
                    st["web_last"] = last
                await store(s, tg, st)
        finally:
            world_service.reset_actor(actor)
    log.info("arcade: %s scored %s in %s (paid %s)", tg, sc, key, paid)
    return {"status": "ok", "score": sc, "record": record, "paid": paid,
            "capped": bool(amount) and not soon and paid < amount, "soon": soon}
