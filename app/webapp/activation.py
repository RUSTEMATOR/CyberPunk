"""The Mini App turns itself on when the outside world can reach it (docs/WEBAPP.md, "Going live").

Every CHECK_S the bot asks `<webapp_url>/healthz` over the public internet (Cloudflare, the tunnel, nginx, back into
this process). Only an answer carrying this process's boot id counts: then the private-chat menu button opens the
app and `/app` hands out links. Three failed checks in a row take the button back. The first time the app goes
live the admins get one message, so nobody has to watch the server for it.
"""

from __future__ import annotations

import logging
import socket
from typing import TYPE_CHECKING

from urllib.parse import urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver
from aiogram.types import MenuButtonDefault, MenuButtonWebApp, WebAppInfo

from app.db import repo
from app.db import session as db
from app.webapp import bus
from app.webapp import settings as wsettings

if TYPE_CHECKING:
    from app.bot.ui.kit import UiKit

log = logging.getLogger(__name__)

CHECK_S = 120
DOH_URL = "https://cloudflare-dns.com/dns-query"
FAILS_TO_DROP = 3
NOTICE_KEY = "webapp_live_notice_v1"
MENU_TEXT = "Найт-Сити"

_state = {"live": False, "fails": 0, "warned": False}


def is_live() -> bool:
    return bool(_state["live"])


def url(ui: "UiKit") -> str:
    return wsettings.get().url.rstrip("/")


class _Pinned(AbstractResolver):
    """Answers with addresses found over DNS-over-HTTPS: the Mac's resolver can keep a stale "no such host" for a
    new subdomain long after it exists (24.10: half an hour and more), while players' phones resolve it at once."""

    def __init__(self, ips: list[str]) -> None:
        self.ips = ips

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET) -> list[dict]:  # noqa: ARG002
        return [{"hostname": host, "host": ip, "port": port, "family": socket.AF_INET, "proto": 0,
                 "flags": socket.AI_NUMERICHOST} for ip in self.ips]

    async def close(self) -> None:
        return None


async def _doh(host: str) -> list[str]:
    conn = aiohttp.TCPConnector(family=socket.AF_INET)
    async with aiohttp.ClientSession(connector=conn, timeout=aiohttp.ClientTimeout(total=10)) as s:
        async with s.get(DOH_URL, params={"name": host, "type": "A"}, headers={"accept": "application/dns-json"}) as r:
            data = await r.json(content_type=None)
    return [a["data"] for a in (data.get("Answer") or []) if a.get("type") == 1 and a.get("data")]


async def _get_health(target: str, resolver: AbstractResolver | None = None) -> bool:
    conn = aiohttp.TCPConnector(family=socket.AF_INET, resolver=resolver) if resolver else \
        aiohttp.TCPConnector(family=socket.AF_INET)
    async with aiohttp.ClientSession(connector=conn, timeout=aiohttp.ClientTimeout(total=15)) as s:
        async with s.get(f"{target}/healthz", headers={"Cache-Control": "no-cache"}) as r:
            if r.status != 200:
                return False
            data = await r.json(content_type=None)
            return isinstance(data, dict) and data.get("boot") == bus.BOOT_ID


async def _probe(target: str) -> bool:
    try:
        return await _get_health(target)
    except Exception as exc:
        first = exc
    try:  # the system resolver failed: ask Cloudflare's DNS directly and connect to that address (same TLS name)
        host = urlsplit(target).hostname or ""
        ips = await _doh(host)
        if ips:
            return await _get_health(target, _Pinned(ips))
    except Exception as exc:
        log.info("webapp: %s/healthz not reachable yet: %s; over DoH: %s", target, first, exc)
        return False
    log.info("webapp: %s/healthz not reachable yet: %s", target, first)
    return False


async def check(ui: "UiKit", notify) -> None:  # noqa: ANN001
    target = url(ui)
    if not target.startswith("https://"):
        return  # Telegram opens Mini Apps over https only
    ok = await _probe(target)
    if ok:
        _state["fails"] = 0
        if not _state["live"]:
            try:
                await ui.bot.set_chat_menu_button(
                    menu_button=MenuButtonWebApp(text=MENU_TEXT, web_app=WebAppInfo(url=target + "/")))
            except Exception as exc:
                log.warning("webapp: could not set the menu button: %s", exc)
                return
            _state["live"] = True
            log.info("webapp: live at %s", target)
            async with db.session_scope() as s:
                told = await repo.ws_get(s, NOTICE_KEY)
                if not told:
                    await repo.ws_set(s, NOTICE_KEY, True)
            if not told:
                await notify(f"Мини-приложение доступно: {target}\nВ личке бота кнопка «{MENU_TEXT}», "
                             "в группе команда /app.")
        return
    _state["fails"] = int(_state["fails"]) + 1
    if _state["live"] and int(_state["fails"]) >= FAILS_TO_DROP:
        _state["live"] = False
        try:
            await ui.bot.set_chat_menu_button(menu_button=MenuButtonDefault())
        except Exception as exc:
            log.warning("webapp: could not reset the menu button: %s", exc)
        log.warning("webapp: %s stopped answering, menu button taken back", target)
