from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import PluginError
from core.timeutil import format_local, localize_to_utc
from models.content_plan import ContentPlan
from models.scheduled_post import ScheduledPost
from plugins.base import HealthStatus, PluginBase
from plugins.build_content_plan.render import render_items  # noqa: F401 — available for callers


class ApproveDraftPlanInput(BaseModel):
    plan_id: str
    plan_summary: str  # rendered item list from draft_block, used as Confirm preview_text


class ApproveDraftPlanOutput(BaseModel):
    content_plan_id: str
    scheduled_post_ids: list[str]
    confirmation: str


class ApproveDraftPlanConfig(BaseModel):
    pass


class ApproveDraftPlanPlugin(PluginBase):
    name: ClassVar[str] = "approve_draft_plan"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Approve a draft content plan and schedule all its posts. "
        "Call this when the user says the plan looks good, they're ready to schedule it, "
        "or they explicitly approve. "
        "Pass plan_id and plan_summary (the rendered item list from the draft context above). "
        "The system will show a final confirmation prompt before scheduling."
    )
    capabilities: ClassVar[list[str]] = ["schedule_post", "instagram_carousel"]
    permissions: ClassVar[list[str]] = ["db:write", "network:write", "social:instagram"]
    dependencies: ClassVar[list[str]] = ["instagram"]
    input_schema: ClassVar[type[BaseModel]] = ApproveDraftPlanInput
    output_schema: ClassVar[type[BaseModel]] = ApproveDraftPlanOutput
    config_schema: ClassVar[type[BaseModel]] = ApproveDraftPlanConfig
    requires_approval: ClassVar[bool] = True
    needs_hosted_image: ClassVar[bool] = False
    needs_hosted_images: ClassVar[bool] = False

    def __init__(self, tz_name: str = "UTC") -> None:
        self._tz_name = tz_name

    @classmethod
    def build_preview(cls, args: dict[str, Any]) -> str:
        summary = args.get("plan_summary", "Your content plan")
        return f"{summary}\n\nApprove and schedule all posts?"

    async def execute(
        self,
        input: BaseModel,  # noqa: A002
        *,
        user_id: uuid.UUID,
        db: AsyncSession,
        **kwargs: Any,
    ) -> ApproveDraftPlanOutput:
        assert isinstance(input, ApproveDraftPlanInput)

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
        if not plan.items:
            raise PluginError("Draft plan has no items to schedule.")
        if not plan.image_urls:
            raise PluginError("Draft plan has no image URLs — cannot resolve items.")

        post_rows: list[ScheduledPost] = []
        for item in plan.items:
            actual_urls = [plan.image_urls[i] for i in item["image_indices"]]
            raw_sf = item["scheduled_for"]
            # Parse back to datetime for localize_to_utc
            naive_dt = datetime.fromisoformat(raw_sf.replace("Z", ""))
            scheduled_for_utc = localize_to_utc(naive_dt, self._tz_name)
            caption = item["caption"]
            n = len(actual_urls)
            if n == 1:
                row = ScheduledPost(
                    id=uuid.uuid4(),
                    user_id=user_id,
                    post_type="single",
                    image_url=actual_urls[0],
                    image_urls=None,
                    plan_id=plan.id,
                    caption=caption,
                    scheduled_for=scheduled_for_utc,
                    status="scheduled",
                )
            else:
                row = ScheduledPost(
                    id=uuid.uuid4(),
                    user_id=user_id,
                    post_type="carousel",
                    image_url=None,
                    image_urls=actual_urls,
                    plan_id=plan.id,
                    caption=caption,
                    scheduled_for=scheduled_for_utc,
                    status="scheduled",
                )
            db.add(row)
            post_rows.append(row)

        plan.status = "approved"
        await db.flush()

        schedule_lines = [
            f"  • {format_local(r.scheduled_for, self._tz_name)} — {r.post_type} — {r.caption[:40]}"
            for r in post_rows
        ]
        confirmation = (
            f"✅ Scheduled {len(post_rows)} post{'s' if len(post_rows) != 1 else ''} — "
            "each will ask for confirmation when it's time:\n" + "\n".join(schedule_lines)
        )
        return ApproveDraftPlanOutput(
            content_plan_id=str(plan.id),
            scheduled_post_ids=[str(r.id) for r in post_rows],
            confirmation=confirmation,
        )

    async def health_check(self) -> HealthStatus:
        return HealthStatus(status="healthy", message="ok", checked_at=datetime.now(UTC))
