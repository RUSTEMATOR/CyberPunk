"""The world beyond Night City (owner request 23.10). Flights, life abroad, the local job and deeds, deportation.

A legal flight costs the ticket and the visa (no visa for the country of birth, flag `birth_country`) and needs the
wanted level at or below the country's `max_heat`; above it only the smuggler flies (COOL + stealth). A flight costs
1 AP. Living abroad: the country's mods, the weekly cost every press; no money means deportation home.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.engine import abroad as ae
from app.engine import checks
from app.engine.effects import AppliedEffects, apply_effects
from app.engine.psychosis import is_fugitive
from app.engine.state import FLAG_COMBAT, FLAG_PENDING_COMBAT
from app.services import ap as ap_services
from app.services.game import ActiveCharacter, save_active

SMUGGLE_DV = 14
FLIGHT_AP = 1


@dataclass
class AbroadResult:
    blocked: str | None = None
    ok: bool = True
    check: object | None = None
    text: str = ""
    lines: list[str] = field(default_factory=list)
    applied: AppliedEffects | None = None
    event: str = ""  # owner request 25.10: a step of the country's story was queued


def _common_block(ch: ActiveCharacter) -> str | None:
    st = ch.state
    if int(st.flags.get("jail_weeks", 0) or 0) > 0:
        return "jail"
    if is_fugitive(st):
        return "fugitive"
    if st.flags.get(FLAG_PENDING_COMBAT) or st.flags.get(FLAG_COMBAT):
        return "fight"
    return None


def ticket(ch: ActiveCharacter, content: Content, key: str) -> int:
    c = content.countries[key]
    visa = 0 if ch.state.flags.get("birth_country") == key else c.visa
    return c.flight + visa


async def fly(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
              key: str, smuggler: bool = False) -> AbroadResult:
    c = content.available("countries", year).get(key)
    if c is None:
        return AbroadResult(blocked="gone")
    if ae.where(ch.state) == key:
        return AbroadResult(blocked="here")
    why = _common_block(ch)
    if why:
        return AbroadResult(blocked=why)
    st = ch.state
    if not smuggler and st.heat > c.max_heat:
        return AbroadResult(blocked="airport")
    price = c.smuggle_price if smuggler else ticket(ch, content, key)
    if st.eddies < price:
        return AbroadResult(blocked="eddies")
    if not ap_services.can_spend(ch, FLIGHT_AP):
        return AbroadResult(blocked="ap")
    ap_services.spend(ch, content, FLIGHT_AP)
    res = AbroadResult()
    if smuggler:
        chk = checks.roll_check(st, content, rng, "COOL", "stealth", SMUGGLE_DV, ("escape",))
        res.check, res.ok = chk, chk.ok
        if not chk.ok:
            res.text = ("Контейнер вскрывают на таможне. Деньги контрабандиста пропали, тебя возвращают туда, "
                        "откуда вылетел контейнер, с новой строчкой в деле.")
            res.applied = apply_effects(st, {"eddies": -price, "heat": 1}, content, rng, year)
            await save_active(session, ch, tz)
            return res
        res.text = f"Двое суток в контейнере с бананами — и ты в городе {c.city}. Новая страна, новые правила."
    else:
        res.text = f"Самолёт садится в городе {c.city}. Паспортный контроль, штамп, новый воздух. {c.name}."
    ae.settle(st, content, key)
    res.applied = apply_effects(st, {"eddies": -price}, content, rng, year)
    await save_active(session, ch, tz)
    return res


async def go_home(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
                  deport: bool = False) -> AbroadResult:
    c = ae.country(ch.state, content)
    if c is None:
        return AbroadResult(blocked="home")
    why = _common_block(ch)
    if why:
        return AbroadResult(blocked=why)
    st = ch.state
    if deport:
        ae.settle(st, content, None)
        res = AbroadResult(text="Ты сдаёшься миграционной службе. Наручники, рейс эконом-классом, Найт-Сити. Бесплатно, "
                                "но запомнят.")
        res.applied = apply_effects(st, {"heat": 1, "mood": -3}, content, rng, year)
    else:
        if st.eddies < c.flight:
            return AbroadResult(blocked="eddies")
        ae.settle(st, content, None)
        res = AbroadResult(text="Самолёт заходит на посадку над заливом. Смог, неон, сирены. Найт-Сити. Дома.")
        res.applied = apply_effects(st, {"eddies": -c.flight}, content, rng, year)
    await save_active(session, ch, tz)
    return res


async def work(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
               tz: str) -> AbroadResult:
    c = ae.country(ch.state, content)
    if c is None:
        return AbroadResult(blocked="home")
    why = _common_block(ch)
    if why:
        return AbroadResult(blocked=why)
    w = c.work
    if not ap_services.can_spend(ch, w.ap):
        return AbroadResult(blocked="ap")
    ap_services.spend(ch, content, w.ap)
    chk = checks.roll_check(ch.state, content, rng, w.stat, w.skill, w.dv, ("career",))
    res = AbroadResult(ok=chk.ok, check=chk)
    if chk.ok:
        fx: dict = {"eddies": rng.randint(*w.pay)}
        if w.skill:
            fx[f"skill_xp:{w.skill}"] = 1
        res.text = w.text_ok
    else:
        fx = dict(w.fail_effects)
        res.text = w.text_fail
    res.applied = apply_effects(ch.state, fx, content, rng, year) if fx else None
    await save_active(session, ch, tz)
    return res


async def deed(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
               key: str) -> AbroadResult:
    c = ae.country(ch.state, content)
    if c is None:
        return AbroadResult(blocked="home")
    d = next((x for x in c.deeds if x.key == key), None)
    if d is None:
        return AbroadResult(blocked="gone")
    why = _common_block(ch)
    if why:
        return AbroadResult(blocked=why)
    if ch.state.eddies < d.cost:
        return AbroadResult(blocked="eddies")
    if not ap_services.can_spend(ch, d.ap):
        return AbroadResult(blocked="ap")
    ap_services.spend(ch, content, d.ap)
    res = AbroadResult()
    out = d.success
    if d.check is not None:
        chk = checks.roll_check(ch.state, content, rng, d.check.stat, d.check.skill, d.check.dv)
        res.check, res.ok = chk, chk.ok
        if not chk.ok and d.fail is not None:
            out = d.fail
    fx = dict(out.effects)
    if d.cost:
        fx["eddies"] = int(fx.get("eddies", 0)) - d.cost
    res.text = out.text
    res.applied = apply_effects(ch.state, fx, content, rng, year) if fx else None
    await save_active(session, ch, tz)
    return res


def press_lines(ch: ActiveCharacter, content: Content, rng: random.Random, year: int) -> list[str]:
    """After a press abroad: the cost of living, or deportation when there is nothing to pay with."""
    c = ae.country(ch.state, content)
    if c is None:
        return []
    st = ch.state
    if ae.has_local_home(st, content):  # owner request 25.10: a rented home of the city replaces the hotel
        return []
    cost = c.weekly_cost * content.balance.time.weeks_per_press
    if st.eddies >= cost:
        if cost:
            apply_effects(st, {"eddies": -cost}, content, rng, year)
        return [f"✈️ {c.name}, {c.city}: отель и жизнь за месяц обошлись в {cost} эдди. Своё жильё в городе дешевле: «Жильё»."]
    ae.settle(st, content, None)
    apply_effects(st, {"mood": -5, "heat": 1}, content, rng, year)
    return [f"✈️ Деньги кончились. Миграционная служба страны «{c.name}» сажает тебя на депортационный рейс. "
            "Найт-Сити встречает тебя без радости."]


# -- the story of the country (owner request 25.10) --------------------------------------------------------------------

STORY_DAY = "wstory_day"


def story_step(st, key: str) -> int:  # noqa: ANN001
    return int(st.flags.get(f"wstory:{key}", 0) or 0)


async def story_next(session: AsyncSession, content: Content, ch: ActiveCharacter, tz: str) -> AbroadResult:
    """The next step of the story of the country the character lives in: one a day."""
    from app.clock import kyiv_date
    from app.services import events as ev_service
    from app.services.week import pending_events, set_pending

    c = ae.country(ch.state, content)
    if c is None:
        return AbroadResult(blocked="home")
    why = _common_block(ch)
    if why:
        return AbroadResult(blocked=why)
    st = ch.state
    n = story_step(st, c.key)
    if n >= len(c.story):
        return AbroadResult(blocked="story_done")
    today = kyiv_date(tz)
    if st.flags.get(STORY_DAY) == today:
        return AbroadResult(blocked="story_day")
    if ev_service.has_pending(ch):
        return AbroadResult(blocked="busy")
    ev = c.story[n]
    if ev not in content.events:
        return AbroadResult(blocked="gone")
    st.flags[f"wstory:{c.key}"] = n + 1
    st.flags[STORY_DAY] = today
    set_pending(ch, [ev] + [e for e in pending_events(ch) if e != ev])
    await save_active(session, ch, tz)
    return AbroadResult(event=ev)
