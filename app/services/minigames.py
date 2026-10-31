"""The meme mini-games (PLAN 4.7, DESIGN.md 12a): caliber, car, cat, coin, dirt and /dump.

All of it belongs to the PLAYER and outlives characters, except what the rules tie to eddies and
wanted level (coin trades, repairs, the dump) which needs a living character. A "day" is a Kyiv day
('YYYY-MM-DD'); every once-a-day game remembers the date of its last play.

Functions return small result objects; the handlers turn them into text. Nothing here sends.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.db import repo
from app.db.models import Player
from app.engine import checks, minigames
from app.engine.daily import days_between
from app.engine.effects import apply_effects
from app.services import game
from app.services.game import ActiveCharacter, save_active

# ---------------------------------------------------------------------------
# Caliber
# ---------------------------------------------------------------------------


@dataclass
class CaliberOutcome:
    already: bool
    mm: int
    title: str
    result: minigames.CaliberResult | None = None


async def caliber_daily(
    session: AsyncSession, content: Content, tg_id: int, username: str | None, rng: random.Random, today: str,
    change: int | None = None, dry_run: bool = False,
) -> CaliberOutcome:
    """The day's caliber change; a second call the same day just shows the caliber. `change` is a roll already made
    (a Telegram dice); `dry_run` only tells whether a roll would happen (`already` False) and changes nothing."""
    player = await repo.ensure_player(session, tg_id, username)
    last = dict(player.mini_last or {})
    if last.get("caliber") == today:
        mm = int(player.caliber_mm or 0)
        return CaliberOutcome(True, mm, minigames.caliber_title(content, mm))
    if dry_run:
        return CaliberOutcome(False, int(player.caliber_mm or 0), "")
    res = minigames.roll_caliber(content, rng, int(player.caliber_mm) if last.get("caliber") else None, change)
    player.caliber_mm = res.after
    last["caliber"] = today
    player.mini_last = last
    await session.flush()
    return CaliberOutcome(False, res.after, res.title, res)


async def caliber_top(session: AsyncSession, content: Content, limit: int) -> list[tuple[Player, int, str]]:
    """Players who have rolled at least once, biggest caliber first: (player, mm, title)."""
    res = await session.execute(select(Player))
    rows = [p for p in res.scalars() if (p.mini_last or {}).get("caliber")]
    rows.sort(key=lambda p: (-int(p.caliber_mm or 0), p.tg_id))
    return [(p, int(p.caliber_mm or 0), minigames.caliber_title(content, int(p.caliber_mm or 0))) for p in rows[:limit]]


# ---------------------------------------------------------------------------
# Car
# ---------------------------------------------------------------------------


async def get_car(session: AsyncSession, content: Content, tg_id: int, username: str | None = None) -> Player:
    """The player's row with a car: the first call buys the starting one (balance.car.start_hp)."""
    player = await repo.ensure_player(session, tg_id, username)
    cfg = content.balance.car
    if int(player.car_hp or 0) <= 0:
        player.car_hp = cfg.start_hp
        player.car_name = cfg.default_name
        player.car_parts = []
        await session.flush()
    return player


@dataclass
class TuneOutcome:
    already: bool
    hp: int
    result: minigames.TuneResult | None = None
    lost_part_name: str | None = None


def part_name(content: Content, key: str) -> str:
    for p in content.balance.car.parts:
        if p.key == key:
            return p.name
    return key


async def tune(
    session: AsyncSession, content: Content, tg_id: int, rng: random.Random, today: str,
) -> TuneOutcome:
    player = await get_car(session, content, tg_id)
    last = dict(player.mini_last or {})
    if last.get("car") == today:
        return TuneOutcome(True, int(player.car_hp))
    missing = list(player.car_parts or [])
    res = minigames.tune_car(content, rng, int(player.car_hp), missing)
    player.car_hp = res.after
    if res.lost_part:
        player.car_parts = missing + [res.lost_part]
    last["car"] = today
    player.mini_last = last
    await session.flush()
    return TuneOutcome(False, res.after, res, part_name(content, res.lost_part) if res.lost_part else None)


