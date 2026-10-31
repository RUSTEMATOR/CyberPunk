"""One-time data fixes: at startup (idempotent, each guarded by a world_state flag), and lazy per-character fixes
applied when the main menu opens."""

from __future__ import annotations

import logging
import random

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content, humanity_free
from app.db import repo
from app.db.models import Character
from app.engine.effects import apply_effects
from app.services.game import ActiveCharacter

log = logging.getLogger(__name__)

AP_MONTH_KEY = "mig_ap_month"


async def ap_month_topup(session: AsyncSession, content: Content) -> int:
    """Owner request (a press lives four weeks): characters whose current turn was granted under the old rule (the AP of
    one week) get the rest of the month's AP once. Returns how many were topped up."""
    if await repo.ws_get(session, AP_MONTH_KEY):
        return 0
    t = content.balance.time
    per_week, per_press = t.ap_per_week, t.ap_per_week * t.weeks_per_press
    rows = (await session.execute(select(Character).where(Character.alive.is_(True)))).scalars().all()
    n = 0
    for row in rows:
        if int(row.ap or 0) <= per_week:
            row.ap = min(per_press, int(row.ap or 0) + (per_press - per_week))
            n += 1
    await repo.ws_set(session, AP_MONTH_KEY, True)
    log.warning("ap month: topped up %d character(s)", n)
    return n


START_DEBTS_KEY = "mig_start_debts_3010"


async def drop_start_debts(session: AsyncSession, content: Content) -> int:
    """Owner request 30.10 «Сними стартовый долг со всех игроков»: the starting debts (debts.yaml keys) of the living
    characters are written off once; loans taken in the game stay. The player gets a letter. Returns how many."""
    from app.db.models import Debt, PlayerMail

    if await repo.ws_get(session, START_DEBTS_KEY):
        return 0
    rows = (await session.execute(
        select(Debt, Character).join(Character, Character.id == Debt.character_id).where(Character.alive.is_(True))
    )).all()
    done: list[dict] = []
    for debt, ch in rows:
        if not debt.debt_key or debt.debt_key not in content.debts:
            continue
        amount = int(debt.principal or 0)
        done.append({"character": int(ch.id), "tg": int(ch.player_tg_id), "creditor": debt.creditor, "principal": amount})
        await session.delete(debt)
        session.add(PlayerMail(to_tg_id=int(ch.player_tg_id), from_tg_id=0, from_name="Найт-Сити", kind="city",
                               to_char_id=int(ch.id),
                               payload={"text": f"🧾 Стартовый долг списан: {debt.creditor} больше не ждёт {amount} эдди. "
                                                "Город дал тебе чистый лист."}))
        log.warning("start debts: character %d: %s %d written off", ch.id, debt.creditor, amount)
    await session.flush()
    await repo.ws_set(session, START_DEBTS_KEY, done or True)
    return len(done)


def refund_netrun_humanity(content: Content, ch: ActiveCharacter, year: int) -> list[str]:
    """Owner request 24.10: the netrunner's gear no longer takes humanity; what it took is given back once."""
    st = ch.state
    if st.flags.get("netrun_refund_v1"):
        return []
    st.flags["netrun_refund_v1"] = True
    back = 0
    for item in st.cyberware:
        cw = content.cyberware.get(item.key)
        if cw is not None and humanity_free(cw) and item.humanity_paid > 0:
            back += item.humanity_paid
            item.humanity_paid = 0
    if back <= 0:
        return []
    st.humanity_lost = max(0, st.humanity_lost - back)
    apply_effects(st, {"humanity": back}, content, random.Random(), year)
    return [f"🧠 Нейропорт, деки и скрипты больше не забирают человечность. Возвращено: +{back}."]


def monk_fix(content: Content, ch: ActiveCharacter) -> list[str]:
    """Owner report 24.10: the monk role grew from plain meditation and martial arts. Off the monk path
    (engine/roles.on_monk_path) its affinity is dropped once and the main role is named again."""
    from app.engine import roles as roles_engine

    st = ch.state
    if st.flags.get("monk_fix_v1"):
        return []
    st.flags["monk_fix_v1"] = True
    if roles_engine.on_monk_path(st) or roles_engine.MONK not in st.role_affinity:
        return []
    st.role_affinity.pop(roles_engine.MONK, None)
    if st.role != roles_engine.MONK:
        return []
    st.role = ""
    new = roles_engine.sync_main_role(st, content)
    role = content.roles.get(new) if new else None
    return ["Роль «Монах» снята: она растёт только у тех, кто держит обет или пришёл из монастыря или общины. "
            + (f"Теперь город зовёт тебя так: {role.name}." if role else "Новая роль появится от твоих дел.")]


def abroad_move_in(content: Content, ch: ActiveCharacter) -> list[str]:
    """Owner request 25.10: a character living abroad since before the cities moves into the city's first district."""
    from app.engine import abroad as ae

    st = ch.state
    if not ae.needs_move_in(st, content):
        return []
    c = ae.country(st, content)
    ae.settle(st, content, c.key)
    d = content.districts[st.district]
    return [f"✈️ {c.city} открылся: своя улица, свои фиксеры, банды и риппер. Ты живёшь в районе «{d.name}». "
            "Загляни в «Мир»: там сюжет страны."]
