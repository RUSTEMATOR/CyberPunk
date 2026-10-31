"""The picture library of the city feed (PLAN 11.2, DESIGN.md 15, docs/IMAGES.md).

`images/manifest.yaml` lists the files and their tags:

    images:
      - file: news_studio.png
        tags: [news]

`Library.pick` returns the picture that fits a request best (engine/feed.py::ImageRequest): the tags are weighted by
their order in `want`, the best score wins, equal scores are drawn at random (and the picture used last for the same
request is avoided when there is a choice). A file that is not on disk is ignored, so the manifest may list pictures
that are not made yet, and an empty `images/` folder is a normal state: every post then goes out as plain text.

The Telegram `file_id` of a picture is remembered in `image_cache` after the first upload. If Telegram refuses a
remembered id, it is dropped and the file is uploaded again; if that fails too, the post goes out without a picture.

`python -m app.services.images` checks the manifest and lists which pictures are still missing.
"""

from __future__ import annotations

import html
import logging
import random
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

import yaml
from aiogram.types import FSInputFile
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from app.db import session as db
from app.db.models import ImageCache
from app.engine.feed import ImageRequest

if TYPE_CHECKING:
    from app.bot.ui.kit import UiKit

log = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.yaml"
EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
CAPTION_MAX = 1024  # Telegram: a caption is at most 1024 characters of visible text
MAX_BYTES = 10 * 1024 * 1024  # Telegram: a photo is at most 10 MB


class ManifestEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str
    tags: list[str]

    @field_validator("file")
    @classmethod
    def _file(cls, v: str) -> str:
        v = v.strip().replace("\\", "/")
        if not v or v.startswith("/") or ".." in v.split("/"):
            raise ValueError("file must be a relative path inside images/")
        if not v.lower().endswith(EXTENSIONS):
            raise ValueError(f"file must end with one of {', '.join(EXTENSIONS)}")
        return v

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: list[str]) -> list[str]:
        tags = [str(t).strip().lower() for t in v if str(t).strip()]
        if not tags:
            raise ValueError("tags must not be empty")
        return list(dict.fromkeys(tags))


@dataclass(frozen=True)
class Picture:
    file: str  # name in the manifest
    tags: frozenset[str]
    path: Path

    @property
    def cache_key(self) -> str:
        """The `image_cache` key: the name plus size and mtime, so a replaced file is uploaded again."""
        st = self.path.stat()
        return f"{self.file}@{st.st_size}-{int(st.st_mtime)}"[:128]


def read_manifest(directory: Path) -> tuple[list[ManifestEntry], list[str]]:
    """The valid entries of `<directory>/manifest.yaml` and the problems found. A missing manifest is not a problem."""
    path = directory / MANIFEST_NAME
    if not path.is_file():
        return [], []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        return [], [f"{MANIFEST_NAME}: cannot read: {e}"]
    items = raw.get("images") if isinstance(raw, dict) else None
    if items is None:
        return [], [] if raw == {} else [f"{MANIFEST_NAME}: expected a top-level `images:` list"]
    if not isinstance(items, list):
        return [], [f"{MANIFEST_NAME}: `images` must be a list"]
    entries: list[ManifestEntry] = []
    problems: list[str] = []
    seen: set[str] = set()
    for i, item in enumerate(items, 1):
        try:
            entry = ManifestEntry.model_validate(item)
        except ValidationError as e:
            problems.append(f"{MANIFEST_NAME}: entry {i}: {e.errors()[0]['msg']}")
            continue
        if entry.file in seen:
            problems.append(f"{MANIFEST_NAME}: entry {i}: {entry.file} is listed twice, the first one is used")
            continue
        seen.add(entry.file)
        entries.append(entry)
    return entries, problems


def score(want: Sequence[str], tags: frozenset[str]) -> int:
    """Weighted overlap: the first wanted tag weighs `len(want)`, the last one 1; 0 = no common tag."""
    n = len(want)
    return sum(n - i for i, t in enumerate(want) if t in tags)


