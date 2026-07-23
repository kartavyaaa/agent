"""Unit tests for ApproveDraftPlanPlugin."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.exceptions import PluginError
from plugins.build_content_plan.approve import ApproveDraftPlanInput, ApproveDraftPlanPlugin

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_plugin(tz: str = "UTC") -> ApproveDraftPlanPlugin:
    return ApproveDraftPlanPlugin(tz_name=tz)


_URLS = [
    "https://r2.example.com/0.jpg",
    "https://r2.example.com/1.jpg",
    "https://r2.example.com/2.jpg",
]


def _make_plan(
    *,
    items: list[dict[str, Any]] | None = None,
    image_urls: list[str] | None = None,
    status: str = "draft",
) -> MagicMock:
    plan = MagicMock()
    plan.id = uuid.uuid4()
    plan.user_id = uuid.uuid4()
    plan.status = status
    plan.items = items
    plan.image_urls = image_urls or _URLS
    return plan


def _make_db(plan: MagicMock | None = None) -> MagicMock:
    db = MagicMock()
    db.flush = AsyncMock()
    db.add = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = plan
    db.execute = AsyncMock(return_value=result)
    return db


# ---------------------------------------------------------------------------
# Happy path: 2-item draft (single + carousel)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_creates_scheduled_posts() -> None:
    plugin = _make_plugin()
    plan = _make_plan(
        items=[
            {
                "image_indices": [0],
                "caption": "Single shot",
                "scheduled_for": "2026-07-28T18:00:00",
            },
            {
                "image_indices": [1, 2],
                "caption": "Carousel",
                "scheduled_for": "2026-07-29T18:00:00",
            },
        ]
    )
    db = _make_db(plan)

    result = await plugin.execute(
        ApproveDraftPlanInput(plan_id=str(plan.id), plan_summary="summary"),
        user_id=plan.user_id,
        db=db,
    )

    assert len(result.scheduled_post_ids) == 2
    assert result.content_plan_id == str(plan.id)
    assert plan.status == "approved"
    assert db.add.call_count == 2

    from models.scheduled_post import ScheduledPost

    posts = [
        call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], ScheduledPost)
    ]
    single = next(p for p in posts if p.post_type == "single")
    carousel = next(p for p in posts if p.post_type == "carousel")

    assert single.image_url == _URLS[0]
    assert single.image_urls is None
    assert carousel.image_url is None
    assert carousel.image_urls == [_URLS[1], _URLS[2]]


# ---------------------------------------------------------------------------
# Invariant regression: merge + split + reorder then approve resolves correct URLs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_invariant_after_mutations() -> None:
    """
    Build plan with 3 images: url0, url1, url2.
    Item A: image_indices=[0,1], Item B: image_indices=[2]
    Op: merge A+B → image_indices=[0,1,2]
    Op: split at split_photo_index=2 → [0,1] + [2]
    Op: reorder [2,1] → item1 gets [2], item2 gets [0,1]
    Approve → ScheduledPost 1 has image_urls=[url2], ScheduledPost 2 has image_urls=[url0, url1]
    """
    urls = [
        "https://r2.example.com/0.jpg",
        "https://r2.example.com/1.jpg",
        "https://r2.example.com/2.jpg",
    ]

    # Simulate the item mutations directly (the operations are tested in test_edit_draft_plan)
    # Final state after all mutations:
    # reorder [2,1] of (merged then split) items
    # After merge A+B: [{indices:[0,1,2], ...}]
    # After split at 2 (0-based index 2 within [0,1,2]): [{indices:[0,1]}, {indices:[2]}]
    # After reorder [2,1]: [{indices:[2]}, {indices:[0,1]}]
    final_items = [
        {"image_indices": [2], "caption": "B caption", "scheduled_for": "2026-07-28T18:00:00"},
        {"image_indices": [0, 1], "caption": "A caption", "scheduled_for": "2026-07-28T18:00:00"},
    ]

    plan = _make_plan(items=final_items, image_urls=urls)
    db = _make_db(plan)

    plugin = _make_plugin()
    await plugin.execute(
        ApproveDraftPlanInput(plan_id=str(plan.id), plan_summary="summary"),
        user_id=plan.user_id,
        db=db,
    )

    from models.scheduled_post import ScheduledPost

    posts = [
        call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], ScheduledPost)
    ]
    assert len(posts) == 2

    # First post: image_indices=[2] → url2 (single)
    p1 = posts[0]
    assert p1.post_type == "single"
    assert p1.image_url == urls[2]

    # Second post: image_indices=[0,1] → url0, url1 (carousel)
    p2 = posts[1]
    assert p2.post_type == "carousel"
    assert p2.image_urls == [urls[0], urls[1]]


# ---------------------------------------------------------------------------
# Error conditions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_no_draft_raises() -> None:
    plugin = _make_plugin()
    db = _make_db(plan=None)
    with pytest.raises(PluginError, match="No active draft plan"):
        await plugin.execute(
            ApproveDraftPlanInput(plan_id=str(uuid.uuid4()), plan_summary="x"),
            user_id=uuid.uuid4(),
            db=db,
        )


@pytest.mark.asyncio
async def test_approve_no_items_raises() -> None:
    plugin = _make_plugin()
    plan = _make_plan(items=None)
    db = _make_db(plan)
    with pytest.raises(PluginError, match="no items"):
        await plugin.execute(
            ApproveDraftPlanInput(plan_id=str(plan.id), plan_summary="x"),
            user_id=plan.user_id,
            db=db,
        )


@pytest.mark.asyncio
async def test_approve_no_image_urls_raises() -> None:
    plugin = _make_plugin()
    plan = _make_plan(
        items=[{"image_indices": [0], "caption": "test", "scheduled_for": "2026-07-28T18:00:00"}],
        image_urls=None,
    )
    plan.image_urls = None
    db = _make_db(plan)
    with pytest.raises(PluginError, match="no image URLs"):
        await plugin.execute(
            ApproveDraftPlanInput(plan_id=str(plan.id), plan_summary="x"),
            user_id=plan.user_id,
            db=db,
        )


# ---------------------------------------------------------------------------
# build_preview
# ---------------------------------------------------------------------------


def test_build_preview_uses_plan_summary() -> None:
    summary = "1. Single (1 photo) — 'Cool' — Mon Jul 28"
    preview = ApproveDraftPlanPlugin.build_preview({"plan_summary": summary, "plan_id": "abc"})
    assert summary in preview
    assert "Approve" in preview
