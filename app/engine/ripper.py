"""Ripper clinics (PLAN 6.1-6.5, 6.1a, 6.7; DESIGN.md sections 7, 8).

Pure functions: who treats the character here and now, what is on the shelf and what it costs, the dice of an
operation (complication, infection), the scan of the tolerance (it may lie), the full conversion, the software
shelf. Money and the character change through `apply_effects` / `apply_install_cyberware` / `apply_remove_cyberware`
in `services/ripper.py`; the numbers are `balance.cyber` and `balance.ripper`.
"""

from __future__ import annotations

import random
import zlib
from dataclasses import dataclass, field

from app.content.loader import Content
from app.content.schemas import TOLERANCE_TYPES, Cyberware, Quickhack, Ripperdoc
from app.engine import checks, derived
from app.engine.checks import CheckResult
from app.engine.dice import half_up
from app.engine.world import WorldFx, available_with_fx
from app.engine.state import (
    FLAG_FUGITIVE,
    FLAG_INFECTION,
    FLAG_JAIL_WEEKS,
    FLAG_TOLERANCE_SHOWN,
    KIND_QUICKHACK,
    CharacterState,
)

# Why an offer cannot be taken (also the keys of the texts).
R_JAIL = "jail"
R_OWNED = "owned"
R_NEEDS = "needs"
R_EDDIES = "eddies"
R_TOLERANCE = "tolerance"  # a full conversion needs a smasher's body
R_FIXED = "fixed"  # cannot be taken out
R_GONE = "gone"
R_NOT_HERE = "not_here"  # a fugitive goes only to the underground

SMASHER = "smasher"


def in_jail(state: CharacterState) -> bool:
    return int(state.flags.get(FLAG_JAIL_WEEKS, 0) or 0) > 0


def is_fugitive(state: CharacterState) -> bool:
    return bool(state.flags.get(FLAG_FUGITIVE))


# -- who treats you ------------------------------------------------------------------------------------------------------


def rippers_here(state: CharacterState, content: Content, year: int) -> list[Ripperdoc]:
    """Rippers of the district in the world year (an underground one has its own record per district).

    A fugitive psycho can only go to the underground ones (PLAN 6.6a); a cell has no clinic. Licensed first."""
    if in_jail(state):
        return []
    out = [
        r for r in content.available("ripperdocs", year).values()
        if (r.district in (None, state.district) or _same_city(content, r.district, state.district))
        and (r.underground or not is_fugitive(state))
    ]
    return sorted(out, key=lambda r: (r.underground, -r.price_mod, r.name))


def ripper_by_key(state: CharacterState, content: Content, year: int, key: str) -> Ripperdoc | None:
    return next((r for r in rippers_here(state, content, year) if r.key == key), None)


def discount_pct(state: CharacterState, content: Content) -> int:
    return max(0, derived.mod(state, content, "ripper_discount_pct"))


WorldMult = WorldFx | float  # the world's effects (prices by category, bans) or a plain multiplier


def world_price(world: WorldMult, category: str | None) -> float:
    """The world's price multiplier for an implant category (`combat` ... `defense`); a plain float applies to all."""
    if isinstance(world, WorldFx):
        return world.price(category) if category else 1.0
    return float(world)


def price_for(
    state: CharacterState, content: Content, year: int, base: int, ripper: Ripperdoc, world_mult: WorldMult = 1.0,
    category: str | None = None,
) -> int:
    """List price x era x the ripper's modifier x the world's multiplier x (1 - ripper_discount_pct)."""
    value = (base * derived.era_price_mult(content, year) * ripper.price_mod * world_price(world_mult, category)
             * content.balance.ripper.chrome_price_mult)
    return max(0, half_up(value * (1 - discount_pct(state, content) / 100)))


def removal_price(
    state: CharacterState, content: Content, year: int, key: str, ripper: Ripperdoc, world_mult: WorldMult = 1.0,
) -> int:
    cw = content.cyberware[key]
    return half_up(price_for(state, content, year, cw.price, ripper, world_mult, cw.category) * content.balance.cyber.removal_cost_pct)


