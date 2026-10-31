"""Bounties on other players' characters (owner request): pay the fixer, and whoever beats the target in a street
fight («Стычка», no deaths) takes the whole pot. State: world_state `bounties` =
{char_id: {"amount", "target_tg", "target_name", "by": {tg: amount}}}."""

from __future__ import annotations

import random

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.db import repo
from app.db.models import Character
from app.engine.effects import apply_effects
from app.services import loud
from app.services.game import ActiveCharacter, save_active

KEY = "bounties"


def _m(n: int) -> str:
    return f"{n:,}".replace(",", " ")
AMOUNTS = (500, 1000, 2500, 5000)
POST_QUIET_S = 3 * 3600  # one post per target in this window; the pot still grows

BLOCK_SELF = "self"
BLOCK_GONE = "gone"
BLOCK_EDDIES = "eddies"


async def all_bounties(session: AsyncSession) -> dict[str, dict]:
    return dict(await repo.ws_get(session, KEY, {}) or {})


async def on(session: AsyncSession, char_id: int) -> int:
    return int((await all_bounties(session)).get(str(char_id), {}).get("amount", 0))


async def place(session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, tz: str, char_id: int,
                amount: int) -> str | None:
    if char_id == ch.id:
        return BLOCK_SELF
    target = await session.get(Character, char_id)
    if target is None or not target.alive or target.player_tg_id == ch.player_tg_id:
        return BLOCK_GONE
    if amount not in AMOUNTS or ch.state.eddies < amount:
        return BLOCK_EDDIES
    apply_effects(ch.state, {"eddies": -amount}, content, random.Random(), year)
    await save_active(session, ch, tz)
    b = await all_bounties(session)
    rec = dict(b.get(str(char_id)) or {"amount": 0, "target_tg": target.player_tg_id,
                                       "target_name": f"{target.name} {target.surname}".strip(), "by": {}})
    rec["amount"] = int(rec["amount"]) + amount
    by = dict(rec.get("by") or {})
    by[str(ch.player_tg_id)] = int(by.get(str(ch.player_tg_id), 0)) + amount
    rec["by"] = by
    import time

    now = time.time()
    quiet = now - float(rec.get("posted", 0) or 0) < POST_QUIET_S  # owner report 25.10: a post per click was spam
    if not quiet:
        rec["posted"] = now
    b[str(char_id)] = rec
    await repo.ws_set(session, KEY, b)
    if not quiet:
        await loud.post(session, ch.id, f"Фиксеры шепчут: за голову {loud.who(rec['target_name'])} теперь "
                                        f"{_m(rec['amount'])} эдди. Кто проучит в стычке, тот заберёт.")
    return None


async def claim(session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, char_id: int) -> int:
    """After a won street fight: the pot on the beaten character goes to the winner. Returns the amount (0 if none)."""
    b = await all_bounties(session)
    rec = b.pop(str(char_id), None)
    if not rec:
        return 0
    amount = int(rec.get("amount", 0))
    await repo.ws_set(session, KEY, b)
    if amount > 0:
        apply_effects(ch.state, {"eddies": amount, "street_cred": 1}, content, random.Random(), year)
        await loud.post(session, ch.id, f"{loud.who(ch.state.name, ch.state.surname)} забирает награду за голову "
                                        f"{loud.who(str(rec.get('target_name', '')))}: {_m(amount)} эдди.")
    return amount


async def drop_dead(session: AsyncSession) -> None:
    """Pots on characters who died go back to those who paid (a city letter)."""
    from app.db.models import PlayerMail

    b = await all_bounties(session)
    changed = False
    for cid in list(b):
        row = await session.get(Character, int(cid))
        if row is not None and row.alive:
            continue
        rec = b.pop(cid)
        changed = True
        for tg, amount in (rec.get("by") or {}).items():
            session.add(PlayerMail(to_tg_id=int(tg), from_tg_id=0, from_name="Фиксер", kind="city",
                                   payload={"eddies": int(amount), "text": f"Цель умерла раньше, чем её нашли. "
                                                                           f"Фиксер возвращает {amount} эдди."}))
    if changed:
        await repo.ws_set(session, KEY, b)
