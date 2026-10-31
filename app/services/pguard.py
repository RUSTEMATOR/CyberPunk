"""Players hired as bodyguards (owner request 25.10: «нанимать юзеров в охрану, и они автоматически присоединяются в бой»).

world_state `pguards` = {employer tg: {guard tg: {char, name, wage, since}}}.
- the employer offers a contract with a wage a month (PlayerMail `offer`, payload offer "guard");
- the guard accepts in the offers; from then the guard's character stands at the employer's side in every fight
  (not a solo challenge), as long as the guard is alive, free, not in a fight of their own and in the same city;
- the employer pays the wage at every press of their own, by mail to the guard; without the money the contract ends;
- either side ends it at any time. The guard's own health and ammo are not spent (as with a called helper).
"""

from __future__ import annotations

import random
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import kyiv_date
from app.content.loader import Content
from app.db import repo
from app.db.models import Character, PlayerMail
from app.engine import combat as cb
from app.engine import jail as jail_eng
from app.engine.effects import apply_effects
from app.engine.state import FLAG_COMBAT
from app.services.game import ActiveCharacter

KEY = "pguards"
OFFER = "guard"


def cfg(content: Content):  # noqa: ANN201
    return content.balance.players.guards


async def book(session: AsyncSession) -> dict[str, dict[str, dict]]:
    raw = await repo.ws_get(session, KEY, {}) or {}
    return {str(k): {str(g): dict(v) for g, v in (gs or {}).items()} for k, gs in raw.items()}


async def _save(session: AsyncSession, b: dict) -> None:
    await repo.ws_set(session, KEY, {k: v for k, v in b.items() if v})