# -- the shelf -----------------------------------------------------------------------------------------------------------


@dataclass
class Offer:
    key: str
    name: str
    price: int  # the new implant, at this ripper
    pch: int  # list humanity cost, before tolerance
    category: str
    reason: str | None = None  # R_* when it cannot be taken now
    needs: list[str] = field(default_factory=list)  # names of the missing base implants (R_NEEDS)
    replaces: str | None = None  # key of the installed implant it replaces (same exclusive_group)
    removal: int = 0  # extra price of taking the replaced one out
    total: int = 0  # price + removal: what leaves the pocket
    banned: bool = False  # the world banned it: only an underground clinic sells it and the heat follows


def smasher_visible(state: CharacterState) -> bool:
    """The full conversion is on the shelf for the one who knows they are a smasher (a scan said so)."""
    if not state.tolerance_known:
        return False
    return str(state.flags.get(FLAG_TOLERANCE_SHOWN, state.cyber_tolerance)) == SMASHER


def sellable(content: Content, year: int, world: WorldMult = 1.0, underground: bool = False) -> list[Cyberware]:
    """What the shelf may hold: the year's implants, the world's unlocked ones added, the banned ones taken off.
    An underground clinic still sells what the world banned (it costs heat, see `is_banned`)."""
    fx = world if isinstance(world, WorldFx) else WorldFx()
    recs = available_with_fx(content, "cyberware", year, fx)
    if underground:
        for key in fx.banned:
            rec = content.available("cyberware", year).get(key)
            if rec is not None:
                recs[key] = rec
    return [cw for cw in recs.values() if not cw.not_for_sale]


def is_banned(world: WorldMult, key: str) -> bool:
    return isinstance(world, WorldFx) and key in world.banned


def catalog(
    state: CharacterState, content: Content, year: int, ripper: Ripperdoc, world_mult: WorldMult = 1.0,
) -> list[Offer]:
    """The shelf for this character: what is for sale in the year, without what is installed already. Cheapest first.

    Items of an exclusive group show as a replacement of the installed one; a missing `requires` is shown, not hidden."""
    out: list[Offer] = []
    installed = {c.key for c in state.cyberware}
    spec = specialty(ripper)
    month = state.weeks_lived // max(1, content.balance.time.weeks_per_press)
    for cw in sellable(content, year, world_mult, ripper.underground):
        if cw.key in installed:
            continue
        if cw.borg_risk:  # owner request 27.10: the underground conversion, for a scanned body that is not a smasher's
            if not ripper.underground or not state.tolerance_known or smasher_visible(state) \
                    or derived.is_full_conversion(state, content):
                continue
        elif cw.category not in spec and not in_stock(ripper, cw.key, month):  # owner request 25.10: a shelf of its own
            continue
        if cw.smasher_only and (not smasher_visible(state) or derived.is_full_conversion(state, content)):
            continue
        price = price_for(state, content, year, cw.price, ripper, world_mult, cw.category)
        if cw.category in spec:
            price = half_up(price * (100 - SPECIALTY_DISCOUNT) / 100)
        offer = Offer(cw.key, cw.name, price, cw.humanity_cost, cw.category, total=price)
        offer.banned = is_banned(world_mult, cw.key)
        if cw.exclusive_group:
            old = next((c for c in state.cyberware
                        if (rec := content.cyberware.get(c.key)) is not None and rec.exclusive_group == cw.exclusive_group), None)
            if old is not None:
                offer.replaces = old.key
                offer.removal = removal_price(state, content, year, old.key, ripper, world_mult)
                offer.total = price + offer.removal
        missing = [r for r in cw.requires if r not in installed]
        if in_jail(state):
            offer.reason = R_JAIL
        elif missing:
            offer.reason, offer.needs = R_NEEDS, [content.cyberware[r].name for r in missing if r in content.cyberware]
        elif state.eddies < offer.total:
            offer.reason = R_EDDIES
        out.append(offer)
    return sorted(out, key=lambda o: (o.price, o.name))


