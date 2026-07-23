from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

from pydantic import BaseModel
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import PluginError
from models.content_plan import ContentPlan
from plugins.base import HealthStatus, PluginBase
from plugins.build_content_plan.render import parse_preview_time, render_items
from plugins.build_content_plan.schemas import (
    BuildContentPlanConfig,
    BuildContentPlanInput,
    BuildContentPlanOutput,
    PlanItem,
)

_MIN_CAROUSEL = 2
_MAX_CAROUSEL = 10


class BuildContentPlanPlugin(PluginBase):
    """Build a scheduled content plan from multiple photos.

    requires_approval=False: the plugin now creates a DRAFT (no ScheduledPost rows).
    The user edits via edit_draft_plan, then approves via approve_draft_plan.
    needs_hosted_images=True: the engine uploads all N images to R2 and injects
    a flat list of URLs via the lazy plural provider. Items specify which indices
    belong to them.
    """

    name: ClassVar[str] = "build_content_plan"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Build a scheduled Instagram content plan from multiple photos. "
        "Call this when the user sends multiple photos and wants them scheduled as a series "
        "of future posts (some single-photo, some carousels) over days or weeks. "
        "Group the photos into items — each item has image_indices (which of the N photos "
        "belong to it), a caption, and a scheduled_for time (user's LOCAL wall-clock, no Z). "
        "Items with 1 image_index become single posts; items with 2+ become carousels. "
        "The system shows the user a readable plan summary before any scheduling happens. "
        "Each scheduled post will ask for a final Confirm at its scheduled time before posting."
    )
    capabilities: ClassVar[list[str]] = ["schedule_post", "instagram_carousel"]
    permissions: ClassVar[list[str]] = ["db:write", "network:write", "social:instagram"]
    dependencies: ClassVar[list[str]] = ["instagram"]
    input_schema: ClassVar[type[BaseModel]] = BuildContentPlanInput
    output_schema: ClassVar[type[BaseModel]] = BuildContentPlanOutput
    config_schema: ClassVar[type[BaseModel]] = BuildContentPlanConfig
    requires_approval: ClassVar[bool] = False
    needs_hosted_image: ClassVar[bool] = False
    needs_hosted_images: ClassVar[bool] = True

    def __init__(self, tz_name: str = "UTC") -> None:
        self._tz_name = tz_name

    @classmethod
    def build_preview(cls, args: dict[str, Any]) -> str:
        items: list[dict[str, Any]] = args.get("items", [])
        lines: list[str] = [
            f"📅 Content plan ({len(items)} post{'s' if len(items) != 1 else ''}):\n"
        ]
        for i, item in enumerate(items, 1):
            indices: list[int] = item.get("image_indices", [])
            n = len(indices)
            kind = "Carousel" if n > 1 else "Single"
            caption: str = item.get("caption", "")
            caption_preview = (caption[:57] + "…") if len(caption) > 60 else caption
            raw_time = item.get("scheduled_for", "")
            time_str = parse_preview_time(raw_time)
            lines.append(
                f"  {i}. {kind} ({n} photo{'s' if n != 1 else ''}) — \"{caption_preview}\" — {time_str}"
            )
        lines.append("\nEach post will ask for final confirmation when it's time.")
        lines.append("Approve this plan?")
        return "\n".join(lines)

    async def execute(
        self,
        input: BaseModel,  # noqa: A002
        *,
        user_id: uuid.UUID,
        db: AsyncSession,
        image_urls: list[str] | None = None,
        **kwargs: Any,
    ) -> BuildContentPlanOutput:
        assert isinstance(input, BuildContentPlanInput)
        if not image_urls:
            raise PluginError(
                "build_content_plan requires image_urls to be injected by the engine."
            )
        if not input.items:
            raise PluginError("Content plan must have at least one item.")

        self._validate_items(input.items, len(image_urls))

        # Auto-discard any existing draft for this user
        await db.execute(
            sa_update(ContentPlan)
            .where(ContentPlan.user_id == user_id, ContentPlan.status == "draft")
            .values(status="discarded")
        )

        # Serialize items to JSONB-compatible dicts
        items_data = [
            {
                "image_indices": item.image_indices,
                "caption": item.caption,
                "scheduled_for": item.scheduled_for.isoformat(),
            }
            for item in input.items
        ]

        plan = ContentPlan(
            id=uuid.uuid4(),
            user_id=user_id,
            status="draft",
            items=items_data,
            image_urls=image_urls,
        )
        db.add(plan)
        await db.flush()

        rendered = render_items(items_data)
        confirmation = (
            f"📅 Draft plan ({len(input.items)} post{'s' if len(input.items) != 1 else ''}):\n"
            + rendered
            + "\n\nReply to edit, or say 'approve' when ready."
        )
        return BuildContentPlanOutput(
            content_plan_id=str(plan.id),
            scheduled_post_ids=[],
            confirmation=confirmation,
        )

    def _validate_items(self, items: list[PlanItem], n_images: int) -> None:
        for i, item in enumerate(items):
            if not item.image_indices:
                raise PluginError(f"Item {i + 1} has no image_indices.")
            for idx in item.image_indices:
                if idx < 0 or idx >= n_images:
                    raise PluginError(
                        f"Item {i + 1} image_index {idx} is out of range "
                        f"(0–{n_images - 1} available)."
                    )
            n = len(item.image_indices)
            if n > _MAX_CAROUSEL:
                raise PluginError(
                    f"Item {i + 1} has {n} images — maximum for a carousel is {_MAX_CAROUSEL}."
                )

    @staticmethod
    def _validate_items_data(items: list[dict[str, Any]], n_images: int) -> None:
        """Validate raw JSONB item dicts (used by edit_draft_plan after mutations)."""
        for i, item in enumerate(items):
            indices: list[int] = item.get("image_indices", [])
            if not indices:
                raise PluginError(f"Item {i + 1} has no image_indices.")
            for idx_val in indices:
                if idx_val < 0 or idx_val >= n_images:
                    raise PluginError(
                        f"Item {i + 1} image_index {idx_val} is out of range (0–{n_images - 1})."
                    )
            if len(indices) > _MAX_CAROUSEL:
                raise PluginError(
                    f"Item {i + 1} has {len(indices)} images — maximum is {_MAX_CAROUSEL}."
                )

    async def health_check(self) -> HealthStatus:
        return HealthStatus(
            status="healthy",
            message="ok",
            checked_at=datetime.now(UTC),
        )
