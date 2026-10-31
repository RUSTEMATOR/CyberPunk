"""The score endpoint of the open-source HTML5 arcade (owner request 29.10, docs/PLAN_ARCADE_WEB.md).

    POST /api/arcade/score  {"t": signed token from the bot's «Аркада», "s": score}

The games themselves are static files in webapp/arcade/ (served by the Mini App's /static/). The token carries the
player and the game; the record and the money go through app/services/arcade.py::web_score.
"""

from __future__ import annotations

import json
import logging

from aiohttp import web

log = logging.getLogger(__name__)

_NO_STORE = {"Cache-Control": "no-store, max-age=0", "CDN-Cache-Control": "no-store"}


def _json(data: dict, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, headers=_NO_STORE, dumps=lambda o: json.dumps(o, ensure_ascii=False))


async def score(request: web.Request) -> web.Response:
    from app.services import arcade
    from app.webapp.server import UI_KEY

    try:
        d = await request.json()
        s = int(d.get("s") or 0)
        token = str(d.get("t") or "")
    except Exception:
        return _json({"status": "bad_request"}, 400)
    try:
        return _json(await arcade.web_score(request.app[UI_KEY], token, s))
    except Exception:
        log.exception("arcade: score failed")
        return _json({"status": "error"}, 500)


def add_routes(app: web.Application) -> None:
    app.router.add_post("/api/arcade/score", score)
