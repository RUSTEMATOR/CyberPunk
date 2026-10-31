"""The memory of the city and the living city (owner request, docs/PLAN_FREEDOM.md stages 4-5).

- vendetta: a killed NPC may be replaced by a relative who hunts the killer (`data.avenge`);
- grudges and gratitude: NPCs who hate or love the character act on it once a press;
- a closed place: kills in one place within a day make the NCPD seal it (world_state `city_closed`);
- guests: every few hours some NPCs of the district sit in another place;
- rumors: a successful «ask» retells the fresh deeds of other players in the district;
- hire: an armed NPC who likes the character works for him for a month (engine/companion.py).
"""

from __future__ import annotations

import random
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.content.loader import Content
from app.db import repo
from app.db.models import CityNpc, EventLog
from app.engine import city as ce
from app.engine import companion as comp
from app.engine.effects import apply_effects
from app.services.game import ActiveCharacter, save_active

CLOSED_KEY, KILLS_KEY = "city_closed", "city_kills"


def cfg(content: Content):  # noqa: ANN201
    return ce.cfg(content).life


def _key(district: str, place: str) -> str:
    return f"{district}:{place}"


# -- vendetta (called from services/city._respawn) ------------------------------------------------------------------------


def maybe_avenger(content: Content, npc: CityNpc, prev: dict, killer: int | None, rng: random.Random) -> None:
    """The new NPC on a murdered one's place may be a relative who wants the killer."""
    if killer and rng.random() < cfg(content).avenge_chance:
        data = dict(npc.data or {})
        data["avenge"] = {"char": int(killer), "for": str(prev.get("name", ""))}
        npc.data = data


# -- a closed place ---------------------------------------------------------------------------------------------------------


async def closed_until(session: AsyncSession, district: str, place: str) -> datetime | None:
    got = (await repo.ws_get(session, CLOSED_KEY, {}) or {}).get(_key(district, place))
    if not got:
        return None
    until = datetime.fromisoformat(str(got))
    return until if until > utcnow() else None


async def note_kills(session: AsyncSession, content: Content, district: str, place: str, n: int = 1) -> bool:
    """Count kills in a place; True when the NCPD seals it now."""
    from app.services import loud, purge

    if n <= 0 or await purge.is_night(session):
        return False
    c = cfg(content)
    now = utcnow()
    cut = (now - timedelta(hours=c.close_hours)).isoformat()
    kills = dict(await repo.ws_get(session, KILLS_KEY, {}) or {})
    k = _key(district, place)
    fresh = [x for x in kills.get(k, []) if str(x) > cut] + [now.isoformat()] * n
    kills[k] = fresh[-20:]
    await repo.ws_set(session, KILLS_KEY, kills)
    if len(fresh) < c.close_kills or await closed_until(session, district, place) is not None:
        return False
    closed = dict(await repo.ws_get(session, CLOSED_KEY, {}) or {})
    closed[k] = (now + timedelta(hours=c.close_hours)).isoformat()
    await repo.ws_set(session, CLOSED_KEY, closed)
    pt = ce.place_type(content, place)
    venue = ce.venue_name(district, pt, content) if pt is not None else place
    name = content.districts[district].name if district in content.districts else district
    d = content.districts.get(district)
    cop = (content.countries[d.city].police or "Полиция") if d is not None and d.city in content.countries else "NCPD"
    await loud.post(session, None, f"{cop} оцепляет место: {venue} ({name}). Слишком много трупов за сутки. Сутки туда не войти.")
    return True


# -- guests -----------------------------------------------------------------------------------------------------------------


