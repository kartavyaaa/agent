"""Add pending_actions table for the engine approval flow.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-21
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TYPE pending_action_status AS ENUM "
        "('pending','executing','confirmed','cancelled','expired','failed')"
    )

    op.create_table(
        "pending_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action_type", sa.String(100), nullable=False),
        sa.Column("action_payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "pending",
                "executing",
                "confirmed",
                "cancelled",
                "expired",
                "failed",
                name="pending_action_status",
                create_type=False,
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("preview_text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_index("ix_pending_actions_user_id", "pending_actions", ["user_id"])

    # Partial unique index: at most one pending action per user at a time.
    op.execute(
        "CREATE UNIQUE INDEX uq_pending_actions_user_pending "
        "ON pending_actions (user_id) "
        "WHERE status = 'pending'::pending_action_status"
    )


def downgrade() -> None:
    op.drop_table("pending_actions")
    op.execute("DROP TYPE IF EXISTS pending_action_status")
