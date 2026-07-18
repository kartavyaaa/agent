from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import PluginError
from integrations.instagram import InstagramClient
from plugins.base import HealthStatus, PluginBase
from plugins.instagram_post.schemas import (
    InstagramPostConfig,
    InstagramPostInput,
    InstagramPostOutput,
)


class InstagramPostPlugin(PluginBase):
    """Post a photo to Instagram with user confirmation.

    requires_approval=True: the registry intercepts the call and the engine proposes
    it to the user before executing. needs_hosted_image=True: the engine uploads the
    request image to R2 and injects the public URL into action_payload before storing
    the pending action — so image_url is available at confirm time.
    """

    name: ClassVar[str] = "instagram_post"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Post a photo to Instagram with a caption. "
        "Use this when the user wants to share their photo on Instagram. "
        "The user must confirm before the post goes live."
    )
    capabilities: ClassVar[list[str]] = ["instagram_post"]
    permissions: ClassVar[list[str]] = ["network:write", "social:instagram"]
    dependencies: ClassVar[list[str]] = ["instagram"]
    input_schema: ClassVar[type[BaseModel]] = InstagramPostInput
    output_schema: ClassVar[type[BaseModel]] = InstagramPostOutput
    config_schema: ClassVar[type[BaseModel]] = InstagramPostConfig
    requires_approval: ClassVar[bool] = True
    needs_hosted_image: ClassVar[bool] = True

    def __init__(self, client: InstagramClient) -> None:
        self._client = client

    async def execute(
        self,
        input: BaseModel,  # noqa: A002
        *,
        user_id: uuid.UUID,  # noqa: ARG002
        db: AsyncSession,  # noqa: ARG002
        image_url: str | None = None,
        **kwargs: Any,
    ) -> InstagramPostOutput:
        assert isinstance(input, InstagramPostInput)
        if image_url is None:
            raise PluginError(
                "instagram_post requires image_url but it was not injected — "
                "the engine must upload the image to R2 before confirming."
            )
        media_id = await self._client.publish_photo(image_url=image_url, caption=input.caption)
        return InstagramPostOutput(
            media_id=media_id,
            confirmation=f"Posted to Instagram (media id: {media_id})",
        )

    async def health_check(self) -> HealthStatus:
        ok = await self._client.health_check()
        return HealthStatus(
            status="healthy" if ok else "unhealthy",
            message="ok" if ok else "Instagram credentials not configured",
            checked_at=datetime.now(UTC),
        )