def guest_map(content: Content, rows: list[CityNpc], district: str, now: datetime) -> dict[int, str]:
    """npc id -> the place it visits now. NPCs with a service (bartender, doc) keep their post."""
    c = cfg(content)
    bucket = int(now.timestamp() // (max(1, c.guest_hours) * 3600))
    rng = random.Random(zlib.crc32(district.encode()) + bucket)
    pool = []
    for n in rows:
        arch = ce.archetype(content, n.archetype)
        if n.alive and arch is not None and not arch.service:
            pool.append(n)
    pool.sort(key=lambda n: n.id)
    places = [p.key for p in ce.cfg(content).places]
    out: dict[int, str] = {}
    for n in rng.sample(pool, min(len(pool), c.guests * 2)):
        others = [p for p in places if p != n.place]
        if others:
            out[n.id] = rng.choice(others)
    return out


async def present(session: AsyncSession, content: Content, district: str, place: str,
                  rng: random.Random) -> list[tuple[CityNpc, str | None]]:
    """Who is in the place now: its own people minus the visitors elsewhere, plus the guests (with their home place)."""
    from app.services import city

    own = await city.people(session, content, district, place, rng)
    res = await session.execute(select(CityNpc).where(CityNpc.district == district))
    rows = list(res.scalars())
    gm = guest_map(content, rows, district, utcnow())
    out: list[tuple[CityNpc, str | None]] = [(n, None) for n in own if gm.get(n.id) is None or not n.alive]
    for n in rows:
        if gm.get(n.id) == place and n.place != place:
            out.append((n, n.place))
    return out


# -- rumors -------------------------------------------------------------------------------------------------------------------

RUMOR_KIND = {"runover": "наезд машиной", "rob": "ограбление", "beat": "избиение", "kill": "убийство", "intimidate": "запугивание",
              "pickpocket": "карманная кража", "bribe": "взятка", "report": "донос", "flirt": "роман"}


async def rumors(session: AsyncSession, content: Content, ch: ActiveCharacter, district: str, rng: random.Random) -> list[str]:
    c = cfg(content)
    since = utcnow() - timedelta(days=c.rumor_days)
    res = await session.execute(select(EventLog).where(EventLog.kind.in_(("city_deed", "city_rampage")),
                                                       EventLog.created_at >= since).order_by(EventLog.id.desc()).limit(300))
    pool: list[str] = []
    for row in res.scalars():
        p = row.payload or {}
        if row.character_id == ch.id or p.get("district") != district or not p.get("who"):
            continue
        pt = ce.place_type(content, str(p.get("place", "")))
        venue = ce.venue_name(district, pt, content, ch.state.current_year) if pt is not None else "?"
        if row.kind == "city_rampage":
            pool.append(f"Слух: бойня, {venue}, {p.get('killed', 0)} погибших. Говорят, это киберпсих {p['who']}.")
            continue
        kind = RUMOR_KIND.get(str(p.get("verb", "")))
        if kind is None or not (p.get("ok") or p.get("fight")):
            continue
        pool.append(f"Слух: {kind}, {venue}. Жертва: {p.get('npc_name', '?')}, {p.get('role', '')}. Говорят, это {p['who']}.")
    rng.shuffle(pool)
    return list(dict.fromkeys(pool))[:c.rumors]


# -- hire -----------------------------------------------------------------------------------------------------------------------

R_NOT, R_ATT, R_EDDIES, R_HAVE, R_GONE, R_FULL = "not", "att", "eddies", "have", "gone", "full"


def can_hire(content: Content, ch: ActiveCharacter, npc: CityNpc) -> bool:
    from app.services import city

    arch = ce.archetype(content, npc.archetype)
    return bool(arch and arch.armed and npc.alive and not city.hurt(npc)
                and city.att(npc, ch.id) >= cfg(content).hire_att)


@dataclass
class HireResult:
    blocked: str | None = None
    price: int = 0
    weeks: int = 0


async def hire(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
               npc_id: int) -> HireResult:
    from app.services import city

    npc = await session.get(CityNpc, npc_id)
    if npc is None or not npc.alive:
        return HireResult(blocked=R_GONE)
    arch = ce.archetype(content, npc.archetype)
    if arch is None or not arch.armed:
        return HireResult(blocked=R_NOT)
    if city.att(npc, ch.id) < cfg(content).hire_att:
        return HireResult(blocked=R_ATT)
    if comp.has(ch.state, npc.id):
        return HireResult(blocked=R_HAVE)
    squad = comp.active_members(ch.state)
    if len(squad) >= cfg(content).squad_max:
        return HireResult(blocked=R_FULL)
    price = comp.price(content, arch.danger)
    if ch.state.eddies < price:
        return HireResult(blocked=R_EDDIES, price=price)
    apply_effects(ch.state, {"eddies": -price}, content, rng, year)
    weeks = cfg(content).hire_weeks
    squad.append({"npc": npc.id, "name": npc.name, "role": arch.name, "danger": arch.danger,
                  "until": ch.state.weeks_lived + weeks})
    comp.save(ch.state, squad)
    city.remember(content, npc, ch.id, f"{ch.state.name}: нанял на месяц", 5)
    await save_active(session, ch, tz)
    await repo.log_event(session, "city_hire", {"npc_name": npc.name, "role": arch.name, "price": price, "weeks": weeks},
                         character_id=ch.id)
    return HireResult(price=price, weeks=weeks)


async def squad_for_fight(session: AsyncSession, ch: ActiveCharacter) -> list[dict]:
    """Owner request 24.10: the hired NPCs who stand in a fight now; a dead one ends the contract, a hurt one sits out."""
    from app.services import city, notices

    out: list[dict] = []
    keep: list[dict] = []
    for r in comp.active_members(ch.state):
        npc = await session.get(CityNpc, int(r.get("npc", 0) or 0))
        if npc is None or not npc.alive or npc.name != r.get("name"):
            notices.push(ch, f"{r.get('name', 'Напарник')} больше не прикрывает тебя: этого человека нет в живых.")
            continue
        keep.append(r)
        if not city.hurt(npc):
            out.append(r)
    if len(keep) != len(comp.active_members(ch.state)):
        comp.save(ch.state, keep)
    return out


async def ally_name(session: AsyncSession, ch: ActiveCharacter) -> str | None:
    """The first squad member who can fight (old callers)."""
    squad = await squad_for_fight(session, ch)
    return str(squad[0].get("name")) if squad else None


async def squad_losses(session: AsyncSession, content: Content, ch: ActiveCharacter, fallen: list[int]) -> list[str]:
    """Squad members killed in a fight die for real: the city NPC is gone, the contract with it."""
    from app.services import city

    lines: list[str] = []
    squad = comp.active_members(ch.state)
    for npc_id in fallen:
        npc = await session.get(CityNpc, npc_id)
        rec_ = next((m for m in squad if int(m.get("npc", 0) or 0) == npc_id), None)
        if npc is not None and npc.alive:
            npc.alive = False
            city.remember(content, npc, ch.id, f"{ch.state.name}: погиб в бою рядом", -5)
        if rec_ is not None:
            lines.append(f"✝ {rec_.get('name', 'Напарник')} погибает в бою. Контракт закрыт навсегда.")
    if fallen:
        comp.save(ch.state, [m for m in squad if int(m.get("npc", 0) or 0) not in fallen])
    return lines


# -- once a press ---------------------------------------------------------------------------------------------------------------


async def press_hook(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random,
                     year: int) -> list[str]:
    from app.engine.state import FLAG_COMBAT, FLAG_PENDING_COMBAT
    from app.services import city

    st = ch.state
    if int(st.flags.get("jail_weeks", 0) or 0) > 0:
        return []
    c = cfg(content)
    lines: list[str] = []
    gone = [m for m in comp.members(st) if st.weeks_lived >= int(m.get("until", 0) or 0)]
    for m in gone:
        lines.append(f"Контракт с {m.get('name', 'напарником')} закончился.")
    if gone:
        comp.save(st, comp.active_members(st))
    res = await session.execute(select(CityNpc).where(CityNpc.alive.is_(True)))
    rows = list(res.scalars())
    # the vendetta
    for npc in rows:
        av = (npc.data or {}).get("avenge")
        if not isinstance(av, dict) or int(av.get("char", 0) or 0) != ch.id or city.hurt(npc):
            continue
        if st.flags.get(FLAG_PENDING_COMBAT) or st.flags.get(FLAG_COMBAT) or rng.random() >= c.vendetta_chance:
            continue
        arch = ce.archetype(content, npc.archetype)
        if arch is None:
            continue
        data = dict(npc.data or {})
        data.pop("avenge", None)
        npc.data = data
        city.remember(content, npc, ch.id, f"{st.name}: убийца {av.get('for', '')}", -100)
        st.flags[FLAG_PENDING_COMBAT] = {"src": "city", "npc": npc.id, "verb": "kill",
                                         "faction": city.fight_faction(content, ch, arch, year),
                                         "danger": max(2, arch.danger)}
        lines.append(f"🩸 {npc.name} ({arch.name}) выслеживает тебя: месть за смерть родного человека, "
                     f"{av.get('for', '')}. С наёмниками, прямо на улице.")
        break
    # grudges and gratitude: one of each at most
    hate = [n for n in rows if city.att(n, ch.id) <= c.grudge_att and not city.hurt(n)]
    love = [n for n in rows if city.att(n, ch.id) >= c.grateful_att and not city.hurt(n)]
    if hate and rng.random() < c.grudge_chance:
        n = rng.choice(hate)
        if rng.random() < 0.5:
            apply_effects(st, {"heat": 1}, content, rng, year)
            lines.append(f"{n.name} помнит обиду и пишет на тебя донос в NCPD. Розыск +1.")
        else:
            apply_effects(st, {"mood": -2}, content, rng, year)
            lines.append(f"{n.name} рассказывает про тебя гадости всему району. Настроение −2.")
    if love and rng.random() < c.grateful_chance:
        n = rng.choice(love)
        if rng.random() < 0.6:
            gift = rng.randint(*c.gift)
            apply_effects(st, {"eddies": gift}, content, rng, year)
            lines.append(f"{n.name} передаёт тебе конверт: «за всё хорошее». +{gift} эдди.")
        else:
            apply_effects(st, {"mood": 3}, content, rng, year)
            lines.append(f"{n.name} находит тебя просто поболтать. Хоть кто-то в этом городе рад тебя видеть. Настроение +3.")
    await session.flush()
    return lines
