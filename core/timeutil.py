from __future__ import annotations

from datetime import UTC, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _get_tz(tz_name: str) -> ZoneInfo | timezone:
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, KeyError):
        return UTC


def format_local(dt_utc: datetime, tz_name: str) -> str:
    """Convert a UTC datetime to a formatted local-time string.

    Format: "YYYY-MM-DD HH:MM ZZZ" (e.g. "2026-07-14 05:44 IST").
    Naive datetimes are assumed UTC. Invalid tz_name falls back to UTC.
    """
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=UTC)
    local = dt_utc.astimezone(_get_tz(tz_name))
    abbr = local.strftime("%Z") or "UTC"
    return local.strftime(f"%Y-%m-%d %H:%M {abbr}")


def localize_to_utc(naive_local: datetime, tz_name: str) -> datetime:
    """Interpret a wall-clock datetime as local time in tz_name and return UTC.

    The LLM is instructed to emit the user's local wall-clock time with no Z
    (e.g. "2026-07-22T06:21:00"), but it sometimes attaches a Z or +00:00 offset
    anyway. Any tzinfo is stripped before localizing — the wall-clock digits are
    always treated as local time in tz_name regardless of what the LLM attached.

    This handles date rollovers automatically: 06:21 IST = 00:51 UTC same day;
    05:15 IST = 23:45 UTC the previous calendar day.
    Invalid tz_name falls back to UTC.
    """
    # Strip any tzinfo the LLM may have attached — always treat as local wall-clock.
    wall_clock = naive_local.replace(tzinfo=None)
    local = wall_clock.replace(tzinfo=_get_tz(tz_name))
    return local.astimezone(UTC)
