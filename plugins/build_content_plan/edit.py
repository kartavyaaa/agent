from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar, Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from core.exceptions import PluginError
from models.content_plan import ContentPlan
from plugins.base import HealthStatus, PluginBase
from plugins.build_content_plan.render import render_items


class EditDraftPlanInput(BaseModel):
    plan_id: str
    op: Literal["drop", "edit_caption", "edit_time", "reorder", "merge", "split"]
    item_index: int | None = None  # 1-based (single-item ops)
    item_indices: list[int] | None = None  # 1-based list (merge)
    caption: str | None = None
    scheduled_for: str | None = None  # local wall-clock ISO string
    new_order: list[int] | None = None  # 1-based new positions (reorder)
    split_photo_index: int | None = None  # 0-based within item's image_indices (split)


class EditDraftPlanOutput(BaseModel):
    plan_id: str
    confirmation: str


class EditDraftPlanConfig(BaseModel):
    pass


class EditDraftPlanPlugin(PluginBase):
    name: ClassVar[str] = "edit_draft_plan"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Edit an active draft content plan. "
        "Use this when the user wants to change something about the plan that was just created: "
        "drop an item, change a caption, change a time, reorder items, merge items into a carousel, "
        "or split a carousel into singles. "
        "Always call this with the plan_id shown in the draft context above."
    )
    capabilities: ClassVar[list[str]] = ["edit_draft_plan"]
    permissions: ClassVar[list[str]] = ["db:write"]
    dependencies: ClassVar[list[str]] = []
    input_schema: ClassVar[type[BaseModel]] = EditDraftPlanInput
    output_schema: ClassVar[type[BaseModel]] = EditDraftPlanOutput
    config_schema: ClassVar[type[BaseModel]] = EditDraftPlanConfig
    requires_approval: ClassVar[bool] = False
    needs_hosted_image: ClassVar[bool] = False
    needs_hosted_images: ClassVar[bool] = False

    def __init__(self, tz_name: str = "UTC") -> None:
        self._tz_name = tz_name

    async def execute(
        self,
        input: BaseModel,  # noqa: A002
        *,
        user_id: uuid.UUID,
        db: AsyncSession,
        **kwargs: Any,
    ) -> EditDraftPlanOutput:
        assert isinstance(input, EditDraftPlanInput)

        # Fetch draft
        result = await db.execute(
            select(ContentPlan).where(
                ContentPlan.id == uuid.UUID(input.plan_id),
                ContentPlan.user_id == user_id,
                ContentPlan.status == "draft",
            )
        )
        plan = result.scalar_one_or_none()
        if plan is None:
            raise PluginError(f"No active draft plan found with id={input.plan_id}.")

        items: list[dict[str, Any]] = list(plan.items or [])
        n = len(items)

        if input.op == "drop":
            idx = (input.item_index or 0) - 1
            if idx < 0 or idx >= n:
                raise PluginError(f"Item {input.item_index} does not exist (plan has {n} items).")
            if n == 1:
                raise PluginError("Cannot drop the only item. Discard the plan instead.")
            items.pop(idx)

        elif input.op == "edit_caption":
            idx = (input.item_index or 0) - 1
            if idx < 0 or idx >= n:
                raise PluginError(f"Item {input.item_index} does not exist.")
            if not input.caption:
                raise PluginError("caption is required for edit_caption.")
            items[idx] = {**items[idx], "caption": input.caption}

        elif input.op == "edit_time":
            idx = (input.item_index or 0) - 1
            if idx < 0 or idx >= n:
                raise PluginError(f"Item {input.item_index} does not exist.")
            if not input.scheduled_for:
                raise PluginError("scheduled_for is required for edit_time.")
            items[idx] = {**items[idx], "scheduled_for": input.scheduled_for}

        elif input.op == "reorder":
            if not input.new_order or len(input.new_order) != n:
                raise PluginError(f"new_order must list all {n} item positions.")
            if sorted(input.new_order) != list(range(1, n + 1)):
                raise PluginError(f"new_order must be a permutation of 1..{n}.")
            items = [items[i - 1] for i in input.new_order]

        elif input.op == "merge":
            indices = sorted(set(input.item_indices or []))
            if len(indices) < 2:
                raise PluginError("merge requires at least 2 item_indices.")
            for i in indices:
                if i < 1 or i > n:
                    raise PluginError(f"Item {i} does not exist (plan has {n} items).")
            source_items = [items[i - 1] for i in indices]
            combined_indices: list[int] = []
            for src in source_items:
                for idx_val in src.get("image_indices", []):
                    if idx_val not in combined_indices:
                        combined_indices.append(idx_val)
            # Use earliest scheduled_for
            times = [src.get("scheduled_for", "") for src in source_items]
            earliest = min((t for t in times if t), default=times[0] if times else "")
            merged = {
                "image_indices": combined_indices,
                "caption": source_items[0].get("caption", ""),
                "scheduled_for": earliest,
            }
            # Remove source items and insert merged at first source position
            first_pos = indices[0] - 1
            keep = [item for i, item in enumerate(items, 1) if i not in set(indices)]
            items = keep[:first_pos] + [merged] + keep[first_pos:]

        elif input.op == "split":
            idx = (input.item_index or 0) - 1
            if idx < 0 or idx >= n:
                raise PluginError(f"Item {input.item_index} does not exist.")
            src = items[idx]
            src_indices = list(src.get("image_indices", []))
            if len(src_indices) < 2:
                raise PluginError("Cannot split an item that already has only 1 photo.")
            split_pos = input.split_photo_index or 0
            if split_pos < 0 or split_pos >= len(src_indices):
                raise PluginError(f"split_photo_index {split_pos} is out of range.")
            popped = src_indices.pop(split_pos)
            source_caption = src.get("caption", "")
            new_item = {
                "image_indices": [popped],
                "caption": source_caption,
                "scheduled_for": src.get("scheduled_for", ""),
            }
            items[idx] = {**src, "image_indices": src_indices}
            items.insert(idx + 1, new_item)

        else:
            raise PluginError(f"Unknown op: {input.op!r}")

        # Validate resulting items
        n_images = len(plan.image_urls or [])
        from plugins.build_content_plan.plugin import BuildContentPlanPlugin

        BuildContentPlanPlugin._validate_items_data(items, n_images)

        plan.items = items
        flag_modified(plan, "items")
        await db.flush()

        rendered = render_items(items, self._tz_name)
        confirmation = (
            f"Updated plan ({len(items)} post{'s' if len(items) != 1 else ''}):\n"
            + rendered
            + "\n\nReply to keep editing, or say 'approve' when ready."
        )
        return EditDraftPlanOutput(plan_id=str(plan.id), confirmation=confirmation)

    async def health_check(self) -> HealthStatus:
        return HealthStatus(status="healthy", message="ok", checked_at=datetime.now(UTC))
