"""Force of a player's megacorp (owner request 26.10: «у корпоратов должна быть возможность влиять на мир через силу,
используя своих бойцов и бойцов юзеров»; docs/PLAN_CORPFORCE.md part 2).

The owner of a megacorp (services/bigmoney.py) leads an operation in any district of Night City:
- «Зачистка»: a fight with a gang of the district; won — the gangs lose ground there, the corps gain it;
- «Удар по корпорации»: a fight with the security of a canon megacorp of the district; won — its hold on the district
  drops and its standing takes a blow (services/corpstand.py).
At the owner's side stand the own fighters (services/army.py) and the hired players: bodyguards and the corp staff
(services/pally.py) on their own skills. The other side brings more fighters for every ally. Two operations a Kyiv day,
AP and money of the owner, a loud deed (heat). Numbers: `balance.corpforce`.
"""

from __future__ import annotations

import random

from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import kyiv_date
from app.content.loader import Content
from app.engine import control as ctl_eng
from app.engine.effects import apply_effects
from app.services.game import ActiveCharacter, save_active

SRC = "corpop"
FLAG_DAY = "corpop_day"
KINDS = {"cleanse": "Зачистка", "strike": "Удар по корпорации"}


def cfg(content: Content):  # noqa: ANN201
    return content.balance.corpforce


async def my_corp(session: AsyncSession, ch: ActiveCharacter) -> dict | None:
    from app.services import bigmoney

    return (await bigmoney.corps(session)).get(str(ch.player_tg_id))


def done_today(ch: ActiveCharacter, tz: str) -> int:
    rec = ch.state.flags.get(FLAG_DAY) or {}
    return int(rec.get("n", 0)) if rec.get("d") == kyiv_date(tz) else 0


def districts(content: Content) -> list[str]:
    return [k for k, d in content.districts.items() if d.city == "nc"]


def gang_of(content: Content, district: str, year: int, rng: random.Random) -> str | None:
    from app.engine import combat as cb

    d = content.districts.get(district)
    avail = content.available("gangs", year)
    pool = [g for g in (d.gangs if d else []) if g in avail and cb._faction_pool(content, g, year)]  # noqa: SLF001
    return rng.choice(pool) if pool else None


def corp_of(content: Content, district: str, year: int, rng: random.Random) -> str | None:
    from app.engine import combat as cb

    avail = content.available("corps", year)
    pool = [k for k in ctl_eng.megacorps_of(content, district) if k in avail and cb._faction_pool(content, k, year)]  # noqa: SLF001
    return rng.choice(pool) if pool else None


async def allies_count(session: AsyncSession, content: Content, ch: ActiveCharacter) -> tuple[int, int]:
    """(own fighters, hired players) who would stand at the side now."""
    from app.engine import army as army_eng
    from app.services import pally, pguard

    t = army_eng.tier(content, len(army_eng.soldiers(ch.state)))
    own = min(t.in_fight, army_eng.cfg(content).ally_cap) if t is not None else 0
    guards = await pguard.for_fight(session, content, ch)
    staff = await pally.staff_for_fight(session, content, ch, {g.player_tg_id for g in guards})
    return own, len(guards) + len(staff)


