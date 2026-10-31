from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Callable

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None

# Callbacks run after a unit of work that wrote an instant public record committed (the city feed, PLAN 10.3).
INSTANT_FEED_INFO_KEY = "instant_feed"
_commit_hooks: list[Callable[[], None]] = []


def add_commit_hook(fn: Callable[[], None]) -> None:
    """Register a plain function called after a commit that carried instant feed records. It must not block."""
    if fn not in _commit_hooks:
        _commit_hooks.append(fn)


def clear_commit_hooks() -> None:
    _commit_hooks.clear()


def init_engine(db_file: Path) -> AsyncEngine:
    global _engine, _sessionmaker
    db_file.parent.mkdir(parents=True, exist_ok=True)
    # A narrator call can run while a unit of work holds the write lock: other writers wait up to 30 s, not the 5 s default.
    _engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", future=True, connect_args={"timeout": 30})

    @event.listens_for(_engine.sync_engine, "connect")
    def _pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        raise RuntimeError("DB engine is not initialised")
    return _sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """One unit of work: commit on success, roll back on any exception."""
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            session.info.pop(INSTANT_FEED_INFO_KEY, None)
            await session.rollback()
            raise
        if session.info.pop(INSTANT_FEED_INFO_KEY, None):
            for hook in list(_commit_hooks):
                try:
                    hook()
                except Exception:  # a broken feed never breaks the game
                    logging.getLogger(__name__).exception("commit hook failed")


async def create_all() -> None:
    if _engine is None:
        raise RuntimeError("DB engine is not initialised")
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)


def _sql_default(column) -> str:  # noqa: ANN001
    """SQL literal for ALTER TABLE ADD COLUMN, from the column's Python default."""
    import json

    d = column.default
    if column.server_default is not None:
        return str(column.server_default.arg.text if hasattr(column.server_default.arg, "text") else column.server_default.arg)
    if d is None:
        return "NULL"
    v = d.arg
    if callable(v):
        try:
            v = v(None)
        except TypeError:
            v = v()
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (dict, list)):
        return "'" + json.dumps(v).replace("'", "''") + "'"
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    return "NULL"


def _add_missing_columns(sync_conn) -> None:  # noqa: ANN001
    """Lightweight migration: add columns that exist in the models but not in the SQLite file.

    Only additive changes are handled (new columns). Renames and type changes need a manual step.
    """
    import logging

    from sqlalchemy import inspect, text

    log = logging.getLogger(__name__)
    insp = inspect(sync_conn)
    for table in Base.metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        existing = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in existing:
                continue
            ddl_type = col.type.compile(dialect=sync_conn.dialect)
            default = _sql_default(col)
            sync_conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl_type} DEFAULT {default}'))
            log.warning("DB migration: added column %s.%s", table.name, col.name)


async def dispose() -> None:
    if _engine is not None:
        await _engine.dispose()
