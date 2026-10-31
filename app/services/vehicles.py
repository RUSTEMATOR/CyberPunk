"""Rides (owner request): buy, sell, tune, repair, steal, race. One ride at a time: the character flag `vehicle` =
{key, tune, dmg, stolen}. The ride's numbers work through `engine/derived.py` (escape, social, its mods)."""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.content.schemas import RaceTier, Vehicle
from app.db import repo
from app.engine import checks
from app.engine.checks import CheckResult
from app.engine.effects import AppliedEffects, apply_effects
from app.services import ap as ap_services
from app.services import game_events as bus
from app.services import followups
from app.services.game import ActiveCharacter, save_active

FLAG = "vehicle"
GARAGE = "garage"  # owner request 24.10: the parked rides (a list of ride dicts), the fleet

BLOCK_HAVE = "have"
BLOCK_NONE = "none"
BLOCK_EDDIES = "eddies"
BLOCK_CRED = "cred"
BLOCK_MAX = "max"
BLOCK_GONE = "gone"
BLOCK_CLEAN = "clean"
BLOCK_NO_AP = "no_ap"
BLOCK_JAIL = "jail"
BLOCK_WRECK = "wreck"
BLOCK_FULL = "full"


def ride(ch: ActiveCharacter) -> dict | None:
    r = ch.state.flags.get(FLAG)
    return dict(r) if isinstance(r, dict) else None


def parked(ch: ActiveCharacter) -> list[dict]:
    return [dict(r) for r in (ch.state.flags.get(GARAGE) or []) if isinstance(r, dict)]


def _park(content: Content, ch: ActiveCharacter) -> bool:
    """Move the ride you drive into the garage. False when the garage is full."""
    r = ride(ch)
    if not r:
        return True
    g = parked(ch)
    if len(g) >= content.balance.vehicles.garage_max:
        return False
    ch.state.flags[GARAGE] = g + [r]
    ch.state.flags.pop(FLAG, None)
    return True


def model(content: Content, ch: ActiveCharacter) -> Vehicle | None:
    r = ride(ch)
    return content.vehicles.get(str(r["key"])) if r else None