def categories(offers: list[Offer]) -> list[str]:
    """Categories that have something on the shelf, in the order of the schema."""
    from app.content.schemas import CYBER_CATEGORIES

    have = {o.category for o in offers}
    return [c for c in CYBER_CATEGORIES if c in have]


def offer_for(
    state: CharacterState, content: Content, year: int, ripper: Ripperdoc, key: str, world_mult: WorldMult = 1.0,
) -> Offer | None:
    return next((o for o in catalog(state, content, year, ripper, world_mult) if o.key == key), None)


# -- an operation -----------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class OpRoll:
    complication: bool = False
    malfunction: bool = False  # the complication that kills the operation: the implant does not take
    infection: bool = False


def roll_operation(content: Content, ripper: Ripperdoc, rng: random.Random) -> OpRoll:
    """The dice of one installation: the ripper's complication, then which kind, then the underground's infection.

    A complication is either +50% humanity loss or a malfunction (`complication_malfunction_chance`)."""
    cyber = content.balance.cyber
    complication = rng.random() < ripper.complication_chance
    malfunction = complication and rng.random() < cyber.complication_malfunction_chance
    infection = ripper.infection_chance > 0 and rng.random() < ripper.infection_chance
    return OpRoll(complication, malfunction, infection)


def install_loss(state: CharacterState, content: Content, year: int, cw: Cyberware, *, extra: bool) -> int:
    """Humanity this implant takes from this character now. `extra`: the complication that adds 50%."""
    return derived.humanity_loss(
        cw.humanity_cost, state, content, era_mult=derived.era_humanity_mult(content, year), complication=extra,
    )


def smasher_real(state: CharacterState) -> bool:
    return state.cyber_tolerance == SMASHER


def black_death_chance(state: CharacterState, content: Content, shown: bool = False) -> float:
    """Owner request 27.10: the chance to die on the table of the underground conversion, by the body's type (the true
    one, or the one the scan showed for the card) and BODY."""
    fc = content.balance.cyber.full_conversion
    tol = shown_tolerance(state) if shown else state.cyber_tolerance
    base = float(fc.black_death.get(tol, 0.0 if tol == SMASHER else max(fc.black_death.values() or [0.5])))
    body = derived.stat_total(state, content, "BODY")
    return round(max(fc.black_min, base - fc.black_body_cut * max(0, body - 5)), 2) if base > 0 else 0.0


# -- the scan ----------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ScanResult:
    truth: str
    shown: str
    lied: bool


def scan(state: CharacterState, ripper: Ripperdoc, rng: random.Random) -> ScanResult:
    """The scan names a type; a ripper with `scan_lie_chance` names a neighbouring one sometimes."""
    truth = state.cyber_tolerance
    if ripper.scan_lie_chance > 0 and rng.random() < ripper.scan_lie_chance:
        i = TOLERANCE_TYPES.index(truth)
        near = [j for j in (i - 1, i + 1) if 0 <= j < len(TOLERANCE_TYPES)]
        return ScanResult(truth, TOLERANCE_TYPES[rng.choice(near)], True)
    return ScanResult(truth, truth, False)


def apply_scan(state: CharacterState, result: ScanResult) -> None:
    """The character now knows a type: the true one, or what the lying scan said (kept until a truthful scan)."""
    state.tolerance_known = True
    if result.lied:
        state.flags[FLAG_TOLERANCE_SHOWN] = result.shown
    else:
        state.flags.pop(FLAG_TOLERANCE_SHOWN, None)


def shown_tolerance(state: CharacterState) -> str:
    return str(state.flags.get(FLAG_TOLERANCE_SHOWN, state.cyber_tolerance))


# -- the full conversion -----------------------------------------------------------------------------------------------------------