async def start(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
                kind: str, district: str):  # noqa: ANN201 - a combat Step or a reason
    from app.engine import jail as jail_eng
    from app.services import ap as ap_services
    from app.services import combat, control
    from app.services import events as ev_service

    c = cfg(content)
    if not c.enabled or kind not in KINDS:
        return "off"
    if await my_corp(session, ch) is None:
        return "nocorp"
    if district not in districts(content):
        return "district"
    d0 = content.districts.get(ch.state.district)
    if d0 is None or d0.city != "nc" or ch.state.flags.get("abroad"):
        return "city"
    if jail_eng.in_jail(ch.state):
        return "jail"
    if ev_service.has_pending(ch):
        return "busy"
    if done_today(ch, tz) >= c.per_day:
        return "today"
    if ch.state.eddies < c.price:
        return "eddies"
    if not ap_services.can_spend(ch, c.ap):
        return "ap"
    faction = gang_of(content, district, year, rng) if kind == "cleanse" else corp_of(content, district, year, rng)
    if faction is None:
        return "nofoe"
    m = (await control.load(session, content)).get(district) or {ctl_eng.GANG: 50, ctl_eng.CORP: 50}
    meter = m[ctl_eng.GANG] if kind == "cleanse" else m[ctl_eng.CORP]
    danger = max(c.danger_min, min(c.danger_max, ctl_eng.fight_danger(content, meter) + 1))
    own, hired = await allies_count(session, content, ch)
    extra = min(c.extra_cap, round((own + hired) * c.extra_per_ally))
    ap_services.spend(ch, content, c.ap)
    apply_effects(ch.state, {"eddies": -c.price, "heat": c.heat}, content, rng, year)
    rec = {"d": kyiv_date(tz), "n": done_today(ch, tz) + 1}
    ch.state.flags[FLAG_DAY] = rec
    await save_active(session, ch, tz)
    dname = content.districts[district].name
    return await combat.begin(session, content, ch, rng, year, tz, faction=faction, danger=danger,
                              src={"src": SRC, "kind": kind, "district": district, "corp": faction, "extra": extra,
                                   "title": f"{KINDS[kind]}: {dname}"})


async def fight_over(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                     tz: str, result: str, ctx: dict) -> list[str]:
    from app.engine import combat as cb
    from app.services import control, loud

    c = cfg(content)
    kind = str(ctx.get("kind", ""))
    district = str(ctx.get("district") or ch.state.district)
    dname = content.districts[district].name if district in content.districts else district
    foe = str(ctx.get("corp") or "")
    fname = (content.gangs[foe].name if foe in content.gangs else content.corps[foe].name if foe in content.corps
             else foe)
    corp = await my_corp(session, ch)
    cname = str((corp or {}).get("name") or "корпорация")
    if result != cb.WON:
        if kind == "cleanse":
            await control.adjust(session, content, district, gang=c.lost_gang)
            return [f"Операция сорвана. {fname} держат район {dname} крепче (+{c.lost_gang}%)."]
        await control.adjust(session, content, district, corp=c.lost_gang)
        return [f"Операция сорвана. Охрана {fname} отбилась, их хватка в районе {dname} крепче (+{c.lost_gang}%)."]
    lo, hi = c.loot
    loot = rng.randint(lo, hi)
    fx = {"eddies": loot, "fame": c.fame}
    if foe in content.corps:
        fx[f"faction_rep:{foe}"] = -8
    elif foe in content.gangs:
        fx[f"faction_rep:{foe}"] = -6
    apply_effects(ch.state, fx, content, rng, year)
    await save_active(session, ch, tz)
    lines: list[str]
    if kind == "cleanse":
        await control.adjust(session, content, district, gang=-c.cleanse_gang, corp=c.cleanse_corp)
        lines = [f"Район {dname}: точки {fname} закрыты. Банды −{c.cleanse_gang}%, корпорации +{c.cleanse_corp}%. "
                 f"Трофеи {loot} эдди."]
        await loud.post(session, ch.id, f"«{cname}» зачищает {dname}: {fname} уходят с улиц.")
    else:
        await control.adjust(session, content, district, corp=-c.strike_corp)
        lines = [f"Район {dname}: охрана {fname} выбита. Хватка корпораций −{c.strike_corp}%. Трофеи {loot} эдди."]
        if foe in content.corps:
            from app.services import corpstand

            lines += await corpstand.hit(session, content, ch, foe, "lab", c.strike_lv, rng, year)
        await loud.post(session, ch.id, f"«{cname}» бьёт по {fname} в районе {dname}.")
    return lines
