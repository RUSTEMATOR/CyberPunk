"""Players hired by a player's megacorp (owner request 24.10).

The staff lives in the megacorp record: world_state `megacorps[owner tg]["staff"]` = {employee tg: {char, name, pos}}.
The employee's own flag `pcorp` = {owner, corp, pos} is a copy for the screens; only the employee's own session
writes it (sync on the employee's screens and press), the owner never touches another character.

- hiring is an offer (PlayerMail kind `offer`, payload offer `corp`); the employee accepts it in «Свои» → offers;
- the owner pays the salaries at the owner's own press (weekly x weeks_per_press); the money goes by mail;
- an employee takes corp jobs (2 AP, twice a month): a bonus for the employee, revenue for the corp by mail;
- each employee makes an attack on the tower harder; employees hide in the tower in the Purge.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.db import repo
from app.db.models import Character, PlayerMail
from app.engine import checks
from app.engine.effects import AppliedEffects, apply_effects
from app.services import ap as ap_services
from app.services.game import ActiveCharacter, save_active

OFFER = "corp"  # payload["offer"] of a hiring offer
FLAG = "pcorp"
FLAG_TASKS = "pcorp_tasks"  # {"w": weeks_lived at the last press, "n": jobs done since}

# job kind -> (stat, skill, check contexts, title)
TASKS: dict[str, tuple[str, str, tuple[str, ...], str]] = {
    "deal": ("COOL", "persuasion", ("career", "social"), "Переговоры"),
    "hack": ("INT", "netrunning", ("career", "gig_hack"), "Шпионаж"),
    "guard": ("REF", "firearms", ("career", "combat"), "Силовая работа"),
}


def cfg(content: Content):  # noqa: ANN201
    return content.balance.economy.megacorp


def posts(content: Content) -> list:  # noqa: ANN201
    return list(cfg(content).staff)


def post(content: Content, pos: int):  # noqa: ANN201
    ps = posts(content)
    return ps[max(0, min(int(pos), len(ps) - 1))] if ps else None


def staff_of(corp: dict | None) -> dict[str, dict]:
    return {str(k): dict(v) for k, v in ((corp or {}).get("staff") or {}).items()}


def staff_cap(content: Content, corp: dict) -> int:
    """Owner request 25.10: the new headquarters fits more players."""
    from app.services import bigmoney

    return cfg(content).staff_max + bigmoney.up_sum(content, corp, "staff")


def staff_dv(content: Content, corp: dict) -> int:
    c = cfg(content)
    return min(c.staff_dv_cap, len(staff_of(corp)) * c.staff_dv_per)


async def _corps(session: AsyncSession) -> dict[str, dict]:
    from app.services import bigmoney

    return await bigmoney.corps(session)


async def _save(session: AsyncSession, all_: dict[str, dict]) -> None:
    from app.services import bigmoney

    await repo.ws_set(session, bigmoney.CORPS_KEY, all_)


def employer_in(all_: dict[str, dict], tg: int) -> tuple[str, dict] | None:
    for owner, corp in all_.items():
        if str(tg) in staff_of(corp):
            return owner, corp
    return None


async def employer_of(session: AsyncSession, tg: int) -> tuple[str, dict] | None:
    return employer_in(await _corps(session), tg)


def _mail(session: AsyncSession, tg: int, text: str, eddies: int = 0) -> None:
    payload: dict = {"text": text}
    if eddies:
        payload["eddies"] = eddies
    session.add(PlayerMail(to_tg_id=int(tg), from_tg_id=0, from_name="Корпорация", kind="city", payload=payload))


@dataclass
class Result:
    blocked: str | None = None
    text: str = ""
    check: object | None = None
    applied: AppliedEffects | None = None
    lines: list[str] = field(default_factory=list)


# -- the employee's side ---------------------------------------------------------------------------------------------------


async def sync(session: AsyncSession, ch: ActiveCharacter) -> tuple[str, dict, dict] | None:
    """The employee's flag follows the megacorp record. Returns (owner tg, corp, staff entry) or None."""
    got = await employer_of(session, ch.player_tg_id)
    st = ch.state
    if got is None:
        st.flags.pop(FLAG, None)  # the employee already got a letter about it
        return None
    owner, corp = got
    me = staff_of(corp)[str(ch.player_tg_id)]
    st.flags[FLAG] = {"owner": owner, "corp": corp["name"], "pos": int(me.get("pos", 0))}
    return owner, corp, me