def _m(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def _name(ch: ActiveCharacter) -> str:
    return f"{ch.state.name} {ch.state.surname}".strip()


def _mail(session: AsyncSession, tg: int, text: str, eddies: int = 0, sender: str = "Охрана") -> None:
    payload: dict = {"text": text}
    if eddies:
        payload["eddies"] = eddies
    session.add(PlayerMail(to_tg_id=int(tg), from_tg_id=0, from_name=sender, kind="city", payload=payload))


async def guards_of(session: AsyncSession, tg: int) -> dict[str, dict]:
    return (await book(session)).get(str(tg), {})


async def guarding(session: AsyncSession, tg: int) -> list[tuple[str, dict]]:
    """Whom this player guards: [(employer tg, entry)]."""
    return [(emp, gs[str(tg)]) for emp, gs in (await book(session)).items() if str(tg) in gs]


async def candidates(session: AsyncSession, ch: ActiveCharacter) -> list[tuple[int, str]]:
    have = await guards_of(session, ch.player_tg_id)
    out: list[tuple[int, str]] = []
    for row in await repo.living_characters(session):
        if row.player_tg_id == ch.player_tg_id or str(row.player_tg_id) in have or (row.flags or {}).get("ai_mode"):
            continue
        out.append((row.id, f"{row.name} {row.surname or ''}".strip()))
    return out


async def offer(session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, char_id: int, wage: int) -> str:
    """Returns a line for the employer's screen."""
    from app.services import players as pl_svc

    c = cfg(content)
    if not c.wage_min <= wage <= c.wage_max:
        return f"Ставка — от {c.wage_min} до {c.wage_max} эдди в месяц."
    row = await session.get(Character, char_id)
    if row is None or not row.alive or row.player_tg_id == ch.player_tg_id:
        return "Этого человека больше нет."
    from app.engine import place

    if place.of_district(content, row.district) != place.place_of(ch.state, content):  # owner request 30.10
        return place.away_line(content, row.name, place.of_district(content, row.district))
    have = await guards_of(session, ch.player_tg_id)
    if str(row.player_tg_id) in have:
        return "Уже в твоей охране."
    if len(have) >= c.max_guards:
        return f"Охраны уже {c.max_guards}: больше не прокормить."
    if await pl_svc._pending_offer(session, ch.player_tg_id, row.player_tg_id, OFFER) is not None:  # noqa: SLF001
        return "Предложение уже ждёт ответа."
    session.add(PlayerMail(to_tg_id=row.player_tg_id, from_tg_id=ch.player_tg_id, from_char_id=ch.id, to_char_id=row.id,
                           from_name=_name(ch), from_year=year, kind="offer", payload={"offer": OFFER, "wage": wage}))
    await session.flush()
    return f"Предложение ушло: {row.name}, {_m(wage)} эдди в месяц. Ответ придёт, когда игрок откроет окно."


async def answer(session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, mail_id: int, accept: bool) -> str:
    from app.services import players as pl_svc

    m = await session.get(PlayerMail, mail_id)
    if m is None or m.to_tg_id != ch.player_tg_id or (m.payload or {}).get("offer") != OFFER or m.delivered_at is not None:
        return "Это предложение уже неактуально."
    m.delivered_at = pl_svc._utcnow()  # noqa: SLF001
    if not accept:
        m.payload = {**(m.payload or {}), "declined": True}
        _mail(session, m.from_tg_id, f"{_name(ch)} отказывается идти к тебе в охрану.")
        await session.flush()
        return "Ты отказываешься."
    from app.engine import place

    boss_row = await session.get(Character, m.from_char_id) if m.from_char_id else None
    if boss_row is not None and place.of_district(content, boss_row.district) != place.place_of(ch.state, content):
        m.delivered_at = None  # the offer waits: it can be taken once both are in one city
        await session.flush()
        return place.away_line(content, m.from_name, place.of_district(content, boss_row.district))
    b = await book(session)
    emp = b.setdefault(str(m.from_tg_id), {})
    if len(emp) >= cfg(content).max_guards and str(ch.player_tg_id) not in emp:
        await session.flush()
        return "Место уже занято."
    wage = int((m.payload or {}).get("wage", 0) or 0)
    emp[str(ch.player_tg_id)] = {"char": ch.id, "name": _name(ch), "wage": wage, "since": kyiv_date("Europe/Kyiv"),
                                 "boss": m.from_name}
    await _save(session, b)
    _mail(session, m.from_tg_id, f"{_name(ch)} теперь в твоей охране: встанет рядом в каждом бою.")
    await session.flush()
    return (f"Ты в охране у {m.from_name}: {_m(wage)} эдди в месяц, письмом. Когда на {m.from_name} нападают, "
            "ты рядом в бою. Твоё здоровье и патроны не тратятся.")


async def fire(session: AsyncSession, employer_tg: int, guard_tg: int, by_guard: bool) -> str:
    b = await book(session)
    e = b.get(str(employer_tg), {})
    entry = e.pop(str(guard_tg), None)
    if entry is None:
        return "Контракта уже нет."
    await _save(session, b)
    if by_guard:
        _mail(session, employer_tg, f"{entry.get('name', 'Охранник')} уходит из твоей охраны.")
        return f"Ты больше не охраняешь {entry.get('boss') or 'нанимателя'}."
    _mail(session, guard_tg, "Наниматель расторгает контракт охраны.")
    return f"{entry.get('name', 'Охранник')} больше не в твоей охране."


def _same_city(content: Content, a: str, b: str) -> bool:
    da, db_ = content.districts.get(a), content.districts.get(b)
    return da is not None and db_ is not None and da.city == db_.city


async def for_fight(session: AsyncSession, content: Content, ch: ActiveCharacter) -> list[ActiveCharacter]:
    """The guards who can stand in the employer's fight now."""
    from app.services import game

    out: list[ActiveCharacter] = []
    b = await book(session)
    changed = False
    for gtg, entry in list(b.get(str(ch.player_tg_id), {}).items()):
        g = await game.get_active_character(session, int(gtg))
        if g is None or g.id != int(entry.get("char", 0)):  # the guard's character died or changed: the contract ends
            b[str(ch.player_tg_id)].pop(gtg, None)
            changed = True
            continue
        st = g.state
        if jail_eng.in_jail(st) or st.flags.get(FLAG_COMBAT) or st.hp <= 0 or not _same_city(content, st.district, ch.state.district):
            continue
        out.append(g)
        if len(out) >= cfg(content).max_guards:
            break
    if changed:
        await _save(session, b)
    return out


def join(content: Content, cs: cb.CombatState, guards: list[ActiveCharacter]) -> list[str]:
    names: list[str] = []
    for g in guards:
        c = cb.build_player(g.state, content)
        idx = len(cs.allies)
        c.id, c.side, c.ai, c.name = f"a{idx}", "ally", "", f"🛡 {g.state.name} {g.state.surname}".strip()
        cs.allies.append(c)
        from app.services import look as look_svc

        cs.ctx.setdefault("looks", {})[c.id] = look_svc.snapshot(content, g)  # owner request 26.10: the figure in the fight
        names.append(f"{g.state.name} {g.state.surname}".strip())
    if names:
        cs.ctx["guards"] = names
        for n in names:
            cb._emit(cs, k="reinf", name=f"охрана, {n}", n=1)  # noqa: SLF001
    return names


async def notify(session: AsyncSession, ch: ActiveCharacter, guards: list[ActiveCharacter], foe: str) -> None:
    """One letter a day to each guard: whom they covered."""
    today = kyiv_date("Europe/Kyiv")
    notes = dict(await repo.ws_get(session, "pguard_note", {}) or {})
    for g in guards:
        if notes.get(str(g.player_tg_id)) == today:
            continue
        notes[str(g.player_tg_id)] = today
        _mail(session, g.player_tg_id, f"🛡 На {_name(ch)} напали ({foe}). Ты в бою рядом, как и положено охране.")
    await repo.ws_set(session, "pguard_note", notes)


async def pay(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int) -> list[str]:
    """At the employer's press: the wages go out by mail; without the money the contract ends."""
    b = await book(session)
    emp = b.get(str(ch.player_tg_id), {})
    if not emp:
        return []
    lines: list[str] = []
    for gtg, entry in list(emp.items()):
        wage = int(entry.get("wage", 0) or 0)
        if ch.state.eddies < wage:
            emp.pop(gtg, None)
            _mail(session, int(gtg), f"{_name(ch)} не может заплатить охране. Контракт окончен.")
            lines.append(f"🛡 Нечем платить: {entry.get('name')} уходит из охраны.")
            continue
        apply_effects(ch.state, {"eddies": -wage}, content, rng, year)
        _mail(session, int(gtg), f"Зарплата охраны от {_name(ch)}.", eddies=wage)
    if emp and not lines:
        lines.append(f"🛡 Охрана: {', '.join(str(e.get('name')) for e in emp.values())} — "
                     f"{sum(int(e.get('wage', 0) or 0) for e in emp.values())} эдди в месяц.")
    await _save(session, b)
    return lines


def since_text(entry: dict) -> str:
    s = str(entry.get("since", ""))
    try:
        return datetime.fromisoformat(s).strftime("%d.%m")
    except ValueError:
        return s
