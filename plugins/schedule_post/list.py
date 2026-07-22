from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.timeutil import format_local
from models.scheduled_post import ScheduledPost
from plugins.base import HealthStatus, PluginBase
from plugins.schedule_post.schemas import (
    ListScheduledPostsConfig,
    ListScheduledPostsInput,
    ListScheduledPostsOutput,
    ScheduledPostSummary,
)


class ListScheduledPostsPlugin(PluginBase):
    name: ClassVar[str] = "list_scheduled_posts"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "List the user's scheduled Instagram posts that have not yet been sent. "
        "Call this when the user asks what posts are scheduled, queued, or upcoming. "
        "Returns up to 50 posts ordered by scheduled time (soonest first). "
        "Each post includes its scheduled_post_id UUID string — pass it to "
        "cancel_scheduled_post to cancel one."
    )
    capabilities: ClassVar[list[str]] = ["schedule_post"]
    permissions: ClassVar[list[str]] = ["db:read"]
    dependencies: ClassVar[list[str]] = []
    input_schema: ClassVar[type[BaseModel]] = ListScheduledPostsInput
    output_schema: ClassVar[type[BaseModel]] = ListScheduledPostsOutput
    config_schema: ClassVar[type[BaseModel]] = ListScheduledPostsConfig

    def __init__(self, tz_name: str = "UTC") -> None:
        self._tz_name = tz_name

    async def execute(
        self,
        input: BaseModel,  # noqa: A002
        *,
        user_id: uuid.UUID,
        db: AsyncSession,
        **kwargs: Any,
    ) -> ListScheduledPostsOutput:
        assert isinstance(input, ListScheduledPostsInput)
        stmt = (
            select(ScheduledPost)
            .where(
                ScheduledPost.user_id == user_id,
                ScheduledPost.status == "scheduled",
            )
            .order_by(ScheduledPost.scheduled_for.asc())
            .limit(50)
        )
        result = await db.execute(stmt)
        rows = result.scalars().all()
        summaries = [
            ScheduledPostSummary(
                scheduled_post_id=str(row.id),
                caption=row.caption,
                scheduled_for_local=format_local(row.scheduled_for, self._tz_name),
                scheduled_for_utc=row.scheduled_for.isoformat(),
            )
            for row in rows
        ]
        return ListScheduledPostsOutput(scheduled_posts=summaries, count=len(summaries))

    async def health_check(self) -> HealthStatus:
        return HealthStatus(
            status="healthy",
            message="ok",
            checked_at=datetime.now(UTC),
        )
