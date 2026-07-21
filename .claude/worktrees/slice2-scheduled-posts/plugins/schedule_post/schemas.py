from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class SchedulePostInput(BaseModel):
    # LLM-supplied fields only — no image_url (injected by engine via needs_hosted_image).
    # LLM must resolve relative times to absolute UTC using the system prompt clock.
    caption: str
    scheduled_for: datetime


class SchedulePostOutput(BaseModel):
    scheduled_post_id: uuid.UUID
    confirmation: str


class SchedulePostConfig(BaseModel):
    pass
