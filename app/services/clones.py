"""The clone corp (owner request 26.10, docs/PLAN_EQUILIBRIUM.md part 3).

world_state `clonecorp` = {gen, status ("alive" | "gone"), since, back_at, expose}:
- `gen` picks the name: when one corp falls, after `return_days` another one like it takes its place — the demand
  does not go anywhere;
- `expose` (0..100): how much the city knows; at `expose_fall` the corp falls. Guard shifts push it down, leaks and
  storms push it up.
The services for the rich: grow a part of flesh back (ends an injury) and the clone insurance: at death, if the brain
is whole, Trauma Team moves it into the clone (`clone_save`, called by services/death.py before `kill`).
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
from app.engine import injury
from app.engine.effects import apply_effects
from app.services.game import ActiveCharacter

if TYPE_CHECKING:
    from app.bot.ui.kit import UiKit

KEY = "clonecorp"
FLAG_READY = "clone_ready"
FLAG_DAY = "clone_day"  # {"d": kyiv date, "acts": [action keys done today]}
NAMES = ("Арк Биосистемс", "Эдем Медикал", "Новый Ковчег", "Лазарь Генетикс", "Генезис Плюс")
SAVE_CAUSES = ("combat", "maxtac", "collector", "prison", "overdose", "implant_failure", "dgame", "wounds")


def cfg(content: Content):  # noqa: ANN201
    return content.balance.clones


def name_of(rec: dict[str, Any]) -> str:
    return NAMES[int(rec.get("gen", 0)) % len(NAMES)]


async def corp(session: AsyncSession) -> dict[str, Any]:
    rec = await repo.ws_get(session, KEY, None)
    if not isinstance(rec, dict):
        rec = {"gen": 0, "status": "alive", "since": time.time(), "expose": 0}
    return dict(rec)


async def _save(session: AsyncSession, rec: dict[str, Any]) -> None:
    await repo.ws_set(session, KEY, rec)


def alive(rec: dict[str, Any]) -> bool:
    return rec.get("status") == "alive"


async def push(session: AsyncSession, content: Content, n: int, ui: "UiKit | None" = None) -> dict[str, Any]:
    """Expose (+) or cover (−) the corp; at the top it falls."""
    rec = await corp(session)
    if not alive(rec):
        return rec
    rec["expose"] = max(0, min(100, int(rec.get("expose", 0)) + n))
    if rec["expose"] >= cfg(content).expose_fall:
        rec.update({"status": "gone", "back_at": time.time() + cfg(content).return_days * 86400, "expose": 0})
        if ui is not None:
            ui.post(f"🧬 <b>«{name_of(rec)}» больше нет.</b> Ковчег открыт, клоны выходят на улицы и щурятся на "
                    f"солнце, которого им не обещали. Те, кто заказывал себе запасные тела, остались без них.",
                    24 * 3600, "news")
        else:
            rec["post_fall"] = True
    await _save(session, rec)
    return rec


async def job(session: AsyncSession, content: Content, ui: "UiKit") -> None:
    """A new corp comes back after the old one falls; the falls of player actions are posted here."""
    rec = await corp(session)
    if rec.pop("post_fall", None):
        ui.post(f"🧬 <b>«{name_of(rec)}» больше нет.</b> Ковчег открыт, клоны выходят на улицы и щурятся на солнце, "
                f"которого им не обещали. Те, кто заказывал себе запасные тела, остались без них.", 24 * 3600, "news")
        await _save(session, rec)
    if not alive(rec) and time.time() >= float(rec.get("back_at", 0) or 0):
        rec = {"gen": int(rec.get("gen", 0)) + 1, "status": "alive", "since": time.time(), "expose": 0}
        await _save(session, rec)
        ui.post(f"🧬 <b>«{name_of(rec)}»</b> открывает клинику премиум-класса: «Ваше здоровье — наш приоритет. "
                f"Индивидуальные биологические решения». Спрос никуда не делся.", 24 * 3600, "news")


# -- the services for the rich --------------------------------------------------------------------------------------------


def _discount(content: Content, ch: ActiveCharacter) -> float:
    return 1 - cfg(content).sold_discount_pct / 100 if ch.state.flags.get("clone_sold") else 1.0


def part_price(content: Content, ch: ActiveCharacter) -> int:
    return int(cfg(content).grow_part * _discount(content, ch))


def insurance_price(content: Content, ch: ActiveCharacter) -> int:
    return int(cfg(content).insurance * _discount(content, ch))


async def grow(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
               part: str) -> str:
    rec = await corp(session)
    if not alive(rec):
        return "Корпорации клонов сейчас нет. Новая придёт: такие всегда приходят."
    if part not in injury.lost(ch.state):
        return "Эта часть тела на месте."
    price = part_price(content, ch)
    if ch.state.eddies < price:
        return "Не хватает эдди."
    apply_effects(ch.state, {"eddies": -price}, content, rng, year)
    injury.regrow(ch.state, part)
    return (f"Неделя в капсуле «{name_of(rec)}». {injury.part_name(part).capitalize()} снова своя, из твоей плоти. "
            f"Откуда взялась плоть, в договоре не сказано.")


async def insure(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int) -> str:
    rec = await corp(session)
    if not alive(rec):
        return "Корпорации клонов сейчас нет."
    if ch.state.flags.get(FLAG_READY):
        return "Твой клон уже растёт в Ковчеге."
    price = insurance_price(content, ch)
    if ch.state.eddies < price:
        return "Не хватает эдди."
    apply_effects(ch.state, {"eddies": -price}, content, rng, year)
    ch.state.flags[FLAG_READY] = int(rec.get("gen", 0))
    return (f"В «Ковчеге» «{name_of(rec)}» появляется ещё один житель. Ему расскажут про апокалипсис снаружи и "
            f"научат ждать лотерею. Если ты умрёшь с целым мозгом, Травма Тим перенесёт его в это тело.")


def clone_save(content: Content, ch: ActiveCharacter, rng: random.Random, year: int, cause: str,
               rec: dict[str, Any]) -> list[str] | None:
    """Before death: the brain into the clone, when there is a clone, the corp still stands and the brain is whole."""
    st = ch.state
    if FLAG_READY not in st.flags:
        return None
    gen = st.flags.pop(FLAG_READY)
    if not alive(rec) or int(rec.get("gen", 0)) != int(gen or 0):
        return ["🧬 Травма Тим едет в Ковчег за твоим клоном, но Ковчега больше нет. Страховка сгорела вместе с ним."]
    if cause not in SAVE_CAUSES or rng.random() >= cfg(content).brain_whole:
        return ["🧬 Клон ждёт в Ковчеге, но переносить нечего: мозг не уцелел."]
    from app.engine import derived

    fx: dict[str, Any] = {f"cyberware_lose:{i.key}": True for i in st.cyberware
                          if i.key in content.cyberware and content.cyberware[i.key].slot != "brain"}
    if fx:
        apply_effects(st, fx, content, rng, year)
    st.flags.pop(injury.FLAG, None)
    st.hp = max(1, derived.max_hp(st, content) // 2)
    return [f"🧬 Тело умирает, но Травма Тим успевает: мозг извлечён и через сутки просыпается в клоне из «Ковчега» "
            f"«{name_of(rec)}». Новое тело чистое, без хрома, кроме чипов в голове. Где-то в Ковчеге один номер "
            f"сегодня выиграл лотерею."]


# -- guard or storm ------------------------------------------------------------------------------------------------------


def _done(ch: ActiveCharacter, tz: str) -> list[str]:
    rec = ch.state.flags.get(FLAG_DAY) or {}
    return list(rec.get("acts") or []) if rec.get("d") == kyiv_date(tz) else []


def _mark(ch: ActiveCharacter, tz: str, act: str) -> None:
    ch.state.flags[FLAG_DAY] = {"d": kyiv_date(tz), "acts": _done(ch, tz) + [act]}


def can(ch: ActiveCharacter, tz: str, act: str) -> bool:
    return act not in _done(ch, tz)


def security(content: Content, year: int) -> str:
    from app.engine import combat as cb

    for f in ("biotechnica", "arasaka", "militech"):
        if f in content.corps and cb._faction_pool(content, f, year):  # noqa: SLF001
            return f
    return "militech"


async def storm(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                tz: str):  # noqa: ANN201 - a combat Step or a reason
    from app.services import ap as ap_services
    from app.services import combat

    c = cfg(content)
    rec = await corp(session)
    if not alive(rec):
        return "Ковчег уже открыт."
    if not can(ch, tz, "storm"):
        return "Сегодня штурм уже был."
    if not ap_services.can_spend(ch, c.storm_ap):
        return "Не хватает ОД."
    ap_services.spend(ch, content, c.storm_ap)
    _mark(ch, tz, "storm")
    from app.services.game import save_active

    await save_active(session, ch, tz)
    return await combat.begin(session, content, ch, rng, year, tz, faction=security(content, year), danger=c.storm_danger,
                              src={"src": "clones", "kind": "storm", "extra": c.storm_extra,
                                   "title": f"Штурм Ковчега «{name_of(rec)}»"})


async def guard(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                tz: str):  # noqa: ANN201
    from app.services import ap as ap_services
    from app.services import combat

    c = cfg(content)
    rec = await corp(session)
    if not alive(rec):
        return "Охранять нечего: Ковчег открыт."
    if not can(ch, tz, "guard"):
        return "Сегодня смена уже была."
    if not ap_services.can_spend(ch, c.guard_ap):
        return "Не хватает ОД."
    ap_services.spend(ch, content, c.guard_ap)
    _mark(ch, tz, "guard")
    from app.services.game import save_active

    await save_active(session, ch, tz)
    return await combat.begin(session, content, ch, rng, year, tz, faction="scavengers", danger=c.guard_danger,
                              src={"src": "clones", "kind": "guard",
                                   "title": f"Смена в охране Ковчега «{name_of(rec)}»"})


async def leak(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
               ui: "UiKit | None") -> str:
    from app.bot import views
    from app.services import ap as ap_services

    c = cfg(content)
    rec = await corp(session)
    if not alive(rec):
        return "Сливать уже нечего: все и так знают."
    if not can(ch, tz, "leak"):
        return "Сегодня ты уже сливаешь. Журналисты боятся частить."
    if not ap_services.can_spend(ch, 1):
        return "Не хватает ОД."
    ap_services.spend(ch, content, 1)
    _mark(ch, tz, "leak")
    res = checks.roll_check(ch.state, content, rng, "INT", "netrunning", c.leak_dv)
    if not res.ok:
        return f"{views.check_line(res)}\nЛёд корпорации оказался толще. Файлы не прошли."
    rec = await push(session, content, c.leak_expose, ui)
    return (f"{views.check_line(res)}\nФайлы о лотерее гуляют по Сети. Город знает больше: "
            + ("Ковчег пал." if not alive(rec) else f"{rec['expose']}/{c.expose_fall}."))


async def fight_over(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int,
                     tz: str, result: str, ctx: dict) -> list[str]:
    from app.engine import combat as cb
    from app.services import loud
    from app.services.game import save_active

    c = cfg(content)
    won = result == cb.WON
    if ctx.get("kind") == "storm":
        rec = await push(session, content, c.storm_expose if won else 5)
        if not won:
            return ["Охрана Ковчега отбилась. Но о штурме уже говорят."]
        apply_effects(ch.state, {"eddies": rng.randint(*c.storm_loot), "street_cred": 2, "fame": 1, "humanity": 1},
                      content, rng, year)
        await save_active(session, ch, tz)
        await loud.post(session, ch.id, f"Штурм Ковчега «{name_of(rec)}»: охрана выбита, клоны видят город.")
        return ["Двери Ковчега открыты. Клоны стоят в коридоре и не понимают, что снаружи нет апокалипсиса. "
                + ("Корпорация пала." if not alive(rec) else f"Город знает больше: {rec['expose']}/{c.expose_fall}.")]
    if not won:
        return ["Беглецы прорвались. Смена провалена, оплаты не будет."]
    await push(session, content, -c.guard_cover)
    pay = rng.randint(*c.guard_pay)
    apply_effects(ch.state, {"eddies": pay, "humanity": -1}, content, rng, year)
    await save_active(session, ch, tz)
    return [f"Смена окончена: беглецов вернули в Ковчег. Им скажут, что снаружи был кислотный дождь. Оплата {pay} эдди."]
