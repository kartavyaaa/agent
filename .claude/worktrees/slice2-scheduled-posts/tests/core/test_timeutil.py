"""Unit tests for core.timeutil.format_local."""

from __future__ import annotations

from datetime import UTC, datetime

from core.timeutil import format_local


def test_utc_to_ist_same_day() -> None:
    """UTC 00:00 → IST 05:30 (UTC+5:30), same calendar day."""
    dt = datetime(2026, 7, 14, 0, 0, tzinfo=UTC)
    result = format_local(dt, "Asia/Kolkata")
    assert result == "2026-07-14 05:30 IST"


def test_utc_to_ist_date_rollover() -> None:
    """UTC 22:00 on July 13 → IST 03:30 on July 14 (crosses midnight)."""
    dt = datetime(2026, 7, 13, 22, 0, tzinfo=UTC)
    result = format_local(dt, "Asia/Kolkata")
    assert result == "2026-07-14 03:30 IST"


def test_naive_datetime_treated_as_utc() -> None:
    """A naive datetime (no tzinfo) must be treated as UTC, not rejected."""
    naive = datetime(2026, 7, 14, 0, 0)
    aware = datetime(2026, 7, 14, 0, 0, tzinfo=UTC)
    assert format_local(naive, "Asia/Kolkata") == format_local(aware, "Asia/Kolkata")


def test_invalid_timezone_falls_back_to_utc() -> None:
    """An invalid timezone name must not raise; returns UTC-labelled string."""
    dt = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
    result = format_local(dt, "Imaginary/Zone")
    assert "2026-07-14" in result
    assert "12:00" in result


def test_utc_zone_produces_utc_string() -> None:
    dt = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
    result = format_local(dt, "UTC")
    assert result == "2026-07-14 12:00 UTC"
