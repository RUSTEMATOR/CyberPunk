"""Who is calling: Telegram Mini App initData, checked with the bot token (docs/WEBAPP.md, "Auth").

Telegram signs initData with HMAC-SHA256 (key = HMAC("WebAppData", bot token)). A valid signature proves the user
id; `auth_date` older than MAX_AGE_S is refused so a leaked string stops working. Only members of the game group (or
players the game already knows) get in: the app is the same game window, and the game lives in that group.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from aiogram.utils.web_app import safe_parse_webapp_init_data

from app.db import session as db
from app.db.models import Player

log = logging.getLogger(__name__)

MAX_AGE_S = 7 * 24 * 3600   # Telegram keeps one initData for the life of the opened app; a week covers a long session
MEMBER_CACHE_S = 600
_MEMBER_OK = {"creator", "administrator", "member", "restricted"}


@dataclass(frozen=True)
class WebUser:
    id: int
    username: str | None
    full_name: str


class AuthError(Exception):
    pass


def parse(bot_token: str, init_data: str) -> WebUser:
    if not init_data:
        raise AuthError("no init data")
    try:
        data = safe_parse_webapp_init_data(bot_token, init_data)
    except ValueError as exc:
        raise AuthError("bad signature") from exc
    if data.user is None:
        raise AuthError("no user")
    if data.auth_date is not None and time.time() - data.auth_date.timestamp() > MAX_AGE_S:
        raise AuthError("init data expired")
    u = data.user
    full = " ".join(x for x in (u.first_name, u.last_name) if x)
    return WebUser(u.id, u.username, full)


_members: dict[int, tuple[bool, float]] = {}


async def allowed(ui, user_id: int) -> bool:  # noqa: ANN001
    """A player the game knows (in the group or in private chat with the bot), or a member of the game group."""
    hit = _members.get(user_id)
    now = time.monotonic()
    if hit is not None and now - hit[1] < MEMBER_CACHE_S:
        return hit[0]
    # owner request 27.10: players outside the group play in private chat with the bot, so anybody the game knows
    # gets in as well; the group check stays for people who have not started the bot yet
    async with db.session_scope() as s:
        ok: bool | None = True if await s.get(Player, user_id) is not None else None
    if ok is None and ui.group_id is not None:
        try:
            m = await ui.bot.get_chat_member(ui.group_id, user_id)
            ok = getattr(m, "status", None) in _MEMBER_OK
        except Exception as exc:
            log.info("webapp: membership check failed for %s: %s", user_id, exc)
    ok = bool(ok)
    _members[user_id] = (ok, now)
    return ok
