from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import PluginError
from integrations.instagram import InstagramClient
from plugins.base import HealthStatus, PluginBase
from plugins.instagram_carousel.schemas import (
    InstagramCarouselConfig,
    InstagramCarouselInput,
    InstagramCarouselOutput,
)

_MIN_IMAGES = 2
_MAX_IMAGES = 10


class InstagramCarouselPlugin(PluginBase):
    """Post a carousel of photos to Instagram with user confirmation.

    requires_approval=True: the registry intercepts and the engine proposes before executing.
    needs_hosted_images=True: the engine uploads each request image to R2 and injects the
    public URLs as image_urls into action_payload before storing the pending action.
    Caption goes on the parent container (not on children or the publish call).
    """

    name: ClassVar[str] = "instagram_carousel"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Post multiple photos as a carousel to Instagram with a caption. "
        "Use this when the user explicitly asks to post or share multiple photos as a carousel "
        "or multi-image post on Instagram. "
        "Call this tool DIRECTLY with the caption — do NOT ask the user for confirmation yourself first. "
        "The system automatically shows the user a confirmation prompt with buttons before anything is posted. "
        "Only call this on a turn where the user has actually sent multiple photos in the current message."
    )
    capabilities: ClassVar[list[str]] = ["instagram_carousel"]
    permissions: ClassVar[list[str]] = ["network:write", "social:instagram"]
    dependencies: ClassVar[list[str]] = ["instagram"]
    input_schema: ClassVar[type[BaseModel]] = InstagramCarouselInput
    output_schema: ClassVar[type[BaseModel]] = InstagramCarouselOutput
    config_schema: ClassVar[type[BaseModel]] = InstagramCarouselConfig
    requires_approval: ClassVar[bool] = True
    needs_hosted_image: ClassVar[bool] = False
    needs_hosted_images: ClassVar[bool] = True

    def __init__(self, client: InstagramClient) -> None:
        self._client = client

    async def execute(
        self,
        input: BaseModel,  # noqa: A002
        *,
        user_id: uuid.UUID,  # noqa: ARG002
        db: AsyncSession,  # noqa: ARG002
        image_urls: list[str] | None = None,
        **kwargs: Any,
    ) -> InstagramCarouselOutput:
        assert isinstance(input, InstagramCarouselInput)
        if not image_urls or len(image_urls) < _MIN_IMAGES:
            raise PluginError(
                f"instagram_carousel requires at least {_MIN_IMAGES} image URLs but got "
                f"{len(image_urls) if image_urls else 0}. "
                "The engine must upload the images to R2 before confirming."
            )
        if len(image_urls) > _MAX_IMAGES:
            raise PluginError(
                f"instagram_carousel accepts at most {_MAX_IMAGES} images but got {len(image_urls)}."
            )
        media_id = await self._client.publish_carousel(image_urls=image_urls, caption=input.caption)
        return InstagramCarouselOutput(
            media_id=media_id,
            confirmation=f"✅ Posted carousel to Instagram (media id: {media_id})",
        )

    async def health_check(self) -> HealthStatus:
        ok = await self._client.health_check()
        return HealthStatus(
            status="healthy" if ok else "unhealthy",
            message="ok" if ok else "Instagram credentials not configured",
            checked_at=datetime.now(UTC),
        )
