"""Services of other players (owner request 24.10): a player ripper patches you up and puts chrome in at their price,
a player fixer gives work and takes a cut, a medic of Trauma Team, a cop, a journalist, a rocker, a mechanic,
a politician, a netrunner. The client pays, the provider gets the money (and path xp) by mail; no consent is needed:
whoever has the trade is open for business.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.db import repo
from app.db.models import PlayerMail
from app.engine import checks, derived
from app.engine import paths as pe
from app.engine.effects import AppliedEffects, apply_effects
from app.services import ap as ap_services
from app.services import game
from app.services.game import ActiveCharacter, save_active


@dataclass
class Offer:
    provider_id: int
    provider: str
    key: str
    name: str
    price: int
    text: str
    path: str = ""


@dataclass
class ServiceResult:
    blocked: str | None = None
    text: str = ""
    check: object | None = None
    applied: AppliedEffects | None = None
    lines: list[str] = field(default_factory=list)


def _offers_of(content: Content, who: ActiveCharacter) -> list[Offer]:
    st = who.state
    name = f"{st.name} {st.surname}".strip()
    out: list[Offer] = []
    p = pe.path_of(st, content)
    r = pe.rank(st, content)
    career = st.career.key if st.career else ""
    roles = derived.role_ranks(st, content)
    if p is not None and p.key == "ripper":
        out.append(Offer(who.id, name, "patch", "Подлатать", 100 + 100 * r,
                         f"здоровье +{15 * r}", "ripper"))
        if r >= 2:
            out.append(Offer(who.id, name, "chrome", "Хром по своей цене", 800 * r,
                             f"−{min(25, 5 * r)}% на импланты у любых рипперов на месяц", "ripper"))
    if career == "trauma_team":
        out.append(Offer(who.id, name, "medic", "Вызвать медика", 600, "здоровье полностью"))
    if p is not None and p.key == "fixer" and r >= 2:
        lo, hi = _job_pay(r)
        out.append(Offer(who.id, name, "job", "Работа от фиксера", 0, f"1 ОД, РЕФЛ + скрытность, {lo}–{hi} эдди, "
                                                                     "фиксеру 15%", "fixer"))
    if p is not None and p.key == "politician" and r >= 2:
        out.append(Offer(who.id, name, "fix", "Решить вопрос", 1500, "розыск −2", "politician"))
    if career == "ncpd":
        out.append(Offer(who.id, name, "cop", "Знакомый коп", 800, "розыск −1"))
    if career == "media":
        out.append(Offer(who.id, name, "press", "Статья о тебе", 1000, "слава +2"))
    if career == "rockerboy":
        out.append(Offer(who.id, name, "gig", "Концерт для своих", 300, "настроение +10"))
    if career == "mechanic":
        out.append(Offer(who.id, name, "garage", "Ремонт машины", 500, "машина как новая"))
    if roles.get("netrunner", 0) >= 2:
        out.append(Offer(who.id, name, "trace", "Почистить следы", 700, "розыск −1, след в Сети обнулён"))
    return out


def _job_pay(rank: int) -> tuple[int, int]:
    return {2: (600, 1200), 3: (1500, 3000), 4: (3000, 6000)}.get(rank, (6000, 12000))


async def offers(session: AsyncSession, content: Content, ch: ActiveCharacter) -> list[Offer]:
    out: list[Offer] = []
    for row in await repo.living_characters(session):
        if row.id == ch.id:
            continue
        other = await game.load_character(session, row.id)
        if other is not None:
            out.extend(_offers_of(content, other))
    return out


def _mail(session: AsyncSession, tg: int, char_id: int, text: str, eddies: int, path: str = "") -> None:
    payload: dict = {"text": text, "eddies": eddies}
    if path:
        payload.update({"path": path, "pxp": 1})
    session.add(PlayerMail(to_tg_id=int(tg), from_tg_id=0, from_name="Клиент", kind="city", to_char_id=char_id,
                           payload=payload))


async def use(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
              provider_id: int, key: str) -> ServiceResult:
    st = ch.state
    if int(st.flags.get("jail_weeks", 0) or 0) > 0:
        return ServiceResult("jail")
    other = await game.load_character(session, provider_id)
    if other is None or other.id == ch.id:
        return ServiceResult("gone")
    offer = next((o for o in _offers_of(content, other) if o.key == key), None)
    if offer is None:
        return ServiceResult("gone")
    if st.eddies < offer.price:
        return ServiceResult("eddies")
    me = st.name
    fx: dict = {"eddies": -offer.price} if offer.price else {}
    res = ServiceResult()
    fee = offer.price
    if key == "patch":
        r = pe.rank(other.state, content)
        fx["hp"] = 15 * r
        res.text = f"{offer.provider} штопает тебя на кухонном столе. Быстро, чисто, без вопросов."
    elif key == "medic":
        fx["hp"] = derived.max_hp(st, content)
        res.text = f"{offer.provider} приезжает с аптечкой Trauma Team. Через полчаса всё зажило."
    elif key == "chrome":
        r = pe.rank(other.state, content)
        st.flags["pl_ripper"] = {"pct": min(25, 5 * r), "until": st.weeks_lived + 4, "by": offer.provider}
        res.text = f"{offer.provider} берёт тебя в постоянные клиенты: месяц хром идёт по своей цене."
    elif key == "fix":
        fx["heat"] = -2
        res.text = f"{offer.provider} делает пару звонков. Твоё дело перекладывают в нижний ящик."
    elif key == "cop":
        fx["heat"] = -1
        res.text = f"{offer.provider} теряет одну бумагу из твоего дела. Случайно."
    elif key == "press":
        fx["fame"] = 2
        res.text = f"{offer.provider} пишет о тебе материал. Город читает."
    elif key == "gig":
        fx["mood"] = 10
        res.text = f"{offer.provider} играет для своих в подвале. Громко, честно, до хрипоты."
    elif key == "trace":
        fx["heat"] = -1
        st.flags.pop("net_trace", None)
        res.text = f"{offer.provider} проходит по логам и стирает тебя оттуда. Сеть тебя больше не помнит."
    elif key == "garage":
        v = st.flags.get("vehicle")
        if not isinstance(v, dict):
            return ServiceResult("novehicle")
        v = dict(v)
        v["dmg"] = 0
        st.flags["vehicle"] = v
        res.text = f"{offer.provider} лезет под капот и через час возвращает машину как с завода."
    elif key == "job":
        if not ap_services.can_spend(ch, 1):
            return ServiceResult("ap")
        ap_services.spend(ch, content, 1)
        r = pe.rank(other.state, content)
        chk = checks.roll_check(st, content, rng, "REF", "stealth", 11 + r, ("gig_quiet",))
        res.check = chk
        if chk.ok:
            pay = rng.randint(*_job_pay(r))
            fee = pay * 15 // 100
            fx["eddies"] = pay - fee
            fx["street_cred"] = 1
            res.text = f"Заказ от {offer.provider}: склад, ключ-карта, коробка. Всё чисто. Фиксер берёт 15%."
        else:
            fee = 0
            fx["hp"] = -6
            fx["heat"] = 1
            res.text = f"Заказ от {offer.provider} пошёл не так: охрана, бег по крышам, рваная куртка."
    else:
        return ServiceResult("gone")
    res.applied = apply_effects(st, {k: v for k, v in fx.items() if v}, content, rng, year) if fx else None
    if fee:
        _mail(session, other.player_tg_id, other.id, f"{me} пользуется твоей услугой «{offer.name}»: +{fee} эдди.", fee,
              offer.path)
    await save_active(session, ch, tz)
    return res
