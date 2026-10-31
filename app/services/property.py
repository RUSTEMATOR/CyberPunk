"""Property actions (PLAN 4.6): rent, move, buy, equip, Trauma Team. Each one checks, spends AP through
`services.ap.spend`, moves eddies through `apply_effects`, saves and logs. A refused action changes nothing."""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.db import repo
from app.engine import property as prop
from app.engine.effects import apply_effects
from app.services import ap as ap_services
from app.services import worldfx
from app.services.game import ActiveCharacter, save_active


@dataclass
class PropertyResult:
    ok: bool
    reason: str | None = None  # a prop.REASON_* code, or "no_ap" / "unavailable"
    paid: int = 0
    equipped: bool = False
    old_home: str = ""


async def world_price_mult(session: AsyncSession, content: Content, target: str, tg_id: int | None = None) -> float:
    """The world's price multiplier of a target at the player's own date (DESIGN.md 9a)."""
    return (await worldfx.load_fx(session, content, tg_id)).price(target)


def _spend(ch: ActiveCharacter, content: Content, ap: int) -> bool:
    if not ap_services.can_spend(ch, ap):
        return False
    ap_services.spend(ch, content, ap)
    return True


async def rent_home(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
    tz: str, home_key: str,
) -> PropertyResult:
    """Take a home of the current district: the deposit (weeks of the new rent) is paid, the old home is left."""
    st = ch.state
    reason = prop.can_rent(st, content, home_key)
    if reason is not None:
        return PropertyResult(False, reason)
    ap = content.balance.property.housing_ap
    if not _spend(ch, content, ap):
        return PropertyResult(False, "no_ap")
    home = content.housing[home_key]
    cost = prop.deposit(content, home, st)
    if cost:
        apply_effects(st, {"eddies": -cost}, content, rng, year)
    old = st.housing
    prop.set_housing(st, home_key)
    await save_active(session, ch, tz)
    await repo.log_event(session, "housing", {"home": home_key, "old": old, "deposit": cost}, character_id=ch.id)
    return PropertyResult(True, paid=cost, old_home=old)


async def move_district(
    session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, tz: str, district: str,
) -> PropertyResult:
    """Change the district (the fixer, gangs and events change with it). The home is lost; the new district's
    rentals are offered next."""
    st = ch.state
    reason = prop.can_move(st, content, year, district, await worldfx.load_fx(session, content, ch.player_tg_id))
    if reason is not None:
        return PropertyResult(False, reason)
    if not _spend(ch, content, content.balance.property.move_ap):
        return PropertyResult(False, "no_ap")
    old_district = st.district
    old_home = prop.move_to(st, district)
    await save_active(session, ch, tz)
    await repo.log_event(
        session, "move", {"from": old_district, "to": district, "lost_home": old_home}, character_id=ch.id,
    )
    return PropertyResult(True, old_home=old_home)


async def buy_item(
    session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
    tz: str, kind: str, key: str,
) -> PropertyResult:
    st = ch.state
    if kind not in prop.SHOP_KINDS:
        return PropertyResult(False, prop.REASON_GONE)
    fx = await worldfx.load_fx(session, content, ch.player_tg_id)
    mult = fx.price("weapons")
    item = next((i for i in prop.shop_items(st, content, year, kind, mult, fx) if i.key == key), None)
    if item is None:
        return PropertyResult(False, prop.REASON_GONE)
    if item.reason is not None:
        return PropertyResult(False, item.reason)
    if not _spend(ch, content, content.balance.property.buy_ap):
        return PropertyResult(False, "no_ap")
    if item.price:
        apply_effects(st, {"eddies": -item.price}, content, rng, year)
    equipped = prop.add_to_inventory(st, kind, key)
    await save_active(session, ch, tz)
    await repo.log_event(session, "shop", {"kind": kind, "item": key, "price": item.price}, character_id=ch.id)
    from app.services import game_events as bus

    await bus.publish("item_bought", player_tg_id=ch.player_tg_id, character_id=ch.id, session=session, ch=ch)
    return PropertyResult(True, paid=item.price, equipped=equipped)


async def equip_item(
    session: AsyncSession, content: Content, ch: ActiveCharacter, tz: str, kind: str, key: str,
) -> PropertyResult:
    """Free: swapping what you carry costs no AP."""
    if kind not in prop.SHOP_KINDS or not prop.equip(ch.state, kind, key):
        return PropertyResult(False, prop.REASON_GONE)
    await save_active(session, ch, tz)
    return PropertyResult(True, equipped=True)


