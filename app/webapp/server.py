"""HTTP side of the Mini App, inside the bot process (docs/WEBAPP.md, "API").

    GET  /healthz        {"ok": true, "boot": ...}  (activation check from outside, nginx probes)
    GET  /api/state      the screen the player's window shows now
    POST /api/press      {"op": id, "screen", "action", "arg"}  a button
    POST /api/text       {"op": id, "text"}                      typed input the window waits for
    POST /api/open       {"op": id}                              /play from the app
    POST /api/nav        {"op": id, "to": menu|<section>}         W12 tabs: /play, then the section tile
    GET  /api/events     Server-Sent Events: every change of the player's window, heartbeat every HEARTBEAT_S
    GET  /               the app (static files from the project's webapp/ folder)

Auth: the Telegram initData string in the `X-Init-Data` header (the event stream takes it as `?init=`, an
EventSource cannot send headers). The server only listens on 127.0.0.1; nginx and the Cloudflare tunnel are the way in.
A failure here never touches the bot: the server starts in its own try block and is logged when it cannot bind.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aiohttp import web

from app.config import BASE_DIR
from app.webapp import auth, bus, gateway
from app.webapp import settings as wsettings

if TYPE_CHECKING:
    from app.bot.ui.kit import UiKit

log = logging.getLogger(__name__)

STATIC_DIR = BASE_DIR / "webapp"
HEARTBEAT_S = 15          # Cloudflare closes a silent connection after 100 s
TEXT_MAX = 1000
UI_KEY = web.AppKey("ui", object)

_NO_STORE = {"Cache-Control": "no-store, max-age=0", "CDN-Cache-Control": "no-store"}


def _json(data: Any, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, headers=_NO_STORE, dumps=lambda o: json.dumps(o, ensure_ascii=False))


async def _user(request: web.Request) -> auth.WebUser:
    ui: UiKit = request.app[UI_KEY]  # type: ignore[assignment]
    init = request.headers.get("X-Init-Data") or request.query.get("init", "")
    try:
        user = auth.parse(ui.settings.bot_token, init)
    except auth.AuthError as exc:
        raise web.HTTPUnauthorized(text=json.dumps({"status": "auth", "error": str(exc)}),
                                   content_type="application/json") from exc
    if not await auth.allowed(ui, user.id):
        raise web.HTTPForbidden(text=json.dumps({"status": "forbidden"}), content_type="application/json")
    return user


async def _body(request: web.Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception as exc:
        raise web.HTTPBadRequest(text='{"status":"bad_request"}', content_type="application/json") from exc
    if not isinstance(data, dict):
        raise web.HTTPBadRequest(text='{"status":"bad_request"}', content_type="application/json")
    return data


def _s(v: Any, limit: int = 64) -> str | None:
    if v is None:
        return None
    v = str(v)
    return v[:limit]


async def healthz(request: web.Request) -> web.Response:
    return _json({"ok": True, "boot": bus.BOOT_ID})


async def api_state(request: web.Request) -> web.Response:
    ui = request.app[UI_KEY]
    user = await _user(request)
    return _json(await gateway.state(ui, user))  # type: ignore[arg-type]


async def api_press(request: web.Request) -> web.Response:
    ui = request.app[UI_KEY]
    user = await _user(request)
    d = await _body(request)
    screen, action = _s(d.get("screen"), 48), _s(d.get("action"), 48)
    if not screen or not action:
        return _json({"status": "bad_request"}, 400)
    res = await gateway.press(ui, user, _s(d.get("op")) or "", screen, action, _s(d.get("arg")))  # type: ignore[arg-type]
    return _json(res.as_json())


async def api_text(request: web.Request) -> web.Response:
    ui = request.app[UI_KEY]
    user = await _user(request)
    d = await _body(request)
    text = str(d.get("text") or "").strip()[:TEXT_MAX]
    if not text:
        return _json({"status": "bad_request"}, 400)
    res = await gateway.send_text(ui, user, _s(d.get("op")) or "", text)  # type: ignore[arg-type]
    return _json(res.as_json())


async def api_open(request: web.Request) -> web.Response:
    ui = request.app[UI_KEY]
    user = await _user(request)
    d = await _body(request)
    res = await gateway.open_window(ui, user, _s(d.get("op")) or "")  # type: ignore[arg-type]
    return _json(res.as_json())


async def api_nav(request: web.Request) -> web.Response:
    ui = request.app[UI_KEY]
    user = await _user(request)
    d = await _body(request)
    to = _s(d.get("to")) or ""
    if to not in gateway.nav_targets():
        return _json({"error": "bad target"}, status=400)
    res = await gateway.nav(ui, user, _s(d.get("op")) or "", to)  # type: ignore[arg-type]
    return _json(res.as_json())


async def api_events(request: web.Request) -> web.StreamResponse:
    ui = request.app[UI_KEY]
    user = await _user(request)
    resp = web.StreamResponse(headers={
        "Content-Type": "text/event-stream; charset=utf-8", "X-Accel-Buffering": "no", **_NO_STORE,
    })
    await resp.prepare(request)
    q = bus.subscribe(user.id)
    try:
        first = await gateway.state(ui, user)  # type: ignore[arg-type]
        await resp.write(f"data: {json.dumps(first, ensure_ascii=False)}\n\n".encode())
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=HEARTBEAT_S)
            except TimeoutError:
                await resp.write(b": ping\n\n")
                continue
            await resp.write(f"data: {json.dumps({'status': 'ok', **msg}, ensure_ascii=False)}\n\n".encode())
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        bus.unsubscribe(user.id, q)
    return resp


_LONG = {"Cache-Control": "public, max-age=2592000, immutable"}


async def emoji_file(request: web.Request) -> web.StreamResponse:
    """A premium emoji picture or video (app/webapp/emoji.py). Public: the same pictures every Telegram user sees."""
    from app.webapp import emoji

    name = request.match_info["name"]
    if name.endswith(".tgs"):  # W16: an animated emoji, played by the app with lottie-web
        from app.webapp import emoji_lottie

        lid = name[:-4]
        lpath = emoji_lottie.file_for(lid) if emoji.ID_RE.match(lid) else None
        if lpath is None:
            raise web.HTTPNotFound(headers={"Cache-Control": "no-store"})
        return web.FileResponse(lpath, headers={**_LONG, "Content-Type": "application/octet-stream"})
    video = name.endswith(".webm")
    eid = name[:-5] if video else name
    if not emoji.ID_RE.match(eid):
        raise web.HTTPNotFound()
    path = emoji.file_for(eid, video)
    if path is None and eid not in emoji._index:  # noqa: SLF001  a new id: fetch it now, briefly
        try:
            await asyncio.wait_for(emoji.ensure([eid]), timeout=8)
        except Exception:
            pass
        path = emoji.file_for(eid, video)
    if path is None:
        raise web.HTTPNotFound(headers={"Cache-Control": "no-store"})
    ctype = {"webm": "video/webm", "webp": "image/webp", "jpg": "image/jpeg", "png": "image/png"}.get(
        path.suffix.lstrip("."), "application/octet-stream")
    return web.FileResponse(path, headers={**_LONG, "Content-Type": ctype})


async def api_body(request: web.Request) -> web.Response:
    from app.webapp import living

    ui = request.app[UI_KEY]
    user = await _user(request)
    return _json({"status": "ok", "body": await living.body(ui, user.id)})  # type: ignore[arg-type]


async def api_fight(request: web.Request) -> web.Response:
    from app.webapp import living

    ui = request.app[UI_KEY]
    user = await _user(request)
    return _json({"status": "ok", "fight": await living.fight(ui, user.id)})  # type: ignore[arg-type]


async def game_page(request: web.Request) -> web.StreamResponse:
    """W11: the HTML5 game, opened by Telegram from a game message (no Mini App, no initData: a signed token)."""
    page = (STATIC_DIR / "game" / "index.html").read_text(encoding="utf-8")
    for name in ("game.css", "game.js"):
        page = page.replace(f"/static/game/{name}\"", f"/static/game/{name}?v={_stamp('game/' + name)}\"")
    return web.Response(text=page, content_type="text/html", headers=_NO_STORE)


async def api_game_score(request: web.Request) -> web.Response:
    from app.webapp import games

    ui = request.app[UI_KEY]
    d = await _body(request)
    try:
        score = int(d.get("score") or 0)
    except (TypeError, ValueError):
        return _json({"status": "bad_request"}, 400)
    return _json(await games.set_score(ui.settings.bot_token, str(d.get("t") or ""), score))  # type: ignore[attr-defined]


async def api_game_top(request: web.Request) -> web.Response:
    from app.webapp import games

    ui = request.app[UI_KEY]
    return _json(await games.top(ui.settings.bot_token, request.query.get("t", "")))  # type: ignore[attr-defined]


async def api_emoji(request: web.Request) -> web.Response:
    from app.webapp import emoji, emoji_lottie

    try:
        emoji_lottie.kick()
    except Exception:
        log.exception("emoji lottie: scan not started")
    return web.json_response({"video": emoji.video_ids(), "lottie": emoji_lottie.ids()},
                             headers={"Cache-Control": "public, max-age=60"})


def _stamp(name: str) -> str:
    try:
        return str(int((STATIC_DIR / name).stat().st_mtime))
    except OSError:
        return bus.BOOT_ID


async def index(request: web.Request) -> web.StreamResponse:
    """The page with versioned asset links: a phone that cached an old app.css or app.js gets the new ones after
    every deploy (owner report 24.10: Telegram on iOS kept the old stylesheet)."""
    page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    for name in ("app.css", "app.js", "fonts.css", "chat.css", "chat.js", "living.css", "living.js", "fx.css", "fx.js", "feedback.js",
                 "vendor/fx-vendor.js", "vendor/fx-vendor2.js", "vendor/augmented-ui.min.css", "glass.css", "fx2.js"):
        page = page.replace(f"/static/{name}\"", f"/static/{name}?v={_stamp(name)}\"")
    return web.Response(text=page, content_type="text/html", headers=_NO_STORE)


@web.middleware
async def _static_headers(request: web.Request, handler):  # noqa: ANN001
    resp = await handler(request)
    if request.path.startswith("/static/fonts/"):
        resp.headers.update(_LONG)  # font files never change under the same name
    elif request.path.startswith("/static/img/"):
        resp.headers["Cache-Control"] = "public, max-age=86400"  # place pictures (W12): a day is enough
    elif request.path.startswith("/static/unity/u") and "/Build/" in request.path:
        resp.headers.update(_LONG)  # owner request 31.10: a Unity build lives under its own version, it never changes
    elif request.path.startswith("/static/arcade/") or request.path.endswith("/three.module.min.js"):  # megabytes
        resp.headers["Cache-Control"] = "public, max-age=3600"  # the 3D page itself is no-store: it changes in place
    elif request.path.startswith("/static/"):
        resp.headers.update(_NO_STORE)  # tiny files: always fresh after a deploy, Cloudflare must not keep them
    resp.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    return resp


def build_app(ui: "UiKit") -> web.Application:
    app = web.Application(middlewares=[_static_headers], client_max_size=64 * 1024)
    app[UI_KEY] = ui
    app.router.add_get("/healthz", healthz)
    app.router.add_get("/api/state", api_state)
    app.router.add_post("/api/press", api_press)
    app.router.add_post("/api/text", api_text)
    app.router.add_post("/api/open", api_open)
    app.router.add_post("/api/nav", api_nav)
    app.router.add_get("/api/events", api_events)
    app.router.add_get("/api/emoji", api_emoji)
    app.router.add_get("/api/body", api_body)
    app.router.add_get("/game/", game_page)
    app.router.add_get("/game", game_page)
    app.router.add_post("/api/game/score", api_game_score)
    app.router.add_get("/api/game/top", api_game_top)
    app.router.add_get("/api/fight", api_fight)
    app.router.add_get("/emoji/{name}", emoji_file)
    app.router.add_get("/", index)
    try:  # owner request 25.10: the group chat in the app
        from app.webapp import chat

        chat.add_routes(app)
    except Exception:
        log.exception("webapp: chat routes not added")
    try:  # owner request 29.10: the score of the open-source HTML5 arcade (docs/PLAN_ARCADE_WEB.md)
        from app.webapp import arcade_api

        arcade_api.add_routes(app)
    except Exception:
        log.exception("webapp: arcade routes not added")
    try:  # owner request 30.10: the district in 3D (docs/PLAN_CITY3D.md)
        from app.webapp import city3d_api

        city3d_api.add_routes(app)
    except Exception:
        log.exception("webapp: city3d routes not added")
    if STATIC_DIR.is_dir():
        app.router.add_static("/static/", STATIC_DIR, show_index=False, follow_symlinks=False)
    return app


class WebappServer:
    def __init__(self, ui: "UiKit") -> None:
        self.ui = ui
        self._runner: web.AppRunner | None = None

    async def start(self) -> bool:
        s = wsettings.get()
        try:
            self._runner = web.AppRunner(build_app(self.ui), access_log=None, shutdown_timeout=3)
            await self._runner.setup()
            site = web.TCPSite(self._runner, s.host, s.port)
            await site.start()
            log.info("webapp: serving on http://%s:%s (static %s)", s.host, s.port,
                     "ok" if Path(STATIC_DIR / "index.html").exists() else "missing")
            return True
        except Exception:
            log.exception("webapp: could not start on %s:%s; the bot runs without the Mini App",
                          s.host, s.port)
            await self.stop()
            return False

    async def stop(self) -> None:
        if self._runner is not None:
            try:
                await self._runner.cleanup()
            except Exception:
                log.exception("webapp: stop failed")
            self._runner = None
        await bus.drain()
