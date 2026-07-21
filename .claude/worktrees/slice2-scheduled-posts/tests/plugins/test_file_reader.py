"""Unit tests for FileReaderPlugin.

LocalFsClient and LLMProvider are mocked — no real filesystem or LLM calls.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.exceptions import SandboxViolationError
from core.llm.base import LLMConfig, LLMMessage, LLMResponse, LLMToolCall, TokenUsage
from core.planner.react import ReActPlanner
from core.tools.registry import ToolRegistry
from integrations.local_fs import FileReadResult
from plugins.file_reader.plugin import _SUMMARIZE_THRESHOLD, FileReaderPlugin
from plugins.file_reader.schemas import FileReaderInput, FileReaderOutput

_FAST_MODEL = "gpt-5.4-nano"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(
    content: str = "hello",
    *,
    size: int | None = None,
    path: str = "test.txt",
    health: bool = True,
    side_effect: Exception | None = None,
) -> MagicMock:
    client = MagicMock()
    if side_effect is not None:
        client.read = AsyncMock(side_effect=side_effect)
    else:
        resolved_size = size if size is not None else len(content.encode())
        client.read = AsyncMock(
            return_value=FileReadResult(path=path, content=content, size_bytes=resolved_size)
        )
    client.health_check = AsyncMock(return_value=health)
    return client


def _make_llm(summary: str = "Summary.") -> MagicMock:
    llm = MagicMock()
    llm.complete = AsyncMock(
        return_value=LLMResponse(
            response_type="message",
            content=summary,
            tool_calls=[],
            model=_FAST_MODEL,
            usage=TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15),
            raw_response_id="r-1",
        )
    )
    return llm


def _make_db() -> MagicMock:
    return MagicMock()


def _make_plugin(
    content: str = "hello",
    *,
    size: int | None = None,
    health: bool = True,
    client_side_effect: Exception | None = None,
    llm_summary: str = "Summary.",
) -> tuple[FileReaderPlugin, MagicMock, MagicMock]:
    client = _make_client(content=content, size=size, health=health, side_effect=client_side_effect)
    llm = _make_llm(summary=llm_summary)
    plugin = FileReaderPlugin(client=client, llm=llm, fast_model=_FAST_MODEL)
    return plugin, client, llm


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


def test_file_reader_input_has_no_user_id() -> None:
    assert "user_id" not in FileReaderInput.model_fields


def test_file_reader_input_fields() -> None:
    assert set(FileReaderInput.model_fields.keys()) == {"path", "summarize"}


def test_file_reader_output_fields() -> None:
    assert set(FileReaderOutput.model_fields.keys()) == {
        "path",
        "summary",
        "size_bytes",
        "summarized",
    }


def test_summarize_defaults_true() -> None:
    inp = FileReaderInput(path="foo.txt")
    assert inp.summarize is True


# ---------------------------------------------------------------------------
# execute() — short content (no LLM call)
# ---------------------------------------------------------------------------


async def test_execute_short_content_no_llm_call() -> None:
    short = "x" * (_SUMMARIZE_THRESHOLD - 1)
    plugin, client, llm = _make_plugin(content=short)

    result = await plugin.execute(
        FileReaderInput(path="short.txt", summarize=True),
        user_id=uuid.uuid4(),
        db=_make_db(),
    )

    assert isinstance(result, FileReaderOutput)
    assert result.summary == short
    assert result.summarized is False
    llm.complete.assert_not_called()


# ---------------------------------------------------------------------------
# execute() — long content with summarize=True (LLM called)
# ---------------------------------------------------------------------------


async def test_execute_long_content_calls_llm() -> None:
    long_content = "word " * 200  # well over threshold
    plugin, client, llm = _make_plugin(content=long_content, llm_summary="Concise summary.")

    result = await plugin.execute(
        FileReaderInput(path="long.txt", summarize=True),
        user_id=uuid.uuid4(),
        db=_make_db(),
    )

    assert result.summarized is True
    assert result.summary == "Concise summary."
    llm.complete.assert_called_once()
    call_kwargs = llm.complete.call_args
    config: LLMConfig = call_kwargs.kwargs["config"]
    assert config.model == _FAST_MODEL
    assert call_kwargs.kwargs["tools"] is None


async def test_execute_long_content_llm_message_contains_path_and_content() -> None:
    long_content = "a" * (_SUMMARIZE_THRESHOLD + 1)
    client = _make_client(content=long_content, path="doc.txt")
    llm = _make_llm()
    plugin = FileReaderPlugin(client=client, llm=llm, fast_model=_FAST_MODEL)

    await plugin.execute(
        FileReaderInput(path="doc.txt", summarize=True),
        user_id=uuid.uuid4(),
        db=_make_db(),
    )

    messages: list[LLMMessage] = llm.complete.call_args.kwargs["messages"]
    assert len(messages) == 1
    assert "doc.txt" in messages[0].content
    assert long_content in messages[0].content


# ---------------------------------------------------------------------------
# execute() — summarize=False skips LLM regardless of length
# ---------------------------------------------------------------------------


async def test_execute_summarize_false_skips_llm() -> None:
    long_content = "y" * (_SUMMARIZE_THRESHOLD + 1)
    plugin, client, llm = _make_plugin(content=long_content)

    result = await plugin.execute(
        FileReaderInput(path="raw.txt", summarize=False),
        user_id=uuid.uuid4(),
        db=_make_db(),
    )

    assert result.summarized is False
    assert result.summary == long_content
    llm.complete.assert_not_called()


# ---------------------------------------------------------------------------
# execute() — error propagation
# ---------------------------------------------------------------------------


async def test_execute_propagates_sandbox_violation() -> None:
    plugin, _, _ = _make_plugin(client_side_effect=SandboxViolationError("escape"))

    with pytest.raises(SandboxViolationError):
        await plugin.execute(
            FileReaderInput(path="../secret"),
            user_id=uuid.uuid4(),
            db=_make_db(),
        )


# ---------------------------------------------------------------------------
# health_check()
# ---------------------------------------------------------------------------


async def test_health_check_healthy() -> None:
    plugin, client, _ = _make_plugin(health=True)
    status = await plugin.health_check()
    client.health_check.assert_called_once()
    assert status.status == "healthy"


async def test_health_check_unhealthy() -> None:
    plugin, _, _ = _make_plugin(health=False)
    status = await plugin.health_check()
    assert status.status == "unhealthy"


# ---------------------------------------------------------------------------
# Planner integration — mocked LLM + mocked LocalFsClient
# ---------------------------------------------------------------------------


async def test_planner_calls_read_file_and_synthesizes() -> None:
    file_content = "This is the content of my notes file."
    fs_client = _make_client(content=file_content, path="notes.txt")

    plugin = FileReaderPlugin(client=fs_client, llm=MagicMock(), fast_model=_FAST_MODEL)

    registry = ToolRegistry()
    registry.register(plugin)

    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(
        side_effect=[
            _tool_response(("read_file", {"path": "notes.txt", "summarize": False})),
            _message_response("Here is what your notes say."),
        ]
    )

    planner = ReActPlanner(
        llm=mock_llm,
        registry=registry,
        config=LLMConfig(model="gpt-5.5"),
        max_iterations=8,
    )

    result = await planner.run(
        messages=[LLMMessage(role="user", content="What is in my notes.txt?")],
        tools=registry.get_tools_for_llm(),
        user_id=uuid.uuid4(),
        db=_make_db(),
    )

    assert "read_file" in result.tool_calls_made
    assert result.iterations == 2
    assert result.content == "Here is what your notes say."
    fs_client.read.assert_called_once_with("notes.txt")
