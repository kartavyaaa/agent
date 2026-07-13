from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clients.api.dependencies import get_session_factory
from models.memory import Memory

router = APIRouter()


class MemoryRow(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    content: str
    importance_score: float
    memory_type: str
    metadata_: dict[str, Any]
    created_at: datetime
    last_accessed_at: datetime | None
    expires_at: datetime | None

    # embedding is intentionally excluded — pgvector Vector(1536) is not JSON-serializable
    model_config = ConfigDict(from_attributes=True)


@router.get("/memories", response_model=list[MemoryRow])
async def list_memories(
    user_id: uuid.UUID,
    factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
    memory_type: Literal["working", "episodic", "semantic", "knowledge"] | None = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[MemoryRow]:
    async with factory() as db:
        q = (
            select(Memory)
            .where(Memory.user_id == user_id)
            .order_by(Memory.created_at.desc())
            .limit(limit)
        )
        if memory_type is not None:
            q = q.where(Memory.memory_type == memory_type)
        result = await db.execute(q)
        rows = result.scalars().all()
    return [MemoryRow.model_validate(r) for r in rows]
