from __future__ import annotations

import uuid as _uuid_mod
from datetime import UTC, datetime
from typing import Any, ClassVar

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.reminder import Reminder
from plugins.base import HealthStatus, PluginBase
from plugins.reminders.schemas import (
    CancelReminderConfig,
    CancelReminderInput,
    CancelReminderOutput,
)


class CancelReminderPlugin(PluginBase):
    name = "cancel_reminder"
    version = "1.0.0"
    description = (
        "Cancel a pending reminder. Requires the reminder_id UUID string from a prior "
        "list_reminders call. Already-fired or nonexistent reminders return a not_found result."
    )
    capabilities: ClassVar[list[str]] = ["reminders"]
    permissions: ClassVar[list[str]] = ["db:write"]
    dependencies: ClassVar[list[str]] = []
    input_schema = CancelReminderInput
    output_schema = CancelReminderOutput
    config_schema = CancelReminderConfig

    async def execute(
        self,
        input: BaseModel,
        *,
        user_id: _uuid_mod.UUID,
        db: AsyncSession,
        **kwargs: Any,
    ) -> CancelReminderOutput:
        assert isinstance(input, CancelReminderInput)
        try:
            rid = _uuid_mod.UUID(input.reminder_id)
        except ValueError:
            return CancelReminderOutput(
                reminder_id=input.reminder_id,
                message="",
                status="not_found",
                detail="Reminder not found.",
            )

        # Scope by id AND user_id AND sent_at IS NULL.
        # id alone never authorizes cancellation; sent_at IS NULL ensures we don't
        # cancel an already-fired reminder. These are different session transactions
        # from the worker, so the SELECT-then-DELETE is race-safe.
        stmt = select(Reminder).where(
            Reminder.id == rid,
            Reminder.user_id == user_id,
            Reminder.sent_at.is_(None),
        )
        result = await db.execute(stmt)
        reminder = result.scalar_one_or_none()

        if reminder is None:
            return CancelReminderOutput(
                reminder_id=input.reminder_id,
                message="",
                status="not_found",
                detail="That reminder already fired or wasn't found.",
            )

        msg = reminder.message
        await db.delete(reminder)
        await db.flush()  # engine owns the commit

        return CancelReminderOutput(
            reminder_id=input.reminder_id,
            message=msg,
            status="cancelled",
            detail=f"Reminder '{msg}' cancelled.",
        )

    async def health_check(self) -> HealthStatus:
        return HealthStatus(
            status="healthy",
            message="ok",
            checked_at=datetime.now(UTC),
        )
