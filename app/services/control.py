"""Gangs against megacorps (owner request 25.10, engine/control.py): the daily shift, the attacks of the press, the
cleansing of the gangs and the strikes at the corps. world_state `control` = {district: {gang, corp}}.
"""

from __future__ import annotations

import random

from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import kyiv_date
from app.content.loader import Content
from app.db import repo
from app.engine import checks
from app.engine import control as eng
from app.engine.effects import apply_effects
from app.engine.state import FLAG_COMBAT, FLAG_PENDING_COMBAT
from app.services.game import ActiveCharacter, save_active

KEY = "control"
SRC = "ctrl"
FLAG_DAY = "ctrl_day"  # {"d": kyiv date, "n": cleansings and strikes that day}
CORP_EVENTS = ["ctrl_corp_scan", "ctrl_corp_buyout", "ctrl_corp_sweep"]
GANG_EVENTS = ["ctrl_gang_tribute", "ctrl_gang_recruit", "ctrl_gang_block"]
MARK_NEWS = {
    "gang50": "🏴 {d}: банды держат половину улиц.",
    "gang75": "🏴 {d}: улицы за бандами. Люди платят дань, чтобы дойти до дома.",
    "gang90": "🏴 {d}: район потерян. Банды нападают на всех, кто там живёт.",
    "gang_down": "🏴 {d}: банды отступают.",
    "corp50": "🏢 {d}: корпорации скупают кварталы. Охрана в каждом подъезде.",
    "corp75": "🏢 {d}: район принадлежит корпорациям. Проверки, выселения, охрана стреляет без предупреждения.",
    "corp90": "🏢 {d}: корпоративная зона. Жильцы здесь — помеха.",
    "corp_down": "🏢 {d}: корпорации сворачивают офисы.",
}


async def load(session: AsyncSession, content: Content) -> dict[str, dict[str, int]]:
    return eng.normalize(content, await repo.ws_get(session, KEY, {}))


async def save(session: AsyncSession, state: dict[str, dict[str, int]]) -> None:
    await repo.ws_set(session, KEY, state)


async def adjust(session: AsyncSession, content: Content, district: str, gang: int = 0, corp: int = 0) -> None:
    if content.districts.get(district) is None or content.districts[district].city != "nc":
        return
    st = await load(session, content)
    eng.shift(st, district, gang, corp)
    await save(session, st)


def gang_district(content: Content, gang: str, prefer: str) -> str | None:
    """The district of Night City where a gang lives: the character's own first."""
    d = content.districts.get(prefer)
    if d is not None and d.city == "nc" and gang in d.gangs:
        return prefer
    return next((k for k, v in content.districts.items() if v.city == "nc" and gang in v.gangs), None)


def corp_district(content: Content, corp: str, prefer: str) -> str | None:
    if corp in eng.megacorps_of(content, prefer) and content.districts[prefer].city == "nc":
        return prefer
    return next((k for k in eng.nc_districts(content) if corp in eng.megacorps_of(content, k)), None)


async def daily(session: AsyncSession, content: Content, rng: random.Random) -> list[str]:
    from app.services import dyn

    st = await load(session, content)
    gangs: dict[str, int] = {}
    corps: dict[str, int] = {}
    for f in dyn.alive(await dyn.all_factions(session)):
        box = gangs if f.get("kind") == "gang" else corps
        box[f["district"]] = box.get(f["district"], 0) + 1
    crossed = eng.day(content, st, gangs, corps, rng)
    from app.services import turf  # owner request 25.10: the districts held by players

    turf.daily(content, await turf.book(session), st, rng)
    await save(session, st)
    return [MARK_NEWS[what].format(d=content.districts[k].name) for k, what in crossed if what in MARK_NEWS]


def _local_gang(content: Content, ch: ActiveCharacter, year: int, rng: random.Random) -> str | None:
    from app.services import gangs

    d = content.districts.get(ch.state.district)
    avail = content.available("gangs", year)
    mine = gangs.gang_of(ch)
    pool = [g for g in (d.gangs if d else []) if g in avail and g != mine]
    return rng.choice(pool) if pool else None


def _local_corp(content: Content, ch: ActiveCharacter, year: int, rng: random.Random) -> str:
    """A megacorp of the district whose security can fight this year; not the character's own employer."""
    from app.engine import combat as cb
    from app.services import corpraid

    mine = (corpraid.member_of(ch.state, content) or ("", ""))[0]
    avail = content.available("corps", year)
    pool = [k for k in eng.megacorps_of(content, ch.state.district)
            if k in avail and k != mine and cb._faction_pool(content, k, year)]  # noqa: SLF001
    if not pool:
        pool = [k for k in ("militech", "arasaka") if k != mine and cb._faction_pool(content, k, year)] or ["militech"]  # noqa: SLF001
    return rng.choice(pool)


# -- the press: the district bites ---------------------------------------------------------------------------------------