def tasks_left(content: Content, ch: ActiveCharacter) -> int:
    rec = ch.state.flags.get(FLAG_TASKS) or {}
    if int(rec.get("w", -1)) != ch.state.weeks_lived:
        return cfg(content).task_per_press
    return max(0, cfg(content).task_per_press - int(rec.get("n", 0)))


async def task(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random, year: int, tz: str,
               kind: str) -> Result:
    c = cfg(content)
    st = ch.state
    if kind not in TASKS:
        return Result("gone")
    if int(st.flags.get("jail_weeks", 0) or 0) > 0:
        return Result("jail")
    got = await sync(session, ch)
    if got is None:
        await save_active(session, ch, tz)
        return Result("nojob")
    owner, corp, me = got
    if tasks_left(content, ch) <= 0:
        return Result("tasks")
    if not ap_services.can_spend(ch, c.task_ap):
        return Result("ap")
    ap_services.spend(ch, content, c.task_ap)
    rec = st.flags.get(FLAG_TASKS) or {}
    n = int(rec.get("n", 0)) if int(rec.get("w", -1)) == st.weeks_lived else 0
    st.flags[FLAG_TASKS] = {"w": st.weeks_lived, "n": n + 1}
    pos = int(me.get("pos", 0))
    p = post(content, pos)
    stat, skill, ctxs, title = TASKS[kind]
    res = Result()
    res.check = checks.roll_check(st, content, rng, stat, skill, c.task_dv + pos * c.task_dv_per_pos, ctxs)
    if res.check.ok:  # type: ignore[attr-defined]
        bonus = (p.weekly if p else 0) * c.task_bonus_pct // 100
        revenue = rng.randint(*c.task_revenue) * (2 + pos) // 2
        res.applied = apply_effects(st, {"eddies": bonus, "status": 1}, content, rng, year)
        _mail(session, int(owner), f"{st.name} {st.surname}".strip() + f" закрывает задачу для «{corp['name']}» "
              f"({title.lower()}): выручка {revenue} эдди.", revenue)
        res.text = {
            "deal": "Переговорная, три часа и ни одной уступки. Контракт подписан, отдел празднует.",
            "hack": "Чужой сервер, чужие планы, теперь твои. Отчёт ложится на стол владельца.",
            "guard": "Склад конкурента, ночь, короткая перестрелка. Груз теперь принадлежит корпорации.",
        }[kind] + f" Премия: {bonus} эдди."
    else:
        fx = {"mood": -5}
        if kind == "guard":
            fx["hp"] = -c.task_fail_hp
        res.applied = apply_effects(st, fx, content, rng, year)
        res.text = {
            "deal": "Партнёры уходят из-за стола. Сделки нет, в отделе на тебя косятся.",
            "hack": "Чёрный лёд выкидывает тебя из сети. Голова гудит, данных нет.",
            "guard": "Охрана конкурента оказалась лучше. Уходишь с пустыми руками и в крови.",
        }[kind]
    await save_active(session, ch, tz)
    return res


async def quit_job(session: AsyncSession, ch: ActiveCharacter, tz: str) -> Result:
    all_ = await _corps(session)
    got = employer_in(all_, ch.player_tg_id)
    ch.state.flags.pop(FLAG, None)
    if got is None:
        await save_active(session, ch, tz)
        return Result("nojob")
    owner, corp = got
    staff = staff_of(corp)
    staff.pop(str(ch.player_tg_id), None)
    corp["staff"] = staff
    all_[owner] = corp
    await _save(session, all_)
    name = f"{ch.state.name} {ch.state.surname}".strip()
    _mail(session, int(owner), f"{name} увольняется из «{corp['name']}». Пропуск сдан на проходной.")
    await save_active(session, ch, tz)
    return Result(text=f"Пропуск «{corp['name']}» сдан. Ты свободен от корпорации.")


