"""Premium custom emoji in the Mini App (owner request 24.10).

The bot already writes `<tg-emoji emoji-id="…">` in its texts (content/icons.yaml). A browser cannot draw those, so
the bot fetches each one from Telegram once (`getCustomEmojiStickers`), keeps the files in `data/emoji/`, and serves:

    GET /emoji/<id>        a still picture (the sticker itself for a static one, its thumbnail for an animated one)
    GET /emoji/<id>.webm   the video of a video emoji (the app swaps the still picture for it)
    GET /api/emoji         {"video": [ids]}: which ones have a video

Buttons in Telegram can only carry plain emoji; in the app a button's leading plain emoji is shown as the premium one
from the same icons.yaml entry. Anything that fails stays the plain emoji: the picture's alt text.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.config import BASE_DIR

if TYPE_CHECKING:
    from app.bot.ui.kit import UiKit

log = logging.getLogger(__name__)

DIR = BASE_DIR / "data" / "emoji"
INDEX = DIR / "index.json"
BATCH = 200          # Bot API limit of getCustomEmojiStickers
PARALLEL = 4
ID_RE = re.compile(r"^\d{1,32}$")

UI: "UiKit | None" = None
by_emoji: dict[str, str] = {}               # plain emoji -> custom emoji id (first icons.yaml entry wins)
_index: dict[str, dict[str, Any]] = {}      # id -> {"img": file name | None, "video": file name | None}
_lock = asyncio.Lock()


def _load_index() -> None:
    try:
        _index.update(json.loads(INDEX.read_text(encoding="utf-8")))
    except Exception:
        pass


def _save_index() -> None:
    try:
        tmp = INDEX.with_suffix(".tmp")
        tmp.write_text(json.dumps(_index, ensure_ascii=False), encoding="utf-8")
        tmp.replace(INDEX)
    except Exception:
        log.exception("emoji: index not saved")


def setup(ui: "UiKit") -> None:
    global UI
    UI = ui
    DIR.mkdir(parents=True, exist_ok=True)
    _load_index()
    by_emoji.clear()
    for icon in ui.content.icons.values():
        if icon.custom_emoji_id and icon.emoji not in by_emoji:
            by_emoji[icon.emoji] = icon.custom_emoji_id


def all_icon_ids(ui: "UiKit") -> list[str]:
    return sorted({i.custom_emoji_id for i in ui.content.icons.values() if i.custom_emoji_id})


def video_ids() -> list[str]:
    return sorted(k for k, v in _index.items() if v.get("video"))


def file_for(eid: str, video: bool) -> Path | None:
    rec = _index.get(eid)
    name = (rec or {}).get("video" if video else "img")
    if not name:
        return None
    p = DIR / name
    return p if p.exists() else None


def _ext(data: bytes) -> str:
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    return "bin"


async def _download(file_id: str) -> bytes:
    assert UI is not None
    buf = await UI.bot.download(file_id)
    return buf.read() if buf is not None else b""


async def _fetch_one(st: Any) -> None:
    eid = st.custom_emoji_id
    rec: dict[str, Any] = {"img": None, "video": None}
    try:
        if getattr(st, "is_video", False):
            data = await _download(st.file_id)
            if data:
                (DIR / f"{eid}.webm").write_bytes(data)
                rec["video"] = f"{eid}.webm"
            src = st.thumbnail.file_id if st.thumbnail else None
        elif getattr(st, "is_animated", False):
            src = st.thumbnail.file_id if st.thumbnail else None  # Lottie (.tgs): the still thumbnail for now
        else:
            src = st.file_id
        if src:
            data = await _download(src)
            if data:
                name = f"{eid}.{_ext(data)}"
                (DIR / name).write_bytes(data)
                rec["img"] = name
    except Exception as exc:
        log.warning("emoji: %s not fetched: %s", eid, exc)
    _index[eid] = rec


async def ensure(ids: list[str]) -> None:
    """Fetch the emoji not on disk yet. Safe to call often; one fetch at a time."""
    if UI is None:
        return
    want = [i for i in dict.fromkeys(ids) if ID_RE.match(i) and i not in _index]
    if not want:
        return
    async with _lock:
        want = [i for i in want if i not in _index]
        for k in range(0, len(want), BATCH):
            batch = want[k:k + BATCH]
            try:
                stickers = list(await UI.bot.get_custom_emoji_stickers(custom_emoji_ids=batch))
                found = {s.custom_emoji_id for s in stickers}
            except Exception as exc:
                log.warning("emoji: getCustomEmojiStickers failed: %s", exc)
                return
            sem = asyncio.Semaphore(PARALLEL)

            async def one(st: Any) -> None:
                async with sem:
                    await _fetch_one(st)

            await asyncio.gather(*(one(s) for s in stickers))
            for missing in set(batch) - found:
                _index[missing] = {"img": None, "video": None}
        _save_index()
        log.info("emoji: %d on disk, %d with video", sum(1 for v in _index.values() if v.get("img")), len(video_ids()))


def want_later(ids: list[str]) -> None:
    """Ids met in a rendered screen that are not on disk: fetch them in the background."""
    missing = [i for i in ids if i not in _index]
    if missing and UI is not None:
        UI.spawn(ensure(missing))


async def prefetch() -> None:
    if UI is not None:
        await ensure(all_icon_ids(UI))
