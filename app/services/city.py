"""The city's people (owner request: freedom of action, docs/PLAN_FREEDOM.md, stage 2).

NPCs live in `city_npcs` and are shared by all players. A district is filled on its first visit. A dead NPC stays dead
for `respawn_days`, then a new person takes the slot. Every deed changes the NPC's attitude to the character and leaves
a memory line; violent deeds raise heat by the crowd of the place; an armed NPC answers with a real fight.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.content.loader import Content
from app.db import repo
from app.db.models import CityNpc
from app.engine import city as ce
from app.engine.effects import AppliedEffects, apply_effects
from app.services import ap as ap_services
from app.services import followups
from app.services.game import ActiveCharacter, save_active

R_GONE, R_DEAD, R_HURT, R_NOT, R_TODAY, R_AP, R_EDDIES, R_JAIL, R_HEAT, R_CLOSED = (
    "gone", "dead", "hurt", "not", "today", "ap", "eddies", "jail", "heat", "closed")
FLAG_CIV_KILLS = "civ_kills"  # civilians killed by the character (the maniac of stage 4)


def _today() -> str:
    return utcnow().date().isoformat()


def _jailed(ch: ActiveCharacter) -> bool:
    return int(ch.state.flags.get("jail_weeks", 0) or 0) > 0


async def ensure_district(session: AsyncSession, content: Content, district: str, rng: random.Random) -> None:
    got = await session.execute(select(CityNpc.id).where(CityNpc.district == district).limit(1))
    if got.first() is not None:
        return
    for place in ce.cfg(content).places:
        for i, key in enumerate(ce.slots(place, rng)):
            arch = ce.archetype(content, key)
            if arch is None:
                continue
            name, gender, money = ce.new_person(content, arch, rng)
            session.add(CityNpc(district=district, place=place.key, slot=i, archetype=key, name=name, gender=gender,
                                alive=True, money=money, data={}))
    await session.flush()


def _respawn(content: Content, npc: CityNpc, rng: random.Random) -> None:
    arch = ce.archetype(content, npc.archetype)
    if arch is None:
        return
    prev = {"name": npc.name, "cause": (npc.data or {}).get("cause"), "killer": (npc.data or {}).get("killer_name")}
    killer = (npc.data or {}).get("killer")
    name, gender, money = ce.new_person(content, arch, rng)
    npc.name, npc.gender, npc.money, npc.alive, npc.died_at = name, gender, money, True, None
    npc.data = {"prev": prev}
    if (npc.data["prev"].get("cause") or "") not in ("purge",):  # a purge death is nobody's crime
        from app.services import city_life

        city_life.maybe_avenger(content, npc, prev, int(killer) if killer else None, rng)


async def people(session: AsyncSession, content: Content, district: str, place: str, rng: random.Random) -> list[CityNpc]:
    await ensure_district(session, content, district, rng)
    res = await session.execute(select(CityNpc).where(CityNpc.district == district, CityNpc.place == place)
                                .order_by(CityNpc.slot))
    out = list(res.scalars())
    cut = utcnow() - timedelta(days=ce.cfg(content).respawn_days)
    for npc in out:
        if not npc.alive and npc.died_at is not None and npc.died_at < cut:
            _respawn(content, npc, rng)
    await session.flush()
    return out


def hurt(npc: CityNpc) -> bool:
    until = (npc.data or {}).get("hurt_until")
    return bool(until) and str(until) > utcnow().isoformat()


def bond(npc: CityNpc, char_id: int) -> dict:
    return dict((npc.data or {}).get("bonds", {}).get(str(char_id), {}))


def att(npc: CityNpc, char_id: int) -> int:
    return int(bond(npc, char_id).get("att", 0) or 0)


def _set_bond(content: Content, npc: CityNpc, char_id: int, b: dict) -> None:
    data = dict(npc.data or {})
    bonds = dict(data.get("bonds", {}))
    b["notes"] = list(b.get("notes", []))[-ce.cfg(content).notes_keep:]
    bonds[str(char_id)] = b
    data["bonds"] = bonds
    npc.data = data


def remember(content: Content, npc: CityNpc, char_id: int, note: str, delta: int = 0, verb: str | None = None) -> None:
    b = bond(npc, char_id)
    b["att"] = max(ce.ATT_MIN, min(ce.ATT_MAX, int(b.get("att", 0) or 0) + delta))
    if note:
        b["notes"] = list(b.get("notes", [])) + [note]
    if verb:
        day = dict(b.get("day", {}))
        day[verb] = _today()
        b["day"] = day
    _set_bond(content, npc, char_id, b)


def used_today(npc: CityNpc, char_id: int, verb: str) -> bool:
    return bond(npc, char_id).get("day", {}).get(verb) == _today()


def fight_faction(content: Content, ch: ActiveCharacter, arch, year: int) -> str:  # noqa: ANN001
    """Whose fighters stand with the NPC: the police, a corp, else a gang of the district that has fighters this year."""
    from app.engine import combat as cb

    if arch.faction == "ncpd":
        return "ncpd"
    cands: list[str] = []
    district = None
    if arch.faction == "corp":
        cands = ["arasaka", "militech", "kang_tao"]
    else:
        district = content.districts.get(ch.state.district)
        cands = list(district.gangs if district is not None else []) + sorted(content.gangs)
    ok = [f for f in cands if cb._faction_pool(content, f, year)
          or (f in content.gangs and content.gangs[f].fights_as)]  # owner request 25.10: the gangs abroad
    if not ok:
        return "ncpd"
    local = [f for f in ok if district is not None and f in district.gangs] if arch.faction != "corp" else ok[:1]
    import zlib

    pool = local or ok
    return pool[zlib.crc32(ch.state.district.encode()) % len(pool)]


@dataclass
class DeedResult:
    blocked: str | None = None
    npc: CityNpc | None = None
    deed: ce.Deed | None = None
    applied: AppliedEffects | None = None
    lines: list[str] = field(default_factory=list)
    fight: object | None = None       # combat Step when a fight began
    died: bool = False
    death_cause: str | None = None
    anon: str | None = None           # the maniac's nickname: the kill is posted without the name


def blocked(content: Content, ch: ActiveCharacter, npc: CityNpc | None, v) -> str | None:  # noqa: ANN001
    if npc is None or v is None:
        return R_GONE
    arch = ce.archetype(content, npc.archetype)
    if arch is None:
        return R_GONE
    if not npc.alive:
        return R_DEAD
    if hurt(npc):
        return R_HURT
    if not ce.allowed(v, arch):
        return R_NOT
    if _jailed(ch):
        return R_JAIL
    if v.once_a_day and used_today(npc, ch.id, v.key):
        return R_TODAY
    if v.ap and not ap_services.can_spend(ch, v.ap):
        return R_AP
    if ch.state.eddies < v.price:
        return R_EDDIES
    return None


async def do(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
             npc_id: int, verb_key: str, said: str = "") -> DeedResult:
    npc = await session.get(CityNpc, npc_id)
    v = ce.verb(content, verb_key)
    why = blocked(content, ch, npc, v)
    if why is not None:
        return DeedResult(blocked=why, npc=npc)
    assert npc is not None and v is not None
    from app.services import city_life

    if await city_life.closed_until(session, npc.district, npc.place) is not None:
        return DeedResult(blocked=R_CLOSED, npc=npc)
    arch = ce.archetype(content, npc.archetype)
    place = ce.place_type(content, npc.place)
    if v.ap:
        ap_services.spend(ch, content, v.ap)
    d = ce.resolve(ch.state, content, rng, v, arch, place.crowd if place else 1, npc.money, att(npc, ch.id))
    from app.services import purge

    if int(d.effects.get("heat", 0) or 0) > 0 and await purge.is_night(session):  # the purge: no law, no heat
        d.effects.pop("heat", None)
    res = DeedResult(npc=npc, deed=d)
    if d.effects:
        res.applied = apply_effects(ch.state, d.effects, content, rng, year)
    me = f"{ch.state.name} {ch.state.surname}".strip()
    npc.money = max(0, npc.money - d.take)
    note = f"{me}: {v.name.lower()} — {'вышло' if d.ok else 'не вышло'}"
    remember(content, npc, ch.id, note, d.att, v.key if v.once_a_day else None)
    if d.killed:
        _kill(npc, ch, verb_key)
        await city_life.note_kills(session, content, npc.district, npc.place)
        from app.engine import psychosis as psy_engine

        # a purge kill is legal and a fugitive psycho is already known by name: neither is the maniac's
        if arch.civilian and not psy_engine.is_fugitive(ch.state) and not await purge.is_night(session):
            ch.state.flags[FLAG_CIV_KILLS] = int(ch.state.flags.get(FLAG_CIV_KILLS, 0) or 0) + 1
            from app.services import maniac

            res.lines.extend(await maniac.after_kill(session, content, ch, rng))
        from app.engine import maniac as maniac_engine
        from app.services import loud

        from app.services import maniac as maniac_svc

        mr = maniac_engine.rec(ch.state)
        shown = bool((await maniac_svc.registry(session)).get(str(ch.id), {}).get("revealed")) if mr else False
        if arch.civilian and mr and not shown and not await purge.is_night(session):
            res.anon = str(mr["nick"])
            await loud.post(session, None, f"«{mr['nick']}» снова убивает: {npc.name}, {arch.name}.")  # the name is hidden
        else:
            await loud.post(session, ch.id, f"{loud.who(ch.state.name, ch.state.surname)} убивает человека в районе: "
                                            f"{npc.name}, {arch.name}.")
    elif d.hurt:
        data = dict(npc.data or {})
        data["hurt_until"] = (utcnow() + timedelta(days=ce.cfg(content).hurt_days)).isoformat()
        npc.data = data
    if res.applied is not None:
        fu = await followups.handle(session, content, ch, res.applied.followups, rng, year, tz)
        res.lines, res.died, res.death_cause = res.lines + fu.lines, fu.died, fu.cause
        if fu.died:
            await session.flush()
            return res
    if d.fight:
        from app.services import combat

        res.fight = await combat.begin(session, content, ch, rng, year, tz, faction=fight_faction(content, ch, arch, year),
                                       danger=arch.danger, src={"src": "city", "npc": npc.id, "verb": verb_key})
        cs = getattr(res.fight, "cs", None)
        if cs is not None and cs.enemies:  # the NPC leads the pack under their own name
            cs.enemies[0].name = f"{npc.name} ({arch.name})"
            combat._store(ch, cs)
    if verb_key == "extort" and d.ok and arch is not None:  # owner request 27.10 «они должны мне платить»
        res.lines.append(set_tribute(content, ch, npc, arch))
    await save_active(session, ch, tz)
    if verb_key == "ask" and d.ok:  # owner request: the NPCs retell what other players did here
        res.lines.extend(await city_life.rumors(session, content, ch, npc.district, rng))
    await repo.log_event(session, "city_deed", {"npc": npc.id, "verb": verb_key, "ok": d.ok, "fight": d.fight,
                                                "killed": d.killed, "district": npc.district, "place": npc.place,
                                                "who": f"«{res.anon}»" if res.anon else me, "npc_name": npc.name,
                                                "role": arch.name if arch else "",
                                                **({"said": said[:300]} if said else {})}, character_id=ch.id)
    await session.flush()
    return res


def _kill(npc: CityNpc, ch: ActiveCharacter, cause: str) -> None:
    npc.alive = False
    npc.died_at = utcnow()
    data = dict(npc.data or {})
    data.update({"killer": ch.id, "killer_name": f"{ch.state.name} {ch.state.surname}".strip(), "cause": cause})
    npc.data = data


async def start_fight(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                      tz: str, npc_id: int, verb_key: str) -> DeedResult:
    """A violent deed against an armed NPC: no check, the fight starts at once."""
    return await do(session, content, ch, rng, year, tz, npc_id, verb_key)


async def fight_over(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                     tz: str, result: str, ctx: dict) -> list[str]:
    """After a fight with a city NPC (services/combat.after_fight): the NPC's fate."""
    from app.engine import combat as cb

    npc = await session.get(CityNpc, int(ctx.get("npc", 0) or 0))
    if npc is None:
        return []
    arch = ce.archetype(content, npc.archetype)
    verb_key = str(ctx.get("verb", ""))
    me = f"{ch.state.name} {ch.state.surname}".strip()
    if result != cb.WON:
        remember(content, npc, ch.id, f"{me}: драка, проигранная", -20)
        return [f"{npc.name} остаётся на ногах и запомнит это."]
    lines: list[str] = []
    if verb_key == "kill" or int(ctx.get("kills", 0) or 0) > 0:
        _kill(npc, ch, verb_key or "fight")
        lines.append(f"{npc.name} {'мертва' if npc.gender == 'f' else 'мёртв'}. Через неделю на это место придёт кто-то другой.")
        from app.services import loud

        await loud.post(session, ch.id, f"{loud.who(ch.state.name, ch.state.surname)} убивает в перестрелке: "
                                        f"{npc.name}{', ' + arch.name if arch else ''}.")
    else:
        data = dict(npc.data or {})
        data["hurt_until"] = (utcnow() + timedelta(days=ce.cfg(content).hurt_days)).isoformat()
        npc.data = data
        remember(content, npc, ch.id, f"{me}: избиение", -40)
        lines.append(f"{npc.name} лежит. Пару дней этого человека здесь не будет.")
    if verb_key in ("rob", "intimidate", "pickpocket", "kill") and npc.money > 0:
        take = npc.money
        npc.money = 0
        apply_effects(ch.state, {"eddies": take}, content, rng, year)
        lines.append(f"Из карманов: +{take} эдди.")
    await session.flush()
    return lines


