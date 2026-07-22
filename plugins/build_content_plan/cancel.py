from __future__ import annotations

import uuid as _uuid_mod
from datetime import UTC, datetime
from typing import Any, ClassVar

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.content_plan import ContentPlan
from models.scheduled_post import ScheduledPost
from plugins.base import HealthStatus, PluginBase
from plugins.build_content_plan.schemas import (
    CancelContentPlanConfig,
    CancelContentPlanInput,
    CancelContentPlanOutput,
)


class CancelContentPlanPlugin(PluginBase):
    name: ClassVar[str] = "cancel_content_plan"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Cancel all remaining scheduled posts in a content plan. "
        "Use the content_plan_id from a list_content_plans call. "
        "Only cancels posts with status 'scheduled' — posts already triggered "
        "(awaiting confirmation at post time) must be cancelled via their inline Cancel button."
    )
    capabilities: ClassVar[list[str]] = ["schedule_post"]
    permissions: ClassVar[list[str]] = ["db:write"]
    dependencies: ClassVar[list[str]] = []
    input_schema: ClassVar[type[BaseModel]] = CancelContentPlanInput
    output_schema: ClassVar[type[BaseModel]] = CancelContentPlanOutput
    config_schema: ClassVar[type[BaseModel]] = CancelContentPlanConfig

    async def execute(
        self,
        input: BaseModel,  # noqa: A002
        *,
        user_id: _uuid_mod.UUID,
        db: AsyncSession,
        **kwargs: Any,
    ) -> CancelContentPlanOutput:
        assert isinstance(input, CancelContentPlanInput)
        try:
            plan_id = _uuid_mod.UUID(input.content_plan_id)
        except ValueError:
            return CancelContentPlanOutput(
                content_plan_id=input.content_plan_id,
                cancelled_count=0,
                detail="Content plan not found.",
            )

        plan_result = await db.execute(
            select(ContentPlan).where(
                ContentPlan.id == plan_id,
                ContentPlan.user_id == user_id,
            )
        )
        plan = plan_result.scalar_one_or_none()
        if plan is None:
            return CancelContentPlanOutput(
                content_plan_id=input.content_plan_id,
                cancelled_count=0,
                detail="Content plan not found.",
            )

        posts_result = await db.execute(
            select(ScheduledPost).where(
                ScheduledPost.plan_id == plan_id,
                ScheduledPost.status == "scheduled",
            )
        )
        posts = posts_result.scalars().all()
        for post in posts:
            post.status = "cancelled"
        await db.flush()

        n = len(posts)
        return CancelContentPlanOutput(
            content_plan_id=str(plan_id),
            cancelled_count=n,
            detail=(
                f"Cancelled {n} scheduled post{'s' if n != 1 else ''} from the plan. "
                "Any posts already triggered must be cancelled via their inline Cancel button."
                if n > 0
                else "No scheduled posts remaining in this plan."
            ),
        )

    async def health_check(self) -> HealthStatus:
        return HealthStatus(
            status="healthy",
            message="ok",
            checked_at=datetime.now(UTC),
        )
