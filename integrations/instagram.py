from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass

import httpx
import structlog
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from core.exceptions import IntegrationError

_log = structlog.get_logger()

# Container readiness poll: max attempts × interval = ~18s ceiling before giving up.
_POLL_INTERVAL_S = 3
_POLL_MAX_ATTEMPTS = 6

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

    def _check_response(self, resp: httpx.Response, *, step: str = "") -> None:
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
            subcode = err.get("error_subcode", "")
            # Log the full body so we can diagnose without guessing.
            _log.warning(
                "instagram.api_error",
                step=step,
                status=resp.status_code,
                code=code,
                subcode=subcode,
                message=msg,
                body=body,
            )
            # Error code 190 = invalid/expired OAuth token.
            if code == 190 or "token" in msg.lower() or "expired" in msg.lower():
                raise IntegrationError(
                    "Instagram access token has expired or is invalid — needs refresh in .env"
                )
            raise IntegrationError(
                f"Instagram API error [{step}] code={code} subcode={subcode}: {msg}"
            )
        resp.raise_for_status()  # 5xx → HTTPStatusError → tenacity retries

    async def _wait_until_ready(self, creation_id: str) -> None:
        """Poll the container status until FINISHED, ERROR, or the attempt cap is hit.

        IG processes the image asynchronously after /media returns. Publishing before
        status_code == FINISHED results in code 9007 / subcode 2207027 "Media ID is not
        available". Poll with a fixed interval rather than tenacity so the retry loop is
        explicit and bounded.
        """
        for attempt in range(1, _POLL_MAX_ATTEMPTS + 1):
            resp = await self._client.get(
                f"{_IG_BASE}/{creation_id}",
                params={"fields": "status_code", "access_token": self._access_token},
                timeout=self._timeout,
            )
            self._check_response(resp, step="status_poll")
            status_code: str = resp.json().get("status_code", "UNKNOWN")
            _log.debug(
                "instagram.container_status",
                creation_id=creation_id,
                attempt=attempt,
                status_code=status_code,
            )
            if status_code == "FINISHED":
                return
            if status_code in ("ERROR", "EXPIRED"):
                raise IntegrationError(
                    f"Instagram container {creation_id} reached terminal status: {status_code}"
                )
            # IN_PROGRESS or unexpected value — wait and retry
            if attempt < _POLL_MAX_ATTEMPTS:
                await asyncio.sleep(_POLL_INTERVAL_S)

        elapsed = _POLL_INTERVAL_S * _POLL_MAX_ATTEMPTS
        raise IntegrationError(
            f"Instagram container still processing after {elapsed}s — try again in a moment."
        )

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def publish_photo(self, image_url: str, caption: str) -> str:
        """Publish a photo to Instagram. Returns the published media id.

        Step 1: create a media container (/media).
        Step 2: poll status_code until FINISHED (IG processes asynchronously; publishing
                before FINISHED → code 9007 "Media ID is not available").
        Step 3: publish (/media_publish).
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
        self._check_response(resp1, step="media")
        creation_id: str = resp1.json()["id"]

        await self._wait_until_ready(creation_id)

        resp2 = await self._client.post(
            f"{_IG_BASE}/{self._ig_user_id}/media_publish",
            params={
                "creation_id": creation_id,
                "access_token": self._access_token,
            },
            timeout=self._timeout,
        )
        self._check_response(resp2, step="media_publish")
        return str(resp2.json()["id"])

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def publish_carousel(self, image_urls: list[str], caption: str) -> str:
        """Publish a carousel of photos to Instagram. Returns the published media id.

        Flow: create N child containers (each polled to FINISHED) → create parent CAROUSEL
        container (polled to FINISHED) → publish. Caption goes on the parent container.
        Partial failure: if a child or parent errors, dangling child containers expire on
        IG's side — no cleanup is attempted; the IntegrationError propagates to the caller.
        """
        child_ids: list[str] = []
        for image_url in image_urls:
            resp = await self._client.post(
                f"{_IG_BASE}/{self._ig_user_id}/media",
                params={
                    "image_url": image_url,
                    "is_carousel_item": "true",
                    "access_token": self._access_token,
                },
                timeout=self._timeout,
            )
            self._check_response(resp, step="carousel_child")
            child_id: str = resp.json()["id"]
            await self._wait_until_ready(child_id)
            child_ids.append(child_id)

        resp_parent = await self._client.post(
            f"{_IG_BASE}/{self._ig_user_id}/media",
            params={
                "media_type": "CAROUSEL",
                "children": ",".join(child_ids),
                "caption": caption,
                "access_token": self._access_token,
            },
            timeout=self._timeout,
        )
        self._check_response(resp_parent, step="carousel_parent")
        parent_id: str = resp_parent.json()["id"]
        await self._wait_until_ready(parent_id)

        resp_publish = await self._client.post(
            f"{_IG_BASE}/{self._ig_user_id}/media_publish",
            params={
                "creation_id": parent_id,
                "access_token": self._access_token,
            },
            timeout=self._timeout,
        )
        self._check_response(resp_publish, step="carousel_publish")
        return str(resp_publish.json()["id"])

    async def health_check(self) -> bool:
        return bool(self._access_token and self._ig_user_id)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
