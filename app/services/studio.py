"""Braindance studio (owner request): shoot a braindance after a book of Ozon671Games, release it to the city,
watch the premieres of other players. The author gets royalties by mail (`city` letter with eddies).

world_state `braindances`: list of {id, title, book, stars, author_char, author_tg, author_name, views: [char ids], at}.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.db import repo
from app.db.models import PlayerMail
from app.engine import studio as se
from app.engine.effects import AppliedEffects, apply_effects
from app.services import ap as ap_services
from app.services import game_events as bus
from app.services.game import ActiveCharacter, save_active

KEY = "braindances"
R_SEEN, R_OWN, R_GONE, R_EDDIES = "seen", "own", "gone", "eddies"


def _name(ch: ActiveCharacter) -> str:
    return f"{ch.state.name} {ch.state.surname}".strip()


def _jailed(ch: ActiveCharacter) -> bool:
    return int(ch.state.flags.get("jail_weeks", 0) or 0) > 0


async def premieres(session: AsyncSession) -> list[dict]:
    got = await repo.ws_get(session, KEY, [])
    return list(got) if isinstance(got, list) else []


@dataclass
class ShootResult:
    blocked: str | None = None
    shoot: se.Shoot | None = None
    applied: AppliedEffects | None = None
    record: dict = field(default_factory=dict)


async def shoot(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
                book_key: str, title: str) -> ShootResult:
    b = se.book(content, book_key)
    if b is None:
        return ShootResult(blocked=se.R_GONE)
    why = se.blocked(ch.state, content, jailed=_jailed(ch))
    if why is not None:
        return ShootResult(blocked=why)
    c = se.cfg(content)
    cost = se.price(ch.state, content)
    if c.ap:
        ap_services.spend(ch, content, c.ap)
    r = se.shoot(ch.state, content, rng, book_key)
    ch.state.flags[se.FLAG_WEEK] = ch.state.weeks_lived
    fx: dict[str, int | bool] = {"eddies": r.pay - cost, f"path_xp:{c.path}": r.xp}
    if r.stars >= c.fame_from_stars:
        fx["fame"] = 1
    res = ShootResult(shoot=r, applied=apply_effects(ch.state, {k: v for k, v in fx.items() if v}, content, rng, year))
    title = " ".join(title.split())[: c.title_max_chars] or se.auto_title(content, book_key, rng)
    rec = {"id": int(time.time() * 1000) % 10**12, "title": title, "book": b.key, "stars": r.stars,
           "author_char": ch.id, "author_tg": ch.player_tg_id, "author_name": _name(ch), "views": [], "at": int(time.time())}
    lst = await premieres(session)
    lst.insert(0, rec)
    await repo.ws_set(session, KEY, lst[: c.keep])
    res.record = rec
    await save_active(session, ch, tz)
    await session.flush()
    return res


@dataclass
class WatchResult:
    blocked: str | None = None
    record: dict = field(default_factory=dict)
    applied: AppliedEffects | None = None
    price: int = 0


async def watch(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
                bd_id: int) -> WatchResult:
    lst = await premieres(session)
    rec = next((x for x in lst if int(x.get("id", 0)) == bd_id), None)
    if rec is None:
        return WatchResult(blocked=R_GONE)
    if int(rec.get("author_char", 0)) == ch.id:
        return WatchResult(blocked=R_OWN, record=rec)
    if ch.id in (rec.get("views") or []):
        return WatchResult(blocked=R_SEEN, record=rec)
    stars = int(rec.get("stars", 1) or 1)
    price = se.ticket(content, stars)
    if ch.state.eddies < price:
        return WatchResult(blocked=R_EDDIES, record=rec, price=price)
    fx = {"eddies": -price, "mood": se.cfg(content).watch_mood_per_star * stars}
    res = WatchResult(record=rec, price=price, applied=apply_effects(ch.state, fx, content, rng, year))
    rec["views"] = list(rec.get("views") or []) + [ch.id]
    await repo.ws_set(session, KEY, lst)
    cut = se.royalty(content, stars)
    if cut and rec.get("author_tg"):
        session.add(PlayerMail(
            to_tg_id=int(rec["author_tg"]), from_tg_id=0, from_name="Прокат", kind="city",
            to_char_id=int(rec.get("author_char") or 0) or None,
            payload={"text": f"Брейнданс «{rec.get('title', '')}»: новый зритель, {_name(ch)}. Отчисления: +{cut} эдди.",
                     "eddies": cut},
        ))
    await save_active(session, ch, tz)
    await session.flush()
    await bus.publish("braindance", player_tg_id=ch.player_tg_id, character_id=ch.id, session=session, ch=ch)
    return res
