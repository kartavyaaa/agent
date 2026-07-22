from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.timeutil import format_local
from models.content_plan import ContentPlan
from models.scheduled_post import ScheduledPost
from plugins.base import HealthStatus, PluginBase
from plugins.build_content_plan.schemas import (
    ContentPlanSummary,
    ListContentPlansConfig,
    ListContentPlansInput,
    ListContentPlansOutput,
)


class ListContentPlansPlugin(PluginBase):
    name: ClassVar[str] = "list_content_plans"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "List the user's approved content plans and their scheduled post counts. "
        "Call this when the user asks what content plans they have, or wants to see their schedule. "
        "Returns up to 20 plans ordered by creation time (newest first). "
        "Each plan includes its content_plan_id — pass it to cancel_content_plan to cancel all posts."
    )
    capabilities: ClassVar[list[str]] = ["schedule_post"]
    permissions: ClassVar[list[str]] = ["db:read"]
    dependencies: ClassVar[list[str]] = []
    input_schema: ClassVar[type[BaseModel]] = ListContentPlansInput
    output_schema: ClassVar[type[BaseModel]] = ListContentPlansOutput
    config_schema: ClassVar[type[BaseModel]] = ListContentPlansConfig

    def __init__(self, tz_name: str = "UTC") -> None:
        self._tz_name = tz_name

    async def execute(
        self,
        input: BaseModel,  # noqa: A002
        *,
        user_id: uuid.UUID,
        db: AsyncSession,
        **kwargs: Any,
    ) -> ListContentPlansOutput:
        assert isinstance(input, ListContentPlansInput)
        plans_result = await db.execute(
            select(ContentPlan)
            .where(ContentPlan.user_id == user_id, ContentPlan.status == "approved")
            .order_by(ContentPlan.created_at.desc())
            .limit(20)
        )
        plans = plans_result.scalars().all()

        summaries: list[ContentPlanSummary] = []
        for plan in plans:
            posts_result = await db.execute(
                select(ScheduledPost).where(ScheduledPost.plan_id == plan.id)
            )
            posts = posts_result.scalars().all()
            total = len(posts)
            scheduled = sum(1 for p in posts if p.status == "scheduled")
            next_post = min(
                (p for p in posts if p.status == "scheduled"),
                key=lambda p: p.scheduled_for,
                default=None,
            )
            summaries.append(
                ContentPlanSummary(
                    content_plan_id=str(plan.id),
                    created_at_local=format_local(plan.created_at, self._tz_name),
                    total_items=total,
                    scheduled_items=scheduled,
                    next_scheduled_local=(
                        format_local(next_post.scheduled_for, self._tz_name) if next_post else None
                    ),
                )
            )

        return ListContentPlansOutput(plans=summaries, count=len(summaries))

    async def health_check(self) -> HealthStatus:
        return HealthStatus(
            status="healthy",
            message="ok",
            checked_at=datetime.now(UTC),
        )
