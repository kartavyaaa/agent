from __future__ import annotations

from dataclasses import dataclass

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from core.exceptions import IntegrationError, IntegrationRateLimitError

_SERPER_URL = "https://google.serper.dev/search"


@dataclass
class SerperResult:
    title: str
    link: str
    snippet: str


def _is_retryable(exc: BaseException) -> bool:
    """Retry on 5xx responses and timeouts only. 429 and other 4xx are not retryable."""
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return False


class SerperClient:
    """Thin async client for the Serper.dev Google Search API."""

    def __init__(
        self,
        api_key: str,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._api_key = api_key
        self._timeout = timeout
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient()

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def search(self, query: str, num_results: int = 5) -> list[SerperResult]:
        """Search the web via Serper.dev. Returns up to num_results organic results."""
        resp = await self._client.post(
            _SERPER_URL,
            headers={"X-API-KEY": self._api_key, "Content-Type": "application/json"},
            json={"q": query, "num": num_results},
            timeout=self._timeout,
        )
        if resp.status_code == 429:
            raise IntegrationRateLimitError("Serper rate limit exceeded (HTTP 429)")
        if 400 <= resp.status_code < 500:
            raise IntegrationError(f"Serper API error: HTTP {resp.status_code}")
        resp.raise_for_status()  # 5xx → httpx.HTTPStatusError → tenacity retries
        data = resp.json()
        return [
            SerperResult(
                title=item.get("title", ""),
                link=item.get("link", ""),
                snippet=item.get("snippet", ""),
            )
            for item in data.get("organic", [])
        ]

    async def health_check(self) -> bool:
        """Return True if an API key is configured. No network call — no quota consumed."""
        return bool(self._api_key)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