@dataclass
class ServiceResult:
    blocked: str | None = None
    npc: CityNpc | None = None
    service: object | None = None
    applied: AppliedEffects | None = None


async def service(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
                  npc_id: int) -> ServiceResult:
    npc = await session.get(CityNpc, npc_id)
    v = ce.verb(content, "service")
    why = blocked(content, ch, npc, v)
    if why is not None:
        return ServiceResult(blocked=why, npc=npc)
    assert npc is not None
    arch = ce.archetype(content, npc.archetype)
    sv = ce.cfg(content).services.get(arch.service or "")
    if sv is None:
        return ServiceResult(blocked=R_NOT, npc=npc)
    if sv.need_heat and ch.state.heat <= 0:
        return ServiceResult(blocked=R_HEAT, npc=npc, service=sv)
    if ch.state.eddies < sv.price:
        return ServiceResult(blocked=R_EDDIES, npc=npc, service=sv)
    fx = dict(sv.effects)
    if sv.price:
        fx["eddies"] = int(fx.get("eddies", 0)) - sv.price
    res = ServiceResult(npc=npc, service=sv, applied=apply_effects(ch.state, fx, content, rng, year))
    npc.money += sv.price
    remember(content, npc, ch.id, f"{ch.state.name}: {sv.name.lower()}", 2)
    await save_active(session, ch, tz)
    await repo.log_event(session, "city_service", {"npc_name": npc.name, "role": arch.name if arch else "",
                                                   "service": sv.name, "price": sv.price, "place": npc.place,
                                                   "district": npc.district}, character_id=ch.id)
    await session.flush()
    return res


