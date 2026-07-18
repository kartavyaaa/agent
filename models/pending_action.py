from __future__ import annotations

import uuid

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base

PendingActionStatusEnum = PG_ENUM(
    "pending",
    "executing",
    "confirmed",
    "cancelled",
    "expired",
    "failed",
    name="pending_action_status",
    create_type=False,
)


class PendingAction(Base):
    __tablename__ = "pending_actions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    action_type: Mapped[str] = mapped_column(String(100), nullable=False)
    action_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        PendingActionStatusEnum, server_default="pending", nullable=False
    )
    preview_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    expires_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_pending_actions_user_id", "user_id"),
        # Partial unique index: only one pending action per user at a time.
        # The engine cancels the old row before inserting a new one to avoid
        # violating this constraint (superseding behaviour).
        Index(
            "uq_pending_actions_user_pending",
            "user_id",
            postgresql_where="status = 'pending'::pending_action_status",
            unique=True,
        ),
    )
