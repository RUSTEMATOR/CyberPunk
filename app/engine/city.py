"""The city's people and places (owner request: freedom of action, docs/PLAN_FREEDOM.md, stage 2). Pure functions.

A place is a type (bar, market...) in a district; its venue name is fixed per district. A place holds NPCs in slots:
the fixed archetypes first, then `extra_count` drawn from the pool. A deed (verb) is a check against the NPC's dv; the
engine says what it costs and gives, the service applies it and keeps the NPC's fate.
"""

from __future__ import annotations

import random
import zlib
from dataclasses import dataclass, field

from app.content.loader import Content
from app.engine.state import CharacterState

ATT_MIN, ATT_MAX = -100, 100


def cfg(content: Content):  # noqa: ANN201
    return content.balance.freedom


def archetype(content: Content, key: str):  # noqa: ANN201
    return next((a for a in cfg(content).archetypes if a.key == key), None)


def place_type(content: Content, key: str):  # noqa: ANN201
    return next((p for p in cfg(content).places if p.key == key), None)


def verb(content: Content, key: str):  # noqa: ANN201
    return next((v for v in cfg(content).verbs if v.key == key), None)


def venue_name(district: str, place, content: Content | None = None, year: int | None = None) -> str:  # noqa: ANN001
    base = place.names[zlib.crc32(f"{district}:{place.key}".encode()) % len(place.names)]
    d = content.districts.get(district) if content is not None else None
    if d is None or d.city == "nc":
        return base
    # owner request 25.10: a city abroad: its police, its corps, its streets
    country = content.countries.get(d.city)
    spot = d.places[zlib.crc32(f"{district}:{place.key}:spot".encode()) % len(d.places)] if d.places else ""
    if place.key == "precinct":
        base = (country.police if country is not None and country.police else "Полицейский участок")
    elif place.key == "corp_lobby" and d.corps:
        # owner report 26.10: a corp the character's year does not know yet (Kang Tao before 2050) never shows up
        known = content.available("corps", year) if year is not None else \
            {k: v for k, v in content.corps.items() if v.available_from is None}
        k = next((k for k in d.corps if k in known), None)
        base = f"Лобби «{content.corps[k].name}»" if k else "Лобби корпорации"
    return f"{base}, {spot}" if spot else base


def kind_name(content: Content, district: str, place) -> str:  # noqa: ANN001
    """The kind of a place; abroad the NCPD is the local police."""
    d = content.districts.get(district)
    return "Участок полиции" if d is not None and d.city != "nc" and place.key == "precinct" else place.name


def slots(place, rng: random.Random) -> list[str]:  # noqa: ANN001
    """The archetypes of a place, slot by slot (a new district is filled with this)."""
    out = list(place.fixed)
    pool = list(place.extra)
    for _ in range(place.extra_count):
        if pool:
            out.append(rng.choice(pool))
    return out


def new_person(content: Content, arch, rng: random.Random) -> tuple[str, str, int]:  # noqa: ANN001
    """(name, gender m/f, money) of a new NPC."""
    first = rng.choice(list(content.given_names))
    gender = first.gender if first.gender in ("m", "f") else rng.choice(("m", "f"))
    surname = rng.choice(list(content.surnames)).form(gender)
    lo, hi = arch.money
    return f"{first.name} {surname}", gender, rng.randint(lo, max(lo, hi))


def allowed(v, arch) -> bool:  # noqa: ANN001
    if not v.only:
        return True
    tags = {arch.service} if arch.service else set()
    if arch.criminal:
        tags.add("criminal")
    tags.add("civilian" if arch.civilian else "armed")
    return bool(tags & set(v.only))


def attitude_word(att: int) -> str:
    if att >= 60:
        return "друг"
    if att >= 25:
        return "тепло"
    if att > -25:
        return "нейтрально"
    if att > -60:
        return "враждебно"
    return "ненавидит"


@dataclass
class Deed:
    ok: bool
    check: object | None = None       # CheckResult
    effects: dict = field(default_factory=dict)
    take: int = 0                     # eddies taken from the NPC
    att: int = 0
    fight: bool = False               # an armed NPC fights back: the service starts a real fight
    killed: bool = False
    hurt: bool = False


def resolve(state: CharacterState, content: Content, rng: random.Random, v, arch, crowd: int, npc_money: int,  # noqa: ANN001
            att: int) -> Deed:
    """One deed. An armed NPC under a violent deed fights at once; the check is skipped then."""
    from app.engine import checks

    if v.violent and arch.armed:
        return Deed(ok=False, fight=True, effects={"eddies": -v.price} if v.price else {}, att=v.att_fail)
    ok = True
    chk = None
    if v.stat is not None:
        social = 0 if v.violent else max(-3, min(3, att // 25))  # a friend is easier to talk round
        chk = checks.roll_check(state, content, rng, v.stat, v.skill, arch.dv + v.dv_add, ("social",) if not v.violent else ("combat",),
                                extra=social)
        ok = chk.ok
    fx: dict = dict(v.effects_ok if ok else v.effects_fail)
    if v.price:
        fx["eddies"] = int(fx.get("eddies", 0)) - v.price
    heat = (v.heat_ok if ok else v.heat_fail)
    if v.heat_per_crowd and (v.violent or not ok or v.key == "intimidate"):
        heat += v.heat_per_crowd * crowd
    if heat:
        fx["heat"] = int(fx.get("heat", 0)) + heat
    take = npc_money * v.take_pct // 100 if ok and v.take_pct else 0
    if take:
        fx["eddies"] = int(fx.get("eddies", 0)) + take
    d = Deed(ok=ok, check=chk, effects={k: x for k, x in fx.items() if x}, take=take, att=v.att_ok if ok else v.att_fail)
    d.fight = (not ok) and v.fight_on_fail and arch.armed
    d.killed = ok and v.kill
    d.hurt = ok and v.violent and not v.kill and not v.take_pct  # a beating puts them in hospital, a robbery does not
    return d
