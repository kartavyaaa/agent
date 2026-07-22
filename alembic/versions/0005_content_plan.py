"""Add content_plans table and extend scheduled_posts for carousel + plan support.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-22

Safe on a populated table:
- content_plans is a new table (no existing rows affected).
- post_type NOT NULL DEFAULT 'single': server default backfills all existing rows at ALTER time.
- image_urls JSONB NULL: nullable, NULL for all existing rows.
- plan_id UUID NULL: nullable, NULL for all existing rows.
- image_url DROP NOT NULL: existing rows keep their values; only new carousel rows will have NULL.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create content_plans BEFORE adding the FK column to scheduled_posts.
    op.create_table(
        "content_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), server_default="approved", nullable=False),
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
    op.create_index("ix_content_plans_user_id", "content_plans", ["user_id"])

    # 2. Extend scheduled_posts.
    op.add_column(
        "scheduled_posts",
        sa.Column("post_type", sa.String(20), server_default="single", nullable=False),
    )
    op.add_column(
        "scheduled_posts",
        sa.Column("image_urls", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "scheduled_posts",
        sa.Column(
            "plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_plans.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.alter_column("scheduled_posts", "image_url", nullable=True)


def downgrade() -> None:
    op.alter_column("scheduled_posts", "image_url", nullable=False)
    op.drop_column("scheduled_posts", "plan_id")
    op.drop_column("scheduled_posts", "image_urls")
    op.drop_column("scheduled_posts", "post_type")
    op.drop_table("content_plans")
