from __future__ import annotations

from pydantic import BaseModel


class InstagramPostInput(BaseModel):
    caption: str  # LLM-supplied; image_url is engine-injected, not in this schema


class InstagramPostOutput(BaseModel):
    media_id: str
    confirmation: str


class InstagramPostConfig(BaseModel):
    pass
