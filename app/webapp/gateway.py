"""The Mini App runs the same screen handlers as the group window (docs/WEBAPP.md, "One game, two windows").

A press from the app goes through exactly what a button press in the group goes through: the player's lock, the
actor date, the check that the button belongs to the screen the window shows now, the death offer first, the
handler, `WindowManager.show`. So the bot and the app can never disagree: there is one window row per player, and
both are views of it. What the app adds is the op id: every action carries one, the answer is stored under it, and
a retried request (lost answer, reconnect, app reopened offline) gets the stored answer instead of a second run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete

from app.bot.ui import callbacks as cb
from app.bot.ui.callbacks import Ctx, _snapshot
from app.bot.ui.common import error_screen
from app.clock import utcnow
from app.db import repo
from app.db import session as db
from app.services import world as world_service
from app.webapp import bus
from app.webapp.models import WebappOp, WebappScreen
from app.webapp.origin import ORIGIN
from app.webapp.auth import WebUser

if TYPE_CHECKING:
    from app.bot.ui.kit import UiKit

log = logging.getLogger(__name__)

OP_KEEP = timedelta(days=2)
OP_ID_MAX = 64


@dataclass
class OpResult:
    status: str                       # ok | stale | closed | error | duplicate
    envelope: dict[str, Any] | None   # the screen the app should show now

    def as_json(self) -> dict[str, Any]:
        return {"status": self.status, **(self.envelope or {})}


def _ctx(ui: "UiKit", user: WebUser, snap: cb.WindowSnapshot | None) -> Ctx:
    chat_id = snap.chat_id if snap is not None else (ui.group_id or 0)
    return Ctx(ui, user.id, user.username, user.full_name, chat_id, snap)


async def _window(user_id: int) -> cb.WindowSnapshot | None:
    async with db.session_scope() as s:
        return _snapshot(await repo.get_window(s, user_id))


async def state(ui: "UiKit", user: WebUser) -> dict[str, Any]:
    """The screen the player's window shows now. After a restart it comes from `webapp_screens`; a window the app
    has never seen, or a closed one, is answered as closed and the app offers to open it."""
    ui.windows.set_owner(user.id, user.full_name)
    snap = await _window(user.id)
    if snap is None or snap.collapsed:
        return {"status": "closed", **bus._envelope(user.id, {"name": "", "html": "", "rows": [],  # noqa: SLF001
                                                              "awaiting_text": None, "closed": True})}
    from app.webapp import hud

    if hud.get(user.id) is None:
        await hud.refresh_bounded(user.id)
    env = bus.current(user.id)
    if env is not None and env["screen"].get("name") == snap.screen:
        return {"status": "ok", **env}
    async with db.session_scope() as s:
        row = await s.get(WebappScreen, user.id)
        payload = dict(row.payload) if row is not None and row.screen == snap.screen else None
    if payload is not None:
        return {"status": "ok", **bus.remember(user.id, payload)}
    return {"status": "unknown", **bus._envelope(user.id, {"name": snap.screen, "html": "", "rows": [],  # noqa: SLF001
                                                            "awaiting_text": None, "unknown": True})}


async def _stored(op_id: str) -> OpResult | None:
    async with db.session_scope() as s:
        row = await s.get(WebappOp, op_id)
        if row is None:
            return None
        return OpResult("duplicate", dict(row.response or {}))


async def _store(op_id: str, user_id: int, res: OpResult) -> None:
    try:
        async with db.session_scope() as s:
            s.add(WebappOp(op_id=op_id, player_tg_id=user_id, status=res.status,
                           response=res.envelope or {}, created_at=utcnow()))
    except Exception:
        log.exception("webapp: could not store op %s", op_id)


async def prune_ops() -> None:
    async with db.session_scope() as s:
        await s.execute(delete(WebappOp).where(WebappOp.created_at < utcnow() - OP_KEEP))


def _now_envelope(user_id: int) -> dict[str, Any] | None:
    return bus.current(user_id)


async def _run(ui: "UiKit", user: WebUser, op_id: str, body) -> OpResult:  # noqa: ANN001
    """Common frame of every app action: lock, op id, actor, origin, error screen."""
    if not op_id or len(op_id) > OP_ID_MAX:
        return OpResult("error", None)
    ui.windows.set_owner(user.id, user.full_name)
    async with ui.lock_for(user.id):
        prev = await _stored(op_id)
        if prev is not None:
            return prev
        actor = world_service.set_actor(user.id)
        origin = ORIGIN.set("webapp")
        try:
            res = await body()
        except Exception:
            log.exception("webapp action failed for %s", user.id)
            try:
                await ui.windows.show(user.id, error_screen(), ui.group_id)
            except Exception:
                log.exception("webapp: could not show the error screen")
            res = OpResult("error", _now_envelope(user.id))
        finally:
            ORIGIN.reset(origin)
            world_service.reset_actor(actor)
        if res.status in ("ok", "error"):  # the card comes with the screen it belongs to (W1)
            from app.webapp import hud

            await hud.refresh_bounded(user.id)
            res = OpResult(res.status, _now_envelope(user.id))
        await _store(op_id, user.id, res)
        return res


async def press(ui: "UiKit", user: WebUser, op_id: str, screen: str, action: str, arg: str | None) -> OpResult:
    async def body() -> OpResult:
        snap = await _window(user.id)
        if snap is None or snap.collapsed:
            return OpResult("closed", _now_envelope(user.id))
        if screen != snap.screen:
            return OpResult("stale", _now_envelope(user.id))  # the same rule as an old button in the group
        async with db.session_scope() as s:
            await repo.touch_window(s, user.id)
        handler = cb._screens.get(screen)  # noqa: SLF001
        name, act, a = screen, action, arg
        if handler is None:
            log.warning("webapp: no handler for screen %r", screen)
            handler, name, act, a = cb._screens["menu"], "menu", "open", None  # noqa: SLF001
        if name not in ("deathoffer", "deathback") and "deathoffer" in cb._screens:  # noqa: SLF001
            from app.services import death as death_service

            async with db.session_scope() as s:
                if await death_service.has_pending(s, user.id):
                    handler, act, a = cb._screens["deathoffer"], "open", None  # noqa: SLF001
        ctx = _ctx(ui, user, snap)
        nxt = await handler(ctx, act, a)
        if nxt is not None:
            await ui.windows.show(user.id, nxt, snap.chat_id)
        return OpResult("ok", _now_envelope(user.id))

    return await _run(ui, user, op_id, body)


async def send_text(ui: "UiKit", user: WebUser, op_id: str, text: str) -> OpResult:
    async def body() -> OpResult:
        snap = await _window(user.id)
        if snap is None or snap.collapsed:
            return OpResult("closed", _now_envelope(user.id))
        handler = cb._texts.get(snap.awaiting_text or "")  # noqa: SLF001
        if handler is None:
            return OpResult("stale", _now_envelope(user.id))
        ctx = _ctx(ui, user, snap)
        nxt = await handler(ctx, text.strip())
        if nxt is not None:
            await ui.windows.show(user.id, nxt, snap.chat_id)
        else:
            await ui.windows.touch(user.id)
        return OpResult("ok", _now_envelope(user.id))

    return await _run(ui, user, op_id, body)


def nav_targets() -> set[str]:
    from app.bot import texts

    return {"menu"} | {k for k, _ in texts.MENU_SECTIONS}


async def nav(ui: "UiKit", user: WebUser, op_id: str, to: str) -> OpResult:
    """W12, the tabs of the app: what /play does from any screen, then the section tile of the menu. Whatever the
    bot puts first (a cell, a pending event, a death offer) stays first, exactly as after /play in the group."""
    async def body() -> OpResult:
        from app.bot.handlers.menu import menu_handler
        from app.bot.handlers.onboarding import entry_screen

        snap = await _window(user.id)
        chat_id = snap.chat_id if snap is not None else ui.group_id
        if chat_id is None:
            return OpResult("error", None)
        first = await entry_screen(_ctx(ui, user, snap))
        await ui.windows.show(user.id, first, chat_id)
        if to != "menu" and first.name == "menu":
            snap = await _window(user.id)
            nxt = await menu_handler(_ctx(ui, user, snap), "go", to)
            if nxt is not None:
                await ui.windows.show(user.id, nxt, snap.chat_id if snap is not None else chat_id)
        return OpResult("ok", _now_envelope(user.id))

    return await _run(ui, user, op_id, body)


async def open_window(ui: "UiKit", user: WebUser, op_id: str) -> OpResult:
    """What /play does, for the app: the entry screen in the player's window. A closed window is written back in
    place (no new message at the bottom of the chat); a player without a window gets one in the group."""
    async def body() -> OpResult:
        from app.bot.handlers.onboarding import entry_screen

        if ui.group_id is None:
            return OpResult("error", None)
        snap = await _window(user.id)
        ctx = _ctx(ui, user, snap)
        await ui.windows.show(user.id, await entry_screen(ctx), ui.group_id)
        return OpResult("ok", _now_envelope(user.id))

    return await _run(ui, user, op_id, body)