# -- the owner's side ------------------------------------------------------------------------------------------------------


async def candidates(session: AsyncSession, ch: ActiveCharacter) -> list[tuple[int, str, str]]:
    """Living characters of other players: (char id, name, where they work now)."""
    all_ = await _corps(session)
    out: list[tuple[int, str, str]] = []
    for row in await repo.living_characters(session):
        if row.player_tg_id == ch.player_tg_id or (row.flags or {}).get("ai_mode"):
            continue
        got = employer_in(all_, row.player_tg_id)
        where = got[1]["name"] if got else ""
        out.append((row.id, f"{row.name} {row.surname or ''}".strip(), where))
    return out


async def offer(session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, char_id: int,
                pos: int) -> Result:
    from app.services import players as pl_svc

    all_ = await _corps(session)
    corp = all_.get(str(ch.player_tg_id))
    if corp is None:
        return Result("nocorp")
    p = post(content, pos)
    if p is None or not 0 <= pos < len(posts(content)):
        return Result("gone")
    row = await session.get(Character, char_id)
    if row is None or not row.alive or row.player_tg_id == ch.player_tg_id:
        return Result("gone")
    if employer_in(all_, row.player_tg_id) is not None:
        return Result("employed")
    if len(staff_of(corp)) >= staff_cap(content, corp):
        return Result("full")
    if await pl_svc._pending_offer(session, ch.player_tg_id, row.player_tg_id, OFFER) is not None:  # noqa: SLF001
        return Result("pending")
    session.add(PlayerMail(
        to_tg_id=row.player_tg_id, from_tg_id=ch.player_tg_id, from_char_id=ch.id, to_char_id=row.id,
        from_name=f"{ch.state.name} {ch.state.surname}".strip(), from_year=year, kind="offer",
        payload={"offer": OFFER, "corp": corp["name"], "owner": str(ch.player_tg_id), "pos": pos, "post": p.name,
                 "weekly": p.weekly},
    ))
    await session.flush()
    return Result(text=f"Предложение ушло: {row.name}, «{p.name}», {p.weekly} эдди в неделю. "
                       "Ответ придёт, когда игрок откроет окно.")


async def answer(session: AsyncSession, content: Content, ch: ActiveCharacter, year: int, tz: str,
                 mail_id: int) -> Result:
    """The employee accepts a hiring offer (a refusal goes through players.answer like any offer)."""
    from app.services import players as pl_svc

    m = await session.get(PlayerMail, mail_id)
    if m is None or m.to_tg_id != ch.player_tg_id or m.kind != "offer" or m.delivered_at is not None:
        return Result("stale")
    p = dict(m.payload or {})
    all_ = await _corps(session)
    owner = str(p.get("owner", ""))
    corp = all_.get(owner)
    if corp is None or corp.get("name") != p.get("corp"):
        m.delivered_at = pl_svc._utcnow()  # noqa: SLF001
        await session.flush()
        return Result("nocorp")
    other = employer_in(all_, ch.player_tg_id)
    if other is not None and other[0] != owner:
        return Result("employed")
    staff = staff_of(corp)
    if str(ch.player_tg_id) not in staff and len(staff) >= staff_cap(content, corp):
        return Result("full")
    pos = int(p.get("pos", 0))
    name = f"{ch.state.name} {ch.state.surname}".strip()
    staff[str(ch.player_tg_id)] = {"char": ch.id, "name": name, "pos": pos}
    corp["staff"] = staff
    all_[owner] = corp
    await _save(session, all_)
    ch.state.flags[FLAG] = {"owner": owner, "corp": corp["name"], "pos": pos}
    m.delivered_at = pl_svc._utcnow()  # noqa: SLF001
    pl_svc._reply(session, m, ch.player_tg_id, name, year,  # noqa: SLF001
                  {"offer": OFFER, "accepted": True, "g": pl_svc.gender(ch), "corp": corp["name"],
                   "post": str(p.get("post", ""))}, ch.id)
    await save_active(session, ch, tz)
    await session.flush()
    pp = post(content, pos)
    return Result(text=f"Ты в штате «{corp['name']}»: {pp.name if pp else '?'}, {pp.weekly if pp else 0} эдди в неделю. "
                       "Поручения корпорации — в «Работе».")


