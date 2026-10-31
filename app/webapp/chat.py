"""The group chat inside the Mini App (owner request 25.10: «на телефоне я не могу вам написать»).

- every message of the game group passes an outer middleware of the first router and lands in a ring buffer
  (in memory, mirrored to world_state `webchat` so a restart keeps the recent talk);
- GET  /api/chat?after=<id>  the messages after that id (the app polls it);
- POST /api/chat {"op", "text", "reply"}  the bot posts the text into the group under the player's name (Telegram
  never lets a bot write as a person), and the app shows it as the player's own message.
"""

from __future__ import annotations

import asyncio
import html
import logging
import time
from collections import deque
from typing import Any

from aiogram import BaseMiddleware, Router
from aiogram.types import Message

log = logging.getLogger(__name__)

KEY = "webchat"
KEEP = 150
TEXT_MAX = 1000
SEND_GAP_S = 1.5

_buf: deque[dict[str, Any]] = deque(maxlen=KEEP)
_loaded = False
_save_task: asyncio.Task[None] | None = None
_last_send: dict[int, float] = {}
_seq = 0  # a local id for the order (Telegram ids are unique per chat too, but the app sorts by this)


def _kind(m: Message) -> str:
    for attr, word in (("photo", "фото"), ("video", "видео"), ("sticker", "стикер"), ("animation", "гифка"),
                       ("voice", "голосовое"), ("video_note", "кружок"), ("audio", "аудио"), ("document", "файл"),
                       ("poll", "опрос")):
        if getattr(m, attr, None):
            return word
    return ""


def _who(m: Message) -> tuple[str, str]:
    u = m.from_user
    if u is None:
        return (m.sender_chat.title if m.sender_chat else "Аноним"), ""
    return " ".join(x for x in (u.first_name, u.last_name) if x) or "Аноним", u.username or ""


def _record(m: Message, author: str | None = None, username: str | None = None, uid: int | None = None,
            via_app: bool = False) -> dict[str, Any]:
    global _seq
    _seq += 1
    name, uname = _who(m)
    text = (m.text or m.caption or "").strip()
    sticker = m.sticker.emoji if m.sticker and m.sticker.emoji else ""
    rec: dict[str, Any] = {
        "seq": _seq, "id": m.message_id, "ts": int(m.date.timestamp()) if m.date else int(time.time()),
        "from": author or name, "user": username if username is not None else uname,
        "uid": uid if uid is not None else (m.from_user.id if m.from_user else 0),
        "text": text[:TEXT_MAX], "kind": _kind(m), "sticker": sticker, "app": via_app,
    }
    r = m.reply_to_message
    if r is not None:
        found = next((x for x in reversed(_buf) if x["id"] == r.message_id), None)
        rec["reply"] = {"id": r.message_id, "from": found["from"] if found else _who(r)[0],
                        "text": ((found["text"] if found else (r.text or r.caption or "")) or _kind(r))[:90]}
    return rec


def _add(rec: dict[str, Any]) -> None:
    _buf.append(rec)
    _schedule_save()


def _schedule_save() -> None:
    global _save_task
    if _save_task is not None and not _save_task.done():
        return
    try:
        _save_task = asyncio.get_running_loop().create_task(_save_later())
    except RuntimeError:
        pass


async def _save_later() -> None:
    await asyncio.sleep(3)
    from app.db import repo
    from app.db import session as db

    try:
        async with db.session_scope() as s:
            await repo.ws_set(s, KEY, {"seq": _seq, "items": list(_buf)})
    except Exception:
        log.exception("webchat: save failed")


