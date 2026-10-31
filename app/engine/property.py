"""The property section (PLAN 4.6, DESIGN.md sections 4, 11): housing, moving, the arms shop,
equipment and the Trauma Team subscription.

Pure functions over a `CharacterState` and the content. Money moves through `apply_effects` in the
service; the changes of the home, the district, the inventory and the subscription flag are made
here, because none of them is a scale of the character. AP is spent by the service.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.content.loader import Content
from app.content.schemas import Armor, Housing, Weapon
from app.engine import derived
from app.engine.dice import half_up
from app.engine.world import WorldFx, available_with_fx, districts_now
from app.engine.state import (
    FLAG_JAIL_WEEKS,
    FLAG_TRAUMA_TEAM,
    KIND_ARMOR,
    KIND_WEAPON,
    CharacterState,
    InventoryItem,
)

TRAUMA_CORP = "trauma_team"
SHOP_KINDS: tuple[str, ...] = (KIND_WEAPON, KIND_ARMOR)

REASON_JAIL = "jail"
REASON_EDDIES = "eddies"
REASON_CRED = "cred"
REASON_STATUS = "status"
REASON_OWNED = "owned"
REASON_HERE = "here"
REASON_GONE = "gone"


def in_jail(state: CharacterState) -> bool:
    return int(state.flags.get(FLAG_JAIL_WEEKS, 0) or 0) > 0


# -- housing and moving -----------------------------------------------------------------------------


def housing_in(content: Content, district: str) -> list[Housing]:
    """Rentals of a district, cheapest first. Housing has no year window (it lives always)."""
    return sorted((h for h in content.housing.values() if h.district == district), key=lambda h: (h.tier, h.rent))


def rent_of(state: CharacterState, home: Housing) -> int:
    """Weekly rent of a home with the world's housing price effect."""
    return half_up(home.rent * state.world_housing_mult)


def deposit(content: Content, home: Housing, state: CharacterState | None = None) -> int:
    """Move-in payment: `housing.move_deposit_weeks` weeks of the new rent (the world's housing effect included)."""
    rent = rent_of(state, home) if state is not None else home.rent
    return content.balance.housing.move_deposit_weeks * rent


def can_rent(state: CharacterState, content: Content, home_key: str) -> str | None:
    """Why the character cannot take this home now, or None."""
    home = content.housing.get(home_key)
    if home is None or home.district != state.district:
        return REASON_GONE
    if state.housing == home_key:
        return REASON_HERE
    if in_jail(state):
        return REASON_JAIL
    if state.status < home.min_status:
        return REASON_STATUS
    if state.eddies < deposit(content, home, state):
        return REASON_EDDIES
    return None


def set_housing(state: CharacterState, home_key: str) -> None:
    state.housing = home_key


def districts_to_move(state: CharacterState, content: Content, year: int, fx: WorldFx | None = None) -> list[str]:
    """Districts of the world year the character can go to (all but the current one); the world may have opened
    or closed some (`fx`)."""
    from app.engine.world import city_of

    from app.engine import elysium as ely

    return [k for k in districts_now(content, year, fx or WorldFx(), city_of(content, state.district))
            if k != state.district  # owner request 25.10: only within the city the character lives in
            and not (k == ely.STATION and not ely.on_board(state))]  # owner request 30.10: the station takes citizens


def can_move(state: CharacterState, content: Content, year: int, district: str, fx: WorldFx | None = None) -> str | None:
    if in_jail(state):
        return REASON_JAIL
    if district not in districts_to_move(state, content, year, fx):
        return REASON_GONE
    return None


def move_to(state: CharacterState, district: str) -> str:
    """Change the district. The home is left behind (no rent, no refund of the deposit); returns the old home key."""
    old = state.housing
    state.district = district
    state.housing = ""
    state.flags.pop(FLAG_VISIT, None)  # owner request 25.10: a real move ends a trip
    return old


# -- the arms shop ---------------------------------------------------------------------------------------


def shop_discount_pct(state: CharacterState, content: Content) -> int:
    return derived.mod(state, content, "shop_discount_pct")


def shop_price(state: CharacterState, content: Content, base: int, world_mult: float = 1.0) -> int:
    """Base price x world price multiplier x (1 - shop discount), rounded half up."""
    discount = max(0, shop_discount_pct(state, content))
    return max(0, half_up(base * world_mult * (1 - discount / 100)))


@dataclass(frozen=True)
class ShopItem:
    kind: str  # weapon | armor
    key: str
    name: str
    description: str
    price: int  # with discount and world multiplier
    min_street_cred: int
    stat_line: str  # damage dice / defense for the card
    owned: bool
    reason: str | None  # why it cannot be bought now (REASON_*), else None


def owns(state: CharacterState, kind: str, key: str) -> bool:
    return any(i.kind == kind and i.key == key for i in state.inventory)


