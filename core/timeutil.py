from __future__ import annotations

from datetime import UTC, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def format_local(dt_utc: datetime, tz_name: str) -> str:
    """Convert a UTC datetime to a formatted local-time string.

    Format: "YYYY-MM-DD HH:MM ZZZ" (e.g. "2026-07-14 05:44 IST").
    Naive datetimes are assumed UTC. Invalid tz_name falls back to UTC.
    """
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=UTC)
    tz: ZoneInfo | timezone
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, KeyError):
        tz = UTC
    local = dt_utc.astimezone(tz)
    abbr = local.strftime("%Z") or "UTC"
    return local.strftime(f"%Y-%m-%d %H:%M {abbr}")
