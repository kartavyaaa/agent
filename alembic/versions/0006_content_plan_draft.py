"""Add items and image_urls JSONB columns to content_plans (draft support).

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-23

Safe on a populated table:
- items JSONB NULL: nullable, NULL for all existing rows.
- image_urls JSONB NULL: nullable, NULL for all existing rows.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "content_plans",
        sa.Column("items", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "content_plans",
        sa.Column("image_urls", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("content_plans", "image_urls")
    op.drop_column("content_plans", "items")
