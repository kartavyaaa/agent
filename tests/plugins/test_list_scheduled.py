"""Unit tests for ListScheduledPostsPlugin."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.schedule_post.list import ListScheduledPostsPlugin
from plugins.schedule_post.schemas import ListScheduledPostsInput, ListScheduledPostsOutput


def _make_row(
    status: str = "scheduled",
    caption: str = "Test caption",
    scheduled_for: datetime | None = None,
) -> MagicMock:
    row = MagicMock()
    row.id = uuid.uuid4()
    row.caption = caption
    row.status = status
    row.scheduled_for = scheduled_for or datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    return row


def _make_db(rows: list[MagicMock]) -> MagicMock:
    scalars = MagicMock()
    scalars.all = MagicMock(return_value=rows)
    result = MagicMock()
    result.scalars = MagicMock(return_value=scalars)
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    return db


# ---------------------------------------------------------------------------
# ClassVar / contract checks
# ---------------------------------------------------------------------------


def test_requires_approval_is_false() -> None:
    assert ListScheduledPostsPlugin.requires_approval is False


def test_needs_hosted_image_is_false() -> None:
    assert ListScheduledPostsPlugin.needs_hosted_image is False


def test_permissions_is_db_read() -> None:
    assert ListScheduledPostsPlugin.permissions == ["db:read"]


# ---------------------------------------------------------------------------
# execute()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_list_returns_zero_count() -> None:
    plugin = ListScheduledPostsPlugin()
    db = _make_db([])
    result = await plugin.execute(ListScheduledPostsInput(), user_id=uuid.uuid4(), db=db)
    assert isinstance(result, ListScheduledPostsOutput)
    assert result.count == 0
    assert result.scheduled_posts == []


@pytest.mark.asyncio
async def test_returns_summaries_for_scheduled_rows() -> None:
    row1 = _make_row(caption="Morning post", scheduled_for=datetime(2026, 8, 1, 4, 30, tzinfo=UTC))
    row2 = _make_row(caption="Evening post", scheduled_for=datetime(2026, 8, 1, 14, 0, tzinfo=UTC))
    plugin = ListScheduledPostsPlugin(tz_name="Asia/Kolkata")
    db = _make_db([row1, row2])

    result = await plugin.execute(ListScheduledPostsInput(), user_id=uuid.uuid4(), db=db)

    assert result.count == 2
    assert result.scheduled_posts[0].caption == "Morning post"
    assert result.scheduled_posts[1].caption == "Evening post"


@pytest.mark.asyncio
async def test_scheduled_post_id_matches_row_uuid() -> None:
    row = _make_row()
    plugin = ListScheduledPostsPlugin()
    db = _make_db([row])

    result = await plugin.execute(ListScheduledPostsInput(), user_id=uuid.uuid4(), db=db)

    assert result.scheduled_posts[0].scheduled_post_id == str(row.id)


@pytest.mark.asyncio
async def test_scheduled_for_local_uses_format_local_ist() -> None:
    """UTC 04:30 → IST 10:00 (UTC+5:30)."""
    row = _make_row(scheduled_for=datetime(2026, 8, 1, 4, 30, tzinfo=UTC))
    plugin = ListScheduledPostsPlugin(tz_name="Asia/Kolkata")
    db = _make_db([row])

    result = await plugin.execute(ListScheduledPostsInput(), user_id=uuid.uuid4(), db=db)

    assert "10:00" in result.scheduled_posts[0].scheduled_for_local
    assert "IST" in result.scheduled_posts[0].scheduled_for_local


@pytest.mark.asyncio
async def test_scheduled_for_utc_is_isoformat() -> None:
    scheduled = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    row = _make_row(scheduled_for=scheduled)
    plugin = ListScheduledPostsPlugin()
    db = _make_db([row])

    result = await plugin.execute(ListScheduledPostsInput(), user_id=uuid.uuid4(), db=db)

    assert result.scheduled_posts[0].scheduled_for_utc == scheduled.isoformat()
