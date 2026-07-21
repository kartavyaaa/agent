from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.timeutil import format_local
from models.task import Task
from plugins.base import HealthStatus, PluginBase
from plugins.tasks.schemas import CreateTaskConfig, CreateTaskInput, CreateTaskOutput


class CreateTaskPlugin(PluginBase):
    name = "create_task"
    version = "1.0.0"
    description = (
        "Create a new task with a title, optional description, optional due date, and optional "
        "priority (1=lowest, 5=highest; default 1). "
        "Use the current local time from the system prompt to resolve relative expressions "
        "like 'tomorrow' or 'next week', then emit due_at as an absolute UTC datetime or null."
    )
    capabilities: ClassVar[list[str]] = ["tasks"]
    permissions: ClassVar[list[str]] = ["db:write"]
    dependencies: ClassVar[list[str]] = []
    input_schema = CreateTaskInput
    output_schema = CreateTaskOutput
    config_schema = CreateTaskConfig

    def __init__(self, tz_name: str = "UTC") -> None:
        self._tz_name = tz_name

    async def execute(
        self,
        input: BaseModel,
        *,
        user_id: uuid.UUID,
        db: AsyncSession,
        **kwargs: Any,
    ) -> CreateTaskOutput:
        assert isinstance(input, CreateTaskInput)
        due_at: datetime | None = None
        if input.due_at is not None:
            due_at = (
                input.due_at.replace(tzinfo=UTC) if input.due_at.tzinfo is None else input.due_at
            )

        task = Task(
            id=uuid.uuid4(),
            user_id=user_id,
            title=input.title,
            description=input.description,
            priority=input.priority if input.priority is not None else 1,
            due_at=due_at,
            # status: omitted → DB server_default "pending"
        )
        db.add(task)
        await db.flush()  # assigns task.id; engine owns the commit

        due_str = f" due {format_local(due_at, self._tz_name)}" if due_at else ""
        return CreateTaskOutput(
            task_id=task.id,
            title=task.title,
            status="pending",
            confirmation=f"Task '{task.title}' created{due_str}.",
        )

    async def health_check(self) -> HealthStatus:
        return HealthStatus(
            status="healthy",
            message="ok",
            checked_at=datetime.now(UTC),
        )
