"""The city leaderboard (owner request): who is the richest, the most famous, the most chromed, the most wanted.

`/top` shows it any time; on Sunday with the weekly legends the leaders get titles (world_state `titles`,
{tg_id: [title, ...]}) and a city letter with a prize. Titles show in «Свои» next to the name.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.texts import esc
from app.db import repo
from app.db.models import Character, PlayerMail
from app.db.models import Cyberware as CyberwareRow

TITLES_KEY = "titles"
PRIZE = 500


@dataclass(frozen=True)
class Board:
    key: str
    head: str       # line head
    title: str      # the title the leader gets
    unit: str       # how the number reads


BOARDS: tuple[Board, ...] = (
    Board("eddies", "Самые богатые", "Король эдди", "эдди"),
    Board("fame", "Самые известные", "Звезда Найт-Сити", "славы"),
    Board("street_cred", "Уважение улиц", "Легенда улиц", "репутации"),
    Board("chrome", "Больше всех хрома", "Хромированный", "имплантов"),
    Board("heat", "Самые разыскиваемые", "Враг NCPD", "розыска"),
)


async def standings(session: AsyncSession, top: int = 3) -> dict[str, list[tuple[int, str, int]]]:
    """Per board: [(tg_id, name, value)] of the living characters, best first, only positive values."""
    rows = list((await session.execute(select(Character).where(Character.alive.is_(True)))).scalars())
    chrome = dict((await session.execute(
        select(CyberwareRow.character_id, func.count()).group_by(CyberwareRow.character_id))).all())
    out: dict[str, list[tuple[int, str, int]]] = {}
    for b in BOARDS:
        vals = []
        for r in rows:
            v = int(chrome.get(r.id, 0)) if b.key == "chrome" else int(getattr(r, b.key) or 0)
            if v > 0:
                vals.append((r.player_tg_id, f"{r.name} {r.surname}".strip(), v))
        vals.sort(key=lambda x: -x[2])
        out[b.key] = vals[:top]
    return out


async def titles(session: AsyncSession) -> dict[int, list[str]]:
    raw = await repo.ws_get(session, TITLES_KEY, {}) or {}
    return {int(k): list(v) for k, v in raw.items()}


async def text(session: AsyncSession, head: str = "🏆 <b>Рейтинг Найт-Сити</b>") -> str:
    st = await standings(session)
    lines = [head]
    for b in BOARDS:
        rows = st[b.key]
        if not rows:
            continue
        cells = [f"{i}. {esc(n)} — {v}" for i, (_, n, v) in enumerate(rows, 1)]
        lines.append(f"\n<b>{b.head}</b> (титул «{b.title}»):\n" + "\n".join(cells))
    if len(lines) == 1:
        lines.append("Пока рейтинг пуст: город ждёт первых героев.")
    return "\n".join(lines)


async def award(session: AsyncSession) -> str:
    """Sunday: the leaders get their titles and a prize. Returns the post text."""
    st = await standings(session, top=1)
    new: dict[str, list[str]] = {}
    for b in BOARDS:
        if not st[b.key]:
            continue
        tg, _, _ = st[b.key][0]
        new.setdefault(str(tg), []).append(b.title)
        session.add(PlayerMail(to_tg_id=tg, from_tg_id=0, from_name="Город", kind="city",
                               payload={"eddies": PRIZE, "fame": 1,
                                        "text": f"Титул недели «{b.title}»: +{PRIZE} эдди, слава +1."}))
    await repo.ws_set(session, TITLES_KEY, new)
    return await text(session, "🏆 <b>Итоги недели: рейтинг Найт-Сити</b>") + (
        "\n\nЛидеры получают титулы на неделю и по " + str(PRIZE) + " эдди. Посмотреть рейтинг в любой момент — /top"
        if new else "")
