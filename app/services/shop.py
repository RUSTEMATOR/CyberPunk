"""The Afterlife token shop (PLAN 4.7, DESIGN.md 12a). Tokens cannot be bought for eddies or given away.

Every effect of `schemas.SHOP_EFFECTS` plus `title:` and `cat_item:` lives here. `buy` checks that the
item can do something before it takes the tokens, so a refused purchase costs nothing.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.content.schemas import ShopItem
from app.db import repo
from app.engine import minigames
from app.engine.state import FLAG_STIM
from app.services import daily
from app.services.game import ActiveCharacter, save_active

# reasons of a refused purchase (texts in bot/texts.py SHOP_BLOCKED)
NO_TOKENS = "tokens"
NEED_CHARACTER = "character"
HAVE_STIM = "have_stim"
NO_ACTIVE = "no_active"
FREEZE_FULL = "freeze_full"
NO_CARD = "no_card"
HAVE_TITLE = "have_title"
NO_CAT = "no_cat"
CAT_GONE = "cat_gone"
HAVE_ITEM = "have_item"
UNKNOWN = "unknown"


@dataclass
class BuyResult:
    ok: bool
    item: ShopItem | None = None
    reason: str | None = None
    detail: str = ""  # the new card's name, the title, ...
    card_key: str | None = None


def items(content: Content) -> list[ShopItem]:
    return list(content.afterlife_shop.values())


def effect_kind(item: ShopItem) -> tuple[str, str]:
    """('stim', '') or ('title', 'Свой в «Посмертии»') or ('cat_item', 'claws')."""
    kind, _, arg = item.effect.partition(":")
    return kind, arg


async def buy(
    session: AsyncSession, content: Content, tg_id: int, item_key: str, ch: ActiveCharacter | None,
    rng: random.Random, year: int, today: str, tz: str,
) -> BuyResult:
    item = content.afterlife_shop.get(item_key)
    if item is None:
        return BuyResult(False, None, UNKNOWN)
    player = await repo.ensure_player(session, tg_id)
    if int(player.tokens or 0) < item.price_tokens:
        return BuyResult(False, item, NO_TOKENS)
    kind, arg = effect_kind(item)
    detail = ""
    card_key: str | None = None

    if kind == "stim":
        if ch is None:
            return BuyResult(False, item, NEED_CHARACTER)
        if int(ch.state.flags.get(FLAG_STIM, 0) or 0) > 0:
            return BuyResult(False, item, HAVE_STIM)
        ch.state.flags[FLAG_STIM] = content.balance.daily.stim_bonus
        await save_active(session, ch, tz)
    elif kind == "recharge_all":
        if ch is None:
            return BuyResult(False, item, NEED_CHARACTER)
        active = [c for c in ch.state.cyberware
                  if (cw := content.cyberware.get(c.key)) is not None and cw.active is not None]
        if not active:
            return BuyResult(False, item, NO_ACTIVE)
        for c in active:
            c.charge = 100
        await save_active(session, ch, tz)
    elif kind == "streak_freeze":
        if int(player.freeze_count or 0) >= content.balance.daily.freeze_max:
            return BuyResult(False, item, FREEZE_FULL)
        player.freeze_count = int(player.freeze_count or 0) + 1
    elif kind == "tarot_reroll":
        card = await daily.reroll_card(session, content, tg_id, ch, rng, year, today, tz)
        if card is None:
            return BuyResult(False, item, NO_CARD)
        detail, card_key = card.name, card.key
    elif kind == "title":
        titles = list(player.titles or [])
        if arg in titles:
            return BuyResult(False, item, HAVE_TITLE)
        player.titles = titles + [arg]
        detail = arg
    elif kind == "cat_item":
        cat = dict(player.cat or {})
        if not cat:
            return BuyResult(False, item, NO_CAT)
        cat = minigames.refresh_cat(content, cat, today)
        if cat.get("gone"):
            player.cat = cat
            return BuyResult(False, item, CAT_GONE)
        if arg in (cat.get("chrome") or []):
            return BuyResult(False, item, HAVE_ITEM)
        cat["chrome"] = list(cat.get("chrome") or []) + [arg]
        player.cat = cat
        detail = arg
    else:
        return BuyResult(False, item, UNKNOWN)

    player.tokens = int(player.tokens or 0) - item.price_tokens
    await session.flush()
    return BuyResult(True, item, None, detail, card_key)


def current_title(titles: list[str] | None) -> str | None:
    """The title shown next to the name: the one bought last."""
    return (titles or [None])[-1]
