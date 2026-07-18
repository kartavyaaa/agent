from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.timeutil import format_local
from models.reminder import Reminder
from plugins.base import HealthStatus, PluginBase
from plugins.reminders.schemas import (
    ListRemindersConfig,
    ListRemindersInput,
    ListRemindersOutput,
    ReminderSummary,
)


class ListRemindersPlugin(PluginBase):
    name = "list_reminders"
    version = "1.0.0"
    description = (
        "List all of the user's pending (unfired) reminders. "
        "Returns up to 50 reminders ordered by scheduled time (soonest first). "
        "Each reminder includes its reminder_id UUID string needed to cancel it."
    )
    capabilities: ClassVar[list[str]] = ["reminders"]
    permissions: ClassVar[list[str]] = ["db:read"]
    dependencies: ClassVar[list[str]] = []
    input_schema = ListRemindersInput
    output_schema = ListRemindersOutput
    config_schema = ListRemindersConfig

    def __init__(self, tz_name: str = "UTC") -> None:
        self._tz_name = tz_name

    async def execute(
        self,
        input: BaseModel,
        *,
        user_id: uuid.UUID,
        db: AsyncSession,
        **kwargs: Any,
    ) -> ListRemindersOutput:
        assert isinstance(input, ListRemindersInput)
        stmt = (
            select(Reminder)
            .where(Reminder.user_id == user_id, Reminder.sent_at.is_(None))
            .order_by(Reminder.remind_at.asc())
            .limit(50)
        )
        result = await db.execute(stmt)
        rows = result.scalars().all()
        summaries = [
            ReminderSummary(
                reminder_id=str(row.id),
                message=row.message,
                remind_at_local=format_local(row.remind_at, self._tz_name),
                remind_at_utc=row.remind_at.isoformat(),
            )
            for row in rows
        ]
        return ListRemindersOutput(reminders=summaries, count=len(summaries))

    async def health_check(self) -> HealthStatus:
        return HealthStatus(
            status="healthy",
            message="ok",
            checked_at=datetime.now(UTC),
        )
