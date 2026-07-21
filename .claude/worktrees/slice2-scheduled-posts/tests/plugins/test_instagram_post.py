"""Unit tests for InstagramPostPlugin.

InstagramClient and DB are mocked — no real HTTP calls or Postgres needed.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.exceptions import IntegrationError, PluginError
from integrations.instagram import InstagramClient
from plugins.instagram_post.plugin import InstagramPostPlugin
from plugins.instagram_post.schemas import InstagramPostInput, InstagramPostOutput


def _make_ig_client(
    media_id: str = "media-123",
    *,
    side_effect: Exception | None = None,
) -> MagicMock:
    client = MagicMock(spec=InstagramClient)
    if side_effect is not None:
        client.publish_photo = AsyncMock(side_effect=side_effect)
    else:
        client.publish_photo = AsyncMock(return_value=media_id)
    client.health_check = AsyncMock(return_value=True)
    return client


def _make_db() -> MagicMock:
    return MagicMock()


# ---------------------------------------------------------------------------
# ClassVar / contract checks
# ---------------------------------------------------------------------------


def test_requires_approval_is_true() -> None:
    assert InstagramPostPlugin.requires_approval is True


def test_needs_hosted_image_is_true() -> None:
    assert InstagramPostPlugin.needs_hosted_image is True


def test_input_schema_has_only_caption() -> None:
    fields = set(InstagramPostInput.model_fields.keys())
    assert fields == {"caption"}


def test_input_schema_has_no_image_url() -> None:
    assert "image_url" not in InstagramPostInput.model_fields


def test_input_schema_has_no_user_id() -> None:
    assert "user_id" not in InstagramPostInput.model_fields


# ---------------------------------------------------------------------------
# execute() — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_calls_publish_photo_with_injected_image_url() -> None:
    client = _make_ig_client("media-789")
    plugin = InstagramPostPlugin(client=client)

    result = await plugin.execute(
        InstagramPostInput(caption="Sunset vibes"),
        user_id=uuid.uuid4(),
        db=_make_db(),
        image_url="https://cdn.example.com/user1/photo.jpg",
    )

    client.publish_photo.assert_called_once_with(
        image_url="https://cdn.example.com/user1/photo.jpg",
        caption="Sunset vibes",
    )
    assert isinstance(result, InstagramPostOutput)
    assert result.media_id == "media-789"
    assert "media-789" in result.confirmation


@pytest.mark.asyncio
async def test_execute_returns_instagram_post_output() -> None:
    client = _make_ig_client("mid-42")
    plugin = InstagramPostPlugin(client=client)

    result = await plugin.execute(
        InstagramPostInput(caption="Test caption"),
        user_id=uuid.uuid4(),
        db=_make_db(),
        image_url="https://cdn.example.com/img.jpg",
    )

    assert result.media_id == "mid-42"
    assert "mid-42" in result.confirmation


# ---------------------------------------------------------------------------
# execute() — error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_raises_plugin_error_when_image_url_missing() -> None:
    client = _make_ig_client()
    plugin = InstagramPostPlugin(client=client)

    with pytest.raises(PluginError, match="image_url"):
        await plugin.execute(
            InstagramPostInput(caption="No image"),
            user_id=uuid.uuid4(),
            db=_make_db(),
            # image_url intentionally omitted
        )

    client.publish_photo.assert_not_called()


@pytest.mark.asyncio
async def test_execute_propagates_integration_error() -> None:
    client = _make_ig_client(side_effect=IntegrationError("Instagram API error: bad request"))
    plugin = InstagramPostPlugin(client=client)

    with pytest.raises(IntegrationError):
        await plugin.execute(
            InstagramPostInput(caption="Test"),
            user_id=uuid.uuid4(),
            db=_make_db(),
            image_url="https://cdn.example.com/img.jpg",
        )


@pytest.mark.asyncio
async def test_execute_propagates_token_expiry_error() -> None:
    client = _make_ig_client(
        side_effect=IntegrationError("Instagram access token has expired or is invalid")
    )
    plugin = InstagramPostPlugin(client=client)

    with pytest.raises(IntegrationError, match="expired or is invalid"):
        await plugin.execute(
            InstagramPostInput(caption="Test"),
            user_id=uuid.uuid4(),
            db=_make_db(),
            image_url="https://cdn.example.com/img.jpg",
        )


# ---------------------------------------------------------------------------
# health_check()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_healthy() -> None:
    client = _make_ig_client()
    plugin = InstagramPostPlugin(client=client)
    status = await plugin.health_check()
    assert status.status == "healthy"


@pytest.mark.asyncio
async def test_health_check_unhealthy_when_client_unhealthy() -> None:
    client = _make_ig_client()
    client.health_check = AsyncMock(return_value=False)
    plugin = InstagramPostPlugin(client=client)
    status = await plugin.health_check()
    assert status.status == "unhealthy"
