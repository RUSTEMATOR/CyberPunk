"""Hooks that plug the Mini App into a running bot without editing the bot's own files (docs/WEBAPP.md,
"Self-contained package"). Installed by app/webapp/boot.py before app.main runs:

- `UiKit.start` / `UiKit.stop`: start and stop the app server, the nginx check and the activation loop;
- `WindowManager.show / open / _refine / collapse`: every saved window change goes to the app bus;
- the kit's `outbox.edit`: an edit caused by an app action goes with low priority;
- `build_routers`: the `/app` command router goes first.

Every hook is guarded: a failure is logged and the bot runs on without the app. If the bot's code ever wires the
app itself (WindowManager has `listeners`), the window hooks stand down so nothing is published twice.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from typing import Any

log = logging.getLogger("app.webapp.runtime")

PRUNE_S = 6 * 3600

_state: dict[str, Any] = {"server": None, "tasks": [], "installed": False}


def _admin_notifier(ui):  # noqa: ANN001, ANN202
    async def notify(text: str) -> None:
        for admin_id in sorted(set(ui.settings.admin_ids) | set(getattr(ui, "bootstrap_admins", set()))):
            try:
                await ui.bot.send_message(admin_id, text)
            except Exception as exc:
                log.warning("webapp: could not write to admin %s: %s", admin_id, exc)

    return notify


async def _loop(every_s: float, first_s: float, fn) -> None:  # noqa: ANN001
    await asyncio.sleep(first_s)
    while True:
        try:
            await fn()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("webapp: background job failed")
        await asyncio.sleep(every_s)


async def start(ui) -> None:  # noqa: ANN001
    from app.webapp import activation, announce, gateway
    from app.webapp import nginx as webapp_nginx
    from app.webapp import settings as wsettings
    from app.webapp.server import WebappServer

    if _state["server"] is not None or not wsettings.get().enabled:
        return
    await _ensure_tables()
    _wrap_outbox(ui)
    from app.webapp import emoji, hud

    hud.UI = ui
    from app.webapp import games

    games.UI = ui
    emoji.setup(ui)
    server = WebappServer(ui)
    if not await server.start():
        return
    _state["server"] = server
    notify = _admin_notifier(ui)
    loop = asyncio.get_running_loop()
    _state["tasks"] = [
        loop.create_task(_loop(1e9, 0, lambda: webapp_nginx.ensure(notify))),
        loop.create_task(_loop(activation.CHECK_S, 30, lambda: activation.check(ui, notify))),
        loop.create_task(_loop(PRUNE_S, 60, gateway.prune_ops)),
        loop.create_task(_loop(1e9, 20, lambda: announce.run(ui))),
        loop.create_task(_loop(6 * 3600, 5, emoji.prefetch)),
    ]


async def stop() -> None:
    for t in _state["tasks"]:
        t.cancel()
    if _state["tasks"]:
        await asyncio.gather(*_state["tasks"], return_exceptions=True)
    _state["tasks"] = []
    server = _state["server"]
    _state["server"] = None
    if server is not None:
        await server.stop()


async def _ensure_tables() -> None:
    """The app's own tables, whatever the order of imports was (create_all with checkfirst is idempotent)."""
    from app.db import session as db
    from app.webapp.models import WebappOp, WebappScreen

    engine = db._engine  # noqa: SLF001
    if engine is None:
        return
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: WebappOp.metadata.create_all(
            c, tables=[WebappOp.__table__, WebappScreen.__table__], checkfirst=True))


def _wrap_outbox(ui) -> None:  # noqa: ANN001
    from app.webapp.origin import ORIGIN

    outbox = ui.outbox
    if getattr(outbox, "_webapp_wrapped", False):
        return
    orig = outbox.edit

    @functools.wraps(orig)
    def edit(chat_id, message_id, text, reply_markup=None, rich=None, low=False):  # noqa: ANN001, ANN202
        return orig(chat_id, message_id, text, reply_markup, rich, low=low or ORIGIN.get() == "webapp")

    outbox.edit = edit
    outbox._webapp_wrapped = True  # noqa: SLF001


def install() -> None:
    """Patch the bot's classes. Called once, before app.main is imported."""
    if _state["installed"]:
        return
    _state["installed"] = True
    import app.webapp.models  # noqa: F401  (tables join Base.metadata before create_all)
    from app.bot import handlers as handlers_pkg
    from app.bot.ui import kit as kit_mod
    from app.bot.ui import window as window_mod
    from app.webapp import bus

    # routers: /app before the "Скоро" stubs and the text input
    orig_build = handlers_pkg.build_routers

    @functools.wraps(orig_build)
    def build_routers():  # noqa: ANN202
        routers = orig_build()
        try:
            from app.webapp.tg import router

            out = [router, *routers]
        except Exception:
            log.exception("webapp: /app router not added")
            out = list(routers)
        try:  # W11: the Telegram game «Протокол взлома» (/hack and its Play button)
            from app.webapp.games import router as games_router

            out = [games_router, *out]
        except Exception:
            log.exception("webapp: games router not added")
        try:  # owner request 25.10: the group chat in the app listens first and never stops a message
            from app.webapp.chat import router as chat_router

            return [chat_router, *out]
        except Exception:
            log.exception("webapp: chat router not added")
            return out

    handlers_pkg.build_routers = build_routers

    # the kit: start and stop the app with the UI services
    UiKit = kit_mod.UiKit
    orig_start, orig_stop = UiKit.start, UiKit.stop

    @functools.wraps(orig_start)
    async def kit_start(self, *a, **k):  # noqa: ANN001, ANN202
        res = await orig_start(self, *a, **k)
        try:
            await start(self)
        except Exception:
            log.exception("webapp: start failed; the bot runs without the Mini App")
        return res

    @functools.wraps(orig_stop)
    async def kit_stop(self, *a, **k):  # noqa: ANN001, ANN202
        try:
            await stop()
        except Exception:
            log.exception("webapp: stop failed")
        return await orig_stop(self, *a, **k)

    UiKit.start, UiKit.stop = kit_start, kit_stop

    # windows: publish every saved change, unless the bot already does it itself
    WM = window_mod.WindowManager
    if hasattr(WM, "_notify"):
        log.info("webapp: WindowManager publishes changes itself, window hooks skipped")
        return
    orig_show, orig_open, orig_refine, orig_collapse = WM.show, WM.open, WM._refine, WM.collapse  # noqa: SLF001

    @functools.wraps(orig_show)
    async def show(self, user_id, screen, chat_id=None):  # noqa: ANN001, ANN202
        res = await orig_show(self, user_id, screen, chat_id)
        bus.on_window_change(user_id, screen)
        return res

    @functools.wraps(orig_open)
    async def open_(self, chat_id, user_id, screen):  # noqa: ANN001, ANN202
        res = await orig_open(self, chat_id, user_id, screen)
        if res is not None:
            bus.on_window_change(user_id, screen)
        return res

    @functools.wraps(orig_refine)
    async def refine(self, user_id, token, screen):  # noqa: ANN001, ANN202
        res = await orig_refine(self, user_id, token, screen)
        if res:
            bus.on_window_change(user_id, screen)
        return res

    @functools.wraps(orig_collapse)
    async def collapse(self, user_id, name=None):  # noqa: ANN001, ANN202
        res = await orig_collapse(self, user_id, name)
        if res:
            bus.on_window_change(user_id, None)
        return res

    WM.show, WM.open, WM._refine, WM.collapse = show, open_, refine, collapse  # noqa: SLF001
