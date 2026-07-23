"""Unit tests for EditDraftPlanPlugin."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.exceptions import PluginError
from plugins.build_content_plan.edit import EditDraftPlanInput, EditDraftPlanPlugin

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_plugin() -> EditDraftPlanPlugin:
    return EditDraftPlanPlugin(tz_name="UTC")


_URLS = [f"https://r2.example.com/{i}.jpg" for i in range(4)]

_BASE_ITEMS = [
    {
        "image_indices": [0],
        "caption": "First post",
        "scheduled_for": "2026-07-28T18:00:00",
    },
    {
        "image_indices": [1, 2],
        "caption": "Carousel",
        "scheduled_for": "2026-07-29T18:00:00",
    },
    {
        "image_indices": [3],
        "caption": "Last post",
        "scheduled_for": "2026-07-30T18:00:00",
    },
]


def _make_draft_plan(
    items: list[dict[str, Any]] | None = None,
    image_urls: list[str] | None = None,
    user_id: uuid.UUID | None = None,
) -> MagicMock:
    plan = MagicMock()
    plan.id = uuid.uuid4()
    plan.user_id = user_id or uuid.uuid4()
    plan.status = "draft"
    plan.items = list(items or _BASE_ITEMS)
    plan.image_urls = list(image_urls or _URLS)
    return plan


def _make_db(plan: MagicMock | None = None) -> MagicMock:
    db = MagicMock()
    db.flush = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = plan
    db.execute = AsyncMock(return_value=result)
    return db


async def _run_op(
    plugin: EditDraftPlanPlugin, plan: MagicMock, input_: EditDraftPlanInput
) -> MagicMock:
    db = _make_db(plan)
    return await plugin.execute(input_, user_id=plan.user_id, db=db)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Tests: drop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drop_removes_correct_item() -> None:
    plugin = _make_plugin()
    plan = _make_draft_plan()
    original_count = len(plan.items)

    result = await _run_op(
        plugin,
        plan,
        EditDraftPlanInput(plan_id=str(plan.id), op="drop", item_index=2),
    )

    assert len(plan.items) == original_count - 1
    # Item at index 2 (1-based = "Carousel") was dropped; items 1 and 3 remain
    captions = [item["caption"] for item in plan.items]
    assert "Carousel" not in captions
    assert "First post" in captions
    assert "Last post" in captions
    assert "2 posts" in result.confirmation or "2 post" in result.confirmation


@pytest.mark.asyncio
async def test_drop_last_item_raises() -> None:
    plugin = _make_plugin()
    plan = _make_draft_plan(
        items=[{"image_indices": [0], "caption": "Only", "scheduled_for": "2026-07-28T18:00:00"}]
    )
    with pytest.raises(PluginError, match="only item"):
        await _run_op(
            plugin, plan, EditDraftPlanInput(plan_id=str(plan.id), op="drop", item_index=1)
        )


@pytest.mark.asyncio
async def test_drop_out_of_range_raises() -> None:
    plugin = _make_plugin()
    plan = _make_draft_plan()
    with pytest.raises(PluginError, match="does not exist"):
        await _run_op(
            plugin, plan, EditDraftPlanInput(plan_id=str(plan.id), op="drop", item_index=99)
        )


# ---------------------------------------------------------------------------
# Tests: edit_caption
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_caption_changes_only_target() -> None:
    plugin = _make_plugin()
    plan = _make_draft_plan()

    await _run_op(
        plugin,
        plan,
        EditDraftPlanInput(
            plan_id=str(plan.id), op="edit_caption", item_index=1, caption="New caption"
        ),
    )

    assert plan.items[0]["caption"] == "New caption"
    # Other items unchanged
    assert plan.items[1]["caption"] == "Carousel"
    assert plan.items[2]["caption"] == "Last post"


@pytest.mark.asyncio
async def test_edit_caption_missing_raises() -> None:
    plugin = _make_plugin()
    plan = _make_draft_plan()
    with pytest.raises(PluginError, match="caption is required"):
        await _run_op(
            plugin,
            plan,
            EditDraftPlanInput(plan_id=str(plan.id), op="edit_caption", item_index=1),
        )


# ---------------------------------------------------------------------------
# Tests: edit_time
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_time_changes_only_target() -> None:
    plugin = _make_plugin()
    plan = _make_draft_plan()

    new_time = "2026-08-01T09:00:00"
    await _run_op(
        plugin,
        plan,
        EditDraftPlanInput(
            plan_id=str(plan.id), op="edit_time", item_index=3, scheduled_for=new_time
        ),
    )

    assert plan.items[2]["scheduled_for"] == new_time
    # Other items unchanged
    assert plan.items[0]["scheduled_for"] == "2026-07-28T18:00:00"
    assert plan.items[1]["scheduled_for"] == "2026-07-29T18:00:00"


@pytest.mark.asyncio
async def test_edit_time_missing_raises() -> None:
    plugin = _make_plugin()
    plan = _make_draft_plan()
    with pytest.raises(PluginError, match="scheduled_for is required"):
        await _run_op(
            plugin,
            plan,
            EditDraftPlanInput(plan_id=str(plan.id), op="edit_time", item_index=1),
        )


# ---------------------------------------------------------------------------
# Tests: reorder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reorder_reorders_list() -> None:
    plugin = _make_plugin()
    plan = _make_draft_plan()

    # Reverse order: 3, 2, 1
    await _run_op(
        plugin,
        plan,
        EditDraftPlanInput(plan_id=str(plan.id), op="reorder", new_order=[3, 2, 1]),
    )

    assert plan.items[0]["caption"] == "Last post"
    assert plan.items[1]["caption"] == "Carousel"
    assert plan.items[2]["caption"] == "First post"
    # image_indices preserved
    assert plan.items[0]["image_indices"] == [3]
    assert plan.items[1]["image_indices"] == [1, 2]
    assert plan.items[2]["image_indices"] == [0]


@pytest.mark.asyncio
async def test_reorder_wrong_length_raises() -> None:
    plugin = _make_plugin()
    plan = _make_draft_plan()
    with pytest.raises(PluginError, match="new_order must list all"):
        await _run_op(
            plugin,
            plan,
            EditDraftPlanInput(plan_id=str(plan.id), op="reorder", new_order=[1, 2]),
        )


@pytest.mark.asyncio
async def test_reorder_invalid_permutation_raises() -> None:
    plugin = _make_plugin()
    plan = _make_draft_plan()
    with pytest.raises(PluginError, match="permutation"):
        await _run_op(
            plugin,
            plan,
            EditDraftPlanInput(plan_id=str(plan.id), op="reorder", new_order=[1, 1, 3]),
        )


# ---------------------------------------------------------------------------
# Tests: merge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merge_combines_image_indices() -> None:
    plugin = _make_plugin()
    plan = _make_draft_plan()

    # Merge items 1 and 2 (First post [0] + Carousel [1,2]) → combined [0,1,2]
    await _run_op(
        plugin,
        plan,
        EditDraftPlanInput(plan_id=str(plan.id), op="merge", item_indices=[1, 2]),
    )

    assert len(plan.items) == 2
    merged = plan.items[0]
    assert merged["image_indices"] == [0, 1, 2]
    # Last post still present at index 1
    assert plan.items[1]["caption"] == "Last post"


@pytest.mark.asyncio
async def test_merge_uses_earliest_time() -> None:
    plugin = _make_plugin()
    plan = _make_draft_plan()

    # Item 1 is 07-28, item 2 is 07-29 → merged gets 07-28
    await _run_op(
        plugin,
        plan,
        EditDraftPlanInput(plan_id=str(plan.id), op="merge", item_indices=[1, 2]),
    )

    assert plan.items[0]["scheduled_for"] == "2026-07-28T18:00:00"


@pytest.mark.asyncio
async def test_merge_too_few_indices_raises() -> None:
    plugin = _make_plugin()
    plan = _make_draft_plan()
    with pytest.raises(PluginError, match="at least 2"):
        await _run_op(
            plugin,
            plan,
            EditDraftPlanInput(plan_id=str(plan.id), op="merge", item_indices=[1]),
        )


# ---------------------------------------------------------------------------
# Tests: split
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_split_produces_two_items() -> None:
    plugin = _make_plugin()
    plan = _make_draft_plan()

    # Split item 2 (Carousel [1,2]) at split_photo_index=0 → [1] + [2]
    await _run_op(
        plugin,
        plan,
        EditDraftPlanInput(plan_id=str(plan.id), op="split", item_index=2, split_photo_index=0),
    )

    assert len(plan.items) == 4
    # Item at idx 1 (original carousel) now has only [2]
    assert plan.items[1]["image_indices"] == [2]
    # New item inserted after, has [1]
    assert plan.items[2]["image_indices"] == [1]
    # Same caption copied to both
    assert plan.items[1]["caption"] == "Carousel"
    assert plan.items[2]["caption"] == "Carousel"


@pytest.mark.asyncio
async def test_split_single_item_raises() -> None:
    plugin = _make_plugin()
    plan = _make_draft_plan()
    with pytest.raises(PluginError, match="only 1 photo"):
        await _run_op(
            plugin,
            plan,
            EditDraftPlanInput(plan_id=str(plan.id), op="split", item_index=1, split_photo_index=0),
        )


# ---------------------------------------------------------------------------
# Tests: error conditions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wrong_plan_id_raises() -> None:
    plugin = _make_plugin()
    db = _make_db(plan=None)
    uid = uuid.uuid4()
    with pytest.raises(PluginError, match="No active draft plan"):
        await plugin.execute(
            EditDraftPlanInput(plan_id=str(uuid.uuid4()), op="drop", item_index=1),
            user_id=uid,
            db=db,
        )


@pytest.mark.asyncio
async def test_approved_plan_not_found() -> None:
    """A plan that is 'approved' is not returned by the draft query (scalar=None)."""
    plugin = _make_plugin()
    db = _make_db(plan=None)  # DB returns None (already approved)
    uid = uuid.uuid4()
    with pytest.raises(PluginError, match="No active draft plan"):
        await plugin.execute(
            EditDraftPlanInput(plan_id=str(uuid.uuid4()), op="drop", item_index=1),
            user_id=uid,
            db=db,
        )
