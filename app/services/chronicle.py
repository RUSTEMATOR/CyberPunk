"""The life chronicle (owner request): Claude tells the character's life as a bar story from what the log knows.
Once per Kyiv day per character (flag `chronicle_day`); the player can post it to the group."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.content.loader import Content
from app.db.models import EventLog
from app.engine.character_gen import FLAG_GENDER
from app.narrator import narrator
from app.services import narration
from app.services.game import ActiveCharacter, save_active

FLAG = "chronicle_day"

DEED_WORDS = {"week_lived": "недель прожито", "gig_result": "заказов", "crime": "преступлений", "action": "мелких дел",
              "implant_install": "имплантов поставлено", "work_hard": "рабочих смен", "event_resolved": "событий",
              "race": "гонок", "player_attack": "стычек с другими", "raid": "ударов по рейд-боссу"}


def used_today(ch: ActiveCharacter, tz: str) -> bool:
    return ch.state.flags.get(FLAG) == datetime.now(ZoneInfo(tz)).date().isoformat()


async def payload(session: AsyncSession, content: Content, ch: ActiveCharacter) -> dict:
    st = ch.state
    counts = dict((await session.execute(select(EventLog.kind, func.count()).where(
        EventLog.character_id == ch.id).group_by(EventLog.kind))).all())
    deeds = {DEED_WORDS[k]: n for k, n in counts.items() if k in DEED_WORDS and n}
    rows = (await session.execute(select(EventLog).where(
        EventLog.character_id == ch.id, EventLog.kind.in_(["event_resolved", "gig_result", "promotion", "race", "loud"]))
        .order_by(EventLog.id.desc()).limit(12))).scalars().all()
    recent = []
    for r in reversed(rows):
        p = r.payload or {}
        title = p.get("title") or p.get("event") or p.get("text") or p.get("tier") or ""
        if r.kind == "event_resolved" and p.get("event") in content.events:
            title = content.events[p["event"]].title
        if title:
            recent.append(str(title)[:120])
    life: dict[str, object] = {"age": st.age_years, "weeks": st.weeks_lived, "district": narration.character_card(content, st)["district"]}
    if st.career:
        car = content.careers.get(st.career.key)
        life["career"] = car.name if car else st.career.key
    ride = st.flags.get("vehicle")
    if isinstance(ride, dict) and ride.get("key") in content.vehicles:
        life["vehicle"] = content.vehicles[ride["key"]].name
    gang = (st.flags.get("gang") or {}).get("key")
    if gang in content.gangs:
        life["gang"] = content.gangs[gang].name
    from app.engine import biz as biz_engine

    names = []
    for b in biz_engine.list_of(st):
        kind = next((k for k in content.balance.players.business.kinds if k.key == b.get("kind")), None)
        if kind:
            names.append(f"«{b['name']}» ({kind.name})" if b.get("name") else kind.name)
    if names:
        life["business"] = ", ".join(names)
    people = [f"{n.name} ({n.kind})" for n in st.npcs if n.alive and n.kind in ("partner", "child", "friend")][:5]
    if people:
        life["people"] = people
    return {"kind": "chronicle", "character": narration.character_card(content, st), "gender": st.flags.get(FLAG_GENDER, "m"),
            "life": life, "deeds": deeds, "recent": recent}


async def write(session: AsyncSession, content: Content, ch: ActiveCharacter, tz: str) -> str | None:
    data = await payload(session, content, ch)
    text = await narrator.generate_text("chronicle", data, year=ch.state.current_year)
    if text:
        ch.state.flags[FLAG] = datetime.now(ZoneInfo(tz)).date().isoformat()
        ch.state.flags["chronicle_last"] = text
        await save_active(session, ch, tz)
    return text
