"""Unit tests for SchedulePostPlugin.

DB is mocked — no Postgres needed.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.exceptions import PluginError
from plugins.schedule_post.plugin import SchedulePostPlugin
from plugins.schedule_post.schemas import SchedulePostInput, SchedulePostOutput


def _make_db() -> MagicMock:
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# ClassVar / contract checks
# ---------------------------------------------------------------------------


def test_requires_approval_is_false() -> None:
    assert SchedulePostPlugin.requires_approval is False


def test_needs_hosted_image_is_true() -> None:
    assert SchedulePostPlugin.needs_hosted_image is True


def test_input_schema_has_no_image_url() -> None:
    assert "image_url" not in SchedulePostInput.model_fields


def test_input_schema_has_no_user_id() -> None:
    assert "user_id" not in SchedulePostInput.model_fields


def test_input_schema_fields() -> None:
    fields = set(SchedulePostInput.model_fields.keys())
    assert fields == {"caption", "scheduled_for"}


# ---------------------------------------------------------------------------
# execute() — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_creates_scheduled_post_row() -> None:
    plugin = SchedulePostPlugin()
    db = _make_db()
    uid = uuid.uuid4()
    scheduled_for = datetime(2026, 7, 21, 15, 30, tzinfo=UTC)

    result = await plugin.execute(
        SchedulePostInput(caption="Summer vibes", scheduled_for=scheduled_for),
        user_id=uid,
        db=db,
        image_url="https://cdn.example.com/user1/photo.jpg",
    )

    db.add.assert_called_once()
    db.flush.assert_called_once()
    assert isinstance(result, SchedulePostOutput)
    assert result.scheduled_post_id is not None
    assert "Scheduled" in result.confirmation


@pytest.mark.asyncio
async def test_execute_localizes_naive_datetime_via_tz_name() -> None:
    """Naive local datetime must be localized using tz_name, NOT stamped as UTC.

    IST is UTC+5:30. 05:15 local IST = 23:45 UTC the PREVIOUS calendar day.
    This is the exact date-rollback case that caused the prod scheduling bug.
    """
    plugin = SchedulePostPlugin(tz_name="Asia/Kolkata")
    db = _make_db()

    # LLM emits: user said "5:15am" on July 22 IST → naive local 2026-07-22T05:15:00
    local_ist = datetime(2026, 7, 22, 5, 15)
    assert local_ist.tzinfo is None

    await plugin.execute(
        SchedulePostInput(caption="Test caption", scheduled_for=local_ist),
        user_id=uuid.uuid4(),
        db=db,
        image_url="https://cdn.example.com/photo.jpg",
    )

    added_row = db.add.call_args[0][0]
    stored = added_row.scheduled_for
    assert stored.tzinfo is not None
    # 05:15 IST = 23:45 UTC on July 21 (date rolls back a day)
    assert stored.year == 2026
    assert stored.month == 7
    assert stored.day == 21
    assert stored.hour == 23
    assert stored.minute == 45


@pytest.mark.asyncio
async def test_execute_ist_no_date_rollback_for_afternoon() -> None:
    """14:00 IST = 08:30 UTC same day — no rollback."""
    plugin = SchedulePostPlugin(tz_name="Asia/Kolkata")
    db = _make_db()

    local_ist = datetime(2026, 7, 22, 14, 0)  # 2pm IST July 22

    await plugin.execute(
        SchedulePostInput(caption="Afternoon post", scheduled_for=local_ist),
        user_id=uuid.uuid4(),
        db=db,
        image_url="https://cdn.example.com/photo.jpg",
    )

    added_row = db.add.call_args[0][0]
    stored = added_row.scheduled_for
    # 14:00 IST = 08:30 UTC same day
    assert stored.day == 22
    assert stored.hour == 8
    assert stored.minute == 30


@pytest.mark.asyncio
async def test_execute_stores_correct_payload() -> None:
    plugin = SchedulePostPlugin()
    db = _make_db()
    uid = uuid.uuid4()
    image_url = "https://cdn.example.com/img.jpg"
    caption = "Hello world"
    scheduled_for = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)

    await plugin.execute(
        SchedulePostInput(caption=caption, scheduled_for=scheduled_for),
        user_id=uid,
        db=db,
        image_url=image_url,
    )

    added_row = db.add.call_args[0][0]
    assert added_row.user_id == uid
    assert added_row.image_url == image_url
    assert added_row.caption == caption
    assert added_row.status == "scheduled"


# ---------------------------------------------------------------------------
# execute() — missing image_url
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_raises_when_image_url_missing() -> None:
    plugin = SchedulePostPlugin()
    db = _make_db()

    with pytest.raises(PluginError, match="image_url"):
        await plugin.execute(
            SchedulePostInput(
                caption="Test",
                scheduled_for=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
            ),
            user_id=uuid.uuid4(),
            db=db,
            image_url=None,
        )
