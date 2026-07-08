from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class ReminderInput(BaseModel):
    # LLM-supplied fields ONLY — no user_id.
    # LLM must resolve relative times ("tomorrow") to an absolute UTC datetime
    # using the current UTC time injected into the system prompt.
    message: str
    remind_at: datetime


class ReminderOutput(BaseModel):
    reminder_id: uuid.UUID
    message: str  # reminder text (copied from input)
    remind_at: datetime
    confirmation: str  # e.g. "Reminder set for 2026-07-08 09:00 UTC"


class ReminderConfig(BaseModel):
    pass
