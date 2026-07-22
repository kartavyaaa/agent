"""Unit tests for CancelScheduledPostPlugin."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.schedule_post.cancel import CancelScheduledPostPlugin
from plugins.schedule_post.schemas import CancelScheduledPostInput, CancelScheduledPostOutput


def _make_row(status: str = "scheduled", caption: str = "Test caption") -> MagicMock:
    row = MagicMock()
    row.id = uuid.uuid4()
    row.caption = caption
    row.status = status
    return row


def _make_db(row: MagicMock | None) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=row)
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# ClassVar / contract checks
# ---------------------------------------------------------------------------


def test_requires_approval_is_false() -> None:
    assert CancelScheduledPostPlugin.requires_approval is False


def test_needs_hosted_image_is_false() -> None:
    assert CancelScheduledPostPlugin.needs_hosted_image is False


def test_permissions_is_db_write() -> None:
    assert CancelScheduledPostPlugin.permissions == ["db:write"]


# ---------------------------------------------------------------------------
# execute() — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_sets_status_and_flushes() -> None:
    row = _make_row(caption="Summer vibes")
    db = _make_db(row)
    plugin = CancelScheduledPostPlugin()
    uid = uuid.uuid4()

    result = await plugin.execute(
        CancelScheduledPostInput(scheduled_post_id=str(row.id)),
        user_id=uid,
        db=db,
    )

    assert result.status == "cancelled"
    assert row.status == "cancelled"
    db.flush.assert_called_once()
    assert result.caption == "Summer vibes"
    assert str(row.id) in result.scheduled_post_id


@pytest.mark.asyncio
async def test_cancel_does_not_commit() -> None:
    """Engine owns the commit — plugin must only flush."""
    row = _make_row()
    db = _make_db(row)
    plugin = CancelScheduledPostPlugin()

    await plugin.execute(
        CancelScheduledPostInput(scheduled_post_id=str(row.id)),
        user_id=uuid.uuid4(),
        db=db,
    )

    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_detail_contains_caption() -> None:
    row = _make_row(caption="Sunset at the beach")
    db = _make_db(row)
    plugin = CancelScheduledPostPlugin()

    result = await plugin.execute(
        CancelScheduledPostInput(scheduled_post_id=str(row.id)),
        user_id=uuid.uuid4(),
        db=db,
    )

    assert "Sunset at the beach" in result.detail


# ---------------------------------------------------------------------------
# execute() — not_found paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_malformed_uuid_returns_not_found_without_db_query() -> None:
    db = _make_db(None)
    plugin = CancelScheduledPostPlugin()

    result = await plugin.execute(
        CancelScheduledPostInput(scheduled_post_id="not-a-uuid"),
        user_id=uuid.uuid4(),
        db=db,
    )

    assert result.status == "not_found"
    db.execute.assert_not_called()
    db.flush.assert_not_called()


@pytest.mark.asyncio
async def test_row_not_found_returns_not_found() -> None:
    """Row doesn't exist or wrong user or already triggered."""
    db = _make_db(None)
    plugin = CancelScheduledPostPlugin()

    result = await plugin.execute(
        CancelScheduledPostInput(scheduled_post_id=str(uuid.uuid4())),
        user_id=uuid.uuid4(),
        db=db,
    )

    assert result.status == "not_found"
    assert result.caption == ""
    db.flush.assert_not_called()


@pytest.mark.asyncio
async def test_returns_not_found_output_type() -> None:
    db = _make_db(None)
    plugin = CancelScheduledPostPlugin()

    result = await plugin.execute(
        CancelScheduledPostInput(scheduled_post_id=str(uuid.uuid4())),
        user_id=uuid.uuid4(),
        db=db,
    )

    assert isinstance(result, CancelScheduledPostOutput)
