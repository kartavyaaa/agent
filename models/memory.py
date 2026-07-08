from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Enum, Float, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base

MemoryTypeEnum = Enum("working", "episodic", "semantic", "knowledge", name="memory_type")


class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # 1536 dims matches text-embedding-3-small. Nullable until embedding job runs.
    # Index: HNSW (not ivfflat) — builds correctly on empty/growing tables.
    # Created in Alembic migration: USING hnsw (embedding vector_cosine_ops)
    # WITH (m=16, ef_construction=64)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)
    importance_score: Mapped[float] = mapped_column(Float, default=0.5)
    memory_type: Mapped[str] = mapped_column(MemoryTypeEnum, nullable=False)
    metadata_: Mapped[dict] = mapped_column(  # type: ignore[type-arg]
        "metadata", JSONB, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_accessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_memories_user_type_created", "user_id", "memory_type", "created_at"),
        Index(
            "ix_memories_expires",
            "expires_at",
            postgresql_where="expires_at IS NOT NULL",
        ),
        # HNSW index is created separately in Alembic (requires pgvector extension).
    )