REPAIR_OK = "ok"
REPAIR_NO_CHAR = "character"
REPAIR_NO_EDDIES = "eddies"
REPAIR_NOTHING = "nothing"


async def repair(
    session: AsyncSession, content: Content, tg_id: int, ch: ActiveCharacter | None, part_key: str,
    rng: random.Random, year: int, tz: str,
) -> str:
    """Put a missing part back for balance.car.repair_cost eddies of the living character."""
    player = await get_car(session, content, tg_id)
    missing = list(player.car_parts or [])
    if part_key not in missing:
        return REPAIR_NOTHING
    cost = content.balance.car.repair_cost
    if ch is None:
        return REPAIR_NO_CHAR
    if ch.state.eddies < cost:
        return REPAIR_NO_EDDIES
    apply_effects(ch.state, {"eddies": -cost}, content, rng, year)
    await save_active(session, ch, tz)
    missing.remove(part_key)
    player.car_parts = missing
    await session.flush()
    return REPAIR_OK


RACE_OK = "ok"
RACE_SELF = "self"
RACE_STAKE = "stake"
RACE_NO_CAR = "no_car"
RACE_NO_CAR_OTHER = "no_car_other"
RACE_NO_TOKENS = "no_tokens"
RACE_NO_TOKENS_OTHER = "no_tokens_other"
RACE_DONE_TODAY = "done_today"
RACE_NO_PLAYER = "no_player"


@dataclass
class RaceOutcome:
    status: str
    result: minigames.RaceResult | None = None
    winner_id: int | None = None
    loser_id: int | None = None
    stake: int = 0
    hp: tuple[int, int] = (0, 0)
    missing: tuple[int, int] = (0, 0)


def _pair(a: int, b: int) -> str:
    lo, hi = sorted((a, b))
    return f"{lo}:{hi}"


async def race(
    session: AsyncSession, content: Content, a_id: int, b_id: int, stake: int, rng: random.Random, today: str,
    rolls: tuple[int, int] | None = None, dry_run: bool = False,
) -> RaceOutcome:
    """A race on the spot (the challenged player is not asked): power + roll, tokens change hands.
    `rolls` are the two race rolls already made (Telegram dice); `dry_run` stops before the roll with RACE_OK."""
    cfg = content.balance.car
    if a_id == b_id:
        return RaceOutcome(RACE_SELF)
    if stake not in cfg.stakes_tokens:
        return RaceOutcome(RACE_STAKE, stake=stake)
    a, b = await session.get(Player, a_id), await session.get(Player, b_id)
    if a is None:
        return RaceOutcome(RACE_NO_PLAYER)
    if b is None:
        return RaceOutcome(RACE_NO_PLAYER)
    if int(a.car_hp or 0) <= 0:
        return RaceOutcome(RACE_NO_CAR)
    if int(b.car_hp or 0) <= 0:
        return RaceOutcome(RACE_NO_CAR_OTHER)
    if stake and int(a.tokens or 0) < stake:
        return RaceOutcome(RACE_NO_TOKENS, stake=stake)
    if stake and int(b.tokens or 0) < stake:
        return RaceOutcome(RACE_NO_TOKENS_OTHER, stake=stake)
    log = dict(await repo.ws_get(session, "race_log", {}) or {})
    todays = list(log.get(today, []))
    if todays.count(_pair(a_id, b_id)) >= cfg.races_per_pair_per_day:
        return RaceOutcome(RACE_DONE_TODAY)
    if dry_run:
        return RaceOutcome(RACE_OK, stake=stake)
    ma, mb = list(a.car_parts or []), list(b.car_parts or [])
    res = minigames.run_race(content, rng, (int(a.car_hp), ma), (int(b.car_hp), mb), rolls)
    winner = loser = None
    if res.winner is not None:
        winner, loser = (a_id, b_id) if res.winner == 0 else (b_id, a_id)
        if stake:
            w, l = (a, b) if res.winner == 0 else (b, a)
            l.tokens = int(l.tokens) - stake
            w.tokens = int(w.tokens) + stake
    log = {today: todays + [_pair(a_id, b_id)]}  # older days are dropped: only today's pairs matter
    await repo.ws_set(session, "race_log", log)
    await session.flush()
    return RaceOutcome(RACE_OK, res, winner, loser, stake, (int(a.car_hp), int(b.car_hp)), (len(ma), len(mb)))