async def note_talk(session: AsyncSession, content: Content, npc_id: int, char_id: int, line: str) -> None:
    npc = await session.get(CityNpc, npc_id)
    if npc is not None:
        remember(content, npc, char_id, line[:220], 1)
        await session.flush()


def npc_dead_since(npc: CityNpc) -> datetime | None:
    return npc.died_at


# -- the cyberpsychopath's rampage (owner request) ------------------------------------------------------------------------


@dataclass
class RampageResult:
    blocked: str | None = None
    killed: list[str] = field(default_factory=list)
    take: int = 0
    applied: AppliedEffects | None = None
    lines: list[str] = field(default_factory=list)
    fight: object | None = None
    level_up: str | None = None


async def rampage(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
                  place_key: str) -> RampageResult:
    """A fugitive psycho kills every civilian in the place; an armed one there starts a real fight."""
    from app.engine import cyberpsycho as cp
    from app.services import loud

    c = cp.cfg(content)
    if not cp.active(ch.state):
        return RampageResult(blocked=R_NOT)
    if not ap_services.can_spend(ch, c.rampage_ap):
        return RampageResult(blocked=R_AP)
    place = ce.place_type(content, place_key)
    if place is None:
        return RampageResult(blocked=R_GONE)
    from app.services import city_life

    if await city_life.closed_until(session, ch.state.district, place_key) is not None:
        return RampageResult(blocked=R_CLOSED)
    crowd = [n for n, _ in await city_life.present(session, content, ch.state.district, place_key, rng)]
    alive = [n for n in crowd if n.alive and not hurt(n)]
    if not alive:
        return RampageResult(blocked=R_GONE)
    ap_services.spend(ch, content, c.rampage_ap)
    res = RampageResult()
    before = cp.level(ch.state, content)
    armed = None
    for npc in alive:
        arch = ce.archetype(content, npc.archetype)
        if arch is None:
            continue
        if arch.armed:
            armed = armed or (npc, arch)
            continue
        _kill(npc, ch, "rampage")
        res.killed.append(npc.name)
        res.take += npc.money
        npc.money = 0
    cp.add_rampage(ch.state)
    from app.engine.state import FLAG_PSYCHO_HUNT_AT

    ch.state.flags[FLAG_PSYCHO_HUNT_AT] = ch.state.weeks_lived  # MaxTac comes with the next press
    after = cp.level(ch.state, content)
    if after is not None and (before is None or after.rampages != before.rampages):
        res.level_up = after.name
    fx = {"heat": c.rampage_heat, "mood": c.rampage_mood}
    if res.take:
        fx["eddies"] = res.take
    res.applied = apply_effects(ch.state, fx, content, rng, year)
    venue = ce.venue_name(ch.state.district, place, content, year)
    await loud.post(session, ch.id, f"Киберпсих {loud.who(ch.state.name, ch.state.surname)} устраивает бойню: "
                                    f"{venue}, {len(res.killed)} погибших.", kind="psycho", w=2)
    if armed is not None:
        npc, arch = armed
        from app.services import combat, notices

        if res.killed:  # the fight screen comes first: the dead are told in the menu after it
            notices.push(ch, f"🩸 Бойня ({venue}): погибли {', '.join(res.killed)}. +{res.take} эдди из карманов.")

        res.fight = await combat.begin(session, content, ch, rng, year, tz, faction=fight_faction(content, ch, arch, year),
                                       danger=arch.danger, src={"src": "city", "npc": npc.id, "verb": "kill"})
        cs = getattr(res.fight, "cs", None)
        if cs is not None and cs.enemies:
            cs.enemies[0].name = f"{npc.name} ({arch.name})"
            combat._store(ch, cs)
    await save_active(session, ch, tz)
    await city_life.note_kills(session, content, ch.state.district, place_key, len(res.killed))
    await repo.log_event(session, "city_rampage", {"place": place_key, "killed": len(res.killed), "district": ch.state.district,
                                                   "who": f"{ch.state.name} {ch.state.surname}".strip()}, character_id=ch.id)
    await session.flush()
    return res