async def press_hook(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random,
                     year: int) -> list[str]:
    from app.services.events import has_pending
    from app.services.week import pending_events, set_pending

    c = eng.cfg(content)
    s = ch.state
    d = content.districts.get(s.district)
    if d is None or d.city != "nc" or int(s.flags.get("jail_weeks", 0) or 0) > 0:
        return []
    if s.flags.get(FLAG_PENDING_COMBAT) or s.flags.get(FLAG_COMBAT) or has_pending(ch):
        return []
    m = (await load(session, content)).get(s.district)
    if m is None:
        return []
    gang, corp = m[eng.GANG], m[eng.CORP]
    if rng.random() < eng.ambush_chance(content, gang):
        g = _local_gang(content, ch, year, rng)
        if g is not None:
            if rng.random() < c.event_share:
                from app.services import krysha  # owner request 25.10: a krysha keeps the racket away

                pool = [e for e in GANG_EVENTS if e in content.events and not krysha.blocks(content, ch.state, e)]
                if pool:
                    set_pending(ch, [rng.choice(pool)] + pending_events(ch))
                    return [f"🏴 Банды держат район {d.name} на {gang}%. Это чувствуется на улице."]
            s.flags[FLAG_PENDING_COMBAT] = {"src": SRC, "kind": "ambush", "faction": g,
                                            "danger": eng.ambush_danger(content, gang),
                                            "extra": eng.ambush_extra(content, gang), "district": s.district}
            return [f"⚔️ Банды держат район {d.name} на {gang}%: «{content.gangs[g].name}» нападают прямо у дома."]
    if rng.random() < eng.sweep_chance(content, corp):
        k = _local_corp(content, ch, year, rng)
        if rng.random() < c.event_share:
            pool = [e for e in CORP_EVENTS if e in content.events]
            if pool:
                set_pending(ch, [rng.choice(pool)] + pending_events(ch))
                return [f"🏢 Корпорации держат район {d.name} на {corp}%. Охрана проверяет жильцов."]
        s.flags[FLAG_PENDING_COMBAT] = {"src": SRC, "kind": "sweep", "faction": k,
                                        "danger": eng.ambush_danger(content, corp),
                                        "extra": eng.ambush_extra(content, corp), "district": s.district}
        return [f"⚔️ Корпорации держат район {d.name} на {corp}%: охрана {content.corps[k].name} зачищает квартал, "
                "и ты у неё на пути."]
    return []


# -- the player moves the meters ----------------------------------------------------------------------------------------


def _day_left(ch: ActiveCharacter, content: Content, tz: str) -> int:
    rec = ch.state.flags.get(FLAG_DAY) or {}
    if rec.get("d") != kyiv_date(tz):
        return eng.cfg(content).per_day
    return max(0, eng.cfg(content).per_day - int(rec.get("n", 0)))


def _spend_day(ch: ActiveCharacter, tz: str) -> None:
    rec = dict(ch.state.flags.get(FLAG_DAY) or {})
    today = kyiv_date(tz)
    if rec.get("d") != today:
        rec = {"d": today, "n": 0}
    rec["n"] = int(rec.get("n", 0)) + 1
    ch.state.flags[FLAG_DAY] = rec


def _blocked(ch: ActiveCharacter, content: Content, tz: str, ap: int) -> str | None:
    from app.engine import jail as jail_eng
    from app.services import ap as ap_services
    from app.services import events as ev_service

    d = content.districts.get(ch.state.district)
    if d is None or d.city != "nc":
        return "city"
    if jail_eng.in_jail(ch.state):
        return "jail"
    if ev_service.has_pending(ch):
        return "busy"
    if _day_left(ch, content, tz) <= 0:
        return "today"
    if not ap_services.can_spend(ch, ap):
        return "ap"
    return None


async def cleanse(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str):  # noqa: ANN201
    """A fight with a gang of the district: the gangs lose ground, the corps move in. A Step or a block reason."""
    from app.services import ap as ap_services
    from app.services import combat

    c = eng.cfg(content)
    r = _blocked(ch, content, tz, c.cleanse_ap)
    if r:
        return r
    g = _local_gang(content, ch, year, rng)
    if g is None:
        return "nogang"
    m = (await load(session, content))[ch.state.district]
    ap_services.spend(ch, content, c.cleanse_ap)
    _spend_day(ch, tz)
    await save_active(session, ch, tz)
    return await combat.begin(session, content, ch, rng, year, tz, faction=g, danger=eng.fight_danger(content, m[eng.GANG]),
                              src={"src": SRC, "kind": "cleanse", "district": ch.state.district})