class Library:
    """The pictures of one `images/` folder. The manifest is re-read when its file changes, so new pictures can be
    added while the bot runs."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self._stamp: tuple[float, int] | None = None
        self._entries: list[ManifestEntry] = []
        self._last: dict[tuple[str, ...], str] = {}

    def _refresh(self) -> None:
        path = self.directory / MANIFEST_NAME
        try:
            st = path.stat()
            stamp: tuple[float, int] | None = (st.st_mtime, st.st_size)
        except OSError:
            stamp = None
        if stamp == self._stamp:
            return
        self._stamp = stamp
        self._entries, problems = read_manifest(self.directory)
        for p in problems:
            log.warning("images: %s", p)

    def pictures(self) -> list[Picture]:
        """The pictures that are in the manifest and on disk."""
        self._refresh()
        out: list[Picture] = []
        for e in self._entries:
            path = self.directory / e.file
            try:
                if not path.is_file() or path.stat().st_size > MAX_BYTES:
                    continue
            except OSError:
                continue
            out.append(Picture(e.file, frozenset(e.tags), path))
        return out

    def pick(self, request: ImageRequest, rng: random.Random | None = None) -> Picture | None:
        """The best picture for the request, or None."""
        rng = rng or random
        need = set(request.require)
        best: list[Picture] = []
        best_score = 0
        for pic in self.pictures():
            if not need <= pic.tags:
                continue
            if request.exclude_prefixes and any(
                t.startswith(request.exclude_prefixes) and t not in request.want for t in pic.tags
            ):
                continue
            sc = score(request.want, pic.tags)
            if sc <= 0:
                continue
            if sc > best_score:
                best, best_score = [pic], sc
            elif sc == best_score:
                best.append(pic)
        if not best:
            return None
        key = tuple(request.want)
        if len(best) > 1:
            fresh = [p for p in best if p.file != self._last.get(key)]
            best = fresh or best
        chosen = rng.choice(best)
        self._last[key] = chosen.file
        return chosen


# -- the file_id cache ----------------------------------------------------------------------------------------------


async def cached_file_id(key: str) -> str | None:
    async with db.session_scope() as s:
        row = await s.get(ImageCache, key)
        return row.tg_file_id if row is not None else None


async def store_file_id(key: str, file_id: str) -> None:
    async with db.session_scope() as s:
        row = await s.get(ImageCache, key)
        if row is None:
            s.add(ImageCache(file=key, tg_file_id=file_id))
        else:
            row.tg_file_id = file_id


async def drop_file_id(key: str) -> None:
    async with db.session_scope() as s:
        row = await s.get(ImageCache, key)
        if row is not None:
            await s.delete(row)


# -- sending ----------------------------------------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")


def visible_length(text: str) -> int:
    """The length Telegram counts for a caption: the text without the HTML tags, entities decoded."""
    return len(html.unescape(_TAG_RE.sub("", text)))


async def send_photo_post(
    ui: "UiKit", chat_id: int, pic: Picture, caption: str | None, ttl_s: float | None, kind: str,
) -> int | None:
    """Send the picture (with a caption or alone); returns the message id, or None when it could not be sent.

    A remembered file_id is tried first; when Telegram refuses it, the file is uploaded again."""
    try:
        key = pic.cache_key
    except OSError:
        return None
    file_id = await cached_file_id(key)
    res = None
    if file_id:
        res = await ui.outbox.send_photo(chat_id, file_id, caption)
        if not res.ok or res.message_id is None:
            log.warning("images: file_id of %s refused (%s), uploading again", pic.file, res.error)
            await drop_file_id(key)
            res = None
    if res is None:
        res = await ui.outbox.send_photo(chat_id, FSInputFile(pic.path), caption)
        if not res.ok or res.message_id is None:
            log.warning("images: could not send %s: %s", pic.file, res.error)
            return None
        if res.file_id:
            await store_file_id(key, res.file_id)
    if ttl_s is not None:
        await ui.cleanup.schedule_delete(chat_id, res.message_id, ttl_s, kind)
    return res.message_id


async def send_post(
    ui: "UiKit", request: ImageRequest, text: str, ttl_s: float | None, kind: str,
) -> int | None:
    """A feed post with a picture (PLAN 11.2): a photo with the text as caption when the text fits `CAPTION_MAX`,
    else the photo alone and the text as a second message; both are deleted at the same time. Returns the id of the
    text message, or None when no picture was sent (the caller then posts the plain text)."""
    if ui.group_id is None:
        return None
    pic = ui.images.pick(request)
    if pic is None:
        return None
    chat_id = ui.group_id
    if visible_length(text) <= CAPTION_MAX:
        return await send_photo_post(ui, chat_id, pic, text, ttl_s, kind)
    photo_id = await send_photo_post(ui, chat_id, pic, None, ttl_s, kind)
    if photo_id is None:
        return None
    res = await ui.outbox.send(chat_id, text, None)
    if not res.ok or res.message_id is None:
        log.warning("images: the text after the picture failed (%s)", res.error)
        return photo_id
    if ttl_s is not None:
        await ui.cleanup.schedule_delete(chat_id, res.message_id, ttl_s, kind)
    return res.message_id


# -- manifest check -----------------------------------------------------------------------------------------------------


def report(directory: Path) -> tuple[list[str], int]:
    """Lines for the owner: problems, and the pictures that are listed but not on disk. Returns (lines, error count)."""
    entries, problems = read_manifest(directory)
    lines = list(problems)
    missing = [e.file for e in entries if not (directory / e.file).is_file()]
    present = len(entries) - len(missing)
    lines.append(f"manifest: {len(entries)} entries, {present} on disk, {len(missing)} missing")
    lines.extend(f"  missing: {f}" for f in missing)
    listed = {e.file for e in entries}
    extra = sorted(
        p.name for p in directory.iterdir() if p.is_file() and p.suffix.lower() in EXTENSIONS and p.name not in listed
    ) if directory.is_dir() else []
    lines.extend(f"  not in the manifest (never used): {f}" for f in extra)
    return lines, len(problems)


def main(argv: list[str]) -> int:
    from app.config import load_settings

    directory = Path(argv[0]) if argv else load_settings().images_path
    lines, errors = report(directory)
    print("\n".join(lines))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
