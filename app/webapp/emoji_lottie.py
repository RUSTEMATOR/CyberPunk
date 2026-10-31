"""Animated (Lottie, .tgs) premium emoji for the Mini App (docs/WEBAPP.md W16).

`emoji.py` keeps only the still thumbnail of an animated emoji. This module finds which known emoji are animated,
downloads their .tgs once into data/emoji/ (gzip json, exactly as Telegram sends it) and serves it:

    GET /emoji/<id>.tgs    the animation; the app gunzips it and plays it with lottie-web
    GET /api/emoji         gains "lottie": [ids]

The scan starts on the first /api/emoji after a boot and runs in the background; until it ends the app shows the
still pictures, as before. Any failure leaves the still picture.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from app.webapp import emoji

log = logging.getLogger(__name__)

INDEX = emoji.DIR / "lottie.json"
_index: dict[str, str | None] = {}   # id -> .tgs file name, or None: not animated / not fetched
_loaded = False
_task: "asyncio.Task[None] | None" = None
_lock = asyncio.Lock()


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        _index.update(json.loads(INDEX.read_text(encoding="utf-8")))
    except Exception:
        pass


def _save() -> None:
    try:
        tmp = INDEX.with_suffix(".tmp")
        tmp.write_text(json.dumps(_index), encoding="utf-8")
        tmp.replace(INDEX)
    except Exception:
        log.exception("emoji lottie: index not saved")


def ids() -> list[str]:
    _load()
    return sorted(k for k, v in _index.items() if v and (emoji.DIR / v).exists())


def file_for(eid: str) -> Path | None:
    _load()
    name = _index.get(eid)
    if not name:
        return None
    p = emoji.DIR / name
    return p if p.exists() else None


async def _download(file_id: str) -> bytes:
    assert emoji.UI is not None
    buf = await emoji.UI.bot.download(file_id)
    return buf.read() if buf is not None else b""


async def _scan() -> None:
    _load()
    if emoji.UI is None:
        return
    async with _lock:
        want = [k for k in emoji._index if emoji.ID_RE.match(k) and k not in _index]  # noqa: SLF001
        for k in range(0, len(want), emoji.BATCH):
            batch = want[k:k + emoji.BATCH]
            try:
                stickers = list(await emoji.UI.bot.get_custom_emoji_stickers(custom_emoji_ids=batch))
            except Exception as exc:
                log.warning("emoji lottie: getCustomEmojiStickers failed: %s", exc)
                return
            sem = asyncio.Semaphore(emoji.PARALLEL)

            async def one(st: Any) -> None:
                eid = st.custom_emoji_id
                if not getattr(st, "is_animated", False):
                    _index[eid] = None
                    return
                async with sem:
                    try:
                        data = await _download(st.file_id)
                    except Exception as exc:
                        log.warning("emoji lottie: %s not fetched: %s", eid, exc)
                        return  # not recorded: tried again after the next boot
                if data[:2] == b"\x1f\x8b":
                    (emoji.DIR / f"{eid}.tgs").write_bytes(data)
                    _index[eid] = f"{eid}.tgs"
                else:
                    _index[eid] = None

            await asyncio.gather(*(one(s) for s in stickers))
            for missing in set(batch) - {s.custom_emoji_id for s in stickers}:
                _index[missing] = None
        _save()
        log.info("emoji lottie: %d animated", len(ids()))


def kick() -> None:
    """Start the background scan once per boot, and again when emoji.py learned new ids."""
    global _task
    _load()
    if emoji.UI is None or (_task is not None and not _task.done()):
        return
    if not any(k not in _index for k in emoji._index):  # noqa: SLF001
        return
    _task = asyncio.get_running_loop().create_task(_scan())