async def strike(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
                 how: str):  # noqa: ANN201
    """A strike at the corps of the district. `hack` — their systems, a check; `fight` — their security, a fight.
    Returns a Step, (ok, check, lines) or a block reason."""
    from app.services import ap as ap_services
    from app.services import combat
    from app.services import gangs

    c = eng.cfg(content)
    r = _blocked(ch, content, tz, c.strike_ap)
    if r:
        return r
    district = ch.state.district
    m = (await load(session, content))[district]
    k = _local_corp(content, ch, year, rng)
    ap_services.spend(ch, content, c.strike_ap)
    _spend_day(ch, tz)
    if how == "fight":
        await save_active(session, ch, tz)
        return await combat.begin(session, content, ch, rng, year, tz, faction=k, danger=eng.fight_danger(content, m[eng.CORP]),
                                  src={"src": SRC, "kind": "strike", "district": district, "corp": k})
    chk = checks.roll_check(ch.state, content, rng, "INT", "netrunning", c.hack_dv + m[eng.CORP] // 10, ("gig_hack",))
    mine = gangs.gang_of(ch)
    name = content.corps[k].name
    if chk.ok:
        fx: dict = {"heat": 1, f"faction_rep:{k}": -5}
        if mine:
            fx[f"faction_rep:{mine}"] = 3
        apply_effects(ch.state, fx, content, rng, year)
        await adjust(session, content, district, gang=c.strike_gang, corp=-c.strike_corp)
        from app.services import season  # owner request 25.10

        await season.add_ch(session, content, ch, "street", content.balance.season.pts.get("street", 0))
        lines = [f"Сеть {name} в районе падает: камеры, турели и пропуска мертвы до утра. Корпорации −{c.strike_corp}%, "
                 f"банды +{c.strike_gang}%." + (" Своя банда это заметила." if mine else "")]
    else:
        apply_effects(ch.state, {"heat": 3, f"faction_rep:{k}": -3}, content, rng, year)
        await adjust(session, content, district, corp=2)
        lines = [f"Лёд {name} отбивает взлом и пишет твой след. Розыск +3, охрана усилена: корпорации +2%."]
    await save_active(session, ch, tz)
    return chk.ok, chk, lines


async def fight_over(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                     tz: str, result: str, ctx: dict) -> list[str]:
    from app.engine import combat as cb
    from app.services import gangs

    c = eng.cfg(content)
    kind = str(ctx.get("kind", ""))
    district = str(ctx.get("district") or ch.state.district)
    dname = content.districts[district].name if district in content.districts else ""
    won = result == cb.WON
    if kind == "ambush":
        await adjust(session, content, district, gang=c.ambush_won if won else c.ambush_lost)
        return [f"Нападение отбито. Банды района {dname} теряют людей ({c.ambush_won}%)." if won
                else f"Банды района {dname} празднуют (+{c.ambush_lost}%)."]
    if kind == "sweep":
        await adjust(session, content, district, corp=c.ambush_won if won else c.ambush_lost)
        return [f"Охрана отступает. Корпорации в районе {dname} теряют хватку ({c.ambush_won}%)." if won
                else f"Охрана докладывает о зачистке. Корпорации в районе {dname} крепче (+{c.ambush_lost}%)."]
    if kind == "cleanse":
        if not won:
            await adjust(session, content, district, gang=2)
            return [f"Зачистка сорвана. Банды района {dname} крепче (+2%)."]
        lo, hi = c.cleanse_loot
        loot = rng.randint(lo, hi)
        apply_effects(ch.state, {"eddies": loot, "street_cred": 1}, content, rng, year)
        await save_active(session, ch, tz)
        await adjust(session, content, district, gang=-c.cleanse_gang, corp=c.cleanse_corp)
        from app.services import season  # owner request 25.10

        await season.add_ch(session, content, ch, "street", content.balance.season.pts.get("street", 0))
        return [f"Зачистка: точка банды в районе {dname} закрыта, +{loot} эдди. Банды −{c.cleanse_gang}%, "
                f"корпорации +{c.cleanse_corp}%: они скупают то, что банды бросили."]
    if kind == "strike":
        k = str(ctx.get("corp") or "")
        if not won:
            apply_effects(ch.state, {"heat": 2}, content, rng, year)
            await save_active(session, ch, tz)
            await adjust(session, content, district, corp=3)
            return ["Охрана отбилась и вызвала подкрепление. Розыск +2, корпорации +3%."]
        lo, hi = c.strike_loot
        loot = rng.randint(lo, hi)
        mine = gangs.gang_of(ch)
        fx: dict = {"eddies": loot, "heat": 2, "street_cred": 1}
        if k in content.corps:
            fx[f"faction_rep:{k}"] = -6
        if mine:
            fx[f"faction_rep:{mine}"] = 4
        apply_effects(ch.state, fx, content, rng, year)
        await save_active(session, ch, tz)
        await adjust(session, content, district, gang=c.strike_gang, corp=-c.strike_fight_corp)
        from app.services import season  # owner request 25.10

        await season.add_ch(session, content, ch, "street", content.balance.season.pts.get("street", 0))
        return [f"Пост охраны разбит, касса и стволы твои: +{loot} эдди. Корпорации −{c.strike_fight_corp}%, "
                f"банды +{c.strike_gang}%. Розыск +2."]
    return []
