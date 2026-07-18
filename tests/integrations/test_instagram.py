"""Unit tests for InstagramClient.

No real network calls — httpx.AsyncClient is injected as a mock.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from core.exceptions import IntegrationError
from integrations.instagram import InstagramClient


def _json_resp(data: dict[str, object], status_code: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json = MagicMock(return_value=data)
    resp.raise_for_status = MagicMock()
    if status_code >= 500:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "server error", request=MagicMock(), response=resp
        )
    return resp


def _make_client(
    media_resp: MagicMock | None = None,
    publish_resp: MagicMock | None = None,
    *,
    side_effect: Exception | None = None,
) -> tuple[InstagramClient, MagicMock]:
    http = MagicMock(spec=httpx.AsyncClient)
    if side_effect is not None:
        http.post = AsyncMock(side_effect=side_effect)
    else:
        _media = media_resp or _json_resp({"id": "creation-123"})
        _pub = publish_resp or _json_resp({"id": "media-456"})
        http.post = AsyncMock(side_effect=[_media, _pub])
    ig = InstagramClient(
        access_token="tok",
        ig_user_id="17841407153636057",
        http_client=http,
    )
    return ig, http


# ---------------------------------------------------------------------------
# publish_photo() — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_photo_returns_media_id() -> None:
    ig, _ = _make_client()
    media_id = await ig.publish_photo(image_url="https://cdn.example.com/img.jpg", caption="hi")
    assert media_id == "media-456"


@pytest.mark.asyncio
async def test_publish_photo_makes_two_posts_in_order() -> None:
    ig, http = _make_client()
    await ig.publish_photo(image_url="https://cdn.example.com/img.jpg", caption="test caption")

    assert http.post.call_count == 2
    first_call, second_call = http.post.call_args_list
    # First call: /media endpoint
    assert "/media" in first_call.args[0]
    assert "media_publish" not in first_call.args[0]
    assert first_call.kwargs["params"]["image_url"] == "https://cdn.example.com/img.jpg"
    assert first_call.kwargs["params"]["caption"] == "test caption"
    # Second call: /media_publish endpoint
    assert "media_publish" in second_call.args[0]
    assert second_call.kwargs["params"]["creation_id"] == "creation-123"


@pytest.mark.asyncio
async def test_publish_photo_passes_access_token() -> None:
    ig, http = _make_client()
    await ig.publish_photo(image_url="https://cdn.example.com/img.jpg", caption="x")

    first_call = http.post.call_args_list[0]
    assert first_call.kwargs["params"]["access_token"] == "tok"


# ---------------------------------------------------------------------------
# publish_photo() — error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_photo_4xx_raises_integration_error() -> None:
    media_resp = _json_resp(
        {"error": {"code": 100, "message": "Invalid parameter"}}, status_code=400
    )
    ig, _ = _make_client(media_resp=media_resp)

    with pytest.raises(IntegrationError, match="Invalid parameter"):
        await ig.publish_photo(image_url="https://cdn.example.com/img.jpg", caption="x")


@pytest.mark.asyncio
async def test_publish_photo_expired_token_raises_clear_error() -> None:
    media_resp = _json_resp(
        {"error": {"code": 190, "message": "Error validating access token"}},
        status_code=400,
    )
    ig, _ = _make_client(media_resp=media_resp)

    with pytest.raises(IntegrationError, match="expired or is invalid"):
        await ig.publish_photo(image_url="https://cdn.example.com/img.jpg", caption="x")


@pytest.mark.asyncio
async def test_publish_photo_token_in_message_raises_clear_error() -> None:
    media_resp = _json_resp(
        {"error": {"code": 200, "message": "The token has expired"}},
        status_code=401,
    )
    ig, _ = _make_client(media_resp=media_resp)

    with pytest.raises(IntegrationError, match="expired or is invalid"):
        await ig.publish_photo(image_url="https://cdn.example.com/img.jpg", caption="x")


@pytest.mark.asyncio
async def test_publish_photo_5xx_retries_three_times() -> None:
    media_resp = _json_resp({}, status_code=503)
    http = MagicMock(spec=httpx.AsyncClient)
    http.post = AsyncMock(return_value=media_resp)
    ig = InstagramClient(access_token="tok", ig_user_id="123", http_client=http)

    with pytest.raises(httpx.HTTPStatusError):
        await ig.publish_photo(image_url="https://cdn.example.com/img.jpg", caption="x")

    assert http.post.call_count == 3


@pytest.mark.asyncio
async def test_publish_photo_timeout_retries() -> None:
    ig, http = _make_client(side_effect=httpx.TimeoutException("timeout"))

    with pytest.raises(httpx.TimeoutException):
        await ig.publish_photo(image_url="https://cdn.example.com/img.jpg", caption="x")

    assert http.post.call_count == 3


# ---------------------------------------------------------------------------
# health_check()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_returns_true_when_configured() -> None:
    ig, _ = _make_client()
    assert await ig.health_check() is True


@pytest.mark.asyncio
async def test_health_check_returns_false_without_token() -> None:
    ig = InstagramClient(access_token="", ig_user_id="123", http_client=MagicMock())
    assert await ig.health_check() is False
