from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field


class CoreRequest(BaseModel):
    user_id: uuid.UUID
    content: str
    session_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    metadata: dict[str, Any] = {}


class CoreResponse(BaseModel):
    content: str
    memories_written: int = 0
    tool_calls_made: list[str] = []
    error: str | None = None
