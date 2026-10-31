"""Gangs and corps that come and go (owner request 23.10). Not canon: they appear, grow, clash, fall apart.

world_state `dyn_factions` = [{id, kind (gang|corp), name, district, power, alive, born, died, how}]
character flag `dyn_clues` = {faction id: clues}: at `search_need` the lair is found and can be stormed.

Fights borrow the fighters of a canon faction that has them in the character's year and give them the crew's name
(services/combat.begin: ctx `dyn_name`).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.content.loader import Content
from app.db import repo
from app.engine import checks
from app.engine import combat as cb
from app.engine.effects import AppliedEffects, apply_effects
from app.engine.state import FLAG_COMBAT, FLAG_PENDING_COMBAT
from app.services import ap as ap_services
from app.services.game import ActiveCharacter, save_active

KEY = "dyn_factions"
FLAG_CLUES = "dyn_clues"
SRC_AMBUSH, SRC_LAIR = "dyn", "dyn_lair"


def cfg(content: Content):  # noqa: ANN201
    return content.balance.dyn


async def all_factions(session: AsyncSession) -> list[dict]:
    got = await repo.ws_get(session, KEY, [])
    return [dict(x) for x in got] if isinstance(got, list) else []


async def _save(session: AsyncSession, items: list[dict]) -> None:
    alive = [x for x in items if x.get("alive")]
    dead = [x for x in items if not x.get("alive")][-12:]  # the dead are remembered a while (the city's memory)
    await repo.ws_set(session, KEY, alive + dead)


def alive(items: list[dict]) -> list[dict]:
    return [x for x in items if x.get("alive")]


def find(items: list[dict], fid: int) -> dict | None:
    return next((x for x in items if int(x.get("id", 0)) == fid), None)


def cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def label(f: dict) -> str:
    return f"банда «{f['name']}»" if f.get("kind") == "gang" else f"корпорация «{f['name']}»"


def base_faction(content: Content, f: dict, year: int) -> str:
    """Whose fighters stand for the crew this year."""
    c = cfg(content)
    pool = c.gang_bases if f.get("kind") == "gang" else c.corp_bases
    ok = [b for b in pool if cb._faction_pool(content, b, year)]
    if not ok:
        return "ncpd"
    return ok[int(f.get("id", 0)) % len(ok)]


def _district_name(content: Content, key: str) -> str:
    d = content.districts.get(key)
    return d.name if d else key


# -- the life of the city: once a day ------------------------------------------------------------------------------


async def daily(session: AsyncSession, content: Content, rng: random.Random) -> list[str]:
    """Spawn, drift, clash, fall apart. Returns posts for the feed."""
    c = cfg(content)
    items = await all_factions(session)
    posts: list[str] = []
    districts = [k for k, v in content.districts.items() if v.city == "nc"]  # owner request 25.10: Night City only
    today = utcnow().date().isoformat()
    from app.engine import control as ctl_eng
    from app.services import control as ctl  # owner request 25.10: a city held by gangs breeds new ones oftener

    ctl_state = await ctl.load(session, content)
    more_chance, more_cap = ctl_eng.spawn_bonus(content, ctl_state, ctl_eng.GANG)
    corp_chance, corp_cap = ctl_eng.spawn_bonus(content, ctl_state, ctl_eng.CORP)  # owner request 25.10 (2)
    for kind, cap, chance, first, second in (("gang", c.max_gangs + more_cap, c.gang_spawn_chance + more_chance,
                                              c.gang_first, c.gang_second),
                                             ("corp", c.max_corps + corp_cap, c.corp_spawn_chance + corp_chance,
                                              c.corp_first, c.corp_second)):
        if not items and kind == "gang":
            chance = 1.0  # the very first day: the city gets its first new crew at once
        if sum(1 for x in alive(items) if x.get("kind") == kind) < cap and first and second and rng.random() < chance:
            fid = max((int(x.get("id", 0)) for x in items), default=0) + 1
            taken = {x.get("name") for x in items}
            name = f"{rng.choice(first)} {rng.choice(second)}"
            if name in taken:
                continue
            born_in = ctl_eng.spawn_district(ctl_state, rng, ctl_eng.GANG if kind == "gang" else ctl_eng.CORP) or rng.choice(districts)
            f = {"id": fid, "kind": kind, "name": name, "district": born_in, "power": rng.randint(*c.start_power),
                 "alive": True, "born": today}
            items.append(f)
            born = content.balance.control.crew_born
            ctl_eng.shift(ctl_state, born_in, **({"gang": born} if kind == "gang" else {"corp": born}))
            where = _district_name(content, f["district"])
            from app.engine import grammar  # owner request 26.10: the same news reads differently

            posts.append(grammar.say(content, rng, "dyn_gang_born" if kind == "gang" else "dyn_corp_born",
                                     where=where, name=name))
    for f in alive(items):
        f["power"] = max(0, min(100, int(f["power"]) + rng.randint(*c.daily_drift)))
    by_district: dict[str, list[dict]] = {}
    for f in alive(items):
        by_district.setdefault(f["district"], []).append(f)
    for district, group in by_district.items():
        if len(group) >= 2 and rng.random() < c.war_chance:
            a, b = rng.sample(group, 2)
            loser = a if rng.random() < b["power"] / max(1, a["power"] + b["power"]) else b
            winner = b if loser is a else a
            loser["power"] = max(0, int(loser["power"]) - rng.randint(*c.war_loss))
            from app.engine import grammar

            posts.append(grammar.say(content, rng, "dyn_shootout", where=_district_name(content, district),
                                     winner=winner["name"], loser=loser["name"]))
    for f in alive(items):
        if int(f["power"]) <= 0:
            f.update(alive=False, died=today, how="collapse")
            from app.engine import grammar

            posts.append(grammar.say(content, rng, "dyn_gang_gone" if f["kind"] == "gang" else "dyn_corp_gone",
                                     name=f["name"]))
    await _save(session, items)
    await ctl.save(session, ctl_state)
    return posts


# -- ambushes: once a press --------------------------------------------------------------------------------------------


async def press_hook(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random,
                     year: int) -> list[str]:
    c = cfg(content)
    st = ch.state
    if int(st.flags.get("jail_weeks", 0) or 0) > 0 or st.flags.get(FLAG_PENDING_COMBAT) or st.flags.get(FLAG_COMBAT):
        return []
    here = [f for f in alive(await all_factions(session)) if f.get("district") == st.district]
    if not here or rng.random() >= c.ambush_chance:
        return []
    f = rng.choice(here)
    st.flags[FLAG_PENDING_COMBAT] = {"src": SRC_AMBUSH, "dyn": int(f["id"]), "dyn_name": f["name"],
                                     "dyn_kind": f["kind"], "faction": base_faction(content, f, year),
                                     "danger": rng.randint(*c.ambush_danger)}
    clues = dict(st.flags.get(FLAG_CLUES) or {})
    clues[str(f["id"])] = int(clues.get(str(f["id"]), 0)) + 1
    st.flags[FLAG_CLUES] = clues
    return [f"⚔️ На тебя нападает {label(f)}. Теперь ты знаешь их в лицо: улика к логову +1."]


async def fight_over(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                     tz: str, src: str, result: str, ctx: dict) -> list[str]:
    """After an ambush (the crew weakens when beaten) or a storm of the lair (the crew is gone when won)."""
    from app.services import loud

    c = cfg(content)
    items = await all_factions(session)
    f = find(items, int(ctx.get("dyn", 0) or 0))
    if f is None or not f.get("alive"):
        return []
    lines: list[str] = []
    if result != cb.WON:
        if src == SRC_LAIR:
            lines.append(f"Штурм провален. {cap(label(f))} празднует и усиливает охрану.")
            f["power"] = min(100, int(f["power"]) + 10)
        await _save(session, items)
        return lines
    if src == SRC_AMBUSH:
        f["power"] = max(0, int(f["power"]) - c.ambush_power_loss)
        lines.append(f"{cap(label(f))} теряет людей. Сила: {f['power']}/100.")
        if f["power"] <= 0:
            f.update(alive=False, died=utcnow().date().isoformat(), how="player")
            lines.append(f"Это были последние. «{f['name']}» больше нет.")
    else:
        lo, hi = c.loot
        loot = rng.randint(lo, hi)
        if f.get("kind") == "corp":
            loot = int(loot * c.corp_loot_mult)
        apply_effects(ch.state, {"eddies": loot, "street_cred": c.cred, "fame": 2}, content, rng, year)
        f.update(alive=False, died=utcnow().date().isoformat(), how="player")
        from app.services import season  # owner request 25.10

        await season.add_ch(session, content, ch, "lair", content.balance.season.pts.get("lair", 0))
        from app.services import control  # owner request 25.10: a lair or an office taken frees the district

        cc = content.balance.control
        if f.get("kind") == "gang":
            await control.adjust(session, content, str(f.get("district", "")), gang=cc.lair_gang, corp=cc.lair_corp)
        else:
            await control.adjust(session, content, str(f.get("district", "")), gang=cc.office_gang, corp=cc.office_corp)
        who = f"{ch.state.name} {ch.state.surname}".strip()
        lines.append(f"Логово взято. {cap(label(f))} уничтожена. Добыча: +{loot} эдди, уличная репутация +{c.cred}, слава +2.")
        await loud.post(session, ch.id, f"{loud.who(ch.state.name, ch.state.surname)} штурмует логово: "
                                        f"{label(f)} уничтожена. Город запомнит это имя.", kind="lair", w=2)
        await repo.log_event(session, "dyn_destroyed", {"name": f["name"], "kind": f["kind"], "by": who}, character_id=ch.id)
    await _save(session, items)
    clues = dict(ch.state.flags.get(FLAG_CLUES) or {})
    if not f.get("alive"):
        clues.pop(str(f["id"]), None)
        ch.state.flags[FLAG_CLUES] = clues
    return lines


# -- the player hunts a lair ---------------------------------------------------------------------------------------------


@dataclass
class HuntResult:
    blocked: str | None = None
    ok: bool = False
    check: object | None = None
    clues: int = 0
    lines: list[str] = field(default_factory=list)
    applied: AppliedEffects | None = None
    fight: object | None = None


def clues_of(ch: ActiveCharacter, fid: int) -> int:
    return int((ch.state.flags.get(FLAG_CLUES) or {}).get(str(fid), 0) or 0)


async def search(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
                 fid: int) -> HuntResult:
    c = cfg(content)
    f = find(await all_factions(session), fid)
    if f is None or not f.get("alive"):
        return HuntResult(blocked="gone")
    if int(ch.state.flags.get("jail_weeks", 0) or 0) > 0:
        return HuntResult(blocked="jail")
    if clues_of(ch, fid) >= c.search_need:
        return HuntResult(blocked="found")
    if not ap_services.can_spend(ch, c.search_ap):
        return HuntResult(blocked="ap")
    ap_services.spend(ch, content, c.search_ap)
    stat, skill = ("INT", "netrunning") if f.get("kind") == "corp" else ("COOL", "persuasion")
    chk = checks.roll_check(ch.state, content, rng, stat, skill, c.search_dv, ("social",) if f["kind"] == "gang" else ("gig_hack",))
    res = HuntResult(ok=chk.ok, check=chk)
    clues = dict(ch.state.flags.get(FLAG_CLUES) or {})
    if chk.ok:
        clues[str(fid)] = int(clues.get(str(fid), 0)) + 1
        res.lines.append("Ещё одна ниточка: адрес склада, номер машины, чей-то длинный язык.")
    else:
        res.applied = apply_effects(ch.state, {"heat": 1}, content, rng, year)
        res.lines.append("Вопросы задавал не тем людям. Теперь спрашивают про тебя.")
    ch.state.flags[FLAG_CLUES] = clues
    res.clues = int(clues.get(str(fid), 0))
    if res.clues >= c.search_need:
        res.lines.append("Логово найдено. Можно идти на штурм.")
    await save_active(session, ch, tz)
    return res


async def assault(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
                  fid: int) -> HuntResult:
    from app.services import combat

    c = cfg(content)
    f = find(await all_factions(session), fid)
    if f is None or not f.get("alive"):
        return HuntResult(blocked="gone")
    if clues_of(ch, fid) < c.search_need:
        return HuntResult(blocked="notfound")
    if not ap_services.can_spend(ch, c.assault_ap):
        return HuntResult(blocked="ap")
    ap_services.spend(ch, content, c.assault_ap)
    danger = max(3, min(5, c.assault_danger - (0 if int(f["power"]) > 50 else 1)))
    res = HuntResult(ok=True)
    res.fight = await combat.begin(session, content, ch, rng, year, tz, faction=base_faction(content, f, year),
                                   danger=danger, src={"src": SRC_LAIR, "dyn": fid, "dyn_name": f["name"],
                                                       "dyn_kind": f["kind"], "lair": True})
    return res
