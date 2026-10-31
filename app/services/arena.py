"""The fighting arena (owner request 30.10: «бойцовская арена, возможность выходить и участвовать; если юзер пошёл драться,
должна открыться ветка квестов по арене», docs/PLAN_WORLD2.md phase 4).

A ladder of ten fighters (balance.arena.ladder). A fight is an ordinary fight of the engine with one named opponent, bare
hands and arm chrome only (no guns on either side), to a knockout: the loser keeps 1 health, nobody dies. A win moves the
character one rung up and pays the purse, street cred and fame; a bet on yourself pays `bet_mult`. The story of the ring
(balance.arena.story, content/events_arena.yaml) opens with the first fight: one step a day, every next step after a win.

flag `arena` = {rank (rungs beaten), wins, losses, day, n (fights today), bet, story (steps taken), story_day, wins_at}
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import kyiv_date
from app.content.loader import Content
from app.engine import combat as cb
from app.engine.effects import AppliedEffects, apply_effects
from app.services import ap as ap_services
from app.services.game import ActiveCharacter, save_active

FLAG = "arena"
SRC = "arena"

BLOCK_OFF, BLOCK_JAIL, BLOCK_FIGHT, BLOCK_DAY, BLOCK_AP, BLOCK_EDDIES, BLOCK_TOP, BLOCK_BUSY = (
    "off", "jail", "fight", "day", "ap", "eddies", "top", "busy")
BLOCK_STORY_DAY, BLOCK_STORY_WIN, BLOCK_STORY_DONE = "story_day", "story_win", "story_done"


def cfg(content: Content):  # noqa: ANN201
    return content.balance.arena


def rec(ch: ActiveCharacter) -> dict[str, Any]:
    r = ch.state.flags.get(FLAG)
    return dict(r) if isinstance(r, dict) else {}


def _put(ch: ActiveCharacter, r: dict[str, Any]) -> None:
    ch.state.flags[FLAG] = r


def rank(ch: ActiveCharacter) -> int:
    return int(rec(ch).get("rank", 0) or 0)


def next_fighter(content: Content, ch: ActiveCharacter):  # noqa: ANN201
    ladder = cfg(content).ladder
    n = rank(ch)
    return ladder[n] if n < len(ladder) else None


def champion(content: Content, ch: ActiveCharacter) -> bool:
    return rank(ch) >= len(cfg(content).ladder)


def fights_today(ch: ActiveCharacter, tz: str) -> int:
    r = rec(ch)
    return int(r.get("n", 0) or 0) if r.get("day") == kyiv_date(tz) else 0


def arena_name(content: Content, ch: ActiveCharacter) -> str:
    from app.engine import place

    names = cfg(content).names
    where = place.place_of(ch.state, content)
    return names.get(where) or (f"Подпольный ринг, {place.name_of(content, where)}" if where != place.NC else names.get("nc", "Ринг"))


def _blocked(ch: ActiveCharacter) -> str | None:
    from app.services import events as ev_service
    from app.engine.state import FLAG_COMBAT, FLAG_PENDING_COMBAT

    st = ch.state
    if int(st.flags.get("jail_weeks", 0) or 0) > 0:
        return BLOCK_JAIL
    if st.flags.get(FLAG_COMBAT) or st.flags.get(FLAG_PENDING_COMBAT):
        return BLOCK_FIGHT
    if ev_service.has_pending(ch):
        return BLOCK_BUSY
    return None


@dataclass
class Start:
    blocked: str | None = None
    step: Any = None
    fighter: Any = None


def _bare_hands(cs: cb.CombatState, content: Content) -> None:
    """No guns and no programs in the ring: ranged attacks go on both sides; a fighter left without a melee attack
    gets fists at the strength of the gun it lost."""
    p = cs.player
    p.attacks.pop(cb.RANGED, None)
    p.arsenal = {k: a for k, a in p.arsenal.items() if a.kind != cb.RANGED}
    p.quickhacks = []
    for e in cs.enemies:
        gun = e.attacks.pop(cb.RANGED, None)
        e.arsenal = {k: a for k, a in e.arsenal.items() if a.kind != cb.RANGED}
        if cb.MELEE not in e.attacks:
            base = gun.base if gun is not None else 10
            e.attacks[cb.MELEE] = cb.Attack(cb.MELEE, "", content.balance.combat.enemy_damage_by_danger[cs.danger],
                                             base, 1, False, False)


async def start(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
                bet: int) -> Start:
    """Step into the ring against the next fighter of the ladder."""
    from app.services import combat

    c = cfg(content)
    if not c.enabled:
        return Start(blocked=BLOCK_OFF)
    why = _blocked(ch)
    if why:
        return Start(blocked=why)
    f = next_fighter(content, ch)
    if f is None:
        f = c.ladder[-1]  # the champion defends the belt against the strongest
    if fights_today(ch, tz) >= c.per_day:
        return Start(blocked=BLOCK_DAY)
    bet = bet if bet in c.bets else 0
    if ch.state.eddies < bet:
        return Start(blocked=BLOCK_EDDIES)
    if not ap_services.can_spend(ch, c.ap):
        return Start(blocked=BLOCK_AP)
    ap_services.spend(ch, content, c.ap)
    if bet:
        apply_effects(ch.state, {"eddies": -bet}, content, rng, year)
    r = rec(ch)
    today = kyiv_date(tz)
    r["n"] = (int(r.get("n", 0) or 0) if r.get("day") == today else 0) + 1
    r["day"], r["bet"] = today, bet
    _put(ch, r)
    step = await combat.begin(session, content, ch, rng, year, tz, faction=c.faction, danger=f.danger,
                              src={"src": SRC, "solo": True, "nonlethal": True, "no_rep": True, "foe": f.name,
                                   "foe_mult": f.hp_mult, "rung": min(rank(ch), len(c.ladder) - 1),
                                   "long": True})
    if not step.cs.over:
        _bare_hands(step.cs, content)
        combat._store(ch, step.cs)  # noqa: SLF001
        await save_active(session, ch, tz)
    return Start(step=step, fighter=f)


async def fight_over(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                     tz: str, result: str, ctx: dict) -> list[str]:
    """The purse, the rung, the bet and the story after a fight in the ring."""
    from app.engine import place
    from app.services import loud

    c = cfg(content)
    r = rec(ch)
    rung = int(ctx.get("rung", 0) or 0)
    f = c.ladder[min(rung, len(c.ladder) - 1)]
    bet = int(r.get("bet", 0) or 0)
    r["bet"] = 0
    lines: list[str] = []
    if result == cb.WON:
        was_champ = rank(ch) >= len(c.ladder)
        r["wins"] = int(r.get("wins", 0) or 0) + 1
        if not was_champ:
            r["rank"] = rung + 1
        fx: dict[str, Any] = {"eddies": f.purse + int(bet * c.bet_mult), "street_cred": f.cred}
        if f.fame:
            fx["fame"] = f.fame
        applied: AppliedEffects = apply_effects(ch.state, fx, content, rng, year)
        lines.append(f"🥊 Победа над «{f.name}»{' по очкам' if ctx.get('points') else ''}. Призовые {f.purse} эдди"
                     + (f", ставка сыграла: +{int(bet * c.bet_mult)}" if bet else "") + ".")
        if r.get("rank", 0) >= len(c.ladder) and not was_champ:
            lines.append("🏆 Ты — чемпион ринга. Пояс твой, пока кто-нибудь не отберёт.")
            await loud.post(session, ch.id, f"{loud.who(ch.state.name, ch.state.surname)} забирает пояс чемпиона "
                                            f"ринга {place.name_in(content, place.place_of(ch.state, content))}.")
        r["wins_at"] = int(r.get("wins_at", 0) or 0) + 1  # wins since the last story step
        del applied
    else:
        r["losses"] = int(r.get("losses", 0) or 0) + 1
        apply_effects(ch.state, {"mood": c.lose_mood}, content, rng, year)
        ch.state.hp = max(1, ch.state.hp)
        lines.append((f"🥊 Судьи отдают бой «{f.name}» по очкам." if ctx.get("points") else
                      f"🥊 «{f.name}» отправляет тебя в нокаут. Очнёшься на матах, но живым.")
                     + (" Ставка сгорела." if bet else ""))
    if int(r.get("story", 0) or 0) == 0:
        lines.append("📜 На выходе с ринга тебя ждут. Открылась «История ринга».")
    _put(ch, r)
    return lines


def story_ready(content: Content, ch: ActiveCharacter, tz: str) -> str | None:
    """Why the next story step is not here yet, or None."""
    r = rec(ch)
    n = int(r.get("story", 0) or 0)
    if n >= len(cfg(content).story):
        return BLOCK_STORY_DONE
    if int(r.get("wins", 0) or 0) + int(r.get("losses", 0) or 0) == 0:
        return BLOCK_STORY_WIN
    if n > 0 and int(r.get("wins_at", 0) or 0) <= 0:
        return BLOCK_STORY_WIN
    if r.get("story_day") == kyiv_date(tz):
        return BLOCK_STORY_DAY
    return None


async def story_next(session: AsyncSession, content: Content, ch: ActiveCharacter, tz: str) -> tuple[str | None, str]:
    """The next step of the story of the ring: (blocked, event id)."""
    from app.services.week import pending_events, set_pending

    why = _blocked(ch) or story_ready(content, ch, tz)
    if why:
        return why, ""
    r = rec(ch)
    n = int(r.get("story", 0) or 0)
    ev = cfg(content).story[n]
    if ev not in content.events:
        return BLOCK_STORY_DONE, ""
    r["story"], r["story_day"], r["wins_at"] = n + 1, kyiv_date(tz), 0
    _put(ch, r)
    set_pending(ch, [ev] + [e for e in pending_events(ch) if e != ev])
    await save_active(session, ch, tz)
    return None, ev


@dataclass
class Standing:
    name: str
    rank: int
    wins: int
    where: str
    me: bool = False
    extra: list[str] = field(default_factory=list)


async def table(session: AsyncSession, content: Content, ch: ActiveCharacter) -> list[Standing]:
    """The players who have fought in any ring, the highest rung first."""
    from app.db import repo
    from app.engine import place

    out: list[Standing] = []
    for row in await repo.living_characters(session):
        a = (row.flags or {}).get(FLAG)
        if not isinstance(a, dict) or not (a.get("wins") or a.get("losses")):
            continue
        out.append(Standing(f"{row.name} {row.surname}".strip(), int(a.get("rank", 0) or 0), int(a.get("wins", 0) or 0),
                            place.name_of(content, place.of_district(content, row.district)), me=row.id == ch.id))
    out.sort(key=lambda s: (-s.rank, -s.wins, s.name))
    return out
