"""The Unity client in the Mini App (owner request 31.10, docs/PLAN_UNITY.md).

The client is a static WebGL build in webapp/unity/<version>/; webapp/unity/current.json names the live version. It
reads the same data as the three.js district (/api/city3d/map with the `city3d` token) and presses the same doors.
Phase 0 also sends the device's numbers (load time, memory, frames) to POST /api/unity/metric.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from app.config import BASE_DIR
from app.content.loader import Content

log = logging.getLogger(__name__)

CURRENT = BASE_DIR / "webapp" / "unity" / "current.json"
METRICS = BASE_DIR / "data" / "unity" / "metrics.jsonl"
METRIC_KEYS = ("load_ms", "bytes", "ua", "screen", "dpr", "fps", "mem_mb", "heap_mb", "gpu", "device", "sys_mb",
               "stage", "error", "version", "tg")


def current() -> dict[str, Any] | None:
    try:
        data = json.loads(CURRENT.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and data.get("v") else None
    except (OSError, ValueError):
        return None


def url(content: Content, bot_token: str, tg_id: int) -> str | None:
    """The live build's page on the Mini App's origin with the district's token, or None before the first build."""
    from app.services import arcade, city3d

    cur = current()
    if cur is None:
        return None
    origin = arcade.cfg(content).web.base_url.split("/static/")[0].rstrip("/")
    return f"{origin}/static/unity/{cur['v']}/index.html#t={arcade.web_token(bot_token, tg_id, city3d.GAME)}"


def metric(ui: Any, token: str, data: dict[str, Any]) -> dict[str, Any]:
    """Store one report of the device (only for a valid token); strings are cut, numbers kept."""
    from app.services import arcade, city3d

    who = arcade.read_web_token(ui.settings.bot_token, token, arcade.cfg(ui.content).web.token_ttl_h)
    if who is None or who.get("g") != city3d.GAME:
        return {"ok": False, "status": "auth"}
    rec: dict[str, Any] = {"at": time.time(), "u": int(who["u"])}
    for k in METRIC_KEYS:
        v = data.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            rec[k] = round(float(v), 1)
        elif isinstance(v, str):
            rec[k] = v[:300]
    METRICS.parent.mkdir(parents=True, exist_ok=True)
    with METRICS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    log.info("unity metric: %s", rec)
    return {"ok": True}
