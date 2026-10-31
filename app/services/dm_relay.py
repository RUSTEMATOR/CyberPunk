"""Owner request 26.10: a player who plays in the bot's private chat or in the Mini App and is not a member of the game
group gets the group's posts (the city feed, the digests, the public results, the pings that tag him) in private.

- Who: players with a living character who are not members of the group (Telegram getChatMember, cached for
  `balance.dm_relay.member_cache_h`). A failed check counts as a member: no doubles for people in the group.
- A private chat the bot cannot write to (never started, blocked) is skipped for `balance.dm_relay.retry_h`.
- A relayed post lives in the private chat as long as in the group (the same cleanup kind and lifetime).
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.db import session as db
from app.db.models import Character

if TYPE_CHECKING:
    from app.bot.ui.kit import UiKit

log = logging.getLogger(__name__)

_OK_STATUS = {"creator", "administrator", "member"}
_member: dict[int, tuple[bool, float]] = {}  # tg id -> (in the group, checked at)
_mute: dict[int, float] = {}  # tg id -> monotonic time until which the private chat is not tried
_players: tuple[list[int], float] = ([], 0.0)


def cfg(ui: "UiKit"):  # noqa: ANN201
    return ui.content.balance.dm_relay


async def _living_players(ui: "UiKit") -> list[int]:
    global _players
    ids, at = _players
    if ids and time.monotonic() - at < 300:
        return ids
    async with db.session_scope() as s:
        res = await s.execute(select(Character.player_tg_id).where(Character.alive.is_(True)).distinct())
        ids = [int(x) for x in res.scalars()]
    _players = (ids, time.monotonic())
    return ids


async def in_group(ui: "UiKit", tg_id: int) -> bool:
    if ui.group_id is None:
        return True
    hit = _member.get(tg_id)
    now = time.monotonic()
    if hit is not None and now - hit[1] < cfg(ui).member_cache_h * 3600:
        return hit[0]
    ok = True
    try:
        m = await ui.bot.get_chat_member(ui.group_id, tg_id)
        status = str(getattr(m, "status", "") or "")
        status = getattr(status, "value", status)
        ok = status in _OK_STATUS or (status == "restricted" and bool(getattr(m, "is_member", False)))
    except Exception as exc:  # noqa: BLE001 - Telegram unreachable: treat as a member, never double
        log.info("dm_relay: membership check failed for %s: %s", tg_id, exc)
    _member[tg_id] = (ok, now)
    return ok


async def outsiders(ui: "UiKit") -> list[int]:
    if not cfg(ui).enabled or ui.group_id is None:
        return []
    now = time.monotonic()
    out = []
    for tg in await _living_players(ui):
        if _mute.get(tg, 0) > now:
            continue
        if not await in_group(ui, tg):
            out.append(tg)
    return out


async def _send(ui: "UiKit", tg_id: int, text: str, ttl_s: float | None, kind: str) -> bool:
    res = await ui.outbox.send(tg_id, text, None)
    if not res.ok or res.message_id is None:
        err = (res.error or "").lower()
        if any(w in err for w in ("forbidden", "blocked", "chat not found", "initiate", "deactivated")):
            _mute[tg_id] = time.monotonic() + cfg(ui).retry_h * 3600
        log.info("dm_relay: could not write to %s: %s", tg_id, res.error)
        return False
    if ttl_s is not None:
        await ui.cleanup.schedule_delete(tg_id, res.message_id, ttl_s, kind)
    return True


async def relay(ui: "UiKit", text: str, ttl_s: float | None, kind: str) -> int:
    """A copy of a group post for every player outside the group. Returns how many got it."""
    n = 0
    for tg in await outsiders(ui):
        if await _send(ui, tg, text, ttl_s, kind):
            n += 1
    return n


async def ping(ui: "UiKit", tg_id: int, text: str, ttl_s: float | None, kind: str) -> None:
    """A message that tags one player: in the group for a member, in private for an outsider."""
    if ui.group_id is not None and (not cfg(ui).enabled or await in_group(ui, tg_id)):
        await ui._send_temp(ui.group_id, text, ttl_s, kind)  # noqa: SLF001
        return
    await _send(ui, tg_id, text, ui.cap_ttl(ttl_s, kind), kind)
