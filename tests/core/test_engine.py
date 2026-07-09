"""Unit tests for CoreEngine.

LLM, registry, memory, and DB session are all mocked.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.engine import CoreEngine
from core.llm.base import LLMMessage, LLMResponse, LLMToolCall, TokenUsage
from core.schemas import CoreRequest, CoreResponse

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _usage() -> TokenUsage:
    return TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15)


def _message_response(text: str) -> LLMResponse:
    return LLMResponse(
        response_type="message",
        content=text,
        tool_calls=[],
        model="gpt-5.5",
        usage=_usage(),
        raw_response_id="r-001",
    )


def _tool_call_response(tool_name: str, args: dict) -> LLMResponse:  # type: ignore[type-arg]
    return LLMResponse(
        response_type="tool_calls",
        content=None,
        tool_calls=[LLMToolCall(id="call-1", name=tool_name, arguments=args)],
        model="gpt-5.5",
        usage=_usage(),
        raw_response_id="r-002",
    )


def _make_engine(
    llm_response: LLMResponse | None = None,
    registry_output: dict | None = None,  # type: ignore[type-arg]
) -> tuple[CoreEngine, MagicMock, MagicMock, MagicMock]:
    """Return (engine, mock_db, mock_llm, mock_registry)."""
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()

    # session_factory is an async context manager that yields mock_db
    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_llm = MagicMock()
    if llm_response and llm_response.response_type == "tool_calls":
        synthesis = _message_response("Reminder set for 2026-07-08 09:00 UTC")
        mock_llm.complete = AsyncMock(side_effect=[llm_response, synthesis])
    else:
        mock_llm.complete = AsyncMock(return_value=llm_response or _message_response("hello"))
    mock_llm.embed = AsyncMock(return_value=[[0.0] * 1536])

    mock_registry = MagicMock()
    mock_registry.get_tools_for_llm = MagicMock(return_value=[])
    mock_registry.execute = AsyncMock(
        return_value=registry_output
        or {"confirmation": "Reminder set for 2026-07-08 09:00 UTC", "message": "call Bob"}
    )

    mock_memory = MagicMock()
    mock_memory.write = AsyncMock(return_value=MagicMock())

    mock_settings = MagicMock()
    mock_settings.openai_default_model = "gpt-5.5"
    mock_settings.planner_max_iterations = 8
    mock_settings.planner_default_temperature = 0.7

    engine = CoreEngine(
        llm=mock_llm,
        memory=mock_memory,
        registry=mock_registry,
        session_factory=mock_factory,
        settings=mock_settings,
    )
    return engine, mock_db, mock_llm, mock_registry


# ---------------------------------------------------------------------------
# handle_request — message path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_request_direct_message() -> None:
    engine, mock_db, mock_llm, _ = _make_engine(
        llm_response=_message_response("Sure, the capital of France is Paris.")
    )

    result = await engine.handle_request(
        CoreRequest(user_id=uuid.uuid4(), content="What is the capital of France?")
    )

    assert isinstance(result, CoreResponse)
    assert result.content == "Sure, the capital of France is Paris."
    assert result.tool_calls_made == []
    assert result.memories_written == 1


@pytest.mark.asyncio
async def test_handle_request_commits_on_success() -> None:
    engine, mock_db, _, _ = _make_engine()

    await engine.handle_request(CoreRequest(user_id=uuid.uuid4(), content="hello"))

    mock_db.commit.assert_called_once()
    mock_db.rollback.assert_not_called()


# ---------------------------------------------------------------------------
# handle_request — tool call path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_request_tool_call() -> None:
    engine, mock_db, _, mock_registry = _make_engine(
        llm_response=_tool_call_response(
            "create_reminder",
            {"message": "call Bob", "remind_at": "2026-07-08T09:00:00Z"},
        ),
        registry_output={
            "reminder_id": str(uuid.uuid4()),
            "message": "call Bob",
            "remind_at": "2026-07-08T09:00:00Z",
            "confirmation": "Reminder set for 2026-07-08 09:00 UTC",
        },
    )

    result = await engine.handle_request(
        CoreRequest(user_id=uuid.uuid4(), content="remind me tomorrow to call Bob")
    )

    assert result.tool_calls_made == ["create_reminder"]
    assert result.content == "Reminder set for 2026-07-08 09:00 UTC"
    mock_registry.execute.assert_called_once()


@pytest.mark.asyncio
async def test_handle_request_registry_receives_user_id() -> None:
    uid = uuid.uuid4()
    engine, _, _, mock_registry = _make_engine(
        llm_response=_tool_call_response(
            "create_reminder", {"message": "x", "remind_at": "2026-07-08T09:00:00Z"}
        ),
        registry_output={"confirmation": "ok", "message": "x"},
    )

    await engine.handle_request(CoreRequest(user_id=uid, content="set reminder"))

    call_kwargs = mock_registry.execute.call_args.kwargs
    assert call_kwargs["user_id"] == uid


# ---------------------------------------------------------------------------
# handle_request — error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_request_rolls_back_on_error() -> None:
    engine, mock_db, mock_llm, _ = _make_engine()
    mock_llm.complete = AsyncMock(side_effect=RuntimeError("boom"))

    with pytest.raises(RuntimeError, match="boom"):
        await engine.handle_request(CoreRequest(user_id=uuid.uuid4(), content="hello"))

    mock_db.rollback.assert_called_once()
    mock_db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_handle_request_no_commit_on_error() -> None:
    engine, mock_db, _, mock_registry = _make_engine(
        llm_response=_tool_call_response("create_reminder", {}),
    )
    mock_registry.execute = AsyncMock(side_effect=ValueError("bad args"))

    with pytest.raises(ValueError):
        await engine.handle_request(CoreRequest(user_id=uuid.uuid4(), content="set reminder"))

    mock_db.commit.assert_not_called()
    mock_db.rollback.assert_called_once()


# ---------------------------------------------------------------------------
# System prompt includes UTC time
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_system_prompt_contains_utc_time() -> None:
    engine, _, mock_llm, _ = _make_engine()

    await engine.handle_request(CoreRequest(user_id=uuid.uuid4(), content="hello"))

    call_args = mock_llm.complete.call_args
    messages: list[LLMMessage] = call_args[1]["messages"] if call_args[1] else call_args[0][0]
    system_msg = next(m for m in messages if m.role == "system")
    # System prompt must include a UTC timestamp so relative times resolve
    assert "UTC" in system_msg.content


# ---------------------------------------------------------------------------
# Memory write
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_written_after_response() -> None:
    engine, _, _, _ = _make_engine(llm_response=_message_response("hi"))
    # engine._memory is already a MagicMock with write=AsyncMock from _make_engine
    mock_memory: MagicMock = engine._memory  # type: ignore[assignment]

    result = await engine.handle_request(CoreRequest(user_id=uuid.uuid4(), content="say hi"))

    mock_memory.write.assert_called_once()
    assert result.memories_written == 1
