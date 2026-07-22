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


# ---------------------------------------------------------------------------
# list_scheduled_posts
# ---------------------------------------------------------------------------


class ScheduledPostSummary(BaseModel):
    scheduled_post_id: str  # UUID str — LLM passes this back to cancel_scheduled_post
    caption: str
    scheduled_for_local: str  # human-readable via format_local
    scheduled_for_utc: str  # ISO for precision


class ListScheduledPostsInput(BaseModel):
    pass  # no LLM fields; always lists the caller's pending posts


class ListScheduledPostsOutput(BaseModel):
    scheduled_posts: list[ScheduledPostSummary]
    count: int


class ListScheduledPostsConfig(BaseModel):
    pass


# ---------------------------------------------------------------------------
# cancel_scheduled_post
# ---------------------------------------------------------------------------


class CancelScheduledPostInput(BaseModel):
    # UUID str obtained from a prior list_scheduled_posts call.
    scheduled_post_id: str


class CancelScheduledPostOutput(BaseModel):
    scheduled_post_id: str
    caption: str
    status: str  # "cancelled" | "not_found"
    detail: str  # human-readable result


class CancelScheduledPostConfig(BaseModel):
    pass