# ---------------------------------------------------------------------------
# Cat
# ---------------------------------------------------------------------------


async def get_cat(session: AsyncSession, content: Content, tg_id: int, today: str, username: str | None = None) -> tuple[Player, dict[str, Any]]:
    """The player and their cat, adopted on the first call and marked gone when it waited too long."""
    player = await repo.ensure_player(session, tg_id, username)
    cat = dict(player.cat or {})
    if not cat:
        cat = minigames.new_cat(content, today)
    else:
        cat = minigames.refresh_cat(content, cat, today)
    player.cat = cat
    await session.flush()
    return player, cat


@dataclass
class FeedOutcome:
    status: str  # "ok" / "fed_today" / "gone"
    cat: dict[str, Any] = field(default_factory=dict)
    new_level: int | None = None


async def feed(session: AsyncSession, content: Content, tg_id: int, today: str) -> FeedOutcome:
    player, cat = await get_cat(session, content, tg_id, today)
    if cat.get("gone"):
        return FeedOutcome("gone", cat)
    if not minigames.can_feed(cat, today):
        return FeedOutcome("fed_today", cat)
    cat, leveled = minigames.feed_cat(content, cat, today)
    player.cat = cat
    await session.flush()
    return FeedOutcome("ok", cat, leveled)


async def bring_cat_back(session: AsyncSession, content: Content, tg_id: int, today: str) -> str:
    """Pay balance.cat.return_cost_tokens to get the cat back. 'ok' / 'not_gone' / 'tokens'."""
    player, cat = await get_cat(session, content, tg_id, today)
    if not cat.get("gone"):
        return "not_gone"
    cost = content.balance.cat.return_cost_tokens
    if int(player.tokens or 0) < cost:
        return "tokens"
    player.tokens = int(player.tokens) - cost
    player.cat = minigames.bring_back(cat, today)
    await session.flush()
    return "ok"


@dataclass
class CatFightOutcome:
    status: str  # ok / self / stake / no_cat / no_cat_other / gone / gone_other / tired / tired_other / no_tokens / no_tokens_other / no_player
    scores: tuple[int, int] = (0, 0)
    rolls: tuple[int, int] = (0, 0)
    winner_id: int | None = None
    loser_id: int | None = None
    stake: int = 0
    names: tuple[str, str] = ("", "")
    sulking: tuple[bool, bool] = (False, False)


