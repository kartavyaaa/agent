from __future__ import annotations

import uuid

from sqlalchemy import DateTime, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base

ScheduledPostStatusEnum = PG_ENUM(
    "scheduled",
    "triggered",
    "posted",
    "cancelled",
    "failed",
    name="scheduled_post_status",
    create_type=False,
)


class ScheduledPost(Base):
    __tablename__ = "scheduled_posts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    image_url: Mapped[str] = mapped_column(Text, nullable=False)
    caption: Mapped[str] = mapped_column(Text, nullable=False)
    scheduled_for: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        ScheduledPostStatusEnum, server_default="scheduled", nullable=False
    )
    pending_action_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pending_actions.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_scheduled_posts_user_id", "user_id"),
        # Partial index for the worker poll hot path.
        Index(
            "ix_scheduled_posts_due",
            "scheduled_for",
            postgresql_where="status = 'scheduled'::scheduled_post_status",
        ),
    )
