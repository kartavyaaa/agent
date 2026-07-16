from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class CreateTaskInput(BaseModel):
    # LLM-supplied fields only — no user_id.
    # For optional fields, type as X | None so the LLM can pass null under strict schema.
    title: str
    description: str | None
    due_at: datetime | None  # LLM emits absolute UTC; naive treated as UTC (same as reminders)
    priority: int | None  # 1–5; None → DB default 1


class CreateTaskOutput(BaseModel):
    task_id: uuid.UUID
    title: str
    status: str
    confirmation: str


class CreateTaskConfig(BaseModel):
    pass


class TaskSummary(BaseModel):
    task_id: str  # UUID as str so the LLM can pass it back to complete_task
    title: str
    status: str
    priority: int
    due_at: str | None  # ISO string or None


class ListTasksInput(BaseModel):
    # None → open tasks (pending + in_progress); a status string filters to that status only.
    # Valid values: "pending", "in_progress", "completed", "cancelled", "failed", or null.
    status_filter: str | None


class ListTasksOutput(BaseModel):
    tasks: list[TaskSummary]
    count: int


class ListTasksConfig(BaseModel):
    pass


class CompleteTaskInput(BaseModel):
    # UUID string the LLM obtained from a prior list_tasks call.
    task_id: str


class CompleteTaskOutput(BaseModel):
    task_id: str
    title: str
    status: str  # "completed" on success, "not_found" if task not found
    message: str


class CompleteTaskConfig(BaseModel):
    pass