async def cat_fight(
    session: AsyncSession, content: Content, a_id: int, b_id: int, stake: int, rng: random.Random, today: str,
    rolls: tuple[int, int] | None = None, dry_run: bool = False,
) -> CatFightOutcome:
    """A cat fight on the spot. `rolls` are the two fight rolls already made (Telegram dice); `dry_run` stops before
    the roll with status "ok" (the cats' moods are refreshed, nothing else changes)."""
    cfg = content.balance.cat
    if a_id == b_id:
        return CatFightOutcome("self")
    if stake not in cfg.stakes_tokens:
        return CatFightOutcome("stake", stake=stake)
    a, b = await session.get(Player, a_id), await session.get(Player, b_id)
    if a is None or b is None or not a.cat:
        return CatFightOutcome("no_cat" if a is None or not a.cat else "no_player")
    if not b.cat:
        return CatFightOutcome("no_cat_other")
    ca = minigames.refresh_cat(content, dict(a.cat), today)
    cb = minigames.refresh_cat(content, dict(b.cat), today)
    a.cat, b.cat = ca, cb
    if ca.get("gone"):
        return CatFightOutcome("gone")
    if cb.get("gone"):
        return CatFightOutcome("gone_other")
    if minigames.fights_left(content, ca, today) <= 0:
        return CatFightOutcome("tired")
    if minigames.fights_left(content, cb, today) <= 0:
        return CatFightOutcome("tired_other")
    if stake and int(a.tokens or 0) < stake:
        return CatFightOutcome("no_tokens", stake=stake)
    if stake and int(b.tokens or 0) < stake:
        return CatFightOutcome("no_tokens_other", stake=stake)
    if dry_run:
        return CatFightOutcome("ok", stake=stake)
    sa, ra = minigames.cat_fight_score(content, rng, ca, today, rolls[0] if rolls else None)
    sb, rb = minigames.cat_fight_score(content, rng, cb, today, rolls[1] if rolls else None)
    out = CatFightOutcome(
        "ok", (sa, sb), (ra, rb), None, None, stake, (str(ca.get("name")), str(cb.get("name"))),
        (minigames.cat_state(content, ca, today) == minigames.CAT_SULK,
         minigames.cat_state(content, cb, today) == minigames.CAT_SULK),
    )
    if sa != sb:
        out.winner_id, out.loser_id = (a_id, b_id) if sa > sb else (b_id, a_id)
        if stake:
            w, l = (a, b) if sa > sb else (b, a)
            l.tokens = int(l.tokens) - stake
            w.tokens = int(w.tokens) + stake
    a.cat, b.cat = minigames.mark_fight(ca, today), minigames.mark_fight(cb, today)
    await session.flush()
    return out


# ---------------------------------------------------------------------------
# Coin
# ---------------------------------------------------------------------------

COIN_RATE_KEY = "coin_rate"
COIN_HISTORY_KEY = "coin_history"
COIN_DAY_KEY = "coin_day"
COIN_SHOCK_KEY = "coin_shock_until"
COIN_SHOCKS_KEY = "coin_shocks"