def break_family(state: CharacterState, content: Content) -> list[str]:
    """After a full conversion the relatives and the partner no longer see a person (`cyber.full_conversion.family_rel`)."""
    kinds = set(content.balance.ripper.family_kinds)
    target = content.balance.cyber.full_conversion.family_rel
    hit: list[str] = []
    for n in state.npcs:
        if n.alive and n.kind in kinds and n.relation > target:
            n.relation = target
            hit.append(n.name)
    return hit


# -- the infection ---------------------------------------------------------------------------------------------------------------------


def infected(state: CharacterState) -> bool:
    return bool(state.flags.get(FLAG_INFECTION))


def cure_price(content: Content) -> int:
    return half_up(content.balance.cyber.infection_cure_cost * content.balance.ripper.clinic_cure_price_mult)


def roll_cure(state: CharacterState, content: Content, rng: random.Random) -> CheckResult:
    ck = content.balance.cyber.infection_cure_check
    return checks.roll_check(state, content, rng, ck.stat, ck.skill, ck.dv, ("heal",))


# -- software ---------------------------------------------------------------------------------------------------------------------------


@dataclass
class SoftwareOffer:
    key: str
    name: str
    price: int
    ram: int
    reason: str | None = None


def owns_program(state: CharacterState, key: str) -> bool:
    return any(i.kind == KIND_QUICKHACK and i.key == key for i in state.inventory)


def software_dealer_open(state: CharacterState, content: Content, year: int) -> bool:
    """The Voodoo Boys' netrunner sells programs in his district while the gang exists."""
    d = content.balance.ripper.software_dealer
    return (not in_jail(state) and state.district == d.district and d.gang in content.available("gangs", year)
            and not is_fugitive(state))


def software_offers(
    state: CharacterState, content: Content, year: int, *, ripper: Ripperdoc | None, world_mult: WorldMult = 1.0,
) -> list[SoftwareOffer]:
    """The programs of the year for the character, cheapest first; already owned ones are left out.
    At a ripper the ripper discount and his price modifier count, at the dealer his multiplier and the shop discount."""
    out: list[SoftwareOffer] = []
    for qh in content.available("quickhacks", year).values():
        if owns_program(state, qh.key) or qh.not_for_sale or qh.key in content.balance.combat.deck_builtin_hacks:
            continue
        if ripper is not None:
            price = price_for(state, content, year, qh.price, ripper, world_mult, "netrun")
        else:
            mult = content.balance.ripper.software_dealer.price_mult
            disc = max(0, derived.mod(state, content, "shop_discount_pct"))
            price = max(0, half_up(qh.price * mult * world_price(world_mult, "netrun") * (1 - disc / 100)))
        offer = SoftwareOffer(qh.key, qh.name, price, qh.ram_cost)
        if state.eddies < price:
            offer.reason = R_EDDIES
        out.append(offer)
    return sorted(out, key=lambda o: (o.price, o.name))


def program(content: Content, key: str) -> Quickhack | None:
    return content.quickhacks.get(key)


def _same_city(content: Content, a: str | None, b: str | None) -> bool:
    """Owner request 25.10: a city abroad has one ripper for all its districts."""
    da, db = content.districts.get(a or ""), content.districts.get(b or "")
    return da is not None and db is not None and da.city == db.city and da.city != "nc"


SPECIALTY_DISCOUNT = 15


def specialty(ripper: Ripperdoc) -> list[str]:
    """Owner request 25.10: what this ripper is known for (always in stock, 15% cheaper)."""
    from app.content.schemas import CYBER_CATEGORIES

    if ripper.specialty:
        return list(ripper.specialty)
    h = zlib.crc32(ripper.key.encode())
    a = CYBER_CATEGORIES[h % len(CYBER_CATEGORIES)]
    b = CYBER_CATEGORIES[(h // 7 + 1) % len(CYBER_CATEGORIES)]
    return [a] if a == b else [a, b]


def in_stock(ripper: Ripperdoc, key: str, month: int) -> bool:
    """Whether an item outside the specialty is on this ripper's shelf this lived month."""
    return zlib.crc32(f"{ripper.key}:{key}:{month}".encode()) % 100 < ripper.stock_pct
