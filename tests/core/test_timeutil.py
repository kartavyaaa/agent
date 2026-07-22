"""Unit tests for core.timeutil."""

from __future__ import annotations

from datetime import UTC, datetime, timezone, timedelta

from core.timeutil import format_local, localize_to_utc


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


# ---------------------------------------------------------------------------
# localize_to_utc
# ---------------------------------------------------------------------------


def test_localize_naive_ist_morning_rolls_back_date() -> None:
    """05:15 IST naive → 23:45 UTC on the PREVIOUS calendar day.

    This is the exact prod bug: LLM emits 2026-07-22T05:15:00 (no Z) for
    'post at 5:15am IST'. Without proper localization it would be stamped
    as 2026-07-22T05:15:00Z (wrong, ~29h late). With correct localization
    it becomes 2026-07-21T23:45:00Z.
    """
    naive_ist = datetime(2026, 7, 22, 5, 15)  # user said "5:15am July 22"
    result = localize_to_utc(naive_ist, "Asia/Kolkata")
    assert result.tzinfo is not None
    assert result == datetime(2026, 7, 21, 23, 45, tzinfo=UTC)


def test_localize_naive_ist_afternoon_same_day() -> None:
    """14:00 IST = 08:30 UTC same calendar day (no rollback)."""
    naive_ist = datetime(2026, 7, 22, 14, 0)
    result = localize_to_utc(naive_ist, "Asia/Kolkata")
    assert result == datetime(2026, 7, 22, 8, 30, tzinfo=UTC)


def test_localize_already_aware_passes_through() -> None:
    """An already-UTC-aware datetime is returned as UTC unchanged."""
    aware = datetime(2026, 7, 22, 8, 30, tzinfo=UTC)
    result = localize_to_utc(aware, "Asia/Kolkata")
    assert result == aware


def test_localize_already_aware_non_utc_converted() -> None:
    """An aware datetime in a non-UTC zone is converted to UTC."""
    ist_offset = timezone(timedelta(hours=5, minutes=30))
    aware_ist = datetime(2026, 7, 22, 5, 15, tzinfo=ist_offset)
    result = localize_to_utc(aware_ist, "UTC")  # tz_name ignored for aware dt
    assert result == datetime(2026, 7, 21, 23, 45, tzinfo=UTC)


def test_localize_invalid_tz_falls_back_to_utc() -> None:
    """Invalid tz_name treats naive datetime as UTC (safe fallback)."""
    naive = datetime(2026, 7, 22, 8, 30)
    result = localize_to_utc(naive, "Imaginary/Zone")
    assert result == datetime(2026, 7, 22, 8, 30, tzinfo=UTC)


def test_localize_naive_utc_is_identity() -> None:
    """For tz_name='UTC', naive datetime localizes to same UTC instant."""
    naive = datetime(2026, 7, 22, 12, 0)
    result = localize_to_utc(naive, "UTC")
    assert result == datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
