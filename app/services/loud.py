"""Loud deeds go to the group at once (owner request: a city that feels shared). The text is HTML, names escaped;
the feed posts it as an alert (app/services/feed.py, kind `loud`)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.texts import esc
from app.db import repo


def who(name: str, surname: str = "") -> str:
    return esc(f"{name} {surname}".strip())


async def post(session: AsyncSession, character_id: int | None, text: str, *, kind: str = "loud", w: int = 1) -> None:
    await repo.log_event(session, "loud", {"text": "📣 " + text}, character_id=character_id, public=True)
    from app.services import deeds  # owner request 26.10: the city remembers what the players did

    await deeds.add(session, character_id, kind, text, w=w)