def shop_items(
    state: CharacterState, content: Content, year: int, kind: str, world_mult: float = 1.0,
    fx: WorldFx | None = None,
) -> list[ShopItem]:
    """The shelf of one kind in the world year, cheapest first. `fx` is the world's effects: banned items leave the
    shelf, unlocked ones appear before their year."""
    out: list[ShopItem] = []
    fx = fx or WorldFx()
    if kind == KIND_WEAPON:
        catalog: dict[str, Weapon | Armor] = dict(available_with_fx(content, "weapons", year, fx))
    else:
        catalog = dict(available_with_fx(content, "armor", year, fx))
    for key, rec in catalog.items():
        if getattr(rec, "not_for_sale", False):
            continue
        price = shop_price(state, content, rec.price, world_mult)
        cred = getattr(rec, "min_street_cred", 0)
        owned = owns(state, kind, key)
        reason: str | None = None
        if owned:
            reason = REASON_OWNED
        elif in_jail(state):
            reason = REASON_JAIL
        elif state.street_cred < cred:
            reason = REASON_CRED
        elif state.eddies < price:
            reason = REASON_EDDIES
        stat = f"{rec.damage}" if isinstance(rec, Weapon) else f"защита {rec.defense}"
        out.append(ShopItem(kind, key, rec.name, rec.description, price, cred, stat, owned, reason))
    return sorted(out, key=lambda i: (i.price, i.name))


def add_to_inventory(state: CharacterState, kind: str, key: str) -> bool:
    """Put a bought item into the inventory; it is equipped when the slot is empty. Returns True if equipped."""
    equipped = state.equipped_key(kind) is None
    state.inventory.append(InventoryItem(key, kind, equipped=equipped))
    return equipped


def owned_items(state: CharacterState, kind: str) -> list[InventoryItem]:
    return [i for i in state.inventory if i.kind == kind]


WEAPON_SLOTS = 3  # owner request 23.10: three weapons at hand (a pistol, a rifle, a blade)


def equipped_weapons(state: CharacterState) -> list[str]:
    return [it.key for it in state.inventory if it.kind == "weapon" and it.equipped]


def equip(state: CharacterState, kind: str, key: str) -> bool:
    """Equip `key`. Armor: the one item of its kind. A weapon goes into a free slot (up to WEAPON_SLOTS); with every
    slot taken it replaces the last one taken. False when the character does not own it."""
    if not owns(state, kind, key):
        return False
    if kind == "weapon":
        if key in equipped_weapons(state):
            return True
        held = [it for it in state.inventory if it.kind == "weapon" and it.equipped]
        if len(held) >= WEAPON_SLOTS:
            held[-1].equipped = False
        for it in state.inventory:
            if it.kind == "weapon" and it.key == key and not it.equipped:
                it.equipped = True
                break
        return True
    for it in state.inventory:
        if it.kind == kind:
            it.equipped = it.key == key
    return True


def unequip(state: CharacterState, kind: str, key: str | None = None) -> bool:
    """Put away the equipped item of a kind (or just weapon `key`): it stays in the bag. False when nothing was."""
    done = False
    for it in state.inventory:
        if it.kind == kind and it.equipped and (key is None or it.key == key):
            it.equipped, done = False, True
    return done


# -- Trauma Team ---------------------------------------------------------------------------------------------


def trauma_offered(content: Content, year: int) -> bool:
    """The company exists in this world year (its corp record's window)."""
    return TRAUMA_CORP in content.available("corps", year)


def current_tier(state: CharacterState, content: Content) -> str | None:
    tier = state.flags.get(FLAG_TRAUMA_TEAM)
    return tier if isinstance(tier, str) and tier in content.balance.trauma_team else None


def set_trauma(state: CharacterState, content: Content, tier: str | None) -> bool:
    """Subscribe at a tier, change it, or cancel with None. The weekly price is charged by advance_week."""
    if tier is None:
        state.flags.pop(FLAG_TRAUMA_TEAM, None)
        return True
    if tier not in content.balance.trauma_team:
        return False
    state.flags[FLAG_TRAUMA_TEAM] = tier
    return True


# -- a trip to another district (owner request 25.10: go there without moving) --------------------------------------------

FLAG_VISIT = "visit"  # {"home": the district the character lives in}


def visiting(state: CharacterState) -> str | None:
    """The home district while the character is on a trip, else None."""
    v = state.flags.get(FLAG_VISIT)
    return str(v.get("home")) if isinstance(v, dict) and v.get("home") else None


def trip_to(state: CharacterState, district: str) -> None:
    """Go to another district for a while: the home and its rent stay, the district's people and services are at hand."""
    home = visiting(state) or state.district
    state.district = district
    if district == home:
        state.flags.pop(FLAG_VISIT, None)
    else:
        state.flags[FLAG_VISIT] = {"home": home}


def trip_home(state: CharacterState) -> bool:
    home = visiting(state)
    if home is None:
        return False
    state.district = home
    state.flags.pop(FLAG_VISIT, None)
    return True
