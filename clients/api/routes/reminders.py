from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clients.api.dependencies import get_engine
from core.engine import CoreEngine
from core.schemas import CoreRequest, CoreResponse

router = APIRouter()


class CreateReminderRequest(BaseModel):
    content: str  # natural language, e.g. "remind me tomorrow to call Bob"
    user_id: uuid.UUID


class ReminderRow(BaseModel):
    id: uuid.UUID
    message: str
    remind_at: datetime
    sent_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


def _session_factory(
    engine: Annotated[CoreEngine, Depends(get_engine)],
) -> async_sessionmaker[AsyncSession]:
    return engine._session_factory


@router.post("/reminders", response_model=CoreResponse)
async def create_reminder(
    body: CreateReminderRequest,
    engine: Annotated[CoreEngine, Depends(get_engine)],
) -> CoreResponse:
    request = CoreRequest(user_id=body.user_id, content=body.content)
    return await engine.handle_request(request)


@router.get("/reminders/{user_id}", response_model=list[ReminderRow])
async def list_reminders(
    user_id: uuid.UUID,
    factory: Annotated[async_sessionmaker[AsyncSession], Depends(_session_factory)],
) -> list[ReminderRow]:
    from models.reminder import Reminder

    async with factory() as db:
        result = await db.execute(
            select(Reminder).where(Reminder.user_id == user_id).order_by(Reminder.remind_at)
        )
        rows = result.scalars().all()
    return [ReminderRow.model_validate(r) for r in rows]
