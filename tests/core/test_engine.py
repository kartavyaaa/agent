"""Unit tests for CoreEngine.

LLM, registry, memory, and DB session are all mocked.
"""

from __future__ import annotations

import re
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.engine import CoreEngine
from core.llm.base import LLMMessage, LLMResponse, LLMToolCall, TokenUsage
from core.memory.manager import ScoredMemory
from core.schemas import CoreRequest, CoreResponse, ImageAttachment

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
) -> tuple[CoreEngine, MagicMock, MagicMock, MagicMock, MagicMock]:
    """Return (engine, mock_db, mock_llm, mock_registry, mock_memory)."""
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()
    # get_or_create_user calls await db.execute(...) — return an existing user so no insert fires
    _mock_exec_result = MagicMock()
    _mock_exec_result.scalar_one_or_none.return_value = MagicMock()  # truthy = user found
    mock_db.execute = AsyncMock(return_value=_mock_exec_result)

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
    mock_memory.get_recent = AsyncMock(return_value=[])
    mock_memory.semantic_search = AsyncMock(return_value=[])

    mock_settings = MagicMock()
    mock_settings.openai_default_model = "gpt-5.4"
    mock_settings.planner_max_iterations = 8
    mock_settings.planner_default_temperature = None
    mock_settings.default_timezone = "Asia/Kolkata"
    mock_settings.conversation_history_turns = 10
    mock_settings.semantic_recall_enabled = True
    mock_settings.semantic_recall_top_k = 5
    mock_settings.semantic_recall_max_distance = 0.35
    mock_settings.semantic_recall_inject_count = 3

    engine = CoreEngine(
        llm=mock_llm,
        memory=mock_memory,
        registry=mock_registry,
        session_factory=mock_factory,
        settings=mock_settings,
    )
    return engine, mock_db, mock_llm, mock_registry, mock_memory


