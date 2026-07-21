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


class ReminderSummary(BaseModel):
    reminder_id: str  # UUID as str — LLM passes this back to cancel_reminder
    message: str
    remind_at_local: str  # human-readable via format_local
    remind_at_utc: str  # ISO string for precision


class ListRemindersInput(BaseModel):
    pass  # no LLM-supplied fields; pending-only is the only behavior


class ListRemindersOutput(BaseModel):
    reminders: list[ReminderSummary]
    count: int


class ListRemindersConfig(BaseModel):
    pass


class CancelReminderInput(BaseModel):
    # UUID string the LLM obtained from a prior list_reminders call.
    reminder_id: str


class CancelReminderOutput(BaseModel):
    reminder_id: str
    message: str  # reminder text, for confirmation
    status: str  # "cancelled" | "not_found"
    detail: str  # human-readable result


class CancelReminderConfig(BaseModel):
    pass