# -- the tribute (owner request 27.10 «они должны мне платить», docs/PLAN_TRIBUTE.md) -------------------------------------

FLAG_TRIBUTE = "tribute"   # {npc id: {name, role, amount, since (weeks lived)}}


def tribute_amount(content: Content, st, arch_key: str) -> int:  # noqa: ANN001
    c = ce.cfg(content).tribute
    base = int(c.base.get(arch_key, c.default))
    mult = (1 + max(0, st.street_cred) / max(1, c.per_cred)) * (1 + max(0, st.fame) / max(1, c.per_fame))
    return max(0, min(c.cap, int(base * mult)))


def tribute_of(st, npc_id: int) -> int:  # noqa: ANN001
    r = (st.flags.get(FLAG_TRIBUTE) or {}).get(str(npc_id))
    return int(r.get("amount", 0)) if isinstance(r, dict) else 0


def set_tribute(content: Content, ch: ActiveCharacter, npc: CityNpc, arch) -> str:  # noqa: ANN001
    c = ce.cfg(content).tribute
    rec = {k: dict(v) for k, v in (ch.state.flags.get(FLAG_TRIBUTE) or {}).items() if isinstance(v, dict)}
    amount = tribute_amount(content, ch.state, npc.archetype)
    rec[str(npc.id)] = {"name": npc.name, "role": arch.name, "amount": amount, "since": ch.state.weeks_lived}
    while len(rec) > c.max_payers:
        oldest = min((k for k in rec if k != str(npc.id)), key=lambda k: int(rec[k].get("since", 0)))
        rec.pop(oldest)
    ch.state.flags[FLAG_TRIBUTE] = rec
    return f"💸 {npc.name} будет платить тебе дань: {amount} эдди каждый прожитый месяц."


