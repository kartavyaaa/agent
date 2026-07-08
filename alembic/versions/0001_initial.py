"""Initial schema: all tables, ENUM types, btree and partial indexes.

Revision ID: 0001
Revises:
Create Date: 2026-07-07
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pgvector extension — required for the Vector column type
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ENUM types
    op.execute("CREATE TYPE memory_type AS ENUM ('working','episodic','semantic','knowledge')")
    op.execute(
        "CREATE TYPE task_status AS ENUM "
        "('pending','in_progress','completed','cancelled','failed')"
    )
    op.execute("CREATE TYPE project_status AS ENUM ('active','archived','deleted')")
    op.execute(
        "CREATE TYPE plugin_health_status AS ENUM " "('healthy','degraded','unhealthy','unknown')"
    )

    # users
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), unique=True, nullable=True),
        sa.Column("api_key_hash", sa.String(64), nullable=True),
        sa.Column("preferences", postgresql.JSONB(), server_default="{}"),
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

    # projects (no FK to users yet resolved — created before tasks/reminders)
    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("active", "archived", "deleted", name="project_status", create_type=False),
            server_default="active",
        ),
        sa.Column("metadata", postgresql.JSONB(), server_default="{}"),
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
    op.create_index("ix_projects_user_id", "projects", ["user_id"])

    # tasks
    op.create_table(
        "tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "in_progress",
                "completed",
                "cancelled",
                "failed",
                name="task_status",
                create_type=False,
            ),
            server_default="pending",
        ),
        sa.Column("priority", sa.SmallInteger(), server_default="1"),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("plugin_name", sa.String(100), nullable=True),
        sa.Column("plugin_payload", postgresql.JSONB(), nullable=True),
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
    op.create_index("ix_tasks_user_status_priority", "tasks", ["user_id", "status", "priority"])
    op.execute(
        "CREATE INDEX ix_tasks_due ON tasks (due_at) "
        "WHERE due_at IS NOT NULL AND status = 'pending'"
    )

    # reminders
    op.create_table(
        "reminders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("remind_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recurrence", postgresql.JSONB(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_reminders_user_id", "reminders", ["user_id"])
    # Hot-path partial index: only unsent reminders are scanned by the poller
    op.execute(
        "CREATE INDEX ix_reminders_due_unsent ON reminders (remind_at, sent_at) "
        "WHERE sent_at IS NULL"
    )

    # memories — embedding column uses pgvector Vector type via raw DDL
    op.execute("""
        CREATE TABLE memories (
            id          UUID PRIMARY KEY,
            user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            content     TEXT NOT NULL,
            embedding   vector(1536),
            importance_score FLOAT NOT NULL DEFAULT 0.5,
            memory_type memory_type NOT NULL,
            metadata    JSONB NOT NULL DEFAULT '{}',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_accessed_at TIMESTAMPTZ,
            expires_at  TIMESTAMPTZ
        )
        """)
    op.create_index(
        "ix_memories_user_type_created",
        "memories",
        ["user_id", "memory_type", "created_at"],
    )
    op.execute(
        "CREATE INDEX ix_memories_expires ON memories (expires_at) " "WHERE expires_at IS NOT NULL"
    )

    # plugin_registry
    op.create_table(
        "plugin_registry",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("plugin_name", sa.String(100), unique=True, nullable=False),
        sa.Column("version", sa.String(20), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="true"),
        sa.Column("config", postgresql.JSONB(), server_default="{}"),
        sa.Column(
            "health_status",
            sa.Enum(
                "healthy",
                "degraded",
                "unhealthy",
                "unknown",
                name="plugin_health_status",
                create_type=False,
            ),
            server_default="unknown",
        ),
        sa.Column("last_health_check_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("plugin_registry")
    op.execute("DROP TABLE IF EXISTS memories")
    op.drop_table("reminders")
    op.drop_table("tasks")
    op.drop_table("projects")
    op.drop_table("users")
    op.execute("DROP TYPE IF EXISTS plugin_health_status")
    op.execute("DROP TYPE IF EXISTS project_status")
    op.execute("DROP TYPE IF EXISTS task_status")
    op.execute("DROP TYPE IF EXISTS memory_type")
