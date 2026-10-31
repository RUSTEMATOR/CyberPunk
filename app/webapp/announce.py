"""One-time posts into the game group (owner rule: one big digest when a batch of work is done, CLAUDE.md).

A file `data/announce/<name>.html` (Telegram HTML) is posted once, as a city feed post of kind news (it keeps the
news lifetime). `{players}` in the text becomes the @usernames of the players with a living character. Sent files
are listed in `world_state.webapp_announced`; a file is never posted twice, also across restarts.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import text as sql

from app.config import BASE_DIR
from app.db import repo
from app.db import session as db

if TYPE_CHECKING:
    from app.bot.ui.kit import UiKit

log = logging.getLogger(__name__)

DIR = BASE_DIR / "data" / "announce"
KEY = "webapp_announced"
MAX_CHARS = 4000


async def _players() -> str:
    async with db.session_scope() as s:
        rows = (await s.execute(sql(
            "select distinct p.username from players p join characters c on c.player_tg_id = p.tg_id "
            "where c.alive = 1 and p.username is not null and p.username != ''"))).all()
    return " ".join(f"@{r[0]}" for r in rows)


def _parts(text: str) -> list[str]:
    """Split on blank lines so no part is over Telegram's limit and no tag is cut."""
    if len(text) <= MAX_CHARS:
        return [text]
    parts, cur = [], ""
    for block in text.split("\n\n"):
        if cur and len(cur) + 2 + len(block) > MAX_CHARS:
            parts.append(cur)
            cur = block
        else:
            cur = f"{cur}\n\n{block}" if cur else block
    if cur:
        parts.append(cur)
    return parts


async def run(ui: "UiKit") -> None:
    from app.bot.ui.cleanup import KIND_NEWS

    if not DIR.is_dir() or ui.group_id is None:
        return
    async with db.session_scope() as s:
        done = list(await repo.ws_get(s, KEY) or [])
    for path in sorted(Path(DIR).glob("*.html")):
        if path.name in done:
            continue
        body = path.read_text(encoding="utf-8").strip()
        if "{players}" in body:
            body = body.replace("{players}", await _players())
        async with db.session_scope() as s:  # mark first: a crash after sending must not post it again
            done.append(path.name)
            await repo.ws_set(s, KEY, done)
        ttl = ui.content.balance.ui.news_ttl_h * 3600
        for part in _parts(body):
            mid = await ui.send_feed(part, ttl, KIND_NEWS)
            log.info("announce: %s posted (message %s)", path.name, mid)