async def tribute_hook(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random,
                       year: int) -> list[str]:
    """Every lived month: the payers pay; a dead one is gone, a stubborn one stops."""
    c = ce.cfg(content).tribute
    rec = {k: dict(v) for k, v in (ch.state.flags.get(FLAG_TRIBUTE) or {}).items() if isinstance(v, dict)}
    if not rec:
        return []
    lines: list[str] = []
    total, names = 0, []
    for k, r in list(rec.items()):
        npc = await session.get(CityNpc, int(k))
        if npc is None or not npc.alive or npc.name != r.get("name"):
            rec.pop(k)
            lines.append(f"💸 {r.get('name')} больше не платит: этого человека нет в живых.")
            continue
        if rng.random() < c.stop_chance:
            rec.pop(k)
            remember(content, npc, ch.id, f"{ch.state.name}: дань больше не платит", -5)
            lines.append(f"💸 {r.get('name')} перестаёт платить. Напомни о себе: «Обложить данью».")
            continue
        total += int(r.get("amount", 0))
        names.append(str(r.get("name")))
    if rec:
        ch.state.flags[FLAG_TRIBUTE] = rec
    else:
        ch.state.flags.pop(FLAG_TRIBUTE, None)
    if total:
        apply_effects(ch.state, {"eddies": total}, content, rng, year)
        lines.insert(0, f"💸 Дань за месяц: +{total} эдди ({', '.join(names)}).")
    return lines
