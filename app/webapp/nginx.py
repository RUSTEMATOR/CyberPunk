"""The bot keeps its own nginx host block in place (owner decision 24.10, docs/WEBAPP.md, "nginx").

The Mac mini serves every *.qa-temple-of-serenity.cc host through Cloudflare Tunnel -> nginx :8080 (Homebrew,
`servers/*.conf`). The Mini App's block lives in the project (`deploy/nginx/nightcity.conf`). On start the bot
copies it into nginx when it differs from what nginx has, runs `nginx -t`, and reloads nginx only when the test
passes; a failed test puts the previous file back (or takes ours out), so the other hosts never go down because
of this one. The result goes to the admins once per version of the file.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
import sys
from pathlib import Path

from app.config import BASE_DIR
from app.db import repo
from app.db import session as db

log = logging.getLogger(__name__)

SRC = BASE_DIR / "deploy" / "nginx" / "nightcity.conf"
DST = Path("/opt/homebrew/etc/nginx/servers/nightcity.conf")
KEY = "nginx_nightcity_applied"
TIMEOUT_S = 20


def _nginx_bin() -> str | None:
    found = shutil.which("nginx")
    if found:
        return found
    for cand in ("/opt/homebrew/bin/nginx", "/usr/local/bin/nginx"):
        if Path(cand).exists():
            return cand
    return None


async def _run(*args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        return 124, "timeout"
    return proc.returncode or 0, (out or b"").decode("utf-8", "replace")[-1500:]


async def ensure(notify) -> None:  # noqa: ANN001
    if sys.platform != "darwin" or not SRC.exists() or not DST.parent.is_dir():
        return
    nginx = _nginx_bin()
    if nginx is None:
        log.warning("nginx: binary not found, %s not applied", SRC.name)
        return
    want = SRC.read_text(encoding="utf-8")
    digest = hashlib.md5(want.encode()).hexdigest()
    async with db.session_scope() as s:
        done = await repo.ws_get(s, KEY)
    have = DST.read_text(encoding="utf-8") if DST.exists() else None
    if done in (digest, f"{digest}:failed") and (have == want or done.endswith(":failed")):
        return
    backup = have
    if have != want:
        DST.write_text(want, encoding="utf-8")
    code, out = await _run(nginx, "-t")
    if code != 0:
        if backup is not None and backup != want:
            DST.write_text(backup, encoding="utf-8")
        elif DST.exists():
            DST.rename(DST.with_suffix(".conf.disabled"))
        async with db.session_scope() as s:
            await repo.ws_set(s, KEY, f"{digest}:failed")
        log.warning("nginx: test failed, %s rolled back: %s", DST.name, out)
        await notify(f"nginx: проверка не прошла, {DST.name} откатан, остальные хосты не тронуты.\n\n<pre>{_esc(out)}</pre>")
        return
    code, out2 = await _run(nginx, "-s", "reload")
    async with db.session_scope() as s:
        await repo.ws_set(s, KEY, digest if code == 0 else f"{digest}:failed")
    if code == 0:
        log.info("nginx: %s applied and reloaded", DST.name)
        await notify(f"nginx: хост мини-приложения на месте ({DST.name}), nginx перечитал настройки.")
    else:
        log.warning("nginx: reload failed: %s", out2)
        await notify(f"nginx: файл {DST.name} на месте и проверен, но перезагрузка не удалась. "
                     f"Он заработает при следующем перезапуске nginx.\n\n<pre>{_esc(out2)}</pre>")


def _esc(s: str) -> str:
    from html import escape

    return escape(s)