def speed(content: Content, ch: ActiveCharacter) -> int:
    r, v = ride(ch), model(content, ch)
    if not r or v is None:
        return 0
    return max(1, v.speed + int(r.get("tune", 0) or 0) - int(r.get("dmg", 0) or 0) // 34)


def tune_cost(content: Content, v: Vehicle, level: int) -> int:
    return v.price * content.balance.vehicles.tune_pct * (level + 1) // 100


def repair_cost(content: Content, v: Vehicle, dmg: int) -> int:
    return v.price * content.balance.vehicles.repair_pct * dmg // 10000


def sell_price(content: Content, v: Vehicle, r: dict) -> int:
    cfg = content.balance.vehicles
    pct = cfg.stolen_sell_pct if r.get("stolen") else cfg.sell_pct
    return max(0, v.price * pct // 100 * (100 - int(r.get("dmg", 0) or 0)) // 100)


def dealer(content: Content, year: int) -> list[Vehicle]:
    return sorted((v for v in content.available("vehicles", year).values() if not v.not_for_sale),
                  key=lambda v: (v.price, v.name))


@dataclass
class VResult:
    blocked: str | None = None
    amount: int = 0
    check: CheckResult | None = None
    applied: AppliedEffects | None = None
    text: str = ""
    lines: list[str] | None = None
    died: bool = False
    death_cause: str | None = None
    won: bool = False
    crashed: bool = False
    wrecked: bool = False
    got: str | None = None


async def _upgrade(session: AsyncSession, ch: ActiveCharacter, kind: str) -> None:
    await bus.publish("upgrade_done", player_tg_id=ch.player_tg_id, character_id=ch.id, session=session, ch=ch,
                      kind=kind)


async def _set(session: AsyncSession, ch: ActiveCharacter, r: dict | None, tz: str) -> None:
    if r is None:
        ch.state.flags.pop(FLAG, None)
    else:
        ch.state.flags[FLAG] = r
    await save_active(session, ch, tz)


def _jailed(ch: ActiveCharacter) -> bool:
    return int(ch.state.flags.get("jail_weeks", 0) or 0) > 0


async def buy(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
              key: str) -> VResult:
    if _jailed(ch):
        return VResult(BLOCK_JAIL)
    v = content.available("vehicles", year).get(key)
    if v is None or v.not_for_sale:
        return VResult(BLOCK_GONE)
    if ch.state.street_cred < v.min_street_cred:
        return VResult(BLOCK_CRED)
    if ch.state.eddies < v.price:
        return VResult(BLOCK_EDDIES)
    if not _park(content, ch):  # owner request 24.10: the old ride goes to the garage, not to a dealer
        return VResult(BLOCK_FULL)
    apply_effects(ch.state, {"eddies": -v.price}, content, rng, year)
    await _set(session, ch, {"key": key, "tune": 0, "dmg": 0, "stolen": False}, tz)
    await _upgrade(session, ch, "ride_bought")
    await repo.log_event(session, "vehicle_buy", {"key": key, "price": v.price}, character_id=ch.id)
    if v.price >= 20000:
        from app.services import loud

        await loud.post(session, ch.id, f"По городу катается новенький {v.name}. За рулём: "
                                        f"{loud.who(ch.state.name, ch.state.surname)}.")
    return VResult(None, v.price)


async def sell(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
               tz: str) -> VResult:
    r, v = ride(ch), model(content, ch)
    if not r or v is None:
        return VResult(BLOCK_NONE)
    amount = sell_price(content, v, r)
    apply_effects(ch.state, {"eddies": amount}, content, rng, year)
    await _set(session, ch, None, tz)
    return VResult(None, amount)


async def drive(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                tz: str, idx: int) -> VResult:
    """Take a ride out of the garage; the one you drive goes in its place."""
    g = parked(ch)
    if not 0 <= idx < len(g):
        return VResult(BLOCK_GONE)
    take = g.pop(idx)
    cur = ride(ch)
    if cur:
        g.append(cur)
    ch.state.flags[GARAGE] = g
    await _set(session, ch, take, tz)
    return VResult(None, got=str(take.get("key")))


async def sell_parked(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                      tz: str, idx: int) -> VResult:
    g = parked(ch)
    if not 0 <= idx < len(g):
        return VResult(BLOCK_GONE)
    r = g.pop(idx)
    v = content.vehicles.get(str(r.get("key")))
    amount = sell_price(content, v, r) if v else 0
    apply_effects(ch.state, {"eddies": amount}, content, rng, year)
    if g:
        ch.state.flags[GARAGE] = g
    else:
        ch.state.flags.pop(GARAGE, None)
    await save_active(session, ch, tz)
    return VResult(None, amount, got=str(r.get("key")))


async def tune(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
               tz: str) -> VResult:
    r, v = ride(ch), model(content, ch)
    if not r or v is None:
        return VResult(BLOCK_NONE)
    level = int(r.get("tune", 0) or 0)
    if level >= content.balance.vehicles.tune_max:
        return VResult(BLOCK_MAX)
    cost = tune_cost(content, v, level)
    if ch.state.eddies < cost:
        return VResult(BLOCK_EDDIES, cost)
    apply_effects(ch.state, {"eddies": -cost, "skill_xp:tech": 1}, content, rng, year)
    r["tune"] = level + 1
    await _set(session, ch, r, tz)
    await _upgrade(session, ch, "tune")
    return VResult(None, cost)


async def repair(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                 tz: str) -> VResult:
    r, v = ride(ch), model(content, ch)
    if not r or v is None:
        return VResult(BLOCK_NONE)
    dmg = int(r.get("dmg", 0) or 0)
    if dmg <= 0:
        return VResult(BLOCK_CLEAN)
    cost = repair_cost(content, v, dmg)
    if ch.state.eddies < cost:
        return VResult(BLOCK_EDDIES, cost)
    apply_effects(ch.state, {"eddies": -cost}, content, rng, year)
    r["dmg"] = 0
    await _set(session, ch, r, tz)
    await _upgrade(session, ch, "repair")
    return VResult(None, cost)


async def renumber(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                   tz: str) -> VResult:
    r = ride(ch)
    if not r:
        return VResult(BLOCK_NONE)
    if not r.get("stolen"):
        return VResult(BLOCK_CLEAN)
    cost = content.balance.vehicles.renumber_cost
    if ch.state.eddies < cost:
        return VResult(BLOCK_EDDIES, cost)
    apply_effects(ch.state, {"eddies": -cost}, content, rng, year)
    r["stolen"] = False
    await _set(session, ch, r, tz)
    return VResult(None, cost)


async def steal(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                tz: str) -> VResult:
    """Steal a street ride for yourself (REF + driving). A miss brings heat, sometimes a few weeks inside."""
    cfg = content.balance.vehicles
    if _jailed(ch):
        return VResult(BLOCK_JAIL)
    if ride(ch) and len(parked(ch)) >= cfg.garage_max:
        return VResult(BLOCK_FULL)
    if not ap_services.can_spend(ch, 1):
        return VResult(BLOCK_NO_AP)
    ap_services.spend(ch, content, 1)
    res = VResult()
    res.check = checks.roll_check(ch.state, content, rng, "REF", "driving", cfg.steal_dv, ("crime",))
    if res.check.ok:
        pool = [v for v in content.available("vehicles", year).values()
                if v.stealable and not v.not_for_sale and v.price <= cfg.steal_price_max and v.kind != "joke"]
        v = rng.choice(pool)
        res.got = v.key
        res.applied = apply_effects(ch.state, {"street_cred": 1, "skill_xp:driving": 1}, content, rng, year)
        _park(content, ch)
        await _set(session, ch, {"key": v.key, "tune": 0, "dmg": rng.randint(0, 30), "stolen": True}, tz)
        await repo.log_event(session, "crime", {"kind": "car_theft", "ok": True, "keep": v.key}, character_id=ch.id)
        await _upgrade(session, ch, "ride_stolen")
        return res
    fx: dict[str, int | bool] = {"heat": cfg.steal_fail_heat}
    if rng.random() < cfg.steal_jail_chance:
        fx["jail_weeks"] = cfg.steal_jail_weeks
    res.applied = apply_effects(ch.state, fx, content, rng, year)
    out = await followups.handle(session, content, ch, res.applied.followups, rng, year, tz)
    res.lines, res.died, res.death_cause = out.lines, out.died, out.cause
    if not out.died:
        await save_active(session, ch, tz)
    await repo.log_event(session, "crime", {"kind": "car_theft", "ok": False}, character_id=ch.id)
    return res


def race_tiers(content: Content) -> list[RaceTier]:
    return list(content.balance.vehicles.races)


async def race(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
               tier_key: str) -> VResult:
    """A street race: REF + driving + (speed - 5) against the tier. Win: double the stake and fame. Lose: the stake,
    and maybe a crash (hp and damage to the ride; at `wreck_at` the ride is gone)."""
    cfg = content.balance.vehicles
    tier = next((t for t in cfg.races if t.key == tier_key), None)
    r, v = ride(ch), model(content, ch)
    if tier is None:
        return VResult(BLOCK_GONE)
    if not r or v is None:
        return VResult(BLOCK_NONE)
    if _jailed(ch):
        return VResult(BLOCK_JAIL)
    if ch.state.street_cred < tier.min_street_cred:
        return VResult(BLOCK_CRED)
    if ch.state.eddies < tier.stake:
        return VResult(BLOCK_EDDIES, tier.stake)
    if not ap_services.can_spend(ch, cfg.race_ap):
        return VResult(BLOCK_NO_AP)
    ap_services.spend(ch, content, cfg.race_ap)
    res = VResult()
    res.check = checks.roll_check(ch.state, content, rng, "REF", "driving", tier.dv, (), extra=speed(content, ch) - 5)
    if res.check.ok:
        res.won = True
        res.applied = apply_effects(ch.state, {"eddies": tier.stake, "fame": tier.fame, "skill_xp:driving": 2},
                                    content, rng, year)
        if tier.key == "night_league":
            from app.services import loud

            await loud.post(session, ch.id, f"Ночная лига: первым финиширует {v.name}. За рулём "
                                            f"{loud.who(ch.state.name, ch.state.surname)}, выигрыш {tier.stake * 2} эдди.")
    else:
        fx: dict[str, int | bool] = {"eddies": -tier.stake, "skill_xp:driving": 1}
        if rng.random() < cfg.crash_chance:
            res.crashed = True
            hp = max(1, rng.randint(*cfg.crash_hp) - v.armor)
            fx["hp"] = -hp
            r["dmg"] = int(r.get("dmg", 0) or 0) + max(5, rng.randint(*cfg.crash_damage) - v.armor * 3)
            if r["dmg"] >= cfg.wreck_at:
                res.wrecked = True
        res.applied = apply_effects(ch.state, fx, content, rng, year)
        out = await followups.handle(session, content, ch, res.applied.followups, rng, year, tz)
        res.lines, res.died, res.death_cause = out.lines, out.died, out.cause
        if out.died:
            return res
    if res.wrecked:
        await _set(session, ch, None, tz)
    else:
        await _set(session, ch, r, tz)
    await repo.log_event(session, "race", {"tier": tier.key, "won": res.won, "crash": res.crashed}, character_id=ch.id)
    from app.services import game_events as bus

    if res.won:
        await bus.publish("drive", player_tg_id=ch.player_tg_id, character_id=ch.id, session=session, ch=ch)
    return res
