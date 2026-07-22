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
    """Interpret a naive local datetime in tz_name and return the equivalent UTC datetime.

    The LLM emits the user's wall-clock time (e.g. "2026-07-22T05:15:00", no Z).
    This function stamps the correct timezone and converts to UTC, handling date
    rollovers automatically (e.g. 05:15 IST = 23:45 UTC the *previous* calendar day).

    If naive_local is already tz-aware, it is converted to UTC directly.
    Invalid tz_name falls back to UTC (treats the value as already UTC).
    """
    if naive_local.tzinfo is not None:
        return naive_local.astimezone(UTC)
    local = naive_local.replace(tzinfo=_get_tz(tz_name))
    return local.astimezone(UTC)
