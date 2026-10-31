"""Small games started by a word in the chat (owner request): the slot machine and the fishing.

State on the character: flags `slots` = {day, n, last} (the daily count and the cooldown), `fishing` =
{rod, day, casts}, `fish` = {catch key: count} (the keepnet). Money moves through `apply_effects`.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import kyiv_date
from app.content.loader import Content
from app.content.schemas import FishCatch
from app.db import repo
from app.engine import chatgames as eng
from app.engine.effects import AppliedEffects, apply_effects
from app.services import game
from app.services import game_events as bus
from app.services.game import ActiveCharacter, save_active

FLAG_SLOTS = "slots"
FLAG_FISHING = "fishing"
FLAG_FISH = "fish"

R_DEAD = "dead"          # the player's character died: the heir is chosen in /play
R_COOLDOWN = "cooldown"
R_DAILY = "daily"
R_EDDIES = "eddies"
R_JAIL = "jail"
R_NO_ROD = "no_rod"
R_CASTS = "casts"
R_HAVE = "have"
R_GONE = "gone"
R_EMPTY = "empty"


# -- the character -------------------------------------------------------------------------------------------------------


@dataclass
class Who:
    ch: ActiveCharacter | None
    created: bool = False
    blocked: str | None = None


async def ensure_character(
    session: AsyncSession, content: Content, tg_id: int, username: str | None, tz: str,
) -> Who:
    """The player's living character; a newcomer who never had one gets a character of a random preset (owner request).
    A player whose character died chooses the heir in /play, so nothing is created for him."""
    ch = await game.get_active_character(session, tg_id)
    if ch is not None:
        return Who(ch)
    from app.services import legacy

    if await legacy.pending_dead(session, tg_id) is not None:
        return Who(None, blocked=R_DEAD)
    if (await game.lineage_info(session, tg_id)).lives_count > 0:
        return Who(None, blocked=R_DEAD)
    rng = random.Random()
    year = await game.current_year(session, content, tg_id)
    presets = list(content.available("presets", year))
    draft = await game.new_draft(session, content, tg_id, rng.choice(presets), rng)
    ch = await game.create_character_from_draft(session, content, tg_id, username, draft, tz)
    return Who(ch, created=True)


def _jailed(ch: ActiveCharacter) -> bool:
    return int(ch.state.flags.get("jail_weeks", 0) or 0) > 0


# -- slots ---------------------------------------------------------------------------------------------------------------


def slots_block(content: Content, ch: ActiveCharacter, stake: int, tz: str) -> str | None:
    cfg = content.balance.chat_games.slots
    st = ch.state
    if _jailed(ch):
        return R_JAIL
    rec = dict(st.flags.get(FLAG_SLOTS) or {})
    if time.time() - float(rec.get("last", 0) or 0) < cfg.cooldown_s:
        return R_COOLDOWN
    if rec.get("day") == kyiv_date(tz) and int(rec.get("n", 0) or 0) >= cfg.daily_max:
        return R_DAILY
    if st.eddies < stake:
        return R_EDDIES
    return None


@dataclass
class SpinResult:
    reels: tuple[int, int, int]
    stake: int
    mult: int
    win: int  # paid back (0 when lost)
    applied: AppliedEffects | None = None


async def spin(
    session: AsyncSession, content: Content, ch: ActiveCharacter, stake: int, value: int, tz: str,
) -> SpinResult:
    """Settle a spin whose 🎰 already landed on `value`: the stake goes, the win comes."""
    cfg = content.balance.chat_games.slots
    st = ch.state
    reels = eng.slot_reels(value)
    mult = eng.slot_mult(reels, cfg)
    win = stake * mult
    rng = random.Random()
    applied = apply_effects(st, {"eddies": win - stake}, content, rng, st.current_year) if win != stake else None
    rec = dict(st.flags.get(FLAG_SLOTS) or {})
    today = kyiv_date(tz)
    if rec.get("day") != today:
        rec = {"day": today, "n": 0}
    rec["n"] = int(rec.get("n", 0) or 0) + 1
    rec["last"] = time.time()
    st.flags[FLAG_SLOTS] = rec
    await save_active(session, ch, tz)
    await repo.log_event(session, "casino", {"stake": stake, "mult": mult, "value": value}, character_id=ch.id)
    if mult >= cfg.jackpot_mult:
        from app.services import loud

        await loud.post(session, ch.id, f"Джекпот в слотах! {loud.who(st.name, st.surname)}: три семёрки, "
                                        f"+{win} эдди. Казино в шоке.")
    await bus.publish("casino_spin", player_tg_id=ch.player_tg_id, character_id=ch.id, session=session, ch=ch,
                      won=mult > 0)
    return SpinResult(reels, stake, mult, win, applied)


# -- fishing -------------------------------------------------------------------------------------------------------------


def fishing_state(ch: ActiveCharacter, tz: str) -> dict:
    rec = dict(ch.state.flags.get(FLAG_FISHING) or {})
    if rec.get("day") != kyiv_date(tz):
        rec["day"] = kyiv_date(tz)
        rec["casts"] = 0
    return rec


def keepnet(ch: ActiveCharacter) -> dict[str, int]:
    return {str(k): int(v) for k, v in (ch.state.flags.get(FLAG_FISH) or {}).items() if int(v) > 0}


async def buy_rod(
    session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, tz: str, key: str, credit: bool,
) -> tuple[str | None, int]:
    """Buy a rod (or take it on credit: a debt with the markup). Returns (blocked, price or debt)."""
    r = next((x for x in eng.rods_for(content, year) if x.key == key), None)
    if r is None:
        return R_GONE, 0
    rec = fishing_state(ch, tz)
    cur = eng.rod(content, rec.get("rod"))
    if cur is not None and cur.tier >= r.tier:
        return R_HAVE, 0
    rng = random.Random()
    if credit:
        debt = r.price * (100 + content.balance.chat_games.fishing.credit_markup_pct) // 100
        apply_effects(ch.state, {"debt": debt}, content, rng, year)
        paid = debt
    else:
        if ch.state.eddies < r.price:
            return R_EDDIES, r.price
        apply_effects(ch.state, {"eddies": -r.price}, content, rng, year)
        paid = r.price
    rec["rod"] = r.key
    ch.state.flags[FLAG_FISHING] = rec
    await save_active(session, ch, tz)
    return None, paid


@dataclass
class CastResult:
    blocked: str | None = None
    catch: FishCatch | None = None
    applied: AppliedEffects | None = None
    event: str | None = None
    extra: list[str] = field(default_factory=list)


async def cast(
    session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, tz: str, rng: random.Random,
) -> CastResult:
    cfg = content.balance.chat_games.fishing
    st = ch.state
    if _jailed(ch):
        return CastResult(R_JAIL)
    rec = fishing_state(ch, tz)
    r = eng.rod(content, rec.get("rod"))
    if r is None:
        return CastResult(R_NO_ROD)
    if int(rec.get("casts", 0) or 0) >= cfg.daily_casts:
        return CastResult(R_CASTS)
    if st.eddies < cfg.bait_cost:
        return CastResult(R_EDDIES)
    fc = eng.roll_catch(content, rng, r.tier, st.district)
    fx: dict = {"eddies": -cfg.bait_cost}
    res = CastResult(catch=fc)
    if fc.kind == "loot":
        for k, v in fc.effects.items():
            fx[k] = (int(fx.get(k, 0)) + int(v)) if k == "eddies" else v
        if fc.event:
            res.event = fc.event
    else:
        net = dict(keepnet(ch))
        net[fc.key] = net.get(fc.key, 0) + 1
        st.flags[FLAG_FISH] = net
    res.applied = apply_effects(st, fx, content, rng, year)
    rec["casts"] = int(rec.get("casts", 0) or 0) + 1
    st.flags[FLAG_FISHING] = rec
    if res.event:
        from app.services.week import pending_events, set_pending

        set_pending(ch, [res.event] + [e for e in pending_events(ch) if e != res.event])
    await save_active(session, ch, tz)
    if fc.loud:
        from app.services import loud

        await loud.post(session, ch.id, fc.loud.format(name=loud.who(st.name, st.surname)))
    await repo.log_event(session, "fishing", {"catch": fc.key}, character_id=ch.id)
    await bus.publish("fish_caught", player_tg_id=ch.player_tg_id, character_id=ch.id, session=session, ch=ch,
                      rare=fc.rarity >= 2)
    return res


async def sell(session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, tz: str) -> tuple[str | None, int]:
    net = keepnet(ch)
    total, _ = eng.sale(content, net, ch.state.district)
    if not net or total <= 0:
        return R_EMPTY, 0
    apply_effects(ch.state, {"eddies": total}, content, random.Random(), year)
    ch.state.flags.pop(FLAG_FISH, None)
    await save_active(session, ch, tz)
    return None, total
