from __future__ import annotations

import uuid as _uuid_mod
from datetime import UTC, datetime
from typing import Any, ClassVar

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.scheduled_post import ScheduledPost
from plugins.base import HealthStatus, PluginBase
from plugins.schedule_post.schemas import (
    CancelScheduledPostConfig,
    CancelScheduledPostInput,
    CancelScheduledPostOutput,
)


class CancelScheduledPostPlugin(PluginBase):
    name: ClassVar[str] = "cancel_scheduled_post"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Cancel a scheduled Instagram post that has not yet been sent. "
        "Use the scheduled_post_id from a list_scheduled_posts call. "
        "Only works on posts with status 'scheduled' — already-triggered posts "
        "have their own Cancel button on the approval message."
    )
    capabilities: ClassVar[list[str]] = ["schedule_post"]
    permissions: ClassVar[list[str]] = ["db:write"]
    dependencies: ClassVar[list[str]] = []
    input_schema: ClassVar[type[BaseModel]] = CancelScheduledPostInput
    output_schema: ClassVar[type[BaseModel]] = CancelScheduledPostOutput
    config_schema: ClassVar[type[BaseModel]] = CancelScheduledPostConfig

    async def execute(
        self,
        input: BaseModel,  # noqa: A002
        *,
        user_id: _uuid_mod.UUID,
        db: AsyncSession,
        **kwargs: Any,
    ) -> CancelScheduledPostOutput:
        assert isinstance(input, CancelScheduledPostInput)
        try:
            pid = _uuid_mod.UUID(input.scheduled_post_id)
        except ValueError:
            return CancelScheduledPostOutput(
                scheduled_post_id=input.scheduled_post_id,
                caption="",
                status="not_found",
                detail="Scheduled post not found.",
            )

        # Scope by id AND user_id AND status='scheduled'.
        # status guard prevents cancelling an already-triggered/posted row.
        stmt = select(ScheduledPost).where(
            ScheduledPost.id == pid,
            ScheduledPost.user_id == user_id,
            ScheduledPost.status == "scheduled",
        )
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()

        if row is None:
            return CancelScheduledPostOutput(
                scheduled_post_id=input.scheduled_post_id,
                caption="",
                status="not_found",
                detail="That post is not scheduled (already triggered, posted, or not found).",
            )

        caption = row.caption
        row.status = "cancelled"
        await db.flush()  # engine owns the commit

        return CancelScheduledPostOutput(
            scheduled_post_id=str(row.id),
            caption=caption,
            status="cancelled",
            detail=f"Scheduled post cancelled: '{caption[:60]}'.",
        )

    async def health_check(self) -> HealthStatus:
        return HealthStatus(
            status="healthy",
            message="ok",
            checked_at=datetime.now(UTC),
        )
