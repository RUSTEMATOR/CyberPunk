"""Time helpers. The DB stores naive UTC datetimes; game days are Kyiv calendar days (DESIGN.md 4)."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

DEFAULT_TZ = "Europe/Kyiv"


def utcnow() -> datetime:
    """Naive UTC now, the format of every DateTime column."""
    return datetime.now(UTC).replace(tzinfo=None)


def kyiv_now(tz: str = DEFAULT_TZ) -> datetime:
    return datetime.now(ZoneInfo(tz))


def kyiv_date(tz: str = DEFAULT_TZ) -> str:
    """Today's game date as 'YYYY-MM-DD'."""
    return kyiv_now(tz).strftime("%Y-%m-%d")


def next_kyiv_midnight_utc(tz: str = DEFAULT_TZ) -> datetime:
    """The next local midnight as naive UTC (expiry of 'until the end of the day' buffs)."""
    zone = ZoneInfo(tz)
    now = datetime.now(zone)
    nxt = datetime.combine(now.date() + timedelta(days=1), time(0, 0), tzinfo=zone)
    return nxt.astimezone(UTC).replace(tzinfo=None)


def world_day(tz: str = DEFAULT_TZ, tick_hour: int = 3, now: datetime | None = None) -> str:
    """The world day as 'YYYY-MM-DD': it turns over at the global tick hour (03:00 Kyiv), not at midnight.

    The gig boards and the world clock count in world days; the player's limit of weeks and the daily
    mini-games still count in Kyiv calendar days."""
    moment = now if now is not None else kyiv_now(tz)
    return (moment - timedelta(hours=tick_hour)).strftime("%Y-%m-%d")
