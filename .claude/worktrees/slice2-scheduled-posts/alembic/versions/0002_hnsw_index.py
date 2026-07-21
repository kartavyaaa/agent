"""HNSW vector index on memories.embedding.

Plain CREATE INDEX (not CONCURRENTLY) — CONCURRENTLY cannot run inside
Alembic's implicit transaction block.  The table is empty at migration time
so there is no downtime concern.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-07
"""

from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE INDEX ix_memories_embedding_hnsw
        ON memories
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_memories_embedding_hnsw")
