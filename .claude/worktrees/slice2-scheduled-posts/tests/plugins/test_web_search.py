"""Unit tests for WebSearchPlugin.

SerperClient and DB are mocked — no real HTTP calls or Postgres needed.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from core.exceptions import IntegrationRateLimitError
from core.llm.base import LLMConfig, LLMMessage, LLMResponse, LLMToolCall, TokenUsage
from core.planner.react import ReActPlanner
from core.tools.registry import ToolRegistry
from integrations.serper import SerperResult
from plugins.web_search.plugin import WebSearchPlugin
from plugins.web_search.schemas import SearchResult, WebSearchInput, WebSearchOutput


def _make_client(
    results: list[SerperResult] | None = None,
    *,
    health: bool = True,
    side_effect: Exception | None = None,
) -> MagicMock:
    client = MagicMock()
    if side_effect is not None:
        client.search = AsyncMock(side_effect=side_effect)
    else:
        client.search = AsyncMock(return_value=results or [])
    client.health_check = AsyncMock(return_value=health)
    return client


def _make_db() -> MagicMock:
    return MagicMock()


def _usage() -> TokenUsage:
    return TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15)


def _message_response(text: str = "Done.") -> LLMResponse:
    return LLMResponse(
        response_type="message",
        content=text,
        tool_calls=[],
        model="gpt-5.5",
        usage=_usage(),
        raw_response_id="r-msg",
    )


def _tool_response(*calls: tuple[str, dict]) -> LLMResponse:  # type: ignore[type-arg]
    return LLMResponse(
        response_type="tool_calls",
        content=None,
        tool_calls=[
            LLMToolCall(id=f"call-{i}", name=name, arguments=args)
            for i, (name, args) in enumerate(calls)
        ],
        model="gpt-5.5",
        usage=_usage(),
        raw_response_id="r-tool",
    )


# ---------------------------------------------------------------------------
# Schema correctness
# ---------------------------------------------------------------------------


def test_web_search_input_has_no_user_id() -> None:
    assert "user_id" not in WebSearchInput.model_fields


def test_web_search_input_fields() -> None:
    fields = set(WebSearchInput.model_fields.keys())
    assert fields == {"query", "max_results"}


def test_web_search_output_fields() -> None:
    fields = set(WebSearchOutput.model_fields.keys())
    assert fields == {"query", "results", "result_count"}


def test_max_results_lower_bound() -> None:
    with pytest.raises(ValidationError):
        WebSearchInput(query="test", max_results=0)


def test_max_results_upper_bound() -> None:
    with pytest.raises(ValidationError):
        WebSearchInput(query="test", max_results=11)


def test_max_results_at_bounds_valid() -> None:
    assert WebSearchInput(query="test", max_results=1).max_results == 1
    assert WebSearchInput(query="test", max_results=10).max_results == 10


# ---------------------------------------------------------------------------
# execute() happy path
# ---------------------------------------------------------------------------


async def test_execute_returns_web_search_output() -> None:
    serper_results = [
        SerperResult(title="T1", link="https://a.com", snippet="S1"),
        SerperResult(title="T2", link="https://b.com", snippet="S2"),
    ]
    client = _make_client(serper_results)
    plugin = WebSearchPlugin(client=client)

    result = await plugin.execute(
        WebSearchInput(query="python async", max_results=2),
        user_id=uuid.uuid4(),
        db=_make_db(),
    )

    assert isinstance(result, WebSearchOutput)
    assert result.query == "python async"
    assert result.result_count == 2
    assert len(result.results) == 2
    assert result.results[0].title == "T1"
    assert result.results[0].link == "https://a.com"
    assert result.results[0].snippet == "S1"
    assert isinstance(result.results[0], SearchResult)


async def test_execute_passes_max_results_to_client() -> None:
    client = _make_client([SerperResult(title="T", link="L", snippet="S")])
    plugin = WebSearchPlugin(client=client)

    await plugin.execute(
        WebSearchInput(query="test", max_results=3),
        user_id=uuid.uuid4(),
        db=_make_db(),
    )

    client.search.assert_called_once_with("test", num_results=3)


async def test_execute_empty_results() -> None:
    client = _make_client([])
    plugin = WebSearchPlugin(client=client)

    result = await plugin.execute(
        WebSearchInput(query="very obscure query"),
        user_id=uuid.uuid4(),
        db=_make_db(),
    )

    assert result.result_count == 0
    assert result.results == []


# ---------------------------------------------------------------------------
# execute() error propagation
# ---------------------------------------------------------------------------


async def test_execute_propagates_rate_limit_error() -> None:
    client = _make_client(side_effect=IntegrationRateLimitError("429"))
    plugin = WebSearchPlugin(client=client)

    with pytest.raises(IntegrationRateLimitError):
        await plugin.execute(
            WebSearchInput(query="test"),
            user_id=uuid.uuid4(),
            db=_make_db(),
        )


# ---------------------------------------------------------------------------
# health_check()
# ---------------------------------------------------------------------------


async def test_health_check_healthy() -> None:
    client = _make_client(health=True)
    plugin = WebSearchPlugin(client=client)
    status = await plugin.health_check()
    client.health_check.assert_called_once()
    assert status.status == "healthy"
    assert status.checked_at is not None


async def test_health_check_unhealthy() -> None:
    client = _make_client(health=False)
    plugin = WebSearchPlugin(client=client)
    status = await plugin.health_check()
    assert status.status == "unhealthy"


# ---------------------------------------------------------------------------
# Planner integration — mocked LLM + mocked SerperClient
# ---------------------------------------------------------------------------


async def test_planner_calls_web_search_and_synthesizes() -> None:
    serper_results = [SerperResult(title="AI News", link="https://news.com", snippet="Latest AI")]
    client = _make_client(serper_results)
    plugin = WebSearchPlugin(client=client)

    registry = ToolRegistry()
    registry.register(plugin)

    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(
        side_effect=[
            _tool_response(("web_search", {"query": "latest AI news", "max_results": 3})),
            _message_response("Here are the latest AI news results."),
        ]
    )

    planner = ReActPlanner(
        llm=mock_llm,
        registry=registry,
        config=LLMConfig(model="gpt-5.5"),
        max_iterations=8,
    )

    result = await planner.run(
        messages=[LLMMessage(role="user", content="what's in AI news today?")],
        tools=registry.get_tools_for_llm(),
        user_id=uuid.uuid4(),
        db=_make_db(),
    )

    assert "web_search" in result.tool_calls_made
    assert result.iterations == 2
    assert result.content == "Here are the latest AI news results."
    client.search.assert_called_once_with("latest AI news", num_results=3)
