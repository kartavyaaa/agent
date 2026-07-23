"""Unit tests for BuildContentPlanPlugin."""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.exceptions import PluginError
from plugins.build_content_plan.plugin import BuildContentPlanPlugin
from plugins.build_content_plan.schemas import (
    BuildContentPlanInput,
    BuildContentPlanOutput,
    PlanItem,
)


def _make_plugin(tz: str = "Asia/Kolkata") -> BuildContentPlanPlugin:
    return BuildContentPlanPlugin(tz_name=tz)


def _make_db() -> MagicMock:
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.execute = AsyncMock()
    return db


_URLS = [f"https://r2.example.com/{i}.jpg" for i in range(6)]
_T1 = datetime(2026, 7, 28, 18, 0)  # 6pm local
_T2 = datetime(2026, 7, 29, 18, 0)
_T3 = datetime(2026, 7, 30, 18, 0)


# ---------------------------------------------------------------------------
# ClassVar contract
# ---------------------------------------------------------------------------


def test_requires_approval_is_false() -> None:
    assert BuildContentPlanPlugin.requires_approval is False


def test_needs_hosted_images_is_true() -> None:
    assert BuildContentPlanPlugin.needs_hosted_images is True


def test_needs_hosted_image_is_false() -> None:
    assert BuildContentPlanPlugin.needs_hosted_image is False


# ---------------------------------------------------------------------------
# build_preview
# ---------------------------------------------------------------------------


def test_build_preview_contains_item_count() -> None:
    args = {
        "items": [
            {"image_indices": [0], "caption": "First post", "scheduled_for": "2026-07-28T18:00:00"},
            {
                "image_indices": [1, 2],
                "caption": "Beach series",
                "scheduled_for": "2026-07-29T18:00:00",
            },
        ]
    }
    preview = BuildContentPlanPlugin.build_preview(args)
    assert "2 posts" in preview
    assert "Single" in preview
    assert "Carousel" in preview
    assert "First post" in preview
    assert "Beach series" in preview
    # Must NOT contain raw JSON / image_indices dump
    assert "image_indices" not in preview


def test_build_preview_contains_confirmation_note() -> None:
    args = {
        "items": [
            {"image_indices": [0], "caption": "Post", "scheduled_for": "2026-07-28T18:00:00"},
        ]
    }
    preview = BuildContentPlanPlugin.build_preview(args)
    assert "confirmation" in preview.lower() or "confirm" in preview.lower()
    assert "Approve" in preview


def test_build_preview_formats_time_humanly() -> None:
    args = {
        "items": [
            {"image_indices": [0], "caption": "Post", "scheduled_for": "2026-07-28T18:00:00"},
        ]
    }
    preview = BuildContentPlanPlugin.build_preview(args)
    # Should not contain raw ISO string like T18:00:00
    assert "T18:00:00" not in preview
    # Should contain human-readable time format (e.g. 6:00 PM or 18:00 formatted)
    assert "Jul 28" in preview or "Jul" in preview


# ---------------------------------------------------------------------------
# execute() — happy path: creates DRAFT (no ScheduledPost rows)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_creates_draft_plan_no_posts() -> None:
    plugin = _make_plugin()
    db = _make_db()
    uid = uuid.uuid4()

    input_ = BuildContentPlanInput(
        items=[
            PlanItem(image_indices=[0], caption="Single shot", scheduled_for=_T1),
            PlanItem(image_indices=[1, 2, 3], caption="Carousel series", scheduled_for=_T2),
            PlanItem(image_indices=[4], caption="Another single", scheduled_for=_T3),
        ]
    )

    result = await plugin.execute(input_, user_id=uid, db=db, image_urls=_URLS)

    assert isinstance(result, BuildContentPlanOutput)
    assert result.content_plan_id  # non-empty UUID str
    # Draft mode: no ScheduledPost rows created
    assert result.scheduled_post_ids == []
    assert "3" in result.confirmation
    # Only 1 db.add call (for ContentPlan — no ScheduledPosts)
    assert db.add.call_count == 1

    added = db.add.call_args_list
    from models.content_plan import ContentPlan

    plan = [call.args[0] for call in added if isinstance(call.args[0], ContentPlan)][0]
    assert plan.status == "draft"
    assert plan.items is not None
    assert len(plan.items) == 3
    assert plan.image_urls == _URLS