async def _load() -> None:
    global _loaded, _seq
    if _loaded:
        return
    _loaded = True
    from app.db import repo
    from app.db import session as db

    try:
        async with db.session_scope() as s:
            data = await repo.ws_get(s, KEY, None) or {}
        items = [x for x in (data.get("items") or []) if isinstance(x, dict)]
        known = {x["id"] for x in _buf}
        old = [x for x in items if x.get("id") not in known]
        merged = old + list(_buf)
        _buf.clear()
        _buf.extend(merged[-KEEP:])
        _seq = max([int(data.get("seq", 0) or 0)] + [int(x.get("seq", 0) or 0) for x in _buf])
    except Exception:
        log.exception("webchat: load failed")


# -- the group side -------------------------------------------------------------------------------------------------


class _Listen(BaseMiddleware):
    """Sees every message of the game group before any handler; never stops it."""

    async def __call__(self, handler, event, data):  # noqa: ANN001, ANN204
        try:
            ui = data.get("ui")
            if isinstance(event, Message) and ui is not None and event.chat.id == ui.group_id:
                await _load()
                _add(_record(event))
        except Exception:
            log.exception("webchat: could not record a message")
        return await handler(event, data)


router = Router(name="webchat")
router.message.outer_middleware(_Listen())


# -- the app side ---------------------------------------------------------------------------------------------------


async def _author(user) -> str:  # noqa: ANN001
    """The character's name when the player has one, else the Telegram name."""
    from app.db import session as db
    from app.services import game

    try:
        async with db.session_scope() as s:
            ch = await game.get_active_character(s, user.id)
        if ch is not None:
            return f"{ch.state.name} {ch.state.surname}".strip() or user.full_name
    except Exception:
        log.exception("webchat: no character name for %s", user.id)
    return user.full_name or "Игрок"


async def api_chat(request):  # noqa: ANN001, ANN201
    from aiohttp import web

    from app.webapp import server

    await server._user(request)  # noqa: SLF001
    await _load()
    try:
        after = int(request.query.get("after", "0") or 0)
    except ValueError:
        after = 0
    items = [x for x in _buf if int(x.get("seq", 0)) > after]
    return web.json_response({"items": items[-80:], "last": _seq}, headers=server._NO_STORE)  # noqa: SLF001


async def api_chat_send(request):  # noqa: ANN001, ANN201
    from aiohttp import web

    from app.webapp import server

    user = await server._user(request)  # noqa: SLF001
    ui = request.app[server.UI_KEY]
    d = await server._body(request)  # noqa: SLF001
    text = str(d.get("text") or "").strip()[:TEXT_MAX]
    if not text:
        return web.json_response({"status": "bad_request"}, status=400)
    if ui.group_id is None:
        return web.json_response({"status": "no_group"}, status=503)
    now = time.monotonic()
    if now - _last_send.get(user.id, 0.0) < SEND_GAP_S:
        return web.json_response({"status": "slow"}, status=429)
    _last_send[user.id] = now
    await _load()
    name = await _author(user)
    tag = f" (@{user.username})" if user.username else ""
    body = f"<b>{html.escape(name)}</b>{html.escape(tag)}:\n{html.escape(text)}"
    reply = d.get("reply")
    kw: dict[str, Any] = {}
    if isinstance(reply, int) and reply > 0:
        from aiogram.types import ReplyParameters

        kw["reply_parameters"] = ReplyParameters(message_id=reply, allow_sending_without_reply=True)
    try:
        from aiogram.types import LinkPreviewOptions

        sent = await ui.bot.send_message(ui.group_id, body, parse_mode="HTML",
                                         link_preview_options=LinkPreviewOptions(is_disabled=True), **kw)
    except Exception:
        log.exception("webchat: send failed for %s", user.id)
        return web.json_response({"status": "error"}, status=502)
    rec = _record(sent, author=name, username=user.username or "", uid=user.id, via_app=True)
    rec["text"] = text
    _add(rec)
    return web.json_response({"status": "ok", "item": rec}, headers=server._NO_STORE)  # noqa: SLF001


def add_routes(app) -> None:  # noqa: ANN001
    app.router.add_get("/api/chat", api_chat)
    app.router.add_post("/api/chat", api_chat_send)
