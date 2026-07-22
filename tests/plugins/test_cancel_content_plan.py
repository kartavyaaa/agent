"""Unit tests for CancelContentPlanPlugin."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.build_content_plan.cancel import CancelContentPlanPlugin
from plugins.build_content_plan.schemas import CancelContentPlanInput, CancelContentPlanOutput


def _make_plan(plan_id: uuid.UUID | None = None) -> MagicMock:
    plan = MagicMock()
    plan.id = plan_id or uuid.uuid4()
    return plan


def _make_post(status: str = "scheduled") -> MagicMock:
    post = MagicMock()
    post.status = status
    return post


def _make_db(
    plan: MagicMock | None,
    posts: list[MagicMock] | None = None,
) -> MagicMock:
    plan_result = MagicMock()
    plan_result.scalar_one_or_none = MagicMock(return_value=plan)

    posts_result = MagicMock()
    posts_result.scalars.return_value.all.return_value = posts or []

    db = MagicMock()
    db.execute = AsyncMock(side_effect=[plan_result, posts_result])
    db.flush = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# ClassVar contract
# ---------------------------------------------------------------------------


def test_requires_approval_is_false() -> None:
    assert CancelContentPlanPlugin.requires_approval is False


def test_permissions_is_db_write() -> None:
    assert CancelContentPlanPlugin.permissions == ["db:write"]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_sets_scheduled_posts_to_cancelled() -> None:
    plan = _make_plan()
    posts = [_make_post("scheduled"), _make_post("scheduled"), _make_post("scheduled")]
    db = _make_db(plan, posts)
    plugin = CancelContentPlanPlugin()

    result = await plugin.execute(
        CancelContentPlanInput(content_plan_id=str(plan.id)),
        user_id=uuid.uuid4(),
        db=db,
    )

    assert result.cancelled_count == 3
    for post in posts:
        assert post.status == "cancelled"
    db.flush.assert_called_once()


@pytest.mark.asyncio
async def test_cancel_does_not_touch_triggered_posts() -> None:
    """Triggered posts must remain untouched — they have their own Cancel button."""
    plan = _make_plan()
    triggered = _make_post("triggered")
    scheduled = _make_post("scheduled")
    # DB query filters WHERE status='scheduled', so triggered is not returned.
    db = _make_db(plan, [scheduled])
    plugin = CancelContentPlanPlugin()

    result = await plugin.execute(
        CancelContentPlanInput(content_plan_id=str(plan.id)),
        user_id=uuid.uuid4(),
        db=db,
    )

    assert result.cancelled_count == 1
    assert triggered.status == "triggered"  # untouched


@pytest.mark.asyncio
async def test_cancel_no_scheduled_posts() -> None:
    plan = _make_plan()
    db = _make_db(plan, [])
    plugin = CancelContentPlanPlugin()

    result = await plugin.execute(
        CancelContentPlanInput(content_plan_id=str(plan.id)),
        user_id=uuid.uuid4(),
        db=db,
    )

    assert result.cancelled_count == 0
    assert "No scheduled" in result.detail


@pytest.mark.asyncio
async def test_cancel_does_not_commit() -> None:
    """Engine owns the commit."""
    plan = _make_plan()
    posts = [_make_post()]
    db = _make_db(plan, posts)
    db.commit = AsyncMock()
    plugin = CancelContentPlanPlugin()

    await plugin.execute(
        CancelContentPlanInput(content_plan_id=str(plan.id)),
        user_id=uuid.uuid4(),
        db=db,
    )

    db.commit.assert_not_called()


# ---------------------------------------------------------------------------
# Not-found paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_malformed_uuid_returns_not_found() -> None:
    db = MagicMock()
    db.execute = AsyncMock()
    plugin = CancelContentPlanPlugin()

    result = await plugin.execute(
        CancelContentPlanInput(content_plan_id="not-a-uuid"),
        user_id=uuid.uuid4(),
        db=db,
    )

    assert result.cancelled_count == 0
    assert "not found" in result.detail.lower()
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_plan_not_found_returns_not_found() -> None:
    plan_result = MagicMock()
    plan_result.scalar_one_or_none = MagicMock(return_value=None)
    db = MagicMock()
    db.execute = AsyncMock(return_value=plan_result)
    plugin = CancelContentPlanPlugin()

    result = await plugin.execute(
        CancelContentPlanInput(content_plan_id=str(uuid.uuid4())),
        user_id=uuid.uuid4(),
        db=db,
    )

    assert result.cancelled_count == 0
    assert isinstance(result, CancelContentPlanOutput)