# ---------------------------------------------------------------------------
# handle_request — message path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_request_direct_message() -> None:
    engine, mock_db, mock_llm, _, _ = _make_engine(
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
    engine, mock_db, _, _, _ = _make_engine()

    await engine.handle_request(CoreRequest(user_id=uuid.uuid4(), content="hello"))

    mock_db.commit.assert_called_once()
    mock_db.rollback.assert_not_called()


# ---------------------------------------------------------------------------
# handle_request — tool call path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_request_tool_call() -> None:
    engine, mock_db, _, mock_registry, _ = _make_engine(
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
    engine, _, _, mock_registry, _ = _make_engine(
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
    engine, mock_db, mock_llm, _, _ = _make_engine()
    mock_llm.complete = AsyncMock(side_effect=RuntimeError("boom"))

    with pytest.raises(RuntimeError, match="boom"):
        await engine.handle_request(CoreRequest(user_id=uuid.uuid4(), content="hello"))

    mock_db.rollback.assert_called_once()
    mock_db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_handle_request_no_commit_on_error() -> None:
    engine, mock_db, _, mock_registry, _ = _make_engine(
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
async def test_system_prompt_contains_timezone_info() -> None:
    engine, _, mock_llm, _, _ = _make_engine()

    await engine.handle_request(CoreRequest(user_id=uuid.uuid4(), content="hello"))

    call_args = mock_llm.complete.call_args
    kwargs = call_args[1] if call_args[1] else {}
    messages: list[LLMMessage] = kwargs["messages"] if kwargs else call_args[0][0]
    system_msg = next(m for m in messages if m.role == "system")
    content: str = system_msg.content  # type: ignore[assignment]
    # System prompt must include the user's timezone name and a local timestamp
    assert "Asia/Kolkata" in content
    # Local time format: "YYYY-MM-DD HH:MM ZZZ"
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} \w+", content)


# ---------------------------------------------------------------------------
# Memory write
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_written_after_response() -> None:
    engine, _, _, _, mock_memory = _make_engine(llm_response=_message_response("hi"))

    result = await engine.handle_request(CoreRequest(user_id=uuid.uuid4(), content="say hi"))

    mock_memory.write.assert_called_once()
    assert result.memories_written == 1


# ---------------------------------------------------------------------------
# Conversation history injection
# ---------------------------------------------------------------------------


def _fake_mem(content: str) -> MagicMock:
    m = MagicMock()
    m.content = content
    return m


@pytest.mark.asyncio
async def test_system_prompt_contains_history_block() -> None:
    engine, _, mock_llm, _, mock_memory = _make_engine()
    mock_memory.get_recent = AsyncMock(
        return_value=[
            _fake_mem("User: hi\nAssistant: hello"),
            _fake_mem("User: my name is Kartavya\nAssistant: Nice to meet you, Kartavya!"),
        ]
    )

    await engine.handle_request(CoreRequest(user_id=uuid.uuid4(), content="what's my name?"))

    call_args = mock_llm.complete.call_args
    kwargs = call_args[1] if call_args[1] else {}
    messages: list[LLMMessage] = kwargs["messages"] if kwargs else call_args[0][0]
    system_content: str = next(m for m in messages if m.role == "system").content  # type: ignore[assignment]
    assert "Recent conversation history (oldest to newest):" in system_content
    assert "User: hi\nAssistant: hello" in system_content
    assert "User: my name is Kartavya\nAssistant: Nice to meet you, Kartavya!" in system_content


@pytest.mark.asyncio
async def test_system_prompt_no_history_block_when_empty() -> None:
    engine, _, mock_llm, _, _ = _make_engine()
    # get_recent returns [] by default in _make_engine

    await engine.handle_request(CoreRequest(user_id=uuid.uuid4(), content="hello"))

    call_args = mock_llm.complete.call_args
    kwargs = call_args[1] if call_args[1] else {}
    messages: list[LLMMessage] = kwargs["messages"] if kwargs else call_args[0][0]
    system_content: str = next(m for m in messages if m.role == "system").content  # type: ignore[assignment]
    assert "Recent conversation history" not in system_content


@pytest.mark.asyncio
async def test_get_recent_called_with_correct_user_and_limit() -> None:
    uid = uuid.uuid4()
    engine, _, _, _, mock_memory = _make_engine()

    await engine.handle_request(CoreRequest(user_id=uid, content="hello"))

    mock_memory.get_recent.assert_called_once()
    call_kwargs = mock_memory.get_recent.call_args.kwargs
    assert call_kwargs["user_id"] == uid
    assert call_kwargs["limit"] == 10


# ---------------------------------------------------------------------------
# Semantic recall injection
# ---------------------------------------------------------------------------


def _scored_mem(content: str, distance: float) -> ScoredMemory:
    m = MagicMock()
    m.content = content
    return ScoredMemory(memory=m, distance=distance)


def _get_system_prompt(mock_llm: MagicMock) -> str:
    call_args = mock_llm.complete.call_args
    kwargs = call_args[1] if call_args[1] else {}
    messages: list[LLMMessage] = kwargs["messages"] if kwargs else call_args[0][0]
    content: str = next(m for m in messages if m.role == "system").content  # type: ignore[assignment]
    return content


@pytest.mark.asyncio
async def test_recall_block_injected_when_below_threshold() -> None:
    engine, _, mock_llm, _, mock_memory = _make_engine()
    mock_memory.semantic_search = AsyncMock(
        return_value=[_scored_mem("User: pottery\nAssistant: nice!", 0.2)]
    )

    await engine.handle_request(CoreRequest(user_id=uuid.uuid4(), content="gift idea?"))

    assert "Relevant past context" in _get_system_prompt(mock_llm)
    assert "User: pottery" in _get_system_prompt(mock_llm)


@pytest.mark.asyncio
async def test_recall_block_absent_when_above_threshold() -> None:
    engine, _, mock_llm, _, mock_memory = _make_engine()
    mock_memory.semantic_search = AsyncMock(
        return_value=[_scored_mem("User: pottery\nAssistant: nice!", 0.5)]
    )

    await engine.handle_request(CoreRequest(user_id=uuid.uuid4(), content="gift idea?"))

    assert "Relevant past context" not in _get_system_prompt(mock_llm)


@pytest.mark.asyncio
async def test_recall_not_called_when_disabled() -> None:
    engine, _, _, _, mock_memory = _make_engine()
    engine._settings.semantic_recall_enabled = False

    await engine.handle_request(CoreRequest(user_id=uuid.uuid4(), content="hello"))

    mock_memory.semantic_search.assert_not_called()


@pytest.mark.asyncio
async def test_recall_dedupes_recent_history() -> None:
    shared_content = "User: pottery\nAssistant: nice!"
    engine, _, mock_llm, _, mock_memory = _make_engine()
    # Same content appears in both recent history and semantic recall
    mock_memory.get_recent = AsyncMock(return_value=[_fake_mem(shared_content)])
    mock_memory.semantic_search = AsyncMock(return_value=[_scored_mem(shared_content, 0.1)])

    await engine.handle_request(CoreRequest(user_id=uuid.uuid4(), content="gift idea?"))

    # Should NOT inject a separate recall block — already in recent history
    assert "Relevant past context" not in _get_system_prompt(mock_llm)


# ---------------------------------------------------------------------------
# Image / vision path
# ---------------------------------------------------------------------------

_FAKE_B64 = "aGVsbG8="  # base64("hello") — stands in for real image bytes
_FAKE_MIME = "image/jpeg"


def _get_user_msg(mock_llm: MagicMock) -> LLMMessage:
    call_args = mock_llm.complete.call_args
    kwargs = call_args[1] if call_args[1] else {}
    messages: list[LLMMessage] = kwargs["messages"] if kwargs else call_args[0][0]
    return next(m for m in messages if m.role == "user")


@pytest.mark.asyncio
async def test_image_request_builds_content_part_list() -> None:
    """When image_base64 is set, the user LLMMessage must carry a content-part list."""
    engine, _, mock_llm, _, _ = _make_engine()

    await engine.handle_request(
        CoreRequest(
            user_id=uuid.uuid4(),
            content="How's the lighting?",
            image_base64=_FAKE_B64,
            image_mime=_FAKE_MIME,
        )
    )

    user_msg = _get_user_msg(mock_llm)
    assert isinstance(user_msg.content, list), "image request must produce a list content"
    types = [part["type"] for part in user_msg.content]
    assert "input_image" in types
    assert "input_text" in types

    image_part = next(p for p in user_msg.content if p["type"] == "input_image")
    assert image_part["image_url"] == f"data:{_FAKE_MIME};base64,{_FAKE_B64}"

    text_part = next(p for p in user_msg.content if p["type"] == "input_text")
    assert text_part["text"] == "How's the lighting?"


@pytest.mark.asyncio
async def test_image_request_semantic_search_receives_string() -> None:
    """Semantic search must receive the string caption, never image bytes."""
    engine, _, _, _, mock_memory = _make_engine()
    caption = "Nice composition here"

    await engine.handle_request(
        CoreRequest(
            user_id=uuid.uuid4(),
            content=caption,
            image_base64=_FAKE_B64,
            image_mime=_FAKE_MIME,
        )
    )

    call_kwargs = mock_memory.semantic_search.call_args.kwargs
    assert call_kwargs["query"] == caption
    assert _FAKE_B64 not in call_kwargs["query"]


@pytest.mark.asyncio
async def test_image_request_memory_write_receives_string() -> None:
    """Episodic memory write must use the string caption, never image bytes."""
    engine, _, _, _, mock_memory = _make_engine(llm_response=_message_response("Great shot!"))
    caption = "Nice composition here"

    await engine.handle_request(
        CoreRequest(
            user_id=uuid.uuid4(),
            content=caption,
            image_base64=_FAKE_B64,
            image_mime=_FAKE_MIME,
        )
    )

    write_kwargs = mock_memory.write.call_args.kwargs
    written_content: str = write_kwargs["content"]
    assert written_content.startswith(f"User: {caption}")
    assert _FAKE_B64 not in written_content


@pytest.mark.asyncio
async def test_text_only_request_unchanged() -> None:
    """Text-only requests must still produce a plain string user message (no list)."""
    engine, _, mock_llm, _, _ = _make_engine()

    await engine.handle_request(CoreRequest(user_id=uuid.uuid4(), content="hello"))

    user_msg = _get_user_msg(mock_llm)
    assert isinstance(user_msg.content, str)
    assert user_msg.content == "hello"


# ---------------------------------------------------------------------------
# Batch image / album path
# ---------------------------------------------------------------------------

_FAKE_IA = ImageAttachment(data=_FAKE_B64, mime=_FAKE_MIME)


@pytest.mark.asyncio
async def test_batch_images_builds_content_part_list() -> None:
    """Batch images build a content-part list: N input_image parts then one input_text."""
    engine, _, mock_llm, _, _ = _make_engine()
    caption = "Plan these for the grid"

    await engine.handle_request(
        CoreRequest(
            user_id=uuid.uuid4(),
            content=caption,
            images=[_FAKE_IA, _FAKE_IA, _FAKE_IA],
        )
    )

    user_msg = _get_user_msg(mock_llm)
    assert isinstance(user_msg.content, list)
    parts = user_msg.content
    assert len(parts) == 4  # 3 images + 1 text

    image_parts = [p for p in parts if p["type"] == "input_image"]
    assert len(image_parts) == 3
    for part in image_parts:
        assert part["image_url"] == f"data:{_FAKE_MIME};base64,{_FAKE_B64}"

    text_parts = [p for p in parts if p["type"] == "input_text"]
    assert len(text_parts) == 1
    assert text_parts[0]["text"] == caption


@pytest.mark.asyncio
async def test_batch_images_detail_high() -> None:
    """Every input_image part in a batch must carry detail='high'."""
    engine, _, mock_llm, _, _ = _make_engine()

    await engine.handle_request(
        CoreRequest(
            user_id=uuid.uuid4(),
            content="content plan",
            images=[_FAKE_IA, _FAKE_IA],
        )
    )

    user_msg = _get_user_msg(mock_llm)
    assert isinstance(user_msg.content, list)
    image_parts = [p for p in user_msg.content if p["type"] == "input_image"]
    for part in image_parts:
        assert part.get("detail") == "high"


@pytest.mark.asyncio
async def test_batch_images_precedence_over_single() -> None:
    """When both images and image_base64 are set, the batch (images) wins."""
    engine, _, mock_llm, _, _ = _make_engine()

    await engine.handle_request(
        CoreRequest(
            user_id=uuid.uuid4(),
            content="plan",
            images=[_FAKE_IA, _FAKE_IA],
            image_base64=_FAKE_B64,
            image_mime=_FAKE_MIME,
        )
    )

    user_msg = _get_user_msg(mock_llm)
    assert isinstance(user_msg.content, list)
    image_parts = [p for p in user_msg.content if p["type"] == "input_image"]
    assert len(image_parts) == 2  # batch wins — not 1 (single)


@pytest.mark.asyncio
async def test_batch_images_semantic_search_receives_string() -> None:
    """Semantic search must receive the string caption, never image bytes."""
    engine, _, _, _, mock_memory = _make_engine()
    caption = "batch caption"

    await engine.handle_request(
        CoreRequest(
            user_id=uuid.uuid4(),
            content=caption,
            images=[_FAKE_IA, _FAKE_IA],
        )
    )

    call_kwargs = mock_memory.semantic_search.call_args.kwargs
    assert call_kwargs["query"] == caption
    assert _FAKE_B64 not in call_kwargs["query"]


@pytest.mark.asyncio
async def test_batch_images_memory_write_receives_string() -> None:
    """Episodic memory write must use the string caption, never image bytes."""
    engine, _, _, _, mock_memory = _make_engine(llm_response=_message_response("Great batch!"))
    caption = "batch caption"

    await engine.handle_request(
        CoreRequest(
            user_id=uuid.uuid4(),
            content=caption,
            images=[_FAKE_IA],
        )
    )

    write_kwargs = mock_memory.write.call_args.kwargs
    written: str = write_kwargs["content"]
    assert written.startswith(f"User: {caption}")
    assert _FAKE_B64 not in written


@pytest.mark.asyncio
async def test_single_image_no_detail_key() -> None:
    """Single-image path must NOT set a detail key (only the batch path does)."""
    engine, _, mock_llm, _, _ = _make_engine()

    await engine.handle_request(
        CoreRequest(
            user_id=uuid.uuid4(),
            content="critique this",
            image_base64=_FAKE_B64,
            image_mime=_FAKE_MIME,
        )
    )

    user_msg = _get_user_msg(mock_llm)
    assert isinstance(user_msg.content, list)
    image_parts = [p for p in user_msg.content if p["type"] == "input_image"]
    assert len(image_parts) == 1
    assert "detail" not in image_parts[0]
