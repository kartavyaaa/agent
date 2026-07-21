"""Add scheduled_posts table for worker-triggered Instagram posting.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-21
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TYPE scheduled_post_status AS ENUM "
        "('scheduled','triggered','posted','cancelled','failed')"
    )

    op.create_table(
        "scheduled_posts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("image_url", sa.Text(), nullable=False),
        sa.Column("caption", sa.Text(), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "scheduled",
                "triggered",
                "posted",
                "cancelled",
                "failed",
                name="scheduled_post_status",
                create_type=False,
            ),
            server_default="scheduled",
            nullable=False,
        ),
        sa.Column(
            "pending_action_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pending_actions.id", ondelete="SET NULL"),
            nullable=True,
        ),
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
    )

    op.create_index("ix_scheduled_posts_user_id", "scheduled_posts", ["user_id"])

    # Partial index for the worker poll hot path: only 'scheduled' rows.
    op.execute(
        "CREATE INDEX ix_scheduled_posts_due "
        "ON scheduled_posts (scheduled_for) "
        "WHERE status = 'scheduled'::scheduled_post_status"
    )


def downgrade() -> None:
    op.drop_table("scheduled_posts")
    op.execute("DROP TYPE IF EXISTS scheduled_post_status")
