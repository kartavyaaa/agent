from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, SmallInteger, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base

TaskStatusEnum = Enum(
    "pending", "in_progress", "completed", "cancelled", "failed", name="task_status"
)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(TaskStatusEnum, server_default="pending")
    priority: Mapped[int] = mapped_column(SmallInteger, default=1, server_default="1")
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    plugin_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    plugin_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # type: ignore[type-arg]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_tasks_user_status_priority", "user_id", "status", "priority"),
        Index(
            "ix_tasks_due",
            "due_at",
            postgresql_where="due_at IS NOT NULL AND status = 'pending'::task_status",
        ),
    )
