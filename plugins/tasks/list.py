from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import ClassVar

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.task import Task
from plugins.base import HealthStatus, PluginBase
from plugins.tasks.schemas import ListTasksConfig, ListTasksInput, ListTasksOutput, TaskSummary

_OPEN_STATUSES = ("pending", "in_progress")


class ListTasksPlugin(PluginBase):
    name = "list_tasks"
    version = "1.0.0"
    description = (
        "List the user's tasks. Pass status_filter=null to see open tasks (pending and "
        "in_progress). Pass a specific status string to filter: 'pending', 'in_progress', "
        "'completed', 'cancelled', or 'failed'. Returns up to 50 tasks ordered by priority "
        "(high first), due date, then creation time. Each task includes its task_id UUID string "
        "needed to complete or reference the task."
    )
    capabilities: ClassVar[list[str]] = ["tasks"]
    permissions: ClassVar[list[str]] = ["db:read"]
    dependencies: ClassVar[list[str]] = []
    input_schema = ListTasksInput
    output_schema = ListTasksOutput
    config_schema = ListTasksConfig

    async def execute(
        self,
        input: BaseModel,
        *,
        user_id: uuid.UUID,
        db: AsyncSession,
    ) -> ListTasksOutput:
        assert isinstance(input, ListTasksInput)
        stmt = select(Task).where(Task.user_id == user_id)

        if input.status_filter is not None:
            stmt = stmt.where(Task.status == input.status_filter)
        else:
            stmt = stmt.where(Task.status.in_(_OPEN_STATUSES))

        stmt = stmt.order_by(
            Task.priority.desc(),
            Task.due_at.asc(),
            Task.created_at.asc(),
        ).limit(50)

        result = await db.execute(stmt)
        rows = result.scalars().all()

        summaries = [
            TaskSummary(
                task_id=str(row.id),
                title=row.title,
                status=row.status,
                priority=row.priority,
                due_at=row.due_at.isoformat() if row.due_at else None,
            )
            for row in rows
        ]
        return ListTasksOutput(tasks=summaries, count=len(summaries))

    async def health_check(self) -> HealthStatus:
        return HealthStatus(
            status="healthy",
            message="ok",
            checked_at=datetime.now(UTC),
        )
