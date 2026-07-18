from __future__ import annotations

import uuid as _uuid_mod
from datetime import UTC, datetime
from typing import Any, ClassVar

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.task import Task
from plugins.base import HealthStatus, PluginBase
from plugins.tasks.schemas import CompleteTaskConfig, CompleteTaskInput, CompleteTaskOutput


class CompleteTaskPlugin(PluginBase):
    name = "complete_task"
    version = "1.0.0"
    description = (
        "Mark a task as completed. Requires the task_id UUID string from a prior list_tasks call. "
        "If the task is not found or belongs to a different user, returns a not_found result."
    )
    capabilities: ClassVar[list[str]] = ["tasks"]
    permissions: ClassVar[list[str]] = ["db:write"]
    dependencies: ClassVar[list[str]] = []
    input_schema = CompleteTaskInput
    output_schema = CompleteTaskOutput
    config_schema = CompleteTaskConfig

    async def execute(
        self,
        input: BaseModel,
        *,
        user_id: _uuid_mod.UUID,
        db: AsyncSession,
        **kwargs: Any,
    ) -> CompleteTaskOutput:
        assert isinstance(input, CompleteTaskInput)
        try:
            tid = _uuid_mod.UUID(input.task_id)
        except ValueError:
            return CompleteTaskOutput(
                task_id=input.task_id,
                title="",
                status="not_found",
                message="Task not found.",
            )

        # user_id scoping is required: task_id alone must not authorize completion
        stmt = select(Task).where(Task.id == tid, Task.user_id == user_id)
        result = await db.execute(stmt)
        task = result.scalar_one_or_none()

        if task is None:
            return CompleteTaskOutput(
                task_id=input.task_id,
                title="",
                status="not_found",
                message="Task not found.",
            )

        task.status = "completed"
        await db.flush()  # engine owns the commit

        return CompleteTaskOutput(
            task_id=str(task.id),
            title=task.title,
            status="completed",
            message=f"Task '{task.title}' marked as completed.",
        )

    async def health_check(self) -> HealthStatus:
        return HealthStatus(
            status="healthy",
            message="ok",
            checked_at=datetime.now(UTC),
        )
