"""Searching the bodies after a won fight (owner request 26.10 «за бой награды очень маленькие … дай выбрать,
обобрать трупы или нет, и пусть будет риск, что заспавнится ещё один бой»).

A won fight with someone down leaves flag `loot` = {faction, danger, n (downed), elite, chain}. The end screen offers
«Обыскать тела» or «Уйти». Searching: eddies by the danger and the downed (more in an elite fight), sometimes a weapon
or armour, sometimes the cameras (heat), and a risk that their friends arrive: a new fight of the same side
(src `loot_ambush`, its bodies can be searched too, the risk grows along the chain).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.engine.effects import AppliedEffects, apply_effects
from app.services.game import ActiveCharacter, save_active

FLAG = "loot"
SRC = "loot_ambush"


def cfg(content: Content):  # noqa: ANN201
    return content.balance.loot


def offer(content: Content, st, cs) -> None:  # noqa: ANN001
    """Called when a fight is won: leave the bodies to search (or not, for fights that are not about bodies)."""
    c = cfg(content)
    src = str(cs.ctx.get("src", ""))
    n = int(cs.kills) + int(cs.takedowns)
    if not c.enabled or n <= 0 or src in c.exclude or cs.ctx.get("duel"):
        st.flags.pop(FLAG, None)
        return
    st.flags[FLAG] = {"faction": cs.faction, "danger": int(cs.danger), "n": min(n, c.max_bodies),
                      "elite": bool(cs.ctx.get("elite")), "chain": int(cs.ctx.get("chain", 0) or 0),
                      "dyn_name": cs.ctx.get("dyn_name"), "dyn_kind": cs.ctx.get("dyn_kind")}


def pending(st) -> dict | None:  # noqa: ANN001
    r = st.flags.get(FLAG)
    return dict(r) if isinstance(r, dict) else None


def ambush_chance(content: Content, r: dict) -> float:
    c = cfg(content)
    return min(c.ambush_cap, c.ambush_base + c.ambush_per_danger * int(r["danger"]) + c.ambush_per_chain * int(r["chain"]))


@dataclass
class Result:
    lines: list[str] = field(default_factory=list)
    applied: AppliedEffects | None = None
    fight: object | None = None


async def search(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                 tz: str) -> Result:
    from app.services import combat

    c = cfg(content)
    st = ch.state
    r = pending(st)
    st.flags.pop(FLAG, None)
    res = Result()
    if r is None:
        await save_active(session, ch, tz)
        return res
    d = max(1, min(5, int(r["danger"])))
    lo, hi = c.per_body[d]
    total = sum(rng.randint(lo, hi) for _ in range(int(r["n"])))
    if r.get("elite"):
        total = int(total * c.elite_mult)
    roll = rng.random()
    fx: dict = {}
    if roll < c.empty_chance:
        total = max(1, total // 5)
        res.lines.append(rng.choice(c.empty_lines))
    else:
        res.lines.append(rng.choice(c.found_lines))
    fx["eddies"] = total
    if rng.random() < c.item_chance:
        pool = [k for k, w in content.available("weapons", year).items()
                if not w.not_for_sale and w.voice is None and 0 < w.price <= d * c.item_price_per_danger]
        if pool:
            key = rng.choice(pool)
            fx[f"item_grant:{key}"] = True
            res.lines.append(f"Среди вещей ствол в хорошем состоянии: {content.weapons[key].name}.")
    if rng.random() < c.heat_chance:
        fx["heat"] = 1
        res.lines.append("Камера на столбе всё это время смотрела на тебя. Розыск +1.")
    res.applied = apply_effects(st, fx, content, rng, year)
    res.lines.append(f"Добыча: +{total} эдди.")
    if rng.random() < ambush_chance(content, r):
        res.lines.append(rng.choice(c.ambush_lines))
        await save_active(session, ch, tz)
        src = {"src": SRC, "chain": int(r["chain"]) + 1}
        if r.get("dyn_name"):
            src.update(dyn_name=r["dyn_name"], dyn_kind=r.get("dyn_kind"))
        res.fight = await combat.begin(session, content, ch, rng, year, tz, faction=str(r["faction"]),
                                       danger=min(5, d + (1 if rng.random() < 0.3 else 0)), src=src)
        return res
    await save_active(session, ch, tz)
    return res


async def leave(session: AsyncSession, ch: ActiveCharacter, tz: str) -> None:
    ch.state.flags.pop(FLAG, None)
    await save_active(session, ch, tz)