async def ensure_coin_rate(session: AsyncSession, content: Content, today: str) -> float:
    """The shared rate for `today`. The first call sets the start rate; every missed day takes one step.

    A day's step is drawn from an rng seeded by that date, so the 03:00 job, a lazy call after a
    restart and a second bot instance all land on the same rate. Idempotent within a day.
    """
    cfg = content.balance.coin
    import time as _time

    period = cfg.period_minutes * 60
    now_idx = int(_time.time() // period)  # owner request: the rate moves every `period_minutes`
    rate = await repo.ws_get(session, COIN_RATE_KEY)
    last = await repo.ws_get(session, COIN_DAY_KEY)
    history = list(await repo.ws_get(session, COIN_HISTORY_KEY, []) or [])
    if rate is None or last is None:
        rate = cfg.start_rate
        history = [{"day": today, "rate": rate}]
        await repo.ws_set(session, COIN_RATE_KEY, rate)
        await repo.ws_set(session, COIN_DAY_KEY, f"p:{now_idx}")
        await repo.ws_set(session, COIN_HISTORY_KEY, history)
        return float(rate)
    last_s = str(last)
    last_idx = int(last_s[2:]) if last_s.startswith("p:") else now_idx - 1  # the old daily key: one step now
    gap = now_idx - last_idx
    if gap <= 0:
        return float(rate)
    shock_until = await repo.ws_get(session, COIN_SHOCK_KEY)
    rate = float(rate)
    steps = min(gap, cfg.history_days)
    from datetime import datetime as _dt

    for i in range(now_idx - steps + 1, now_idx + 1):
        stamp = _dt.fromtimestamp(i * period)
        day = stamp.date().isoformat()
        shock = bool(shock_until and day <= str(shock_until))
        rate = minigames.next_rate(content, random.Random(f"coin:p{i}"), rate, shock)
        history.append({"day": stamp.strftime("%d.%m %H:%M"), "rate": rate})
    await repo.ws_set(session, COIN_RATE_KEY, rate)
    await repo.ws_set(session, COIN_DAY_KEY, f"p:{now_idx}")
    await repo.ws_set(session, COIN_HISTORY_KEY, history[-cfg.history_days:])
    return rate


async def apply_coin_shock(
    session: AsyncSession, content: Content, mult: float, today: str, history_id: int | None = None,
) -> float:
    """A world event hits the Eddy-crypto (`coin_shock`): the rate is multiplied once, inside the rate corridor, and the
    daily swing is wider for `shock_volatility_days` days. With the `history_id` of the world event the shock is
    remembered, so `/admin_veto` can undo it (`undo_coin_shock`). Returns the new rate."""
    cfg = content.balance.coin
    old_rate = await ensure_coin_rate(session, content, today)
    rate = max(cfg.min_rate, min(cfg.max_rate, old_rate * mult))
    history = list(await repo.ws_get(session, COIN_HISTORY_KEY, []) or [])
    if history and history[-1].get("day") == today:
        history[-1] = {"day": today, "rate": rate}
    else:
        history.append({"day": today, "rate": rate})
    until = (date.fromisoformat(today) + timedelta(days=cfg.shock_volatility_days)).isoformat()
    prev_until = await repo.ws_get(session, COIN_SHOCK_KEY)
    await repo.ws_set(session, COIN_RATE_KEY, rate)
    await repo.ws_set(session, COIN_HISTORY_KEY, history[-cfg.history_days:])
    await repo.ws_set(session, COIN_SHOCK_KEY, max(str(prev_until or ""), until))
    if history_id is not None:
        shocks = dict(await repo.ws_get(session, COIN_SHOCKS_KEY, {}) or {})
        shocks[str(history_id)] = {"mult": rate / old_rate if old_rate else 1.0, "day": today, "until": until}
        for key in sorted(shocks, key=int)[:-10]:  # only the recent ones can still be vetoed
            shocks.pop(key)
        await repo.ws_set(session, COIN_SHOCKS_KEY, shocks)
    return rate


async def undo_coin_shock(session: AsyncSession, content: Content, history_id: int) -> bool:
    """Take back the shock of a vetoed world event: the rate and the chart days since the shock are divided by the
    multiplier that was really applied (the corridor may have cut it), and the wider swing ends unless another shock
    still holds it. Returns False when this event had no remembered shock."""
    cfg = content.balance.coin
    shocks = dict(await repo.ws_get(session, COIN_SHOCKS_KEY, {}) or {})
    rec = shocks.pop(str(history_id), None)
    if rec is None:
        return False
    mult = float(rec.get("mult") or 1.0)
    if mult > 0:
        def back(x: float) -> float:
            return max(cfg.min_rate, min(cfg.max_rate, float(x) / mult))

        rate = await repo.ws_get(session, COIN_RATE_KEY)
        if rate is not None:
            await repo.ws_set(session, COIN_RATE_KEY, back(rate))
        history = list(await repo.ws_get(session, COIN_HISTORY_KEY, []) or [])
        for h in history:
            if str(h.get("day")) >= str(rec.get("day")):
                h["rate"] = back(h["rate"])
        await repo.ws_set(session, COIN_HISTORY_KEY, history)
    if shocks:
        await repo.ws_set(session, COIN_SHOCKS_KEY, shocks)
    else:
        await repo.ws_delete(session, COIN_SHOCKS_KEY)
    if shocks:
        await repo.ws_set(session, COIN_SHOCK_KEY, max(str(r.get("until") or "") for r in shocks.values()))
    elif await repo.ws_get(session, COIN_SHOCK_KEY) == rec.get("until"):
        await repo.ws_delete(session, COIN_SHOCK_KEY)
    return True


async def coin_history(session: AsyncSession) -> list[dict[str, Any]]:
    return list(await repo.ws_get(session, COIN_HISTORY_KEY, []) or [])


COIN_OK = "ok"
COIN_NO_CHAR = "character"
COIN_MIN = "min"
COIN_NO_EDDIES = "eddies"
COIN_NO_COINS = "coins"


@dataclass
class CoinTrade:
    status: str
    coins: float = 0.0
    eddies: int = 0
    fee: int = 0
    rate: float = 0.0


async def coin_buy(
    session: AsyncSession, content: Content, ch: ActiveCharacter | None, eddies: int, rng: random.Random,
    year: int, today: str, tz: str,
) -> CoinTrade:
    """Buy coins for `eddies` of the living character at today's rate; the coins go to the player's wallet."""
    rate = await ensure_coin_rate(session, content, today)
    if ch is None:
        return CoinTrade(COIN_NO_CHAR, rate=rate)
    if eddies < content.balance.coin.min_deal_eddies:
        return CoinTrade(COIN_MIN, rate=rate)
    if ch.state.eddies < eddies:
        return CoinTrade(COIN_NO_EDDIES, rate=rate)
    coins = minigames.coins_for(eddies, rate)
    apply_effects(ch.state, {"eddies": -eddies}, content, rng, year)
    await save_active(session, ch, tz)
    player = await repo.ensure_player(session, ch.player_tg_id)
    player.coins = round(float(player.coins or 0.0) + coins, 4)
    await session.flush()
    return CoinTrade(COIN_OK, coins, eddies, 0, rate)


async def coin_sell(
    session: AsyncSession, content: Content, ch: ActiveCharacter | None, tg_id: int, share: float,
    rng: random.Random, year: int, today: str, tz: str,
) -> CoinTrade:
    """Sell `share` (0..1) of the wallet for eddies of the living character, minus the commission."""
    rate = await ensure_coin_rate(session, content, today)
    player = await repo.ensure_player(session, tg_id)
    wallet = float(player.coins or 0.0)
    if wallet <= 0:
        return CoinTrade(COIN_NO_COINS, rate=rate)
    if ch is None:
        return CoinTrade(COIN_NO_CHAR, rate=rate)
    coins = wallet if share >= 1.0 else round(wallet * share, 4)
    gross, fee, net = minigames.sale(content, coins, rate)
    if gross < content.balance.coin.min_deal_eddies:
        return CoinTrade(COIN_MIN, rate=rate)
    apply_effects(ch.state, {"eddies": net}, content, rng, year)
    await save_active(session, ch, tz)
    player.coins = round(wallet - coins, 4) if share < 1.0 else 0.0
    await session.flush()
    return CoinTrade(COIN_OK, coins, net, fee, rate)


async def coin_top(session: AsyncSession, rate: float, limit: int) -> list[tuple[Player, float, int]]:
    """Wallets by value at the rate: (player, coins, worth in eddies)."""
    res = await session.execute(select(Player))
    rows = [(p, float(p.coins or 0.0)) for p in res.scalars() if float(p.coins or 0.0) > 0]
    rows.sort(key=lambda r: (-r[1], r[0].tg_id))
    return [(p, c, round(c * rate)) for p, c in rows[:limit]]


# ---------------------------------------------------------------------------
# Dirt and /dump
# ---------------------------------------------------------------------------


@dataclass
class DirtOutcome:
    already: bool
    gain: int
    total: int


async def dirt_daily(
    session: AsyncSession, content: Content, tg_id: int, username: str | None, rng: random.Random, today: str,
) -> DirtOutcome:
    player = await repo.ensure_player(session, tg_id, username)
    last = dict(player.mini_last or {})
    if last.get("dirt") == today:
        return DirtOutcome(True, 0, int(player.dirt or 0))
    gain = minigames.dirt_gain(content, rng)
    player.dirt = int(player.dirt or 0) + gain
    last["dirt"] = today
    player.mini_last = last
    await session.flush()
    return DirtOutcome(False, gain, int(player.dirt))


DUMP_OK = "ok"
DUMP_DEFENDED = "defended"
DUMP_SELF = "self"
DUMP_NO_DIRT = "no_dirt"
DUMP_NO_TARGET = "no_target"
DUMP_COOLDOWN = "cooldown"


@dataclass
class DumpOutcome:
    status: str
    defense: str | None = None  # "fixer" / "check" when defended
    days_left: int = 0
    heat: int = 0
    target_name: str = ""
    target_name_full: str = ""
    attacker_dirt: int = 0


async def dump(
    session: AsyncSession, content: Content, attacker_id: int, target_id: int, rng: random.Random, year: int,
    today: str, tz: str,
) -> DumpOutcome:
    """Spend the dirt on a player: wanted level +1 on their character unless they fend it off.

    The target defends with a COOL check or with a good relation to their district fixer; then the dirt
    comes back to the attacker. A blocked or defended dump still starts the per-target cooldown only
    when the dirt was actually spent (a refused command costs nothing).
    """
    cfg = content.balance.dirt
    if attacker_id == target_id:
        return DumpOutcome(DUMP_SELF)
    attacker = await repo.ensure_player(session, attacker_id)
    if int(attacker.dirt or 0) < cfg.dump_cost:
        return DumpOutcome(DUMP_NO_DIRT, attacker_dirt=int(attacker.dirt or 0))
    ch = await game.get_active_character(session, target_id)
    if ch is None:
        return DumpOutcome(DUMP_NO_TARGET)
    cooldowns = dict(attacker.dirt_targets or {})
    last = cooldowns.get(str(target_id))
    if last is not None:
        passed = days_between(str(last), today)
        if passed < cfg.target_cooldown_days:
            return DumpOutcome(DUMP_COOLDOWN, days_left=cfg.target_cooldown_days - passed)
    attacker.dirt = int(attacker.dirt) - cfg.dump_cost
    cooldowns[str(target_id)] = today
    attacker.dirt_targets = cooldowns
    st = ch.state
    out = DumpOutcome(DUMP_OK, target_name=st.name, target_name_full=f"{st.name} {st.surname}".strip())

    fixer = game.district_fixer(content, st.district, year)
    rel = st.fixer_rel.get(fixer.key, content.balance.gigs.fixer_rel_start) if fixer is not None else 0
    if fixer is not None and rel >= cfg.defense_fixer_rel_min:
        out.status, out.defense = DUMP_DEFENDED, "fixer"
    else:
        chk = checks.roll_check(st, content, rng, cfg.defense_check.stat, None, cfg.defense_check.dv, ("social",))
        if chk.ok:
            out.status, out.defense = DUMP_DEFENDED, "check"
    if out.status == DUMP_DEFENDED:
        attacker.dirt = int(attacker.dirt) + cfg.dump_cost  # the dirt boomerangs back
        out.attacker_dirt = int(attacker.dirt)
    else:
        applied = apply_effects(st, {"heat": cfg.heat_gain}, content, rng, year)
        out.heat = applied.deltas.get("heat", 0)
        out.attacker_dirt = int(attacker.dirt)
    await save_active(session, ch, tz)
    log = list(await repo.ws_get(session, "dump_log", []) or [])
    log.append({"day": today, "from": attacker_id, "to": target_id, "ok": out.status == DUMP_OK})
    keep = content.balance.reminders.dump_log_days
    log = [e for e in log if days_between(e["day"], today) < keep]
    await repo.ws_set(session, "dump_log", log)
    await repo.log_event(
        session, "dump", {"from": attacker_id, "to": target_id, "status": out.status, "defense": out.defense},
        character_id=ch.id, public=False,
    )
    await session.flush()
    return out
