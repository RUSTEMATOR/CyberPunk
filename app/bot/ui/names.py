"""Owner request 24.10: wherever a living character's name is written, the player's Telegram handle follows it,
so the group can tell who plays whom. The outbox decorates every text it sends or edits («Кэрол Кобаяши (@user)»).

The map is rebuilt from the database by `refresh` (at start and every few minutes, scheduler/jobs.py); the decoration
itself is sync and cheap. Full names always; a bare first name only when it is unique among the living and at least
three letters. Text inside HTML tags is left alone, a name already followed by a handle is not decorated twice, and a
text that would outgrow Telegram's limit is sent as it is.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_tag: dict[str, str] = {}
_re: re.Pattern[str] | None = None
_TAG_SPLIT = re.compile(r"(<[^>]*>)")


def set_names(entries: list[tuple[str, str, str]]) -> None:
    """entries: (full name, first name, handle) of every living character."""
    global _re
    tag: dict[str, str] = {}
    firsts: dict[str, int] = {}
    for _, first, _h in entries:
        if first:
            firsts[first] = firsts.get(first, 0) + 1
    for full, first, handle in entries:
        if not handle:
            continue
        if full and len(full) >= 4:
            tag[full] = handle
        if first and len(first) >= 3 and firsts.get(first) == 1 and first not in tag:
            tag[first] = handle
    _tag.clear()
    _tag.update(tag)
    if not tag:
        _re = None
        return
    alts = "|".join(re.escape(n) for n in sorted(tag, key=len, reverse=True))
    _re = re.compile(rf"(?<![\w@])({alts})(?![\w])(?![^\S\n]*[\w-]*[^\S\n]*\((?:@|[^)]*\)))")


def decorate(text: str | None, limit: int = 4096) -> str | None:
    if not text or _re is None:
        return text
    rx = _re
    parts = _TAG_SPLIT.split(text)
    out = []
    for part in parts:
        if part.startswith("<") and part.endswith(">"):
            out.append(part)
        else:
            out.append(rx.sub(lambda m: f"{m.group(1)} ({_tag[m.group(1)]})", part))
    res = "".join(out)
    return res if len(res) <= limit else text


async def refresh(session: AsyncSession) -> int:
    from app.db.models import Character, Player

    rows = (await session.execute(
        select(Character.name, Character.surname, Player.username, Player.tg_id)
        .join(Player, Player.tg_id == Character.player_tg_id)
        .where(Character.alive.is_(True))
    )).all()
    entries = []
    for name, surname, username, _tg in rows:
        handle = f"@{username}" if username else ""
        full = f"{name or ''} {surname or ''}".strip()
        entries.append((full, (name or "").strip(), handle))
    set_names(entries)
    return len(entries)
