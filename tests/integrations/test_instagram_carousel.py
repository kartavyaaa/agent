"""Unit tests for InstagramClient.publish_carousel().

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


def _make_carousel_client(
    n: int = 3,
    *,
    child_status: str = "FINISHED",
    parent_status: str = "FINISHED",
    child_create_error_index: int | None = None,
    parent_create_error: bool = False,
    error_code: int = 100,
    error_message: str = "Invalid parameter",
) -> tuple[InstagramClient, MagicMock]:
    """Build a mock InstagramClient configured for a carousel of n images.

    POST side_effect order: child_0, child_1, ..., child_{n-1}, parent, publish
    GET side_effect order: child_0_status, ..., child_{n-1}_status, parent_status
    """
    http = MagicMock(spec=httpx.AsyncClient)

    post_responses: list[MagicMock] = []
    for i in range(n):
        if child_create_error_index == i:
            post_responses.append(
                _json_resp(
                    {"error": {"code": error_code, "message": error_message}},
                    status_code=400,
                )
            )
        else:
            post_responses.append(_json_resp({"id": f"child-{i}"}))

    if parent_create_error:
        post_responses.append(
            _json_resp(
                {"error": {"code": error_code, "message": error_message}},
                status_code=400,
            )
        )
    else:
        post_responses.append(_json_resp({"id": "parent-999"}))

    post_responses.append(_json_resp({"id": "carousel-published-id"}))
    http.post = AsyncMock(side_effect=post_responses)

    get_responses: list[MagicMock] = []
    for i in range(n):
        if child_create_error_index is None or i < child_create_error_index:
            get_responses.append(_json_resp({"status_code": child_status}))
    if not parent_create_error and child_create_error_index is None:
        get_responses.append(_json_resp({"status_code": parent_status}))
    http.get = AsyncMock(side_effect=get_responses)

    ig = InstagramClient(
        access_token="tok",
        ig_user_id="17841407153636057",
        http_client=http,
    )
    return ig, http


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_carousel_returns_media_id() -> None:
    ig, _ = _make_carousel_client(n=3)
    media_id = await ig.publish_carousel(
        image_urls=[
            "https://r2.example.com/a.jpg",
            "https://r2.example.com/b.jpg",
            "https://r2.example.com/c.jpg",
        ],
        caption="My carousel",
    )
    assert media_id == "carousel-published-id"


@pytest.mark.asyncio
async def test_publish_carousel_call_counts_and_order() -> None:
    """3 images → 3 child creates + 3 child polls + 1 parent create + 1 parent poll + 1 publish."""
    ig, http = _make_carousel_client(n=3)
    image_urls = [f"https://r2.example.com/{i}.jpg" for i in range(3)]
    await ig.publish_carousel(image_urls=image_urls, caption="test")

    assert http.post.call_count == 5  # 3 children + parent + publish
    assert http.get.call_count == 4  # 3 child polls + 1 parent poll


@pytest.mark.asyncio
async def test_publish_carousel_children_polled_before_parent_created() -> None:
    """Each child must be polled to FINISHED before the parent container is created."""
    ig, http = _make_carousel_client(n=2)
    image_urls = ["https://r2.example.com/a.jpg", "https://r2.example.com/b.jpg"]
    await ig.publish_carousel(image_urls=image_urls, caption="ordering test")

    post_calls = http.post.call_args_list

    # child-0 created (post[0]) → child-0 polled (get[0]) → child-1 created (post[1]) → ...
    # parent created (post[2]) → parent polled (get[2]) → publish (post[3])
    child0_create_url = post_calls[0].args[0]
    assert "/media" in child0_create_url
    assert "media_publish" not in child0_create_url

    # parent container params: media_type=CAROUSEL
    parent_params = post_calls[2].kwargs["params"]
    assert parent_params["media_type"] == "CAROUSEL"
    assert parent_params["caption"] == "ordering test"
    assert "children" in parent_params
    # Both child ids comma-separated
    assert "child-0" in parent_params["children"]
    assert "child-1" in parent_params["children"]

    # publish is the last POST
    publish_params = post_calls[3].kwargs["params"]
    assert "creation_id" in publish_params
    assert publish_params["creation_id"] == "parent-999"


@pytest.mark.asyncio
async def test_publish_carousel_child_has_is_carousel_item() -> None:
    ig, http = _make_carousel_client(n=2)
    await ig.publish_carousel(
        image_urls=["https://r2.example.com/a.jpg", "https://r2.example.com/b.jpg"],
        caption="x",
    )
    child_params = http.post.call_args_list[0].kwargs["params"]
    assert child_params.get("is_carousel_item") == "true"
    assert "image_url" in child_params
    assert "caption" not in child_params  # caption only on parent


@pytest.mark.asyncio
async def test_publish_carousel_caption_on_parent_not_child() -> None:
    ig, http = _make_carousel_client(n=2)
    await ig.publish_carousel(
        image_urls=["https://r2.example.com/a.jpg", "https://r2.example.com/b.jpg"],
        caption="My caption",
    )
    # child creates have no caption
    for i in range(2):
        child_params = http.post.call_args_list[i].kwargs["params"]
        assert "caption" not in child_params
    # parent create has the caption
    parent_params = http.post.call_args_list[2].kwargs["params"]
    assert parent_params["caption"] == "My caption"


@pytest.mark.asyncio
async def test_publish_carousel_minimum_two_images() -> None:
    ig, _ = _make_carousel_client(n=2)
    media_id = await ig.publish_carousel(
        image_urls=["https://r2.example.com/a.jpg", "https://r2.example.com/b.jpg"],
        caption="min",
    )
    assert media_id == "carousel-published-id"


@pytest.mark.asyncio
async def test_publish_carousel_maximum_ten_images() -> None:
    ig, http = _make_carousel_client(n=10)
    urls = [f"https://r2.example.com/{i}.jpg" for i in range(10)]
    media_id = await ig.publish_carousel(image_urls=urls, caption="max")
    assert media_id == "carousel-published-id"
    assert http.post.call_count == 12  # 10 children + parent + publish
    assert http.get.call_count == 11  # 10 child polls + 1 parent poll


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_carousel_child_error_status_raises() -> None:
    """A child container reaching ERROR status raises IntegrationError."""
    ig, _ = _make_carousel_client(n=2, child_status="ERROR")
    with pytest.raises(IntegrationError, match="terminal status: ERROR"):
        await ig.publish_carousel(
            image_urls=["https://r2.example.com/a.jpg", "https://r2.example.com/b.jpg"],
            caption="x",
        )


@pytest.mark.asyncio
async def test_publish_carousel_parent_error_status_raises() -> None:
    """A parent container reaching ERROR status raises IntegrationError."""
    ig, _ = _make_carousel_client(n=2, parent_status="ERROR")
    with pytest.raises(IntegrationError, match="terminal status: ERROR"):
        await ig.publish_carousel(
            image_urls=["https://r2.example.com/a.jpg", "https://r2.example.com/b.jpg"],
            caption="x",
        )


@pytest.mark.asyncio
async def test_publish_carousel_child_create_http400_raises() -> None:
    ig, _ = _make_carousel_client(
        n=2, child_create_error_index=0, error_code=100, error_message="Invalid image URL"
    )
    with pytest.raises(IntegrationError, match="Invalid image URL"):
        await ig.publish_carousel(
            image_urls=["https://r2.example.com/a.jpg", "https://r2.example.com/b.jpg"],
            caption="x",
        )


@pytest.mark.asyncio
async def test_publish_carousel_parent_create_http400_raises() -> None:
    ig, _ = _make_carousel_client(
        n=2, parent_create_error=True, error_code=100, error_message="Carousel creation failed"
    )
    with pytest.raises(IntegrationError, match="Carousel creation failed"):
        await ig.publish_carousel(
            image_urls=["https://r2.example.com/a.jpg", "https://r2.example.com/b.jpg"],
            caption="x",
        )


@pytest.mark.asyncio
async def test_publish_carousel_token_190_raises_clear_message() -> None:
    ig, _ = _make_carousel_client(
        n=2,
        child_create_error_index=0,
        error_code=190,
        error_message="Error validating access token",
    )
    with pytest.raises(IntegrationError, match="expired or is invalid"):
        await ig.publish_carousel(
            image_urls=["https://r2.example.com/a.jpg", "https://r2.example.com/b.jpg"],
            caption="x",
        )
