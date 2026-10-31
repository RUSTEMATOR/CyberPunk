"""The own PMC in a conflict of two megacorps (owner request 25.10 «собрать свою ЧВК и вступить в войну»,
docs/PLAN_ELITE.md part 4).

- A character with an army from `need_army` fighters registers it as a PMC (a license fee). flag `pmc` = {"name", "side",
  "conflict" (the conflict number the contract is for), "today": {"d", "n"}}.
- The city always has one conflict of two megacorps: world_state `corp_conflict` = {"n", "a", "b", "front" (−100: a
  wins … +100: b wins), "since", "pmcs": {tg: {"side", "name", "won"}}}. The corps' own forces push the front every day.
- A PMC signs with one side: a retainer every lived month while the conflict lasts, pay for every won battle.
- A battle is an elite fight in waves with the own fighters at the side (engine/elite.py). A win moves the front.
- The front at the edge: the side wins, its PMCs get a bonus by their won battles; the next conflict starts.
"""

from __future__ import annotations

import random

from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import kyiv_date, utcnow
from app.content.loader import Content
from app.db import repo
from app.db.models import PlayerMail
from app.engine import army as army_eng
from app.engine.effects import apply_effects
from app.services.game import ActiveCharacter, save_active

KEY = "corp_conflict"
FLAG = "pmc"
SRC = "pmcbattle"


def cfg(content: Content):  # noqa: ANN201
    return content.balance.pmc


def megacorps(content: Content, year: int) -> list[str]:
    from app.engine import combat as cb

    return [k for k, c in content.available("corps", year).items()
            if c.kind == "megacorp" and cb._faction_pool(content, k, year)]  # noqa: SLF001


async def conflict(session: AsyncSession, content: Content, year: int, rng: random.Random | None = None) -> dict:
    """The current conflict; the first one starts on demand."""
    w = await repo.ws_get(session, KEY, None)
    if isinstance(w, dict) and w.get("a"):
        return dict(w)
    return await _start(session, content, year, rng or random.Random(), 1)


async def _start(session: AsyncSession, content: Content, year: int, rng: random.Random, n: int) -> dict:
    # the conflict is one for the whole group, whose characters live in different years: only corps of every era
    corps = [k for k in megacorps(content, year) if content.corps[k].available_from is None
             and content.corps[k].available_until is None]
    a, b = rng.sample(corps, 2) if len(corps) >= 2 else ("arasaka", "militech")
    w = {"n": n, "a": a, "b": b, "front": 0, "since": utcnow().date().isoformat(), "pmcs": {}}
    await repo.ws_set(session, KEY, w)
    return w


def side_corp(w: dict, side: str) -> str:
    return str(w["a"] if side == "a" else w["b"])


def mine(ch: ActiveCharacter) -> dict | None:
    r = ch.state.flags.get(FLAG)
    return dict(r) if isinstance(r, dict) else None


async def register(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                   tz: str) -> str | None:
    c = cfg(content)
    if mine(ch) is not None:
        return "have"
    if len(army_eng.soldiers(ch.state)) < c.need_army:
        return "army"
    if ch.state.eddies < c.license:
        return "eddies"
    apply_effects(ch.state, {"eddies": -c.license}, content, rng, year)
    name = army_eng.rec(ch.state).get("name") or f"ЧВК {ch.state.surname}".strip()
    ch.state.flags[FLAG] = {"name": name, "side": "", "conflict": 0}
    await save_active(session, ch, tz)
    return None


async def sign(session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, tz: str, side: str) -> str | None:
    r = mine(ch)
    if r is None:
        return "nopmc"
    if side not in ("a", "b"):
        return "gone"
    w = await conflict(session, content, year)
    if r.get("conflict") == w["n"] and r.get("side"):
        return "signed"
    r.update(side=side, conflict=w["n"])
    ch.state.flags[FLAG] = r
    pm = dict(w.get("pmcs") or {})
    pm[str(ch.player_tg_id)] = {"side": side, "name": r["name"], "won": 0, "char": ch.id}
    w["pmcs"] = pm
    await repo.ws_set(session, KEY, w)
    await save_active(session, ch, tz)
    return None


def signed_side(ch: ActiveCharacter, w: dict) -> str:
    r = mine(ch)
    return str(r.get("side")) if r and r.get("conflict") == w.get("n") and r.get("side") else ""


async def battle(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                 tz: str):  # noqa: ANN201
    from app.engine import elite
    from app.engine import jail as jail_eng
    from app.services import ap as ap_services
    from app.services import combat
    from app.services import events as ev_service
    from app.services.combat import _store

    c = cfg(content)
    w = await conflict(session, content, year)
    side = signed_side(ch, w)
    if not side:
        return "nosign"
    if jail_eng.in_jail(ch.state):
        return "jail"
    if ev_service.has_pending(ch):
        return "busy"
    r = mine(ch) or {}
    today = kyiv_date(tz)
    day = dict(r.get("today") or {})
    n = int(day.get("n", 0)) if day.get("d") == today else 0
    if n >= c.battles_per_day:
        return "today"
    if not ap_services.can_spend(ch, c.battle_ap):
        return "ap"
    ap_services.spend(ch, content, c.battle_ap)
    r["today"] = {"d": today, "n": n + 1}
    ch.state.flags[FLAG] = r
    await save_active(session, ch, tz)
    enemy = side_corp(w, "b" if side == "a" else "a")
    step = await combat.begin(session, content, ch, rng, year, tz, faction=enemy, danger=5,
                              src={"src": SRC, "army_war": True, "conflict": w["n"], "side": side,
                                   "title": f"фронт: {content.corps[enemy].name}"})
    elite.setup(step.cs, content, rng, year, boss=f"Командир {content.corps[enemy].name}", waves=c.waves)
    _store(ch, step.cs)
    await save_active(session, ch, tz)
    return step


