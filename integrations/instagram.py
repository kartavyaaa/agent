from __future__ import annotations

import contextlib
from dataclasses import dataclass

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from core.exceptions import IntegrationError

# Confirmed from probe: graph.instagram.com, v21.0
_IG_BASE = "https://graph.instagram.com/v21.0"


@dataclass
class IGPublishResult:
    media_id: str


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return False


class InstagramClient:
    """Thin async client for the Instagram Graph API (photo publishing).

    Two-step flow (proven): POST /media → creation_id, POST /media_publish → media_id.
    No readiness wait between the two calls is needed.
    """

    def __init__(
        self,
        access_token: str,
        ig_user_id: str,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._access_token = access_token
        self._ig_user_id = ig_user_id
        self._timeout = timeout
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient()

    def _check_response(self, resp: httpx.Response) -> None:
        """Raise IntegrationError with a clear message for 4xx responses."""
        if 400 <= resp.status_code < 500:
            body: dict[object, object] = {}
            with contextlib.suppress(Exception):
                body = resp.json()
            err = body.get("error", {})
            if not isinstance(err, dict):
                err = {}
            msg: str = str(err.get("message", "")) or f"HTTP {resp.status_code}"
            code = err.get("code", 0)
            # Error code 190 = invalid/expired OAuth token.
            if code == 190 or "token" in msg.lower() or "expired" in msg.lower():
                raise IntegrationError(
                    "Instagram access token has expired or is invalid — needs refresh in .env"
                )
            raise IntegrationError(f"Instagram API error: {msg}")
        resp.raise_for_status()  # 5xx → HTTPStatusError → tenacity retries

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def publish_photo(self, image_url: str, caption: str) -> str:
        """Publish a photo to Instagram. Returns the published media id.

        Step 1: create a media container (/media).
        Step 2: publish it (/media_publish). Back-to-back, no readiness wait needed (proven).
        """
        resp1 = await self._client.post(
            f"{_IG_BASE}/{self._ig_user_id}/media",
            params={
                "image_url": image_url,
                "caption": caption,
                "access_token": self._access_token,
            },
            timeout=self._timeout,
        )
        self._check_response(resp1)
        creation_id: str = resp1.json()["id"]

        resp2 = await self._client.post(
            f"{_IG_BASE}/{self._ig_user_id}/media_publish",
            params={
                "creation_id": creation_id,
                "access_token": self._access_token,
            },
            timeout=self._timeout,
        )
        self._check_response(resp2)
        return str(resp2.json()["id"])

    async def health_check(self) -> bool:
        return bool(self._access_token and self._ig_user_id)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
