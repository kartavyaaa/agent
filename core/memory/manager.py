from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import Settings
from core.llm.base import LLMProvider
from core.memory.types import MemoryType
from models.memory import Memory


class MemoryManager:
    def __init__(self, llm: LLMProvider, settings: Settings) -> None:
        self._llm = llm
        self._settings = settings

    async def write(
        self,
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        content: str,
        memory_type: MemoryType,
        metadata: dict[str, Any] | None = None,
        importance_score: float | None = None,
        expires_at: datetime | None = None,
    ) -> Memory:
        vecs = await self._llm.embed([content], model=self._settings.openai_embedding_model)
        score = (
            importance_score if importance_score is not None else _heuristic(content, memory_type)
        )
        mem = Memory(
            id=uuid.uuid4(),
            user_id=user_id,
            content=content,
            embedding=vecs[0],
            memory_type=memory_type,
            importance_score=score,
            metadata_=metadata or {},
            expires_at=expires_at,
        )
        db.add(mem)
        return mem  # caller (engine) commits

    async def semantic_search(
        self,
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        query: str,
        top_k: int = 5,
        memory_types: list[MemoryType] | None = None,
    ) -> list[Memory]:
        vecs = await self._llm.embed([query], model=self._settings.openai_embedding_model)
        q = select(Memory).where(
            Memory.user_id == user_id,
            Memory.embedding.is_not(None),
        )
        if memory_types:
            q = q.where(Memory.memory_type.in_(memory_types))
        q = q.order_by(Memory.embedding.cosine_distance(vecs[0])).limit(top_k)
        result = await db.execute(q)
        rows = list(result.scalars().all())
        now = datetime.now(UTC)
        for row in rows:
            row.last_accessed_at = now
        return rows


def _heuristic(content: str, memory_type: MemoryType) -> float:
    if "reminder" in content.lower():
        return 0.8
    if memory_type == "episodic":
        return 0.6
    return 0.5
