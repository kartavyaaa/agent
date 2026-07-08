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
    content_part = SimpleNamespace(text=text)
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
