from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from core.memory.manager import MemoryManager, ScoredMemory


async def search_semantic(
    manager: MemoryManager,
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    query: str,
    top_k: int = 5,
) -> list[ScoredMemory]:
    return await manager.semantic_search(
        db, user_id=user_id, query=query, top_k=top_k, memory_types=["semantic"]
    )
