"""Unit tests for InstagramCarouselPlugin."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.exceptions import PluginError
from integrations.instagram import InstagramClient
from plugins.instagram_carousel.plugin import InstagramCarouselPlugin
from plugins.instagram_carousel.schemas import InstagramCarouselInput, InstagramCarouselOutput


def _make_plugin(media_id: str = "carousel-123") -> tuple[InstagramCarouselPlugin, MagicMock]:
    client = MagicMock(spec=InstagramClient)
    client.publish_carousel = AsyncMock(return_value=media_id)
    client.health_check = AsyncMock(return_value=True)
    return InstagramCarouselPlugin(client=client), client


# ---------------------------------------------------------------------------
# Class-var contract
# ---------------------------------------------------------------------------


def test_requires_approval_is_true() -> None:
    assert InstagramCarouselPlugin.requires_approval is True


def test_needs_hosted_images_is_true() -> None:
    assert InstagramCarouselPlugin.needs_hosted_images is True


def test_needs_hosted_image_is_false() -> None:
    assert InstagramCarouselPlugin.needs_hosted_image is False


# ---------------------------------------------------------------------------
# execute() — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_calls_publish_carousel() -> None:
    plugin, client = _make_plugin()
    db = MagicMock()
    user_id = uuid.uuid4()

    result = await plugin.execute(
        InstagramCarouselInput(caption="Sunset series"),
        user_id=user_id,
        db=db,
        image_urls=["https://r2.example.com/a.jpg", "https://r2.example.com/b.jpg"],
    )

    client.publish_carousel.assert_awaited_once_with(
        image_urls=["https://r2.example.com/a.jpg", "https://r2.example.com/b.jpg"],
        caption="Sunset series",
    )
    assert isinstance(result, InstagramCarouselOutput)
    assert result.media_id == "carousel-123"
    assert "carousel-123" in result.confirmation


# ---------------------------------------------------------------------------
# execute() — validation errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_raises_when_image_urls_is_none() -> None:
    plugin, _ = _make_plugin()
    with pytest.raises(PluginError, match="at least 2"):
        await plugin.execute(
            InstagramCarouselInput(caption="test"),
            user_id=uuid.uuid4(),
            db=MagicMock(),
            image_urls=None,
        )


@pytest.mark.asyncio
async def test_execute_raises_when_image_urls_is_empty() -> None:
    plugin, _ = _make_plugin()
    with pytest.raises(PluginError, match="at least 2"):
        await plugin.execute(
            InstagramCarouselInput(caption="test"),
            user_id=uuid.uuid4(),
            db=MagicMock(),
            image_urls=[],
        )


@pytest.mark.asyncio
async def test_execute_raises_when_one_image_url() -> None:
    plugin, _ = _make_plugin()
    with pytest.raises(PluginError, match="at least 2"):
        await plugin.execute(
            InstagramCarouselInput(caption="test"),
            user_id=uuid.uuid4(),
            db=MagicMock(),
            image_urls=["https://r2.example.com/only.jpg"],
        )


@pytest.mark.asyncio
async def test_execute_raises_when_eleven_image_urls() -> None:
    plugin, _ = _make_plugin()
    urls = [f"https://r2.example.com/{i}.jpg" for i in range(11)]
    with pytest.raises(PluginError, match="at most 10"):
        await plugin.execute(
            InstagramCarouselInput(caption="test"),
            user_id=uuid.uuid4(),
            db=MagicMock(),
            image_urls=urls,
        )


@pytest.mark.asyncio
async def test_execute_accepts_exactly_two_urls() -> None:
    plugin, _ = _make_plugin()
    result = await plugin.execute(
        InstagramCarouselInput(caption="min"),
        user_id=uuid.uuid4(),
        db=MagicMock(),
        image_urls=["https://r2.example.com/a.jpg", "https://r2.example.com/b.jpg"],
    )
    assert result.media_id == "carousel-123"


@pytest.mark.asyncio
async def test_execute_accepts_exactly_ten_urls() -> None:
    plugin, _ = _make_plugin()
    urls = [f"https://r2.example.com/{i}.jpg" for i in range(10)]
    result = await plugin.execute(
        InstagramCarouselInput(caption="max"),
        user_id=uuid.uuid4(),
        db=MagicMock(),
        image_urls=urls,
    )
    assert result.media_id == "carousel-123"
