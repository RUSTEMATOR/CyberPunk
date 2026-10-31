"""The balance of the three forces (owner request 26.10, docs/PLAN_EQUILIBRIUM.md part 1). Rules: engine/equilib.py.

world_state:
- `agency` = {"v": strength 0..100, "d": the Kyiv day of the last decay};
- `equil_last` = {"tier", "dom", "weak"} — the last state posted to the group.
The gangs and the Order are the averages of the district meters (services/control.py, `gang` and `corp`).
`tick` (every 30 minutes and at the start) refreshes the live state every character feels; `press_hook` (every
«Прожить месяц») takes the tax and lets an implant fail; `riot` (the menu) may pull a character into a street fight.
"""

from __future__ import annotations

import random
import time
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import kyiv_date
from app.content.loader import Content
from app.db import repo
from app.engine import checks
from app.engine import equilib as eng
from app.engine.effects import apply_effects
from app.engine.state import FLAG_COMBAT, FLAG_PENDING_COMBAT
from app.services.game import ActiveCharacter

if TYPE_CHECKING:
    from app.bot.ui.kit import UiKit

AGENCY_KEY, LAST_KEY = "agency", "equil_last"
FLAG_DAY = "equil_day"     # {"d": kyiv date, "n": balance actions that day}
FLAG_RIOT = "riot_at"
TIER_POSTS = {
    0: "⚖️ <b>Равновесие</b>: банды, Порядок и Агентство «Полночь» держат друг друга. Город выдыхает: зарплаты и цены "
       "возвращаются.",
    1: "⚖️ <b>Напряжение</b>: {dom} тянут город на себя. Зарплаты чуть ниже, хром чуть дороже.",
    2: "⚠️ <b>Смута</b>: {dom} подминают город, {weak} не справляются. Зарплаты падают, хром дорожает, учёба идёт "
       "туго.",
    3: "🔥 <b>Хаос</b>: {dom} держат город за горло, {weak} почти исчезли. Всё дорожает, всё ломается, на улицах война.",
}


def cfg(content: Content):  # noqa: ANN201
    return content.balance.equilib


async def agency(session: AsyncSession, content: Content) -> int:
    rec = await repo.ws_get(session, AGENCY_KEY, None)
    if not isinstance(rec, dict):
        return cfg(content).agency_start
    return int(rec.get("v", cfg(content).agency_start))


async def add_agency(session: AsyncSession, content: Content, n: int) -> int:
    rec = dict(await repo.ws_get(session, AGENCY_KEY, {}) or {})
    v = max(0, min(cfg(content).agency_max, int(rec.get("v", cfg(content).agency_start)) + n))
    rec["v"] = v
    await repo.ws_set(session, AGENCY_KEY, rec)
    return v


async def state(session: AsyncSession, content: Content) -> dict[str, Any]:
    from app.services import control

    meters = await control.load(session, content)
    nc = [m for k, m in meters.items() if k in content.districts and content.districts[k].city == "nc"] or [{}]
    vals = {eng.GANG: sum(int(m.get("gang", 0)) for m in nc) / len(nc),
            eng.ORDER: sum(int(m.get("corp", 0)) for m in nc) / len(nc),
            eng.AGENCY: float(await agency(session, content))}
    sh = eng.shares(vals)
    return {"values": vals, "shares": sh, "tier": eng.tier_of(content, sh), "dom": eng.dominant(sh),
            "weak": eng.weakest(sh)}


async def tick(session: AsyncSession, content: Content, ui: "UiKit | None", tz: str) -> dict[str, Any]:
    """The Agency fades a little every day; the live state is refreshed; a change of tier is posted."""
    c = cfg(content)
    if not c.enabled:
        eng.set_live(content, 0, "", "")
        return {}
    rec = dict(await repo.ws_get(session, AGENCY_KEY, {}) or {})
    today = kyiv_date(tz)
    if rec.get("d") != today:
        if "d" in rec:
            rec["v"] = max(0, int(rec.get("v", c.agency_start)) - c.agency_decay_day)
        rec.setdefault("v", c.agency_start)
        rec["d"] = today
        await repo.ws_set(session, AGENCY_KEY, rec)
    st = await state(session, content)
    eng.set_live(content, st["tier"], st["dom"], st["weak"])
    last = await repo.ws_get(session, LAST_KEY, None)
    now = {"tier": st["tier"], "dom": st["dom"], "weak": st["weak"]}
    if last is None:
        await repo.ws_set(session, LAST_KEY, now)
    elif int(last.get("tier", 0)) != st["tier"]:
        await repo.ws_set(session, LAST_KEY, now)
        if ui is not None:
            ui.post(TIER_POSTS[min(3, st["tier"])].format(dom=eng.NAMES[st["dom"]], weak=eng.NAMES[st["weak"]]),
                    12 * 3600, "news")
    return st


