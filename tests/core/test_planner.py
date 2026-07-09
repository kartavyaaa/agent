"""Unit tests for ReActPlanner.

LLM and registry are fully mocked. DB session is a MagicMock passed through.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.exceptions import PlannerMaxIterationsError, PlannerStuckLoopError
from core.llm.base import LLMConfig, LLMMessage, LLMResponse, LLMToolCall, TokenUsage
from core.planner.react import ReActPlanner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _tool_response(
    *calls: tuple[str, dict],  # type: ignore[type-arg]
    resp_id: str = "r-tool",
) -> LLMResponse:
    return LLMResponse(
        response_type="tool_calls",
        content=None,
        tool_calls=[
            LLMToolCall(id=f"call-{i}", name=name, arguments=args)
            for i, (name, args) in enumerate(calls)
        ],
        model="gpt-5.5",
        usage=_usage(),
        raw_response_id=resp_id,
    )


def _make_planner(
    llm_responses: list[LLMResponse],
    registry_outputs: list[dict] | None = None,  # type: ignore[type-arg]
    max_iterations: int = 8,
) -> tuple[ReActPlanner, MagicMock, MagicMock, MagicMock]:
    """Return (planner, mock_db, mock_llm, mock_registry)."""
    mock_db = MagicMock()

    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(side_effect=llm_responses)

    mock_registry = MagicMock()
    mock_registry.execute = AsyncMock(
        side_effect=registry_outputs or [{"confirmation": "ok"} for _ in range(20)]
    )

    planner = ReActPlanner(
        llm=mock_llm,
        registry=mock_registry,
        config=LLMConfig(model="gpt-5.5"),
        max_iterations=max_iterations,
    )
    return planner, mock_db, mock_llm, mock_registry


# ---------------------------------------------------------------------------
# Basic paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_direct_message_no_tools() -> None:
    planner, mock_db, mock_llm, mock_registry = _make_planner(
        [_message_response("Hello!")],
    )
    result = await planner.run(
        messages=[LLMMessage(role="user", content="hi")],
        tools=[],
        user_id=uuid.uuid4(),
        db=mock_db,
    )
    assert result.content == "Hello!"
    assert result.tool_calls_made == []
    assert result.iterations == 1
    mock_registry.execute.assert_not_called()


@pytest.mark.asyncio
async def test_single_tool_call_then_message() -> None:
    planner, mock_db, _, mock_registry = _make_planner(
        [
            _tool_response(
                ("create_reminder", {"message": "x", "remind_at": "2026-07-09T09:00:00Z"})
            ),
            _message_response("Reminder set."),
        ],
    )
    result = await planner.run(
        messages=[LLMMessage(role="user", content="remind me")],
        tools=[],
        user_id=uuid.uuid4(),
        db=mock_db,
    )
    assert result.tool_calls_made == ["create_reminder"]
    assert result.iterations == 2
    mock_registry.execute.assert_called_once()


# ---------------------------------------------------------------------------
# Multi-tool accumulation fix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_tool_accumulation_all_results_survive() -> None:
    """Both tools in one LLM turn must both be executed — not last-write-wins."""
    planner, mock_db, _, mock_registry = _make_planner(
        [
            _tool_response(
                ("tool_a", {"x": 1}),
                ("tool_b", {"y": 2}),
            ),
            _message_response("Both done."),
        ],
        registry_outputs=[{"confirmation": "a ok"}, {"confirmation": "b ok"}],
    )
    result = await planner.run(
        messages=[LLMMessage(role="user", content="do both")],
        tools=[],
        user_id=uuid.uuid4(),
        db=mock_db,
    )
    assert mock_registry.execute.call_count == 2
    assert result.tool_calls_made == ["tool_a", "tool_b"]


@pytest.mark.asyncio
async def test_tool_result_messages_appended_to_history() -> None:
    """The second LLM call must receive tool_result messages in history."""
    planner, mock_db, mock_llm, _ = _make_planner(
        [
            _tool_response(("my_tool", {"val": "hello"})),
            _message_response("Done."),
        ],
    )
    await planner.run(
        messages=[LLMMessage(role="user", content="go")],
        tools=[],
        user_id=uuid.uuid4(),
        db=mock_db,
    )
    second_call_messages: list[LLMMessage] = mock_llm.complete.call_args_list[1].kwargs["messages"]
    roles = [m.role for m in second_call_messages]
    assert "tool_result" in roles


@pytest.mark.asyncio
async def test_assistant_message_carries_tool_calls() -> None:
    """The second LLM call must see an assistant message with tool_calls set."""
    planner, mock_db, mock_llm, _ = _make_planner(
        [
            _tool_response(("my_tool", {"val": "hello"})),
            _message_response("Done."),
        ],
    )
    await planner.run(
        messages=[LLMMessage(role="user", content="go")],
        tools=[],
        user_id=uuid.uuid4(),
        db=mock_db,
    )
    second_call_messages: list[LLMMessage] = mock_llm.complete.call_args_list[1].kwargs["messages"]
    assistant_msgs = [m for m in second_call_messages if m.role == "assistant"]
    assert assistant_msgs, "no assistant message in history for second LLM call"
    assert assistant_msgs[0].tool_calls is not None and len(assistant_msgs[0].tool_calls) > 0


# ---------------------------------------------------------------------------
# Iteration cap and stuck loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_iterations_raises() -> None:
    planner, mock_db, _, _ = _make_planner(
        # 4 tool responses — more than max_iterations=3
        [_tool_response(("t", {"i": i})) for i in range(4)],
        max_iterations=3,
    )
    with pytest.raises(PlannerMaxIterationsError):
        await planner.run(
            messages=[LLMMessage(role="user", content="go")],
            tools=[],
            user_id=uuid.uuid4(),
            db=mock_db,
        )


@pytest.mark.asyncio
async def test_stuck_loop_raises() -> None:
    """Same tool name + same args on consecutive iterations → PlannerStuckLoopError."""
    same_call = _tool_response(("t", {"x": 1}))
    planner, mock_db, _, _ = _make_planner([same_call, same_call, same_call])
    with pytest.raises(PlannerStuckLoopError):
        await planner.run(
            messages=[LLMMessage(role="user", content="go")],
            tools=[],
            user_id=uuid.uuid4(),
            db=mock_db,
        )


@pytest.mark.asyncio
async def test_stuck_loop_different_args_no_raise() -> None:
    """Same tool name but different args must NOT raise."""
    planner, mock_db, _, _ = _make_planner(
        [
            _tool_response(("t", {"x": 1})),
            _tool_response(("t", {"x": 2})),
            _message_response("Done."),
        ],
    )
    result = await planner.run(
        messages=[LLMMessage(role="user", content="go")],
        tools=[],
        user_id=uuid.uuid4(),
        db=mock_db,
    )
    assert result.iterations == 3


@pytest.mark.asyncio
async def test_stuck_loop_different_tool_name_no_raise() -> None:
    """Different tool names on consecutive turns must NOT raise."""
    planner, mock_db, _, _ = _make_planner(
        [
            _tool_response(("tool_a", {"x": 1})),
            _tool_response(("tool_b", {"x": 1})),
            _message_response("Done."),
        ],
    )
    result = await planner.run(
        messages=[LLMMessage(role="user", content="go")],
        tools=[],
        user_id=uuid.uuid4(),
        db=mock_db,
    )
    assert result.iterations == 3


@pytest.mark.asyncio
async def test_stuck_loop_nested_args_no_crash() -> None:
    """Nested dict argument values must not crash the signature computation."""
    nested = _tool_response(("t", {"opts": {"a": 1, "b": [1, 2]}}))
    same_nested = _tool_response(("t", {"opts": {"a": 1, "b": [1, 2]}}))
    planner, mock_db, _, _ = _make_planner([nested, same_nested, same_nested])
    with pytest.raises(PlannerStuckLoopError):
        await planner.run(
            messages=[LLMMessage(role="user", content="go")],
            tools=[],
            user_id=uuid.uuid4(),
            db=mock_db,
        )


# ---------------------------------------------------------------------------
# Trusted context injection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_id_injected_into_registry_execute() -> None:
    uid = uuid.uuid4()
    planner, mock_db, _, mock_registry = _make_planner(
        [
            _tool_response(("my_tool", {"v": "x"})),
            _message_response("Done."),
        ],
    )
    await planner.run(
        messages=[LLMMessage(role="user", content="go")],
        tools=[],
        user_id=uid,
        db=mock_db,
    )
    call_kwargs = mock_registry.execute.call_args.kwargs
    assert call_kwargs["user_id"] == uid


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_tool_list_still_works() -> None:
    planner, mock_db, _, _ = _make_planner([_message_response("Hi!")])
    result = await planner.run(
        messages=[LLMMessage(role="user", content="hello")],
        tools=[],
        user_id=uuid.uuid4(),
        db=mock_db,
    )
    assert result.content == "Hi!"


@pytest.mark.asyncio
async def test_multi_turn_three_iterations() -> None:
    """Two tool calls on separate turns, then a final answer — 3 iterations total."""
    planner, mock_db, _, mock_registry = _make_planner(
        [
            _tool_response(("tool_a", {"x": 1})),
            _tool_response(("tool_b", {"y": 2})),
            _message_response("All done."),
        ],
        registry_outputs=[{"confirmation": "a"}, {"confirmation": "b"}],
    )
    result = await planner.run(
        messages=[LLMMessage(role="user", content="go")],
        tools=[],
        user_id=uuid.uuid4(),
        db=mock_db,
    )
    assert result.iterations == 3
    assert result.tool_calls_made == ["tool_a", "tool_b"]
    assert mock_registry.execute.call_count == 2
