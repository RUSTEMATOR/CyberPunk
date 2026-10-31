"""The daily claim (PLAN 4.7, DESIGN.md 12a): the Misty card, eddies, the streak, streak chests, freezes.

Works with or without a living character. With one: the card becomes a buff until Kyiv midnight and
the eddies land on the character. Without one (the character died): the streak and the tokens still
count, the card waits and is attached to the next character created today (`attach_pending`).
The card of the day lives in `player_data["tarot"]`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.content.schemas import Tarot
from app.db import repo
from app.engine import daily as engine_daily
from app.engine.effects import apply_effects
from app.engine.state import CharacterState
from app.services import playerdata
from app.services.game import ActiveCharacter, save_active


@dataclass
class DailyResult:
    already: bool
    card: Tarot | None
    streak: int
    streak_broken: bool = False
    freezes_used: int = 0
    payout: int = 0  # eddies of the claim (signed; the card may take)
    paid: bool = False  # the eddies went to a living character
    tokens: int = 0
    chests: list[int] = field(default_factory=list)  # streak days whose reward was paid today
    chest_eddies: int = 0
    has_character: bool = False


def _drop_tarot_buffs(st: CharacterState) -> None:
    st.buffs = [b for b in st.buffs if not b.source.startswith("tarot:")]


def _put_card(st: CharacterState, content: Content, card_key: str, eddies: int, rng: random.Random, year: int) -> None:
    """The card as a buff on the character plus its eddies, through apply_effects."""
    _drop_tarot_buffs(st)
    fx: dict[str, int | bool] = {f"buff:{card_key}": True}
    if eddies:
        fx["eddies"] = eddies
    apply_effects(st, fx, content, rng, year)


async def current_card(session: AsyncSession, content: Content, tg_id: int, today: str) -> Tarot | None:
    rec = await playerdata.get(session, tg_id, playerdata.KEY_TAROT, {})
    if rec.get("date") == today:
        return content.tarot.get(str(rec.get("card")))
    return None


async def claim(
    session: AsyncSession, content: Content, tg_id: int, username: str | None, ch: ActiveCharacter | None,
    rng: random.Random, year: int, today: str, tz: str,
) -> DailyResult:
    """Claim the day. Idempotent within a Kyiv day: the second call returns the same card with `already`."""
    player = await repo.ensure_player(session, tg_id, username)
    rec = await playerdata.get(session, tg_id, playerdata.KEY_TAROT, {})
    if player.last_daily == today:
        return DailyResult(True, content.tarot.get(str(rec.get("card"))), int(player.streak or 0),
                           has_character=ch is not None)

    sr = engine_daily.advance_streak(player.last_daily, today, int(player.streak or 0), int(player.freeze_count or 0))
    player.streak = sr.streak
    player.last_daily = today
    player.freeze_count = max(0, int(player.freeze_count or 0) - sr.freezes_used)

    card = engine_daily.draw_card(content, rng)
    payout = engine_daily.card_payout(content, card, sr.streak)
    res = DailyResult(False, card, sr.streak, sr.broken, sr.freezes_used, payout, has_character=ch is not None)

    chest_eddies = 0
    for day, reward in engine_daily.streak_chests(content, sr.streak):
        res.chests.append(day)
        res.tokens += reward.tokens
        chest_eddies += reward.eddies
    res.chest_eddies = chest_eddies
    if res.tokens:
        player.tokens = int(player.tokens or 0) + res.tokens

    owed = payout + chest_eddies
    if ch is not None:
        _put_card(ch.state, content, card.key, owed, rng, year)
        await save_active(session, ch, tz)
        res.paid = True
        await playerdata.put(session, tg_id, playerdata.KEY_TAROT,
                             {"date": today, "card": card.key, "applied": True, "owed": 0})
    else:
        await playerdata.put(session, tg_id, playerdata.KEY_TAROT,
                             {"date": today, "card": card.key, "applied": False, "owed": owed})
    await session.flush()
    return res


async def attach_pending(
    session: AsyncSession, content: Content, tg_id: int, st: CharacterState, rng: random.Random, year: int,
    today: str,
) -> bool:
    """A character created today gets the card (and its eddies) that waited for them. True when attached."""
    rec = await playerdata.get(session, tg_id, playerdata.KEY_TAROT, {})
    if rec.get("date") != today or rec.get("applied") or str(rec.get("card")) not in content.tarot:
        return False
    _put_card(st, content, str(rec["card"]), int(rec.get("owed", 0) or 0), rng, year)
    rec.update(applied=True, owed=0)
    await playerdata.put(session, tg_id, playerdata.KEY_TAROT, rec)
    return True


async def reroll_card(
    session: AsyncSession, content: Content, tg_id: int, ch: ActiveCharacter | None, rng: random.Random,
    year: int, today: str, tz: str,
) -> Tarot | None:
    """A new card instead of today's (the Afterlife shop). The card's own eddies are swapped, the base
    payout is not paid again. None when nothing was claimed today."""
    rec = await playerdata.get(session, tg_id, playerdata.KEY_TAROT, {})
    old = content.tarot.get(str(rec.get("card")))
    if rec.get("date") != today or old is None:
        return None
    new = engine_daily.draw_card(content, rng, exclude=old.key)
    swing = new.eddies - old.eddies
    if ch is not None:
        _put_card(ch.state, content, new.key, swing, rng, year)
        await save_active(session, ch, tz)
        rec.update(card=new.key, applied=True)
    else:
        rec.update(card=new.key, owed=int(rec.get("owed", 0) or 0) + swing)
    await playerdata.put(session, tg_id, playerdata.KEY_TAROT, rec)
    return new
