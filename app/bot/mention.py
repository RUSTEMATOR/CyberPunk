"""Names of players in the group: a ping where a ping is the point, a plain name elsewhere."""

from __future__ import annotations

from app.bot.texts import esc


def plain(username: str | None, name: str | None, tg_id: int) -> str:
    """A name that does not ping: the character's name, else the username without '@'."""
    return esc(name or username or f"игрок {tg_id}")


def ping(username: str | None, name: str | None, tg_id: int) -> str:
    """@username when there is one, else a tg://user link on the name (both notify the player)."""
    if username:
        return "@" + username
    return f'<a href="tg://user?id={tg_id}">{esc(name or "игрок")}</a>'