async def set_post(session: AsyncSession, content: Content, ch: ActiveCharacter, tg: str, pos: int) -> Result:
    all_ = await _corps(session)
    corp = all_.get(str(ch.player_tg_id))
    if corp is None:
        return Result("nocorp")
    staff = staff_of(corp)
    if tg not in staff or not 0 <= pos < len(posts(content)):
        return Result("gone")
    old = int(staff[tg].get("pos", 0))
    staff[tg]["pos"] = pos
    corp["staff"] = staff
    all_[str(ch.player_tg_id)] = corp
    await _save(session, all_)
    p = post(content, pos)
    word = "Повышение" if pos > old else ("Понижение" if pos < old else "Должность")
    _mail(session, int(tg), f"{word} в «{corp['name']}»: теперь {p.name if p else '?'}, "
                            f"{p.weekly if p else 0} эдди в неделю.")
    return Result(text=f"{staff[tg]['name']}: теперь {p.name if p else '?'}.")


async def fire(session: AsyncSession, ch: ActiveCharacter, tg: str) -> Result:
    all_ = await _corps(session)
    corp = all_.get(str(ch.player_tg_id))
    if corp is None:
        return Result("nocorp")
    staff = staff_of(corp)
    gone = staff.pop(tg, None)
    if gone is None:
        return Result("gone")
    corp["staff"] = staff
    all_[str(ch.player_tg_id)] = corp
    await _save(session, all_)
    _mail(session, int(tg), f"Тебя уволили из «{corp['name']}». Охрана провожает до лифта.")
    return Result(text=f"{gone['name']} больше не в штате. Пропуск аннулирован.")


def notify_gone(session: AsyncSession, corp: dict, why: str) -> None:
    """The megacorp is sold, razed or bankrupt: the staff learns it by mail."""
    for tg in staff_of(corp):
        _mail(session, int(tg), f"Корпорации «{corp['name']}» больше нет: {why}. Ты без работы.")


async def payroll(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random,
                  year: int) -> list[str]:
    """The owner's press: a month of salaries from the owner's eddies, by mail to the staff."""
    all_ = await _corps(session)
    corp = all_.get(str(ch.player_tg_id))
    if corp is None:
        return []
    staff = staff_of(corp)
    if not staff:
        return []
    alive: dict[str, dict] = {}
    for tg, rec in staff.items():
        row = await session.get(Character, int(rec.get("char", 0) or 0))
        if row is not None and row.alive:
            alive[tg] = rec
    if len(alive) != len(staff):
        corp["staff"] = alive
        all_[str(ch.player_tg_id)] = corp
        await _save(session, all_)
    if not alive:
        return [f"🏢 «{corp['name']}»: штат пуст."]
    weeks = content.balance.time.weeks_per_press
    pays = {tg: (post(content, int(r.get("pos", 0))).weekly if post(content, int(r.get("pos", 0))) else 0) * weeks
            for tg, r in alive.items()}
    total = sum(pays.values())
    if ch.state.eddies < total:
        for tg in alive:
            _mail(session, int(tg), f"«{corp['name']}» задерживает зарплату: у владельца нет денег на счёте.")
        return [f"🏢 Зарплаты «{corp['name']}» ({total} эдди за месяц) платить нечем. Штат сидит без денег и злится."]
    apply_effects(ch.state, {"eddies": -total}, content, rng, year)
    for tg, pay in pays.items():
        p = post(content, int(alive[tg].get("pos", 0)))
        _mail(session, int(tg), f"Зарплата от «{corp['name']}» за месяц ({p.name if p else '?'}): {pay} эдди.", pay)
    return [f"🏢 Зарплаты «{corp['name']}»: {len(alive)} чел., {total} эдди за месяц."]


async def press_lines(session: AsyncSession, content: Content, ch: ActiveCharacter, rng: random.Random,
                      year: int) -> list[str]:
    out = await payroll(session, content, ch, rng, year)
    await sync(session, ch)
    return out