async def unequip_item(
    session: AsyncSession, content: Content, ch: ActiveCharacter, tz: str, kind: str, key: str | None = None,
) -> PropertyResult:
    """Free, like equipping: the item goes back to the bag."""
    if kind not in prop.SHOP_KINDS or not prop.unequip(ch.state, kind, key):
        return PropertyResult(False, prop.REASON_GONE)
    await save_active(session, ch, tz)
    return PropertyResult(True)


def item_price(content: Content, kind: str, key: str) -> int:
    """Owner request 24.10: what a shop pays for a weapon or armor from the bag (0 = nobody buys it)."""
    rec = (content.weapons if kind == "weapon" else content.armor).get(key)
    if rec is None or not rec.price:
        return 0
    return max(1, rec.price * content.balance.vehicles.item_sell_pct // 100)


async def sell_item(
    session: AsyncSession, content: Content, ch: ActiveCharacter, tz: str, kind: str, key: str,
) -> tuple[bool, int]:
    """Sell one copy of an item from the bag (not the one in hand or on the body)."""
    import random as _random

    from app.engine.effects import apply_effects

    st = ch.state
    for it in list(st.inventory):
        if it.kind == kind and it.key == key and not it.equipped:
            price = item_price(content, kind, key)
            if price <= 0:
                return False, 0
            st.inventory.remove(it)
            apply_effects(st, {"eddies": price}, content, _random.Random(), st.current_year)
            await save_active(session, ch, tz)
            return True, price
    return False, 0


async def change_trauma(
    session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, tz: str, tier: str | None,
) -> PropertyResult:
    """Subscribe, change the tier, or cancel (tier None). Price per week is charged by the week."""
    st = ch.state
    if tier is not None and not prop.trauma_offered(content, year):
        return PropertyResult(False, "unavailable")
    if prop.in_jail(st):
        return PropertyResult(False, prop.REASON_JAIL)
    if tier == prop.current_tier(st, content):
        return PropertyResult(False, prop.REASON_HERE)
    if not _spend(ch, content, content.balance.property.trauma_ap):
        return PropertyResult(False, "no_ap")
    if not prop.set_trauma(st, content, tier):
        return PropertyResult(False, prop.REASON_GONE)
    await save_active(session, ch, tz)
    await repo.log_event(session, "trauma_team", {"tier": tier}, character_id=ch.id)
    return PropertyResult(True)


TRIP_AP = 1
TRIP_FARE = 60


async def trip(session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, tz: str, district: str) -> PropertyResult:
    """Owner request 25.10: a trip to another district of the same city (1 AP and a taxi), no move, no lost home."""
    st = ch.state
    home = prop.visiting(st) or st.district
    if district == home:
        prop.trip_home(st)
        await save_active(session, ch, tz)
        return PropertyResult(True)
    if district not in prop.districts_to_move(st, content, year, await worldfx.load_fx(session, content, ch.player_tg_id)):
        return PropertyResult(False, prop.REASON_GONE)
    if prop.in_jail(st):
        return PropertyResult(False, prop.REASON_JAIL)
    from app.services import events as ev_service

    if ev_service.has_pending(ch):
        return PropertyResult(False, "busy")
    if st.eddies < TRIP_FARE:
        return PropertyResult(False, "no_money")
    if not _spend(ch, content, TRIP_AP):
        return PropertyResult(False, "no_ap")
    apply_effects(st, {"eddies": -TRIP_FARE}, content, random.Random(), year)
    prop.trip_to(st, district)
    queue_trip_event(content, ch, random.Random())  # owner request 25.10: every trip is a small story
    await save_active(session, ch, tz)
    return PropertyResult(True, paid=TRIP_FARE)


TRIP_EVENTS = ("trip_talky_driver", "trip_roadblock", "trip_carjack", "trip_av_crash", "trip_hitchhiker",
               "trip_gang_toll", "trip_auto_taxi", "trip_street_race", "trip_protest", "trip_lost_wallet")


def queue_trip_event(content: Content, ch: ActiveCharacter, rng: random.Random) -> str | None:
    from app.services.week import pending_events, set_pending

    seen = list(ch.state.flags.get("trip_seen") or [])
    pool = [e for e in TRIP_EVENTS if e in content.events and e not in seen] or [e for e in TRIP_EVENTS if e in content.events]
    if not pool:
        return None
    ev = rng.choice(pool)
    ch.state.flags["trip_seen"] = (seen + [ev])[-6:]
    set_pending(ch, [ev] + [e for e in pending_events(ch) if e != ev])
    return ev
