from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import PluginError
from core.timeutil import format_local, localize_to_utc
from models.scheduled_post import ScheduledPost
from plugins.base import HealthStatus, PluginBase
from plugins.schedule_post.schemas import SchedulePostConfig, SchedulePostInput, SchedulePostOutput


class SchedulePostPlugin(PluginBase):
    """Schedule a photo for future posting to Instagram.

    requires_approval=False: scheduling is immediate, no approval gate needed.
    needs_hosted_image=True: the engine uploads the request image to R2 and injects
    the public URL into execute() kwargs before the plugin runs — same channel as
    instagram_post. The LLM schema never sees image_url.

    At the scheduled time the worker poll creates a PendingAction and sends
    a proactive photo+approval message so the user can Confirm or Cancel.
    """

    name: ClassVar[str] = "schedule_post"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Schedule a photo for posting to Instagram at a future time. "
        "Use when the user says 'post this at <time>' or 'schedule this for <time>'. "
        "Only call on a turn where the user has actually sent a photo. "
        "Emit scheduled_for as the user's LOCAL wall-clock time with NO timezone suffix "
        "(e.g. '5:15am' in Asia/Kolkata → 2026-07-22T05:15:00, no Z, no +05:30). "
        "The system converts it to UTC. "
        "Do NOT call this if the user wants to post immediately — use instagram_post instead."
    )
    capabilities: ClassVar[list[str]] = ["schedule_post"]
    permissions: ClassVar[list[str]] = ["db:write", "network:write", "social:instagram"]
    dependencies: ClassVar[list[str]] = ["instagram"]
    input_schema: ClassVar[type[BaseModel]] = SchedulePostInput
    output_schema: ClassVar[type[BaseModel]] = SchedulePostOutput
    config_schema: ClassVar[type[BaseModel]] = SchedulePostConfig
    requires_approval: ClassVar[bool] = False
    needs_hosted_image: ClassVar[bool] = True

    def __init__(self, tz_name: str = "UTC") -> None:
        self._tz_name = tz_name

    async def execute(
        self,
        input: BaseModel,  # noqa: A002
        *,
        user_id: uuid.UUID,
        db: AsyncSession,
        image_url: str | None = None,
        **kwargs: Any,
    ) -> SchedulePostOutput:
        assert isinstance(input, SchedulePostInput)
        if image_url is None:
            raise PluginError(
                "schedule_post requires image_url but it was not injected — "
                "the engine must upload the image to R2 before scheduling."
            )
        scheduled_for = localize_to_utc(input.scheduled_for, self._tz_name)
        row = ScheduledPost(
            id=uuid.uuid4(),
            user_id=user_id,
            image_url=image_url,
            caption=input.caption,
            scheduled_for=scheduled_for,
            status="scheduled",
        )
        db.add(row)
        await db.flush()
        return SchedulePostOutput(
            scheduled_post_id=row.id,
            confirmation=f"Scheduled for {format_local(scheduled_for, self._tz_name)}",
        )

    async def health_check(self) -> HealthStatus:
        return HealthStatus(
            status="healthy",
            message="ok",
            checked_at=datetime.now(UTC),
        )