@pytest.mark.asyncio
async def test_execute_status_is_draft() -> None:
    plugin = _make_plugin()
    db = _make_db()
    input_ = BuildContentPlanInput(
        items=[PlanItem(image_indices=[0], caption="Test", scheduled_for=_T1)]
    )
    await plugin.execute(input_, user_id=uuid.uuid4(), db=db, image_urls=_URLS)

    from models.content_plan import ContentPlan

    added = db.add.call_args_list
    plan = [call.args[0] for call in added if isinstance(call.args[0], ContentPlan)][0]
    assert plan.status == "draft"


@pytest.mark.asyncio
async def test_execute_auto_discards_existing_draft() -> None:
    """execute() should issue an UPDATE to discard any existing draft before creating a new one."""
    plugin = _make_plugin()
    db = _make_db()
    uid = uuid.uuid4()

    input_ = BuildContentPlanInput(
        items=[PlanItem(image_indices=[0], caption="New plan", scheduled_for=_T1)]
    )
    await plugin.execute(input_, user_id=uid, db=db, image_urls=_URLS)

    # db.execute should have been called with the UPDATE (auto-discard)
    assert db.execute.call_count >= 1


@pytest.mark.asyncio
async def test_execute_confirmation_contains_approve_hint() -> None:
    plugin = _make_plugin()
    db = _make_db()
    input_ = BuildContentPlanInput(
        items=[PlanItem(image_indices=[0], caption="Test", scheduled_for=_T1)]
    )
    result = await plugin.execute(input_, user_id=uuid.uuid4(), db=db, image_urls=_URLS)
    assert "approve" in result.confirmation.lower()


# ---------------------------------------------------------------------------
# execute() — validation errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_raises_index_out_of_range() -> None:
    plugin = _make_plugin()
    db = _make_db()
    input_ = BuildContentPlanInput(
        items=[PlanItem(image_indices=[99], caption="Bad", scheduled_for=_T1)]
    )
    with pytest.raises(PluginError, match="out of range"):
        await plugin.execute(input_, user_id=uuid.uuid4(), db=db, image_urls=_URLS)


@pytest.mark.asyncio
async def test_execute_raises_carousel_exceeds_max() -> None:
    plugin = _make_plugin()
    db = _make_db()
    urls = [f"https://r2.example.com/{i}.jpg" for i in range(11)]
    input_ = BuildContentPlanInput(
        items=[PlanItem(image_indices=list(range(11)), caption="Too many", scheduled_for=_T1)]
    )
    with pytest.raises(PluginError, match="maximum"):
        await plugin.execute(input_, user_id=uuid.uuid4(), db=db, image_urls=urls)


@pytest.mark.asyncio
async def test_execute_raises_no_image_urls() -> None:
    plugin = _make_plugin()
    db = _make_db()
    input_ = BuildContentPlanInput(
        items=[PlanItem(image_indices=[0], caption="Test", scheduled_for=_T1)]
    )
    with pytest.raises(PluginError, match="image_urls"):
        await plugin.execute(input_, user_id=uuid.uuid4(), db=db, image_urls=None)


@pytest.mark.asyncio
async def test_execute_raises_empty_items() -> None:
    plugin = _make_plugin()
    db = _make_db()
    input_ = BuildContentPlanInput(items=[])
    with pytest.raises(PluginError, match="at least one"):
        await plugin.execute(input_, user_id=uuid.uuid4(), db=db, image_urls=_URLS)


@pytest.mark.asyncio
async def test_execute_raises_empty_image_indices() -> None:
    plugin = _make_plugin()
    db = _make_db()
    input_ = BuildContentPlanInput(
        items=[PlanItem(image_indices=[], caption="Test", scheduled_for=_T1)]
    )
    with pytest.raises(PluginError, match="no image_indices"):
        await plugin.execute(input_, user_id=uuid.uuid4(), db=db, image_urls=_URLS)


# ---------------------------------------------------------------------------
# _validate_items_data (static helper for edit_draft_plan)
# ---------------------------------------------------------------------------


def test_validate_items_data_ok() -> None:
    items = [{"image_indices": [0, 1], "caption": "test", "scheduled_for": "2026-07-28T18:00:00"}]
    BuildContentPlanPlugin._validate_items_data(items, 3)  # no exception


def test_validate_items_data_out_of_range() -> None:
    items = [{"image_indices": [5], "caption": "test", "scheduled_for": "2026-07-28T18:00:00"}]
    with pytest.raises(PluginError, match="out of range"):
        BuildContentPlanPlugin._validate_items_data(items, 3)


def test_validate_items_data_too_many() -> None:
    items = [
        {
            "image_indices": list(range(11)),
            "caption": "test",
            "scheduled_for": "2026-07-28T18:00:00",
        }
    ]
    with pytest.raises(PluginError, match="maximum"):
        BuildContentPlanPlugin._validate_items_data(items, 12)
