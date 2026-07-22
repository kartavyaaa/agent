from __future__ import annotations

from pydantic import BaseModel


class InstagramCarouselInput(BaseModel):
    caption: str  # LLM-supplied; image_urls is engine-injected, not in this schema


class InstagramCarouselOutput(BaseModel):
    media_id: str
    confirmation: str


class InstagramCarouselConfig(BaseModel):
    pass
