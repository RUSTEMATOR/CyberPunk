"""The player market (docs/EPIC_PLAYERS.md, MMO part): weapons and armor for eddies.

A listed item leaves the seller's inventory at once; a sale pays the seller by mail (minus the fee); a withdrawn lot
comes back to the seller's current character.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.db.models import Listing, PlayerMail
from app.engine.effects import apply_effects
from app.services.game import ActiveCharacter, save_active

OPEN, SOLD, WITHDRAWN = "open", "sold", "withdrawn"
MAIL_SOLD = "msold"

BLOCK_GONE = "gone"
BLOCK_OWN = "own"
BLOCK_EDDIES = "eddies"
BLOCK_HAVE = "have"
BLOCK_LIMIT = "limit"
BLOCK_PRICE = "price"


def item_of(content: Content, kind: str, key: str):  # noqa: ANN201
    return (content.weapons if kind == "weapon" else content.armor).get(key)


def sellable(content: Content, ch: ActiveCharacter) -> list[tuple[int, str, str]]:
    """(index in inventory, kind, key) of the weapons and armor the character holds."""
    return [(i, it.kind, it.key) for i, it in enumerate(ch.state.inventory)
            if it.kind in ("weapon", "armor") and item_of(content, it.kind, it.key) is not None]


async def open_lots(session: AsyncSession) -> list[Listing]:
    res = await session.execute(select(Listing).where(Listing.status == OPEN).order_by(Listing.id))
    return list(res.scalars())


@dataclass
class MarketResult:
    blocked: str | None = None
    lot: Listing | None = None


async def list_item(
    session: AsyncSession, content: Content, ch: ActiveCharacter, tz: str, inv_index: int, pct: int,
) -> MarketResult:
    cfg = content.balance.players.market
    if pct not in cfg.price_pcts:
        return MarketResult(BLOCK_PRICE)
    mine = [lot for lot in await open_lots(session) if lot.seller_tg == ch.player_tg_id]
    if len(mine) >= cfg.max_listings:
        return MarketResult(BLOCK_LIMIT)
    if not 0 <= inv_index < len(ch.state.inventory):
        return MarketResult(BLOCK_GONE)
    it = ch.state.inventory[inv_index]
    item = item_of(content, it.kind, it.key)
    if item is None or it.kind not in ("weapon", "armor"):
        return MarketResult(BLOCK_GONE)
    ch.state.inventory.pop(inv_index)
    await save_active(session, ch, tz)
    lot = Listing(seller_tg=ch.player_tg_id, seller_char=ch.id, seller_name=f"{ch.state.name} {ch.state.surname}".strip(),
                  kind=it.kind, item_key=it.key, price=max(1, item.price * pct // 100), status=OPEN)
    session.add(lot)
    await session.flush()
    return MarketResult(None, lot)


async def buy(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str, lot_id: int,
) -> MarketResult:
    lot = await session.get(Listing, lot_id)
    if lot is None or lot.status != OPEN:
        return MarketResult(BLOCK_GONE)
    if lot.seller_tg == ch.player_tg_id:
        return MarketResult(BLOCK_OWN, lot)
    if ch.state.eddies < lot.price:
        return MarketResult(BLOCK_EDDIES, lot)
    if any(it.key == lot.item_key for it in ch.state.inventory):
        return MarketResult(BLOCK_HAVE, lot)
    apply_effects(ch.state, {"eddies": -lot.price, f"item_grant:{lot.item_key}": True}, content, rng, year)
    await save_active(session, ch, tz)
    lot.status, lot.buyer_tg = SOLD, ch.player_tg_id
    net = lot.price - lot.price * content.balance.players.market.fee_pct // 100
    session.add(PlayerMail(to_tg_id=lot.seller_tg, from_tg_id=ch.player_tg_id, from_char_id=ch.id,
                           to_char_id=lot.seller_char, from_name=f"{ch.state.name} {ch.state.surname}".strip(),
                           kind=MAIL_SOLD, payload={"eddies": net, "item": lot.item_key, "kind": lot.kind}))
    await session.flush()
    return MarketResult(None, lot)


async def withdraw(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str, lot_id: int,
) -> MarketResult:
    lot = await session.get(Listing, lot_id)
    if lot is None or lot.status != OPEN or lot.seller_tg != ch.player_tg_id:
        return MarketResult(BLOCK_GONE)
    if any(it.key == lot.item_key for it in ch.state.inventory):
        return MarketResult(BLOCK_HAVE, lot)
    lot.status = WITHDRAWN
    apply_effects(ch.state, {f"item_grant:{lot.item_key}": True}, content, rng, year)
    await save_active(session, ch, tz)
    await session.flush()
    return MarketResult(None, lot)
