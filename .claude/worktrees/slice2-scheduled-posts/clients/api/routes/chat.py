from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from clients.api.dependencies import get_engine
from core.engine import CoreEngine
from core.schemas import CoreRequest, CoreResponse

router = APIRouter()


class ChatRequest(BaseModel):
    user_id: uuid.UUID
    content: str


@router.post("/chat", response_model=CoreResponse)
async def create_chat(
    body: ChatRequest,
    engine: Annotated[CoreEngine, Depends(get_engine)],
) -> CoreResponse:
    request = CoreRequest(user_id=body.user_id, content=body.content)
    return await engine.handle_request(request)
