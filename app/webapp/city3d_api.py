"""The data of the 3D district (owner request 30.10, docs/PLAN_CITY3D.md).

    GET /api/city3d/map?t=<signed token from the city's «Город в 3D»>

The page itself is static (webapp/city3d/<version>/); the doors it shows press the player's window through the app.
"""

from __future__ import annotations

import json
import logging

from aiohttp import web

log = logging.getLogger(__name__)

_NO_STORE = {"Cache-Control": "no-store, max-age=0", "CDN-Cache-Control": "no-store"}


def _json(data: dict, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, headers=_NO_STORE, dumps=lambda o: json.dumps(o, ensure_ascii=False))


async def city_map(request: web.Request) -> web.Response:
    from app.services import city3d
    from app.webapp.server import UI_KEY

    token = str(request.query.get("t") or "")[:400]
    try:
        return _json(await city3d.map_for(request.app[UI_KEY], token))
    except Exception:
        log.exception("city3d: map failed")
        return _json({"ok": False, "status": "error"}, 500)


async def city_hit(request: web.Request) -> web.Response:
    """POST {"t": token, "n": resident id, "s": the ride's speed}: the ride hit a resident (owner request 30.10)."""
    from app.services import city3d
    from app.webapp.server import UI_KEY

    try:
        d = await request.json()
        token, npc, speed = str(d.get("t") or "")[:400], int(d.get("n") or 0), float(d.get("s") or 0)
    except Exception:
        return _json({"ok": False, "status": "bad_request"}, 400)
    try:
        return _json(await city3d.hit(request.app[UI_KEY], token, npc, max(0.0, min(speed, 80.0))))
    except Exception:
        log.exception("city3d: hit failed")
        return _json({"ok": False, "status": "error"}, 500)


async def _post(request: web.Request) -> tuple[dict, str] | None:
    try:
        d = await request.json()
        return (d if isinstance(d, dict) else {}), str(d.get("t") or "")[:400]
    except Exception:
        return None


async def city_pos(request: web.Request) -> web.Response:
    """POST {"t", "x", "z", "yaw", "drv", "room"}: the player's spot; the answer lists the other players of the district."""
    from app.services import city3d
    from app.webapp.server import UI_KEY

    got = await _post(request)
    if got is None:
        return _json({"ok": False, "status": "bad_request"}, 400)
    try:
        return _json(await city3d.pos(request.app[UI_KEY], got[1], got[0]))
    except Exception:
        log.exception("city3d: pos failed")
        return _json({"ok": False, "status": "error"}, 500)


async def city_look(request: web.Request) -> web.Response:
    """GET ?t=&c=<character id>: the figure of a character as PNG (oneself or a player on the same street)."""
    from app.services import city3d
    from app.webapp.server import UI_KEY

    try:
        c = int(request.query.get("c") or 0)
        png = await city3d.look_png(request.app[UI_KEY], str(request.query.get("t") or "")[:400], c)
    except Exception:
        log.exception("city3d: look failed")
        png = None
    if png is None:
        return web.Response(status=404, headers=_NO_STORE)
    return web.Response(body=png, content_type="image/png", headers={"Cache-Control": "private, max-age=300"})


async def city_escape(request: web.Request) -> web.Response:
    from app.services import city3d
    from app.webapp.server import UI_KEY

    got = await _post(request)
    if got is None:
        return _json({"ok": False, "status": "bad_request"}, 400)
    try:
        return _json(await city3d.escape(request.app[UI_KEY], got[1]))
    except Exception:
        log.exception("city3d: escape failed")
        return _json({"ok": False, "status": "error"}, 500)


async def city_crash(request: web.Request) -> web.Response:
    from app.services import city3d
    from app.webapp.server import UI_KEY

    got = await _post(request)
    if got is None:
        return _json({"ok": False, "status": "bad_request"}, 400)
    try:
        sp = max(0.0, min(float(got[0].get("s") or 0), 80.0))
        return _json(await city3d.crash(request.app[UI_KEY], got[1], sp))
    except Exception:
        log.exception("city3d: crash failed")
        return _json({"ok": False, "status": "error"}, 500)


async def unity_metric(request: web.Request) -> web.Response:
    """POST {"t", load_ms, fps, mem_mb, ...}: the numbers of the device from the Unity client (docs/PLAN_UNITY.md)."""
    from app.services import unity3d
    from app.webapp.server import UI_KEY

    got = await _post(request)
    if got is None:
        return _json({"ok": False, "status": "bad_request"}, 400)
    try:
        return _json(unity3d.metric(request.app[UI_KEY], got[1], got[0]))
    except Exception:
        log.exception("unity: metric failed")
        return _json({"ok": False, "status": "error"}, 500)


async def fight3d_state(request: web.Request) -> web.Response:
    """GET ?t=: the player's fight for the Unity client (docs/PLAN_FIGHT3D.md phase 2)."""
    from app.services import fight3d
    from app.webapp.server import UI_KEY

    try:
        return _json(await fight3d.state(request.app[UI_KEY], str(request.query.get("t") or "")[:400]))
    except Exception:
        log.exception("fight3d: state failed")
        return _json({"ok": False, "status": "error"}, 500)


async def fight3d_post(request: web.Request) -> web.Response:
    """POST /api/fight3d/{hit|hurt|leave}: one strike of the player or of an enemy, or the client closing."""
    from app.services import fight3d
    from app.webapp.server import UI_KEY

    got = await _post(request)
    if got is None:
        return _json({"ok": False, "status": "bad_request"}, 400)
    d, token = got
    what = request.match_info.get("what", "")
    ui = request.app[UI_KEY]
    try:
        idx, melee = int(d.get("i", -1) or 0), bool(d.get("m"))
        if what == "hit":
            return _json(await fight3d.hit(ui, token, idx, str(d.get("w") or "")[:48], melee))
        if what == "hurt":
            return _json(await fight3d.hurt(ui, token, idx, melee))
        if what == "leave":
            return _json(await fight3d.leave(ui, token))
        return _json({"ok": False, "status": "bad_request"}, 400)
    except Exception:
        log.exception("fight3d: %s failed", what)
        return _json({"ok": False, "status": "error"}, 500)


def add_routes(app: web.Application) -> None:
    app.router.add_post("/api/unity/metric", unity_metric)
    app.router.add_get("/api/fight3d/state", fight3d_state)  # owner request 31.10: the fight in first person
    app.router.add_post("/api/fight3d/{what}", fight3d_post)  # owner request 31.10: the Unity client
    app.router.add_post("/api/city3d/pos", city_pos)
    app.router.add_get("/api/city3d/look", city_look)
    app.router.add_post("/api/city3d/escape", city_escape)
    app.router.add_post("/api/city3d/crash", city_crash)
    app.router.add_get("/api/city3d/map", city_map)
    app.router.add_post("/api/city3d/hit", city_hit)
