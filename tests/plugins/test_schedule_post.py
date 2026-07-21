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
async def test_execute_coerces_naive_datetime_to_utc() -> None:
    plugin = SchedulePostPlugin()
    db = _make_db()

    # Naive datetime (no tzinfo) should be treated as UTC
    naive_dt = datetime(2026, 7, 21, 15, 30)
    assert naive_dt.tzinfo is None

    await plugin.execute(
        SchedulePostInput(caption="Test caption", scheduled_for=naive_dt),
        user_id=uuid.uuid4(),
        db=db,
        image_url="https://cdn.example.com/photo.jpg",
    )

    # The row added to DB should have a tz-aware scheduled_for
    added_row = db.add.call_args[0][0]
    assert added_row.scheduled_for.tzinfo is not None
    assert added_row.scheduled_for.tzinfo == UTC


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
