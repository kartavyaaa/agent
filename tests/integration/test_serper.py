"""Unit tests for SerperClient.

HTTP calls are mocked via pytest-httpx — no real Serper API is called.
"""

from __future__ import annotations

import httpx
import pytest
from pytest_httpx import HTTPXMock

from core.exceptions import IntegrationError, IntegrationRateLimitError
from integrations.serper import SerperClient, SerperResult

_URL = "https://google.serper.dev/search"

_ORGANIC_RESPONSE = {
    "organic": [
        {"title": "T1", "link": "https://example.com/1", "snippet": "Snippet 1"},
        {"title": "T2", "link": "https://example.com/2", "snippet": "Snippet 2"},
    ]
}


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_search_returns_results(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="POST", url=_URL, json=_ORGANIC_RESPONSE)
    client = SerperClient(api_key="test-key")
    results = await client.search("python tutorials", num_results=2)
    assert len(results) == 2
    assert isinstance(results[0], SerperResult)
    assert results[0].title == "T1"
    assert results[0].link == "https://example.com/1"
    assert results[0].snippet == "Snippet 1"


async def test_search_sends_correct_headers(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="POST", url=_URL, json=_ORGANIC_RESPONSE)
    client = SerperClient(api_key="my-secret-key")
    await client.search("test")
    request = httpx_mock.get_requests()[0]
    assert request.headers["x-api-key"] == "my-secret-key"
    assert "application/json" in request.headers["content-type"]


async def test_search_empty_organic(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="POST", url=_URL, json={"organic": []})
    client = SerperClient(api_key="test-key")
    results = await client.search("very obscure query")
    assert results == []


async def test_search_missing_organic_key(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="POST", url=_URL, json={"knowledgeGraph": {}})
    client = SerperClient(api_key="test-key")
    results = await client.search("query with no organic")
    assert results == []


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


async def test_search_429_raises_rate_limit_no_retry(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="POST", url=_URL, status_code=429)
    client = SerperClient(api_key="test-key")
    with pytest.raises(IntegrationRateLimitError):
        await client.search("query")
    assert len(httpx_mock.get_requests()) == 1


async def test_search_401_raises_integration_error_no_retry(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="POST", url=_URL, status_code=401)
    client = SerperClient(api_key="bad-key")
    with pytest.raises(IntegrationError):
        await client.search("query")
    assert len(httpx_mock.get_requests()) == 1


async def test_search_500_retries_three_times(httpx_mock: HTTPXMock) -> None:
    for _ in range(3):
        httpx_mock.add_response(method="POST", url=_URL, status_code=500)
    client = SerperClient(api_key="test-key")
    with pytest.raises(httpx.HTTPStatusError):
        await client.search("query")
    assert len(httpx_mock.get_requests()) == 3


async def test_search_timeout_retries_three_times(httpx_mock: HTTPXMock) -> None:
    for _ in range(3):
        httpx_mock.add_exception(httpx.TimeoutException("timeout"), method="POST", url=_URL)
    client = SerperClient(api_key="test-key")
    with pytest.raises(httpx.TimeoutException):
        await client.search("query")
    assert len(httpx_mock.get_requests()) == 3


# ---------------------------------------------------------------------------
# health_check — no HTTP calls
# ---------------------------------------------------------------------------


async def test_health_check_true_with_key() -> None:
    client = SerperClient(api_key="any-key")
    assert await client.health_check() is True


async def test_health_check_false_empty_key() -> None:
    client = SerperClient(api_key="")
    assert await client.health_check() is False
