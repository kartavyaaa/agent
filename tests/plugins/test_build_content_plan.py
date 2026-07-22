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
    return db


_URLS = [f"https://r2.example.com/{i}.jpg" for i in range(6)]
_T1 = datetime(2026, 7, 28, 18, 0)  # 6pm local
_T2 = datetime(2026, 7, 29, 18, 0)
_T3 = datetime(2026, 7, 30, 18, 0)


# ---------------------------------------------------------------------------
# ClassVar contract
# ---------------------------------------------------------------------------


def test_requires_approval_is_true() -> None:
    assert BuildContentPlanPlugin.requires_approval is True


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
# execute() — happy path: mixed single + carousel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_creates_plan_and_posts() -> None:
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
    assert len(result.scheduled_post_ids) == 3
    assert "3" in result.confirmation
    assert db.flush.call_count >= 2

    added = db.add.call_args_list
    assert len(added) == 4  # 1 ContentPlan + 3 ScheduledPosts

    # Check ScheduledPost objects
    from models.scheduled_post import ScheduledPost

    posts = [call.args[0] for call in added if isinstance(call.args[0], ScheduledPost)]
    assert len(posts) == 3

    single1, carousel, single2 = posts
    assert single1.post_type == "single"
    assert single1.image_url == _URLS[0]
    assert single1.image_urls is None
    assert single1.caption == "Single shot"

    assert carousel.post_type == "carousel"
    assert carousel.image_url is None
    assert carousel.image_urls == [_URLS[1], _URLS[2], _URLS[3]]
    assert carousel.caption == "Carousel series"

    assert single2.post_type == "single"
    assert single2.image_url == _URLS[4]

    # All posts must have the same plan_id
    from models.content_plan import ContentPlan

    plan = [call.args[0] for call in added if isinstance(call.args[0], ContentPlan)][0]
    assert single1.plan_id == plan.id
    assert carousel.plan_id == plan.id
    assert single2.plan_id == plan.id


@pytest.mark.asyncio
async def test_execute_localizes_scheduled_for_to_utc() -> None:
    """localize_to_utc must be applied — IST 18:00 → UTC 12:30."""
    plugin = _make_plugin(tz="Asia/Kolkata")
    db = _make_db()

    input_ = BuildContentPlanInput(
        items=[PlanItem(image_indices=[0], caption="Test", scheduled_for=_T1)]
    )
    await plugin.execute(input_, user_id=uuid.uuid4(), db=db, image_urls=_URLS)

    from models.scheduled_post import ScheduledPost

    posts = [
        call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], ScheduledPost)
    ]
    assert len(posts) == 1
    utc_hour = posts[0].scheduled_for.hour
    # 18:00 IST = 12:30 UTC
    assert utc_hour == 12


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