def consequences(content: Content, st: dict[str, Any]) -> list[str]:
    """What the city feels now, for the window."""
    c = cfg(content)
    t = int(st.get("tier", 0))
    if t <= 0:
        return ["Город в равновесии: зарплаты, цены и учёба как обычно."]
    i = min(t, len(c.salary_pct)) - 1
    out = [f"• зарплаты {c.salary_pct[i]}%, оплата заказов {c.gig_pay_pct[i]}%, опыт {c.xp_pct[i]}%;",
           f"• хром у риперов ×{eng.chrome_mult_for(content, t):g};"]
    if t >= 2 and st.get("dom") == eng.GANG:
        out.append("• бунты: на улице могут втянуть в драку, которая тебе не нужна;")
    if t >= 2 and st.get("dom") == eng.ORDER:
        out.append(f"• налог Порядка: {c.tax_pct[min(t, 3)]}% наличных при каждом «Прожить месяц»;")
    if t >= 2 and st.get("weak") == eng.AGENCY:
        out.append("• риперов мобилизовали: обслуживать хром некому, имплант может отказать и отвалиться;")
    return out


# -- what the characters feel ---------------------------------------------------------------------------------------------


async def press_hook(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random,
                     year: int) -> list[str]:
    """Every «Прожить месяц»: the tax of the Order on top, the implant that fails without rippers."""
    from app.engine import injury

    c = cfg(content)
    live = eng.live()
    t = int(live.get("tier", 0))
    d = content.districts.get(ch.state.district)
    if not c.enabled or t < 2 or d is None or d.city != "nc":
        return []
    lines: list[str] = []
    if live.get("dom") == eng.ORDER and ch.state.eddies > 0:
        tax = ch.state.eddies * c.tax_pct[min(t, 3)] // 100
        if tax > 0:
            apply_effects(ch.state, {"eddies": -tax}, content, rng, year)
            lines.append(f"🏛 Порядок поднял налоги: −{tax} эдди.")
    if live.get("weak") == eng.AGENCY and ch.state.cyberware and rng.random() < c.fail_chance[min(t, 3)]:
        limbs = [p for p in injury.PARTS if injury.implants_in(ch.state, content, p)]
        if limbs:
            part, what, fx = injury.hit_part(ch.state, content, rng, crit_bonus=1.0, part=rng.choice(limbs))
            if fx:
                apply_effects(ch.state, fx, content, rng, year)
            lines.append(f"🦾 Риперов мобилизовали, хром никто не обслуживал. {what}")
        else:
            item = rng.choice(ch.state.cyberware)
            name = content.cyberware[item.key].name if item.key in content.cyberware else item.key
            apply_effects(ch.state, {f"cyberware_lose:{item.key}": True}, content, rng, year)
            lines.append(f"🦾 Риперов мобилизовали, хром никто не обслуживал: {name} отказал, его пришлось вырезать.")
    return lines


def riot(content: Content, ch: ActiveCharacter, rng: random.Random, year: int) -> str | None:
    """The menu: gangs on top in unrest or chaos — a street riot may pull the character into a fight."""
    c = cfg(content)
    live = eng.live()
    t = int(live.get("tier", 0))
    st = ch.state
    d = content.districts.get(st.district)
    if (t < 2 or live.get("dom") != eng.GANG or d is None or d.city != "nc" or st.flags.get("abroad")
            or st.flags.get(FLAG_PENDING_COMBAT) or st.flags.get(FLAG_COMBAT)
            or int(st.flags.get("jail_weeks", 0) or 0) > 0):
        return None
    if time.time() - float(st.flags.get(FLAG_RIOT, 0) or 0) < c.riot_cooldown_h * 3600:
        return None
    if rng.random() >= c.riot_chance[min(t, 3)]:
        return None
    from app.services import combat

    st.flags[FLAG_RIOT] = time.time()
    st.flags[FLAG_PENDING_COMBAT] = {"src": "riot", "faction": combat.default_faction(content, ch), "danger": t,
                                     "extra": t - 1, "title": "Бунт на улице"}
    return "🔥 Бунт: улица горит, и толпа с арматурой решила, что ты тоже враг."


