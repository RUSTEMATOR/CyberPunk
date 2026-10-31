"""Legends in the city (owner request 25.10, docs/PLAN_ENDGAME2.md part 3).

- A legend that left keeps its fighter in the Hall (services/legend.build_of). Any character can challenge it once a
  day: a duel against the build, alone, the legend as a boss. Losing a duel never kills: the legend lowers the weapon.
  The first win over a legend: money, fame, cred, season points; later wins only a little money.
- A legend of the character's own line gives a blessing once a real week: a buff for `bless_days` days.

flag `lg_duel_day` = Kyiv date of the last duel; flag `lg_beaten` = [hall keys]; flag `lg_bless` = ISO week.
"""

from __future__ import annotations

import random
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import kyiv_date, utcnow
from app.content.loader import Content
from app.db import repo
from app.db.models import Character
from app.engine import combat as cb
from app.engine.effects import apply_effects
from app.engine.state import Buff
from app.services.game import ActiveCharacter, save_active

SRC = "lgduel"
FLAG_DAY, FLAG_BEATEN, FLAG_BLESS = "lg_duel_day", "lg_beaten", "lg_bless"
BLESS_SRC = "legend:bless"


def cfg(content: Content):  # noqa: ANN201
    return content.balance.legend


def hall_key(h: dict) -> str:
    return f"{h.get('tg')}:{h.get('name')}:{h.get('date')}"


async def build_for(session: AsyncSession, content: Content, h: dict) -> dict | None:
    """The stored build, or (an older entry) the build rebuilt from the dead character's last state."""
    from app.services import legend

    if h.get("build"):
        return dict(h["build"])
    st = None
    if h.get("char"):
        st = await repo.load_character(session, int(h["char"]))
    else:
        rows = (await session.execute(select(Character).where(Character.player_tg_id == int(h.get("tg", 0) or 0),
                                                               Character.alive.is_(False)))).scalars().all()
        for row in rows:
            if f"{row.name} {row.surname}".strip() == str(h.get("name", "")):
                st = await repo.load_character(session, row.id)
                break
    if st is None:
        return None
    try:
        return legend.build_of(content, st)
    except Exception:  # noqa: BLE001
        return None


async def challenge(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
                    idx: int):  # noqa: ANN201
    from app.engine import jail as jail_eng
    from app.services import ap as ap_services
    from app.services import combat, legend
    from app.services import events as ev_service

    c = cfg(content)
    hall = await legend.hall(session)
    if not 0 <= idx < len(hall):
        return "gone"
    h = hall[idx]
    if jail_eng.in_jail(ch.state):
        return "jail"
    if ev_service.has_pending(ch):
        return "busy"
    today = kyiv_date(tz)
    if ch.state.flags.get(FLAG_DAY) == today:
        return "today"
    if not ap_services.can_spend(ch, c.duel_ap):
        return "ap"
    build = await build_for(session, content, h)
    if build is None:
        return "nobuild"
    foe = cb._load_combatant(build)  # noqa: SLF001
    foe.id, foe.side, foe.leader = "e0", "enemy", True
    foe.max_hp = foe.hp = int(foe.max_hp * c.duel_hp_mult)
    ap_services.spend(ch, content, c.duel_ap)
    ch.state.flags[FLAG_DAY] = today
    await save_active(session, ch, tz)
    name = str(h.get("name", "Легенда"))
    return await combat.begin(session, content, ch, rng, year, tz, faction="cyberpsycho", danger=5, foes=[foe],
                              src={"src": SRC, "solo": True, "duel": True, "foe": f"легенда {name}",
                                   "hall": hall_key(h), "name": name})


async def duel_over(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
                    result: str, ctx: dict) -> list[str]:
    from app.services import loud, season

    c = cfg(content)
    name = str(ctx.get("name", "легенда"))
    if result != cb.WON:
        return [f"{name} опускает оружие: на сегодня хватит. Легенда остаётся легендой."]
    beaten = list(ch.state.flags.get(FLAG_BEATEN) or [])
    key = str(ctx.get("hall", ""))
    if key in beaten:
        apply_effects(ch.state, {"eddies": c.duel_repeat_eddies}, content, rng, year)
        await save_active(session, ch, tz)
        return [f"Ещё одна победа над легендой {name}. Зрители платят: +{c.duel_repeat_eddies} эдди."]
    ch.state.flags[FLAG_BEATEN] = (beaten + [key])[-100:]
    apply_effects(ch.state, {"eddies": c.duel_eddies, "fame": c.duel_fame, "street_cred": c.duel_cred}, content, rng, year)
    await save_active(session, ch, tz)
    await season.add_ch(session, content, ch, "legend_duel", content.balance.season.pts.get("legend_duel", 0))
    await loud.post(session, ch.id, f"⚔️ {loud.who(ch.state.name, ch.state.surname)} побеждает легенду {name} в поединке.", kind="legend_duel", w=2)
    lines = [f"Легенда {name} повержена. +{c.duel_eddies} эдди, слава +{c.duel_fame}, репутация +{c.duel_cred}."]
    from app.services import relic  # docs/PLAN_ENDGAME2.md part 6: the legend's relic changes hands

    lines.extend(await relic.from_legend(session, content, ch, key))
    return lines


def week_key(tz: str) -> str:
    from datetime import date

    d = date.fromisoformat(kyiv_date(tz))
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def blessing_now(st) -> Buff | None:  # noqa: ANN001
    return next((b for b in st.buffs if b.source == BLESS_SRC), None)


async def bless(session: AsyncSession, content: Content, ch: ActiveCharacter, tz: str, key: str) -> str | None:
    from app.services import legend

    c = cfg(content)
    n = await legend.legends_of(session, ch.player_tg_id)
    if not n:
        return "noline"
    wk = week_key(tz)
    if ch.state.flags.get(FLAG_BLESS) == wk:
        return "week"
    b = next((x for x in c.blessings if x.key == key), None)
    if b is None:
        return "gone"
    k = min(c.bless_cap, n)
    ch.state.buffs = [x for x in ch.state.buffs if x.source != BLESS_SRC]
    ch.state.buffs.append(Buff(source=BLESS_SRC, mods={m: v * k for m, v in b.mods.items()},
                               expires=(utcnow() + timedelta(days=c.bless_days)).isoformat()))
    ch.state.flags[FLAG_BLESS] = wk
    await save_active(session, ch, tz)
    return None
