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
    *,
    media_resp: MagicMock | None = None,
    status_resps: list[MagicMock] | None = None,
    publish_resp: MagicMock | None = None,
    post_side_effect: Exception | None = None,
) -> tuple[InstagramClient, MagicMock]:
    """Build a mock InstagramClient.

    Flow: POST /media → GET status_code (1+ times) → POST /media_publish.
    - media_resp: response for POST /media (default: {"id": "creation-123"}, 200)
    - status_resps: list of GET /status_code responses (default: [FINISHED])
    - publish_resp: response for POST /media_publish (default: {"id": "media-456"}, 200)
    - post_side_effect: if set, all POST calls raise this exception
    """
    http = MagicMock(spec=httpx.AsyncClient)

    if post_side_effect is not None:
        http.post = AsyncMock(side_effect=post_side_effect)
    else:
        _media = media_resp or _json_resp({"id": "creation-123"})
        _pub = publish_resp or _json_resp({"id": "media-456"})
        http.post = AsyncMock(side_effect=[_media, _pub])

    _statuses = status_resps or [_json_resp({"id": "creation-123", "status_code": "FINISHED"})]
    http.get = AsyncMock(side_effect=_statuses)

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
async def test_publish_photo_three_step_order() -> None:
    """POST /media → GET status → POST /media_publish."""
    ig, http = _make_client()
    await ig.publish_photo(image_url="https://cdn.example.com/img.jpg", caption="test caption")

    assert http.post.call_count == 2
    assert http.get.call_count == 1

    first_post, second_post = http.post.call_args_list
    assert "/media" in first_post.args[0]
    assert "media_publish" not in first_post.args[0]
    assert first_post.kwargs["params"]["image_url"] == "https://cdn.example.com/img.jpg"
    assert first_post.kwargs["params"]["caption"] == "test caption"

    get_call = http.get.call_args_list[0]
    assert "creation-123" in get_call.args[0]
    assert get_call.kwargs["params"]["fields"] == "status_code"

    assert "media_publish" in second_post.args[0]
    assert second_post.kwargs["params"]["creation_id"] == "creation-123"


@pytest.mark.asyncio
async def test_publish_photo_passes_access_token() -> None:
    ig, http = _make_client()
    await ig.publish_photo(image_url="https://cdn.example.com/img.jpg", caption="x")

    first_call = http.post.call_args_list[0]
    assert first_call.kwargs["params"]["access_token"] == "tok"


@pytest.mark.asyncio
async def test_publish_photo_polls_until_finished() -> None:
    """If status is IN_PROGRESS then FINISHED, polls twice before publishing."""
    in_progress = _json_resp({"id": "creation-123", "status_code": "IN_PROGRESS"})
    finished = _json_resp({"id": "creation-123", "status_code": "FINISHED"})
    ig, http = _make_client(status_resps=[in_progress, finished])

    media_id = await ig.publish_photo(image_url="https://cdn.example.com/img.jpg", caption="x")

    assert http.get.call_count == 2
    assert media_id == "media-456"


# ---------------------------------------------------------------------------
# publish_photo() — error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_photo_4xx_on_media_raises_integration_error() -> None:
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
async def test_publish_photo_container_error_status_raises() -> None:
    error_status = _json_resp({"id": "creation-123", "status_code": "ERROR"})
    ig, _ = _make_client(status_resps=[error_status])

    with pytest.raises(IntegrationError, match="terminal status: ERROR"):
        await ig.publish_photo(image_url="https://cdn.example.com/img.jpg", caption="x")


@pytest.mark.asyncio
async def test_publish_photo_poll_timeout_raises() -> None:
    """If the container never reaches FINISHED within the cap, raise a clear error."""
    in_progress = _json_resp({"id": "creation-123", "status_code": "IN_PROGRESS"})
    # Return IN_PROGRESS for all 6 poll attempts
    ig, http = _make_client(status_resps=[in_progress] * 6)

    with pytest.raises(IntegrationError, match="still processing"):
        await ig.publish_photo(image_url="https://cdn.example.com/img.jpg", caption="x")

    assert http.get.call_count == 6


@pytest.mark.asyncio
async def test_publish_photo_5xx_retries_three_times() -> None:
    media_resp = _json_resp({}, status_code=503)
    http = MagicMock(spec=httpx.AsyncClient)
    http.post = AsyncMock(return_value=media_resp)
    http.get = AsyncMock()  # never reached — fails at /media
    ig = InstagramClient(access_token="tok", ig_user_id="123", http_client=http)

    with pytest.raises(httpx.HTTPStatusError):
        await ig.publish_photo(image_url="https://cdn.example.com/img.jpg", caption="x")

    assert http.post.call_count == 3


@pytest.mark.asyncio
async def test_publish_photo_timeout_retries() -> None:
    ig, http = _make_client(post_side_effect=httpx.TimeoutException("timeout"))

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