# -- what the players do -----------------------------------------------------------------------------------------------------


def done_today(ch: ActiveCharacter, tz: str) -> int:
    rec = ch.state.flags.get(FLAG_DAY) or {}
    return int(rec.get("n", 0)) if rec.get("d") == kyiv_date(tz) else 0


def _blocked(content: Content, ch: ActiveCharacter, tz: str, ap: int) -> str | None:
    from app.engine import jail as jail_eng
    from app.services import ap as ap_services
    from app.services import events as ev_service

    if jail_eng.in_jail(ch.state):
        return "Из камеры город не уравновесить."
    if ev_service.has_pending(ch):
        return "Сначала разберись с тем, что уже ждёт."
    if done_today(ch, tz) >= cfg(content).per_day:
        return "На сегодня хватит: город не выравнивается за один день."
    if not ap_services.can_spend(ch, ap):
        return "Не хватает ОД."
    return None


def _count(ch: ActiveCharacter, tz: str) -> None:
    ch.state.flags[FLAG_DAY] = {"d": kyiv_date(tz), "n": done_today(ch, tz) + 1}


async def case(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
               tz: str) -> str:
    """A case of the Midnight Agency: a missing person, a forged chip, a quiet murder. The Agency grows."""
    from app.bot import views
    from app.services import ap as ap_services

    c = cfg(content)
    why = _blocked(content, ch, tz, c.case_ap)
    if why:
        return why
    ap_services.spend(ch, content, c.case_ap)
    _count(ch, tz)
    res = checks.roll_check(ch.state, content, rng, "INT", "persuasion", c.case_dv)
    line = views.check_line(res)
    if res.ok:
        pay = rng.randint(*c.case_pay)
        apply_effects(ch.state, {"eddies": pay, "street_cred": 1}, content, rng, year)
        v = await add_agency(session, content, c.case_gain)
        await tick(session, content, None, tz)
        return (f"{line}\nДело закрыто: пропавший курьер нашёлся, а с ним и тот, кто его прятал. Агентство платит "
                f"{pay} эдди. Сила Агентства {v}.")
    v = await add_agency(session, content, 1)
    return f"{line}\nСлед обрывается. Агентство всё равно засчитывает работу: сила {v}."


async def mediate(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                  tz: str) -> str:
    """Talks between the forces: the strongest one gives a little ground everywhere."""
    from app.bot import views
    from app.services import ap as ap_services
    from app.services import control

    c = cfg(content)
    why = _blocked(content, ch, tz, c.mediate_ap)
    if why:
        return why
    st0 = await state(session, content)
    dom = st0["dom"]
    ap_services.spend(ch, content, c.mediate_ap)
    _count(ch, tz)
    res = checks.roll_check(ch.state, content, rng, "COOL", "persuasion", c.mediate_dv)
    line = views.check_line(res)
    if not res.ok:
        return f"{line}\n{eng.NAMES[dom]} не слышат. Переговоры сорваны, никто не уступил."
    if dom == eng.AGENCY:
        await add_agency(session, content, -c.mediate_shift * 2)
    else:
        for k, d in content.districts.items():
            if d.city == "nc":
                await control.adjust(session, content, k, **({"gang": -c.mediate_shift} if dom == eng.GANG
                                                             else {"corp": -c.mediate_shift}))
    apply_effects(ch.state, {"street_cred": 1, "fame": 1}, content, rng, year)
    st = await tick(session, content, None, tz)
    return (f"{line}\n{eng.NAMES[dom]} уступают понемногу во всех районах. Город: "
            f"{eng.TIER_NAMES[min(3, st.get('tier', 0))]}.")
