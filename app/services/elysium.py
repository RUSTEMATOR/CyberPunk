"""Elysium, the station of the rich (owner request: the «Elysium» crossover). The service side.

Citizenship for a fortune or a data heist; a smuggler's shuttle for those without; the Med-Bay that cures everything;
the plant on the ground that pays and may give a lethal dose; the smuggler's exoskeleton.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.db import repo
from app.engine import checks, derived
from app.engine import elysium as ee
from app.engine.effects import AppliedEffects, apply_effects
from app.services import ap as ap_services
from app.services.game import ActiveCharacter, save_active

R_HAVE, R_EDDIES, R_AP, R_JAIL, R_NOT, R_COOL, R_OWN = "have", "eddies", "ap", "jail", "not", "cool", "own"


@dataclass
class ElysiumResult:
    blocked: str | None = None
    ok: bool = True
    check: object | None = None
    text: str = ""
    lines: list[str] = field(default_factory=list)
    applied: AppliedEffects | None = None
    died: bool = False
    death_cause: str | None = None


def _jailed(ch: ActiveCharacter) -> bool:
    return int(ch.state.flags.get("jail_weeks", 0) or 0) > 0


async def _follow(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
                  res: ElysiumResult) -> bool:
    """Followups of the applied effects; True when the character died."""
    from app.services import followups

    if res.applied is None:
        return False
    fu = await followups.handle(session, content, ch, res.applied.followups, rng, year, tz)
    res.lines.extend(fu.lines)
    res.died, res.death_cause = fu.died, fu.cause
    return fu.died


def _board(content: Content, ch: ActiveCharacter) -> None:
    """Owner request 30.10: on board means in the station's district (a trip: the home on the ground stays)."""
    from app.engine import place
    from app.engine import property as prop

    if place.place_of(ch.state, content) == "elysium" and ch.state.district != ee.STATION and ee.STATION in content.districts:
        prop.trip_to(ch.state, ee.STATION)


async def buy_citizenship(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                          tz: str) -> ElysiumResult:
    from app.services import loud

    c = ee.cfg(content)
    if ee.citizen(ch.state):
        return ElysiumResult(blocked=R_HAVE)
    if ch.state.eddies < c.citizen_price:
        return ElysiumResult(blocked=R_EDDIES)
    res = ElysiumResult()
    res.applied = apply_effects(ch.state, {"eddies": -c.citizen_price, "status": 3}, content, rng, year)
    ee.set_rec(ch.state, citizen=True)
    _board(content, ch)
    await save_active(session, ch, tz)
    await loud.post(session, ch.id, f"{loud.who(ch.state.name, ch.state.surname)} получает гражданство Элизиума и "
                                    f"улетает жить над облаками.")
    await repo.log_event(session, "elysium_citizen", {"how": "bought"}, character_id=ch.id)
    return res


async def shuttle(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                  tz: str) -> ElysiumResult:
    """A smuggler's shuttle: REF + driving. Success: on board until the next press. Failure: shot down or caught."""
    c = ee.cfg(content)
    if ee.on_board(ch.state):
        return ElysiumResult(blocked=R_HAVE)
    if _jailed(ch):
        return ElysiumResult(blocked=R_JAIL)
    if ch.state.eddies < c.shuttle_price:
        return ElysiumResult(blocked=R_EDDIES)
    chk = checks.roll_check(ch.state, content, rng, "REF", "driving", c.shuttle_dv, ("escape",))
    res = ElysiumResult(ok=chk.ok, check=chk)
    fx: dict = {"eddies": -c.shuttle_price}
    if chk.ok:
        ee.set_rec(ch.state, pass_until=ch.state.weeks_lived, pass_from=ch.state.weeks_lived)
        _board(content, ch)
        res.text = ("Шаттл без опознавательных знаков ныряет под щит станции. Трава, бассейны, тишина. "
                    "Ты здесь чужой, и у тебя есть время до конца месяца.")
    elif rng.random() < 0.5:
        fx["hp"] = -c.shuttle_crash_hp
        res.text = "Станция сбивает шаттл ракетой. Обломки падают в пустыню, ты выползаешь из них последним."
    else:
        fx["heat"] = c.shuttle_caught_heat
        res.text = "Дроны охраны сажают шаттл. Тебя выкидывают обратно на Землю, деньги контрабандиста не вернут."
    res.applied = apply_effects(ch.state, fx, content, rng, year)
    if await _follow(session, content, ch, rng, year, tz, res):
        return res
    await save_active(session, ch, tz)
    return res


async def medbay(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                 tz: str) -> ElysiumResult:
    """The Med-Bay: full health, the infection and the radiation gone. A citizen every few weeks, a visitor once."""
    c = ee.cfg(content)
    if not ee.on_board(ch.state):
        return ElysiumResult(blocked=R_NOT)
    if not ee.medbay_ready(ch.state, content):
        return ElysiumResult(blocked=R_COOL)
    fx: dict = {"hp": derived.max_hp(ch.state, content), "mood": c.medbay_mood}
    if c.medbay_humanity:
        fx["humanity"] = c.medbay_humanity
    if ch.state.flags.get("infection"):
        fx["flag_clear:infection"] = True
    if ch.state.flags.get("sick"):
        fx["flag_clear:sick"] = True
    res = ElysiumResult(text="Капсула закрывается, свет сканирует тело слой за слоем. Через минуту ты здоров, "
                             "как не был с детства.")
    if ee.radiation(ch.state):
        ch.state.flags.pop(ee.RAD, None)
        res.lines.append("Лучевая болезнь ушла. Ты будешь жить.")
    res.applied = apply_effects(ch.state, fx, content, rng, year)
    ee.set_rec(ch.state, medbay_at=ch.state.weeks_lived)
    await save_active(session, ch, tz)
    return res


