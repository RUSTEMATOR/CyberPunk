"""Window changes fanned out to the open Mini Apps of a player (docs/WEBAPP.md, "Sync").

`WindowManager` calls `on_window_change` after it saved a window row, for a change made from the bot or from the
app. Each change bumps the player's revision, is kept as the latest payload (in memory and in `webapp_screens`, so a
restart can still answer the app), and is put on every open event stream of that player.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from app.bot.ui.screen import Screen
from app.clock import utcnow
from app.db import session as db
from app.webapp.models import WebappScreen
from app.webapp.render import closed_payload, screen_payload

log = logging.getLogger(__name__)

BOOT_ID = uuid.uuid4().hex[:12]  # a new process: the app drops revisions it holds from the old one
QUEUE_MAX = 32

_rev: dict[int, int] = {}
_last: dict[int, dict[str, Any]] = {}
_subs: dict[int, set[asyncio.Queue[dict[str, Any]]]] = {}
_bg: set[asyncio.Task[None]] = set()


def rev(user_id: int) -> int:
    return _rev.get(user_id, 0)


def last(user_id: int) -> dict[str, Any] | None:
    return _last.get(user_id)


def _envelope(user_id: int, payload: dict[str, Any], at_rev: int | None = None) -> dict[str, Any]:
    """A screen with the character card (W1). On the menu `menu_rest` is the text without the card's lines."""
    from app.webapp import hud

    env: dict[str, Any] = {"type": "screen", "boot": BOOT_ID, "rev": rev(user_id) if at_rev is None else at_rev,
                           "screen": payload, "ts": time.time(), "hud": hud.get(user_id)}
    if payload.get("name") == "menu" and env["hud"] is not None:
        rest = hud.menu_rest(user_id, payload.get("html") or "")
        if rest is not None:
            env["menu_rest"] = rest
    return env


def _put(user_id: int, msg: dict[str, Any]) -> None:
    for q in list(_subs.get(user_id, ())):
        if q.full():  # a stalled reader keeps only the newest state
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
        q.put_nowait(msg)


_deliver_locks: dict[int, asyncio.Lock] = {}


async def _deliver(user_id: int, at_rev: int, payload: dict[str, Any]) -> None:
    """Changes go out in the order they happened, each with the character card recomputed after it."""
    from app.webapp import hud

    lock = _deliver_locks.setdefault(user_id, asyncio.Lock())
    async with lock:
        await hud.refresh_bounded(user_id)
        _put(user_id, _envelope(user_id, payload, at_rev))


def publish(user_id: int, payload: dict[str, Any], persist: bool = True) -> dict[str, Any]:
    _rev[user_id] = rev(user_id) + 1
    _last[user_id] = payload
    _spawn(_deliver(user_id, _rev[user_id], payload))
    if persist:
        _spawn(_persist(user_id, payload))
    return _envelope(user_id, payload)


def on_window_change(user_id: int, screen: Screen | None) -> None:
    """WindowManager listener: never raises, never blocks."""
    try:
        payload = screen_payload(screen) if screen is not None else closed_payload()
    except Exception:
        log.exception("webapp: could not render screen %r", getattr(screen, "name", None))
        return
    publish(user_id, payload)


def current(user_id: int) -> dict[str, Any] | None:
    """The latest envelope for a player, None when nothing is known in this process."""
    payload = _last.get(user_id)
    return _envelope(user_id, payload) if payload is not None else None


def remember(user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """A payload restored from the DB after a restart becomes the current one without a new revision."""
    if user_id not in _last:
        _rev.setdefault(user_id, 1)
        _last[user_id] = payload
    return _envelope(user_id, _last[user_id])


def subscribe(user_id: int) -> asyncio.Queue[dict[str, Any]]:
    q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_MAX)
    _subs.setdefault(user_id, set()).add(q)
    return q


def unsubscribe(user_id: int, q: asyncio.Queue[dict[str, Any]]) -> None:
    subs = _subs.get(user_id)
    if subs is not None:
        subs.discard(q)
        if not subs:
            _subs.pop(user_id, None)


def online(user_id: int) -> bool:
    return bool(_subs.get(user_id))


def _spawn(coro: Any) -> None:
    try:
        task = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()
        return
    _bg.add(task)
    task.add_done_callback(_bg.discard)


_write_locks: dict[int, asyncio.Lock] = {}


async def _persist(user_id: int, payload: dict[str, Any]) -> None:
    lock = _write_locks.setdefault(user_id, asyncio.Lock())
    async with lock:
        if _last.get(user_id) is not payload:  # a newer change is already on its way
            return
        try:
            async with db.session_scope() as s:
                row = await s.get(WebappScreen, user_id)
                if row is None:
                    row = WebappScreen(player_tg_id=user_id)
                    s.add(row)
                row.screen = payload.get("name") or ""
                row.payload = payload
                row.updated_at = utcnow()
        except Exception:
            log.exception("webapp: could not store the screen of %s", user_id)


async def drain() -> None:
    if _bg:
        await asyncio.gather(*list(_bg), return_exceptions=True)