async def battle_over(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                      tz: str, result: str, ctx: dict) -> list[str]:
    from app.engine import combat as cb
    from app.services import corpraid, season

    c = cfg(content)
    w = await conflict(session, content, year)
    if int(ctx.get("conflict", -1)) != int(w["n"]):
        return ["Этот конфликт уже закончился."]
    side = str(ctx.get("side", ""))
    sign = -1 if side == "a" else 1
    if result != cb.WON:
        w["front"] = max(-100, min(100, int(w["front"]) - sign * c.front_loss))
        await repo.ws_set(session, KEY, w)
        return ["Сражение проиграно: твои отходят, фронт сдвигается не в твою пользу."]
    pay = c.battle_pay * max(1, await corpraid.ladder(session, ch))
    apply_effects(ch.state, {"eddies": pay, "street_cred": 2}, content, rng, year)
    w["front"] = max(-100, min(100, int(w["front"]) + sign * c.front_win))
    pm = dict(w.get("pmcs") or {})
    rec = dict(pm.get(str(ch.player_tg_id)) or {"side": side, "name": (mine(ch) or {}).get("name", ""), "won": 0})
    rec["won"] = int(rec.get("won", 0)) + 1
    pm[str(ch.player_tg_id)] = rec
    w["pmcs"] = pm
    await repo.ws_set(session, KEY, w)
    await season.add_ch(session, content, ch, "pmc_battle", content.balance.season.pts.get("pmc_battle", 0))
    await save_active(session, ch, tz)
    lines = [f"Сражение выиграно: +{pay} эдди. Фронт: {front_word(content, w)}."]
    lines += await _maybe_end(session, content, w, year, rng)
    return lines


def front_word(content: Content, w: dict) -> str:
    f = int(w["front"])
    a, b = content.corps[w["a"]].name, content.corps[w["b"]].name
    if f == 0:
        return "ровно"
    return f"{a if f < 0 else b} теснит ({abs(f)}/100)"


async def _maybe_end(session: AsyncSession, content: Content, w: dict, year: int, rng: random.Random) -> list[str]:
    from app.services import loud, season

    f = int(w["front"])
    if abs(f) < 100:
        return []
    c = cfg(content)
    win = "a" if f < 0 else "b"
    corp = content.corps[side_corp(w, win)].name
    names = []
    for tg, r in (w.get("pmcs") or {}).items():
        if r.get("side") != win or not int(r.get("won", 0)):
            continue
        bonus = c.win_bonus * int(r["won"])
        session.add(PlayerMail(to_tg_id=int(tg), from_tg_id=0, from_name=corp, kind="city",
                               payload={"text": f"Конфликт выигран. {corp} платит премию своей ЧВК: +{bonus} эдди.",
                                        "eddies": bonus, "fame": 2}))
        names.append(str(r.get("name", "")))
        await season.add(session, content, int(tg), str(r.get("name", "")), "pmc_win",
                         content.balance.season.pts.get("pmc_win", 0))
    await loud.post(session, None, f"🏢 Конфликт корпораций окончен: побеждает {corp}."
                    + (f" Отличились ЧВК: {', '.join(names)}." if names else ""))
    await _start(session, content, year, rng, int(w["n"]) + 1)
    return [f"🏢 Конфликт окончен: побеждает {corp}."]


async def daily(session: AsyncSession, content: Content, rng: random.Random, year: int) -> list[str]:
    """The corps' own forces push the front every day."""
    w = await conflict(session, content, year, rng)
    w["front"] = max(-100, min(100, int(w["front"]) + rng.randint(-cfg(content).drift, cfg(content).drift)))
    await repo.ws_set(session, KEY, w)
    return await _maybe_end(session, content, w, year, rng)


async def press_hook(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random,
                     year: int) -> list[str]:
    """Every lived month the side pays the retainer of a signed PMC."""
    r = mine(ch)
    if r is None or not r.get("side"):
        return []
    w = await conflict(session, content, year)
    if r.get("conflict") != w["n"]:
        return []
    n = len(army_eng.soldiers(ch.state))
    pay = cfg(content).retainer_per_fighter * n
    if pay:
        apply_effects(ch.state, {"eddies": pay}, content, rng, year)
    corp = content.corps[side_corp(w, str(r["side"]))].name
    return [f"🏢 ЧВК «{r.get('name')}»: {corp} платит содержание за {n} бойцов: +{pay} эдди."]
