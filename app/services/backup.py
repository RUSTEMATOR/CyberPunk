"""The daily backup of the database (PLAN 12).

The copy is made with the sqlite3 online backup API, which is safe while the bot writes in WAL mode: a plain file copy
of `game.db` could miss what still sits in `game.db-wal`. The copy is written to a temporary name, checked
(`PRAGMA integrity_check`) and only then renamed, so a half-written file never counts as a backup. Copies are named
`<db name>-YYYYmmdd-HHMMSS.db` in `backups/` next to the database (`data/backups` by default); the newest `keep`
stay, older ones are deleted.

`python -m app.services.backup` makes one copy now (for example before moving the database).
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

BACKUP_DIR_NAME = "backups"


def backup_dir(db_file: Path) -> Path:
    return db_file.parent / BACKUP_DIR_NAME


def backups(db_file: Path) -> list[Path]:
    """The finished copies of this database, oldest first."""
    d = backup_dir(db_file)
    if not d.is_dir():
        return []
    return sorted(d.glob(f"{db_file.stem}-*.db"))


def make_backup(db_file: Path, keep: int, now: datetime | None = None) -> Path:
    """Copy the database now and prune old copies. Blocking: call it in a thread. Raises on failure (the old copies
    are then left untouched)."""
    if not db_file.is_file():
        raise FileNotFoundError(f"database not found: {db_file}")
    now = now or datetime.now()
    d = backup_dir(db_file)
    d.mkdir(parents=True, exist_ok=True)
    final = d / f"{db_file.stem}-{now:%Y%m%d-%H%M%S}.db"
    tmp = d / (final.name + ".tmp")
    tmp.unlink(missing_ok=True)
    src = sqlite3.connect(str(db_file), timeout=30)
    try:
        dst = sqlite3.connect(str(tmp))
        try:
            src.backup(dst)
            ok = dst.execute("PRAGMA integrity_check").fetchone()
            if not ok or ok[0] != "ok":
                raise RuntimeError(f"backup failed the integrity check: {ok}")
        finally:
            dst.close()
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    finally:
        src.close()
    tmp.replace(final)
    for stale in d.glob("*.tmp"):
        stale.unlink(missing_ok=True)
    for old in backups(db_file)[:-max(1, keep)]:
        old.unlink(missing_ok=True)
    return final


async def backup_job(db_file: Path, keep: int) -> Path | None:
    """The scheduled job: a failure is logged, never raised into the scheduler."""
    try:
        path = await asyncio.to_thread(make_backup, db_file, keep)
    except Exception:
        log.exception("database backup failed")
        return None
    log.info("database backup: %s (%d kB), %d kept", path.name, path.stat().st_size // 1024, len(backups(db_file)))
    return path


def main() -> int:
    from app.config import load_settings
    from app.content.loader import load_content_or_exit

    settings = load_settings()
    keep = load_content_or_exit().balance.backup.keep
    path = make_backup(settings.db_file, keep)
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
