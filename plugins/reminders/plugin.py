from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import ClassVar

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.timeutil import format_local
from models.reminder import Reminder
from plugins.base import HealthStatus, PluginBase
from plugins.reminders.schemas import ReminderConfig, ReminderInput, ReminderOutput


class RemindersPlugin(PluginBase):
    name = "create_reminder"
    version = "1.0.0"
    description = (
        "Create a reminder that will be delivered at a specified future time. "
        "Use the current local time from the system prompt to resolve relative expressions "
        "like 'tomorrow' or 'in 2 hours', then emit remind_at as an absolute UTC datetime."
    )
    capabilities: ClassVar[list[str]] = ["reminders"]
    permissions: ClassVar[list[str]] = ["db:write"]
    dependencies: ClassVar[list[str]] = []
    input_schema = ReminderInput
    output_schema = ReminderOutput
    config_schema = ReminderConfig

    def __init__(self, tz_name: str = "UTC") -> None:
        self._tz_name = tz_name

    async def execute(
        self,
        input: BaseModel,
        *,
        user_id: uuid.UUID,
        db: AsyncSession,
    ) -> ReminderOutput:
        assert isinstance(input, ReminderInput)
        remind_at = (
            input.remind_at.replace(tzinfo=UTC)
            if input.remind_at.tzinfo is None
            else input.remind_at
        )
        reminder = Reminder(
            id=uuid.uuid4(),
            user_id=user_id,
            message=input.message,
            remind_at=remind_at,
        )
        db.add(reminder)
        await db.flush()  # assigns reminder.id; engine owns the commit
        return ReminderOutput(
            reminder_id=reminder.id,
            message=input.message,
            remind_at=remind_at,
            confirmation=f"Reminder set for {format_local(remind_at, self._tz_name)}",
        )

    async def health_check(self) -> HealthStatus:
        return HealthStatus(
            status="healthy",
            message="ok",
            checked_at=datetime.now(UTC),
        )
