"""The death count of Night City (owner request 24.10): how many people died in all the city in a day, in the morning
news. The exact part comes from the game: enemies killed in fights, city people killed, player characters dead. Around
it the city's own background (illness, age, accidents, the street), which a crisis, a Purge night and the gang wars
make heavier.

world_state `mortality` = {"total": all counted, "days": {kyiv date: {...the day...}}}
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.content.loader import Content
from app.db import repo
from app.db.models import CityNpc, EventLog

KEY = "mortality"


def _mult(m: dict, part: str, share: float = 1.0) -> float:
    x = float((m or {}).get(part, 1.0) or 1.0)
    return 1 + (x - 1) * share


async def _exact(session: AsyncSession, since: datetime) -> dict[str, int]:
    kills = 0
    res = await session.execute(select(EventLog.payload).where(EventLog.kind == "fight_end", EventLog.created_at >= since))
    for (p,) in res.all():
        kills += int((p or {}).get("kills", 0) or 0)
    npc = int((await session.execute(select(func.count()).select_from(CityNpc).where(
        CityNpc.alive.is_(False), CityNpc.died_at >= since))).scalar() or 0)
    chars = int((await session.execute(select(func.count()).select_from(EventLog).where(
        EventLog.kind == "death", EventLog.created_at >= since))).scalar() or 0)
    return {"fights": kills, "npc": npc, "chars": chars}


async def day(session: AsyncSession, content: Content, tz: str, rng: random.Random | None = None,
              store: bool = True) -> dict:
    """The last 24 hours. Stored once per Kyiv date (the morning tick); a repeat call of the same date reuses it."""
    rng = rng or random.Random()
    c = content.balance.mortality
    today = datetime.now(ZoneInfo(tz)).date().isoformat()
    book = dict(await repo.ws_get(session, KEY, {}) or {})
    days = dict(book.get("days") or {})
    if today in days:
        return dict(days[today])
    exact = await _exact(session, utcnow() - timedelta(hours=24))
    natural, violent = rng.randint(*c.natural), rng.randint(*c.violent)
    why: list[str] = []
    from app.services import crisis as crisis_svc

    st = await crisis_svc.current(session)
    if st is not None:
        m = c.crisis.get(str(st.get("key")), {})
        natural, violent = int(natural * _mult(m, "natural")), int(violent * _mult(m, "violent"))
        if m:
            why.append(f"кризис «{crisis_svc.title(st)}»")
    else:
        a = await crisis_svc.after(session)
        m = c.crisis.get(str((a or {}).get("key")), {}) if a else {}
        if m:
            natural = int(natural * _mult(m, "natural", c.after_share))
            violent = int(violent * _mult(m, "violent", c.after_share))
            why.append("последствия кризиса")
    from app.services import purge

    pst = await purge.state(session)
    try:
        end = datetime.fromisoformat(str(pst.get("end"))) if pst.get("end") else None
    except ValueError:
        end = None
    if end is not None and pst.get("status") == purge.DONE and datetime.now(ZoneInfo(tz)) - end <= timedelta(hours=24):
        extra = rng.randint(*c.purge_extra)
        violent += extra
        why.append(f"Судная ночь +{extra}")
    from app.services import worldfx

    wars = len(await worldfx.gang_wars(session))
    if wars:
        violent += sum(rng.randint(*c.per_gang_war) for _ in range(wars))
        why.append(f"войны банд: {wars}")
    by_players = exact["fights"] + exact["npc"]
    rec = {"natural": natural, "violent": violent + by_players + exact["chars"], "by_players": by_players,
           "fights": exact["fights"], "npc": exact["npc"], "chars": exact["chars"], "why": why}
    rec["total"] = rec["natural"] + rec["violent"]
    if store:
        days[today] = rec
        keep = sorted(days)[-c.keep_days:]
        book["days"] = {k: days[k] for k in keep}
        book["total"] = int(book.get("total", 0) or 0) + rec["total"]
        await repo.ws_set(session, KEY, book)
    return rec


async def news_block(session: AsyncSession, content: Content, tz: str, rng: random.Random | None = None,
                     store: bool = True) -> str:
    from app.bot.views import money

    rec = await day(session, content, tz, rng, store)
    book = dict(await repo.ws_get(session, KEY, {}) or {})
    days = book.get("days") or {}
    week = sum(int(v.get("total", 0)) for k, v in sorted(days.items())[-7:])
    lines = [f"☠️ <b>Смертность в Найт-Сити за сутки: {money(rec['total'])}</b>",
             f"Улица и оружие: {money(rec['violent'])}"
             + (f", из них на счету игроков {rec['by_players']} ("
                + ", ".join(x for x in (f"в боях {rec['fights']}" if rec["fights"] else "",
                                        f"горожан {rec['npc']}" if rec["npc"] else "") if x) + ")"
                if rec["by_players"] else "")
             + (f", персонажей игроков: {rec['chars']}" if rec["chars"] else "") + ".",
             f"Болезни, возраст, несчастные случаи: {money(rec['natural'])}."]
    if rec.get("why"):
        lines.append("Больше обычного: " + ", ".join(rec["why"]) + ".")
    if store or days:
        lines.append(f"За неделю: {money(week or rec['total'])} · с начала счёта: {money(int(book.get('total', 0) or rec['total']))}.")
    return "\n".join(lines)