async def plant(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                tz: str) -> ElysiumResult:
    """A shift at the robot plant that builds the station's droids: eddies, and a chance of a lethal dose."""
    c = ee.cfg(content)
    if _jailed(ch):
        return ElysiumResult(blocked=R_JAIL)
    if not ap_services.can_spend(ch, c.plant_ap):
        return ElysiumResult(blocked=R_AP)
    ap_services.spend(ch, content, c.plant_ap)
    chk = checks.roll_check(ch.state, content, rng, "BODY", None, c.plant_dv, ("career",))
    res = ElysiumResult(ok=chk.ok, check=chk)
    fx: dict = {}
    if chk.ok:
        fx["eddies"] = rng.randint(*c.plant_pay)
        res.text = "Конвейер, сварка, дроны для станции, которую ты видишь только на небе. Платят в конце смены."
    else:
        fx["hp"] = -5
        res.text = "Пресс заклинило, бригадир кричит, рука в ожоге. Смену не засчитали."
    if rng.random() < c.plant_rad_chance and not ee.radiation(ch.state):
        ch.state.flags[ee.RAD] = c.rad_weeks
        res.lines.append(f"☢️ Дверь камеры заело, тебя заперли внутри с источником. Смертельная доза. "
                         f"Жить осталось {c.rad_weeks} недель. Спасёт только медкапсула Элизиума.")
    res.applied = apply_effects(ch.state, fx, content, rng, year) if fx else None
    if await _follow(session, content, ch, rng, year, tz, res):
        return res
    await save_active(session, ch, tz)
    return res


async def exo(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
              tz: str) -> ElysiumResult:
    c = ee.cfg(content)
    if any(cw.key == c.exo_key for cw in ch.state.cyberware):
        return ElysiumResult(blocked=R_OWN)
    if ch.state.eddies < c.exo_price:
        return ElysiumResult(blocked=R_EDDIES)
    res = ElysiumResult(text="Подпольная мастерская, бетонный стол, дрель у виска. Утром ты встаёшь, и пол скрипит "
                             "под весом каркаса.")
    res.applied = apply_effects(ch.state, {"eddies": -c.exo_price, f"cyberware_grant:{c.exo_key}": True},
                                content, rng, year)
    if await _follow(session, content, ch, rng, year, tz, res):
        return res
    await save_active(session, ch, tz)
    return res


async def heist(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                tz: str) -> ElysiumResult:
    """Steal the citizenship data of a station executive: INT + netrunning. Success: a citizen for free."""
    from app.services import loud

    c = ee.cfg(content)
    if ee.citizen(ch.state):
        return ElysiumResult(blocked=R_HAVE)
    if _jailed(ch):
        return ElysiumResult(blocked=R_JAIL)
    if not ap_services.can_spend(ch, c.heist_ap):
        return ElysiumResult(blocked=R_AP)
    ap_services.spend(ch, content, c.heist_ap)
    chk = checks.roll_check(ch.state, content, rng, "INT", "netrunning", c.heist_dv, ("gig_hack",))
    res = ElysiumResult(ok=chk.ok, check=chk)
    if chk.ok:
        ee.set_rec(ch.state, citizen=True)
        res.text = ("Данные директора станции переписаны на тебя. Система Элизиума признаёт нового гражданина и "
                    "даже желает доброго утра.")
        res.applied = apply_effects(ch.state, {"status": 2, "street_cred": 3}, content, rng, year)
        await loud.post(session, ch.id, f"{loud.who(ch.state.name, ch.state.surname)} взламывает реестр Элизиума и "
                                        f"становится гражданином станции.")
    else:
        res.text = "Лёд реестра отвечает агентами на земле. Они находят тебя через двадцать минут."
        res.applied = apply_effects(ch.state, {"hp": -c.heist_fail_hp, "heat": c.heist_fail_heat}, content, rng, year)
    if await _follow(session, content, ch, rng, year, tz, res):
        return res
    await save_active(session, ch, tz)
    await repo.log_event(session, "elysium_heist", {"ok": chk.ok}, character_id=ch.id)
    return res


def press_lines(ch: ActiveCharacter, content: Content, rng: random.Random) -> list[str]:
    """After a press: a wanted citizen may be deported; a dose counts down."""
    st, c = ch.state, ee.cfg(content)
    out: list[str] = []
    if ee.citizen(st) and st.heat >= c.deport_heat and rng.random() < c.deport_chance:
        ee.set_rec(st, citizen=False)
        out.append("Элизиум аннулирует твоё гражданство: розыск на Земле слишком громкий. Шаттл обратно — в один конец.")
    if st.district == ee.STATION and not ee.on_board(st):  # owner request 30.10: the pass ran out, back to the ground
        from app.engine import property as prop

        if not prop.trip_home(st):
            prop.move_to(st, ee.GROUND)
        if st.district == ee.STATION:
            prop.move_to(st, ee.GROUND)
        out.append("Пропуск на станцию истёк. Охрана сажает тебя на шаттл до Лос-Анджелеса.")
    n = ee.radiation(st)
    if n:
        out.append(f"☢️ Лучевая болезнь: осталось {n} нед. Медкапсула Элизиума — единственный шанс.")
    return out
