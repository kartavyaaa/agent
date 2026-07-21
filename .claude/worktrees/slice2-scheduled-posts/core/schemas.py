from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field


class ImageAttachment(BaseModel):
    data: str  # base64-encoded bytes
    mime: str  # e.g. "image/jpeg"


class CoreRequest(BaseModel):
    user_id: uuid.UUID
    content: str
    session_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    metadata: dict[str, Any] = {}
    image_base64: str | None = None  # base64-encoded image bytes (single-image path)
    image_mime: str | None = None  # e.g. "image/jpeg" (single-image path)
    images: list[ImageAttachment] | None = None  # batch path (album); takes precedence


class ProposalPayload(BaseModel):
    pending_action_id: uuid.UUID
    preview_text: str


class CoreResponse(BaseModel):
    content: str
    memories_written: int = 0
    tool_calls_made: list[str] = []
    error: str | None = None
    proposal: ProposalPayload | None = None
