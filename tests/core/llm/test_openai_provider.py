"""Unit tests for OpenAIProvider.

All OpenAI network calls are mocked — no real API key needed.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import openai
import pytest

from core.exceptions import LLMError, LLMRateLimitError, LLMTimeoutError
from core.llm.base import LLMConfig, LLMMessage, LLMTool
from core.llm.openai_provider import OpenAIProvider


def _make_provider() -> OpenAIProvider:
    with patch("core.llm.openai_provider.AsyncOpenAI"):
        return OpenAIProvider(
            api_key="test-key",
            default_model="gpt-5.5",
            fast_model="gpt-5.4-nano",
        )


def _make_usage() -> SimpleNamespace:
    return SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15)


def _make_message_response(text: str) -> SimpleNamespace:
    # content items must carry type="output_text" to match real Responses API shape
    content_part = SimpleNamespace(type="output_text", text=text)
    item = SimpleNamespace(type="message", content=[content_part])
    return SimpleNamespace(
        output=[item],
        model="gpt-5.5",
        usage=_make_usage(),
        id="resp-001",
    )


def _make_tool_call_response(name: str, args_json: str, call_id: str = "call-1") -> SimpleNamespace:
    item = SimpleNamespace(
        type="function_call",
        name=name,
        arguments=args_json,
        call_id=call_id,
    )
    return SimpleNamespace(
        output=[item],
        model="gpt-5.5",
        usage=_make_usage(),
        id="resp-002",
    )


def _config() -> LLMConfig:
    return LLMConfig(model="gpt-5.5")


def _messages() -> list[LLMMessage]:
    return [LLMMessage(role="user", content="hello")]


def _mock_client(provider: OpenAIProvider) -> MagicMock:
    """Return the MagicMock that replaced AsyncOpenAI on this provider instance."""
    return provider._client  # type: ignore[return-value]


@pytest.mark.asyncio
async def test_complete_message() -> None:
    provider = _make_provider()
    _mock_client(provider).responses.create = AsyncMock(
        return_value=_make_message_response("hi there")
    )

    result = await provider.complete(_messages(), tools=None, config=_config())

    assert result.response_type == "message"
    assert result.content == "hi there"
    assert result.tool_calls == []
    assert result.model == "gpt-5.5"
    assert result.usage.input_tokens == 10


@pytest.mark.asyncio
async def test_complete_tool_call() -> None:
    provider = _make_provider()
    _mock_client(provider).responses.create = AsyncMock(
        return_value=_make_tool_call_response(
            "create_reminder",
            '{"message": "call Bob", "remind_at": "2026-07-08T09:00:00Z"}',
        )
    )
    tool = LLMTool(
        name="create_reminder",
        description="Create a reminder",
        parameters={"type": "object", "properties": {}},
    )

    result = await provider.complete(_messages(), tools=[tool], config=_config())

    assert result.response_type == "tool_calls"
    assert len(result.tool_calls) == 1
    tc = result.tool_calls[0]
    assert tc.name == "create_reminder"
    assert tc.arguments == {"message": "call Bob", "remind_at": "2026-07-08T09:00:00Z"}
    assert isinstance(tc.arguments, dict)


@pytest.mark.asyncio
async def test_complete_malformed_args() -> None:
    provider = _make_provider()
    _mock_client(provider).responses.create = AsyncMock(
        return_value=_make_tool_call_response("some_tool", "NOT_VALID_JSON")
    )

    with pytest.raises(LLMError, match="malformed tool arguments"):
        await provider.complete(_messages(), tools=None, config=_config())


@pytest.mark.asyncio
async def test_complete_rate_limit_retry() -> None:
    provider = _make_provider()
    call_count = 0

    async def _side_effect(*args: object, **kwargs: object) -> SimpleNamespace:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise openai.RateLimitError(
                "rate limited",
                response=MagicMock(status_code=429, headers={}),
                body={},
            )
        return _make_message_response("ok")

    _mock_client(provider).responses.create = _side_effect

    result = await provider.complete(_messages(), tools=None, config=_config())

    assert result.response_type == "message"
    assert call_count == 3


@pytest.mark.asyncio
async def test_complete_rate_limit_exhausted() -> None:
    provider = _make_provider()

    async def _always_rate_limit(*args: object, **kwargs: object) -> SimpleNamespace:
        raise openai.RateLimitError(
            "rate limited",
            response=MagicMock(status_code=429, headers={}),
            body={},
        )

    _mock_client(provider).responses.create = _always_rate_limit

    with pytest.raises(LLMRateLimitError):
        await provider.complete(_messages(), tools=None, config=_config())


@pytest.mark.asyncio
async def test_complete_timeout() -> None:
    provider = _make_provider()

    async def _timeout(*args: object, **kwargs: object) -> SimpleNamespace:
        raise openai.APITimeoutError(request=MagicMock())

    _mock_client(provider).responses.create = _timeout

    with pytest.raises(LLMTimeoutError):
        await provider.complete(_messages(), tools=None, config=_config())


@pytest.mark.asyncio
async def test_complete_httpx_timeout_maps_to_llm_timeout_error() -> None:
    """httpx.TimeoutException (low-level) must be caught and wrapped as LLMTimeoutError.

    openai.APITimeoutError wraps most timeouts, but connection-phase timeouts can
    surface as raw httpx.TimeoutException before the OpenAI SDK wraps them.
    """
    import httpx

    provider = _make_provider()

    async def _httpx_timeout(*args: object, **kwargs: object) -> SimpleNamespace:
        raise httpx.ConnectTimeout("Connection timed out")

    _mock_client(provider).responses.create = _httpx_timeout

    with pytest.raises(LLMTimeoutError):
        await provider.complete(_messages(), tools=None, config=_config())


@pytest.mark.asyncio
async def test_embed_cache() -> None:
    provider = _make_provider()

    vec = [0.1] * 1536
    embed_response = SimpleNamespace(data=[SimpleNamespace(embedding=vec)])
    _mock_client(provider).embeddings.create = AsyncMock(return_value=embed_response)

    result1 = await provider.embed(["hello world"], model="text-embedding-3-small")
    result2 = await provider.embed(["hello world"], model="text-embedding-3-small")

    # API called only once — second call hits cache
    assert _mock_client(provider).embeddings.create.call_count == 1
    assert result1 == result2
    assert result1[0] == vec


@pytest.mark.asyncio
async def test_embed_batch() -> None:
    provider = _make_provider()

    vec_a = [0.1] * 1536
    vec_b = [0.2] * 1536
    embed_response = SimpleNamespace(
        data=[SimpleNamespace(embedding=vec_a), SimpleNamespace(embedding=vec_b)]
    )
    _mock_client(provider).embeddings.create = AsyncMock(return_value=embed_response)

    results = await provider.embed(["text a", "text b"], model="text-embedding-3-small")

    assert _mock_client(provider).embeddings.create.call_count == 1
    assert results[0] == vec_a
    assert results[1] == vec_b


@pytest.mark.asyncio
async def test_embed_partial_cache() -> None:
    """Second text cached, first fetched from API."""
    provider = _make_provider()

    vec_a = [0.3] * 1536
    vec_b = [0.4] * 1536
    provider._embed_cache["already cached"] = vec_b

    embed_response = SimpleNamespace(data=[SimpleNamespace(embedding=vec_a)])
    _mock_client(provider).embeddings.create = AsyncMock(return_value=embed_response)

    results = await provider.embed(["new text", "already cached"], model="text-embedding-3-small")

    assert _mock_client(provider).embeddings.create.call_count == 1
    assert results[0] == vec_a
    assert results[1] == vec_b


def test_list_models() -> None:
    provider = _make_provider()
    models = provider.list_models()
    assert "gpt-5.5" in models
    assert "gpt-5.4-nano" in models


@pytest.mark.asyncio
async def test_complete_temperature_none_not_sent_to_api() -> None:
    """When config.temperature is None the parameter must be absent from the API call.

    GPT-5 family models reject temperature with 400; NOT_GIVEN causes the SDK to
    omit it from the request body entirely.
    """
    provider = _make_provider()
    mock_create = AsyncMock(return_value=_make_message_response("ok"))
    _mock_client(provider).responses.create = mock_create

    config = LLMConfig(model="gpt-5.5")  # temperature defaults to None
    assert config.temperature is None

    await provider.complete(_messages(), tools=None, config=config)

    _, kwargs = mock_create.call_args
    # NOT_GIVEN causes the SDK to omit the key; the kwarg value should be NOT_GIVEN
    from openai import NOT_GIVEN as _NOT_GIVEN

    assert kwargs.get("temperature") is _NOT_GIVEN


@pytest.mark.asyncio
async def test_complete_temperature_set_is_forwarded_to_api() -> None:
    """When config.temperature is explicitly set it must be included in the API call."""
    provider = _make_provider()
    mock_create = AsyncMock(return_value=_make_message_response("ok"))
    _mock_client(provider).responses.create = mock_create

    config = LLMConfig(model="gpt-5.5", temperature=0.3)

    await provider.complete(_messages(), tools=None, config=config)

    _, kwargs = mock_create.call_args
    assert kwargs.get("temperature") == 0.3


# ---------------------------------------------------------------------------
# Multi-turn serialization — Responses API item shapes
# ---------------------------------------------------------------------------


def test_to_items_tool_result() -> None:
    """tool_result → top-level function_call_output item."""
    from core.llm.openai_provider import _to_items

    msg = LLMMessage(role="tool_result", content="done", tool_call_id="call-99")
    items = _to_items(msg)
    assert len(items) == 1
    assert items[0]["type"] == "function_call_output"
    assert items[0]["call_id"] == "call-99"
    assert items[0]["output"] == "done"


def test_to_items_assistant_with_two_tool_calls_expands_to_two_items() -> None:
    """assistant + N tool calls → N separate top-level function_call items.

    The Responses API rejects "function_call" as a content-block type; prior
    function calls must be top-level items, not nested in a message.
    """
    from core.llm.base import LLMToolCall
    from core.llm.openai_provider import _to_items

    tc1 = LLMToolCall(id="cid-1", name="web_search", arguments={"query": "python"})
    tc2 = LLMToolCall(
        id="cid-2",
        name="create_reminder",
        arguments={"message": "x", "remind_at": "2026-07-08T09:00:00Z"},
    )
    msg = LLMMessage(role="assistant", content="", tool_calls=[tc1, tc2])
    items = _to_items(msg)

    assert len(items) == 2
    types = {item["type"] for item in items}
    assert types == {"function_call"}
    call_ids = [item["call_id"] for item in items]
    assert call_ids == ["cid-1", "cid-2"]
    names = [item["name"] for item in items]
    assert names == ["web_search", "create_reminder"]
    # arguments must be JSON strings, not dicts
    import json as _json

    for item in items:
        assert isinstance(item["arguments"], str)
        _json.loads(item["arguments"])  # must parse without error


def test_to_items_multi_turn_call_id_pairing() -> None:
    """Full multi-turn sequence: function_call items pair with function_call_output by call_id."""

    from core.llm.base import LLMToolCall
    from core.llm.openai_provider import _to_items

    call_id = "cid-42"
    # Turn 1: assistant emits tool call
    assistant_msg = LLMMessage(
        role="assistant",
        content="",
        tool_calls=[LLMToolCall(id=call_id, name="web_search", arguments={"query": "hello"})],
    )
    # Turn 2: tool result
    result_msg = LLMMessage(role="tool_result", content='{"results": []}', tool_call_id=call_id)

    assistant_items = _to_items(assistant_msg)
    result_items = _to_items(result_msg)

    assert len(assistant_items) == 1
    assert assistant_items[0]["type"] == "function_call"
    assert assistant_items[0]["call_id"] == call_id

    assert len(result_items) == 1
    assert result_items[0]["type"] == "function_call_output"
    assert result_items[0]["call_id"] == call_id  # call_ids match — correct pairing


def test_to_items_reasoning_echoed_verbatim() -> None:
    """reasoning messages are echoed back as-is (raw_item passthrough)."""
    from core.llm.openai_provider import _to_items

    raw = {"type": "reasoning", "id": "r-001", "summary": []}
    msg = LLMMessage(role="reasoning", content="", raw_item=raw)
    items = _to_items(msg)
    assert len(items) == 1
    # Pydantic copies the dict on assignment, so check equality not identity
    assert items[0] == raw
    assert items[0]["type"] == "reasoning"
    assert items[0]["id"] == "r-001"


def test_to_items_user_message() -> None:
    """user/system messages become a single EasyInputMessage dict."""
    from core.llm.openai_provider import _to_items

    msg = LLMMessage(role="user", content="hello world")
    items = _to_items(msg)
    assert len(items) == 1
    assert items[0]["role"] == "user"
    assert items[0]["content"] == "hello world"


@pytest.mark.asyncio
async def test_complete_reasoning_items_collected() -> None:
    """Reasoning items in the response are collected into LLMResponse.reasoning_items."""
    provider = _make_provider()

    reasoning_item = SimpleNamespace(
        type="reasoning",
        id="r-001",
        summary=[SimpleNamespace(type="summary_text", text="I thought about this")],
    )
    message_item = SimpleNamespace(
        type="message",
        content=[SimpleNamespace(type="output_text", text="Here is my answer")],
    )
    response = SimpleNamespace(
        output=[reasoning_item, message_item],
        model="gpt-5.5",
        usage=_make_usage(),
        id="resp-003",
    )
    _mock_client(provider).responses.create = AsyncMock(return_value=response)

    result = await provider.complete(_messages(), tools=None, config=_config())

    assert result.response_type == "message"
    assert result.content == "Here is my answer"
    assert len(result.reasoning_items) == 1
    ri = result.reasoning_items[0]
    assert ri["type"] == "reasoning"
    assert ri["id"] == "r-001"
    assert ri["summary"][0]["text"] == "I thought about this"


@pytest.mark.asyncio
async def test_complete_unknown_output_items_ignored() -> None:
    """Unknown item types (web_search_call, etc.) are silently skipped."""
    provider = _make_provider()

    unknown_item = SimpleNamespace(type="web_search_call", id="ws-001")
    message_item = SimpleNamespace(
        type="message",
        content=[SimpleNamespace(type="output_text", text="result")],
    )
    response = SimpleNamespace(
        output=[unknown_item, message_item],
        model="gpt-5.5",
        usage=_make_usage(),
        id="resp-004",
    )
    _mock_client(provider).responses.create = AsyncMock(return_value=response)

    result = await provider.complete(_messages(), tools=None, config=_config())
    assert result.response_type == "message"
    assert result.content == "result"
