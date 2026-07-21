from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from core.llm.base import LLMConfig, LLMMessage, LLMResponse, LLMToolCall, TokenUsage
from core.planner.react import ReActPlanner


def _tool_call(name: str, args: dict[str, object], call_id: str = "call1") -> LLMToolCall:
    return LLMToolCall(id=call_id, name=name, arguments=args)


_USAGE = TokenUsage(input_tokens=1, output_tokens=1, total_tokens=2)


def _llm_tool_response(tool_calls: list[LLMToolCall]) -> LLMResponse:
    return LLMResponse(
        response_type="tool_calls",
        content=None,
        tool_calls=tool_calls,
        reasoning_items=[],
        model="test-model",
        usage=_USAGE,
        raw_response_id="resp_test",
    )


def _llm_message_response(content: str) -> LLMResponse:
    return LLMResponse(
        response_type="message",
        content=content,
        tool_calls=[],
        reasoning_items=[],
        model="test-model",
        usage=_USAGE,
        raw_response_id="resp_test2",
    )


def _make_planner(llm: AsyncMock, registry: AsyncMock) -> ReActPlanner:
    return ReActPlanner(
        llm=llm,
        registry=registry,
        config=LLMConfig(model="test-model"),
        max_iterations=5,
    )


@pytest.mark.asyncio
async def test_sentinel_halts_loop_and_returns_pending_action() -> None:
    """Approval sentinel from registry causes the loop to return immediately."""
    llm = AsyncMock()
    registry = AsyncMock()

    llm.complete = AsyncMock(
        return_value=_llm_tool_response([_tool_call("approval_tool", {"message": "hi"})])
    )
    registry.execute = AsyncMock(
        return_value={
            "__approval_required__": True,
            "tool": "approval_tool",
            "args": {"message": "hi"},
        }
    )

    planner = _make_planner(llm, registry)
    result = await planner.run(
        messages=[LLMMessage(role="user", content="do the thing")],
        tools=[],
        user_id=uuid.uuid4(),
        db=AsyncMock(),
    )

    assert result.pending_action is not None
    assert result.pending_action.action_type == "approval_tool"
    assert result.pending_action.action_payload == {"message": "hi"}
    assert result.content == ""
    # LLM was called exactly once (one iteration before sentinel fired)
    assert llm.complete.call_count == 1
    # Registry was called once (the sentinel call)
    assert registry.execute.call_count == 1


@pytest.mark.asyncio
async def test_sentinel_mid_batch_discards_remaining_tools() -> None:
    """If sentinel fires on first tool in a batch, second tool is NOT called."""
    llm = AsyncMock()
    registry = AsyncMock()

    tc1 = _tool_call("approval_tool", {"message": "hi"}, call_id="c1")
    tc2 = _tool_call("safe_tool", {}, call_id="c2")

    llm.complete = AsyncMock(return_value=_llm_tool_response([tc1, tc2]))

    sentinel: dict[str, object] = {
        "__approval_required__": True,
        "tool": "approval_tool",
        "args": {"message": "hi"},
    }
    safe_result: dict[str, object] = {"result": "done", "confirmation": "done"}

    async def registry_execute(
        name: str, args: dict[str, object], **kwargs: object
    ) -> dict[str, object]:
        if name == "approval_tool":
            return sentinel
        return safe_result

    registry.execute = AsyncMock(side_effect=registry_execute)

    planner = _make_planner(llm, registry)
    result = await planner.run(
        messages=[LLMMessage(role="user", content="do things")],
        tools=[],
        user_id=uuid.uuid4(),
        db=AsyncMock(),
    )

    assert result.pending_action is not None
    assert result.pending_action.action_type == "approval_tool"
    # safe_tool was never called
    called_names = [call.args[0] for call in registry.execute.call_args_list]
    assert "safe_tool" not in called_names


@pytest.mark.asyncio
async def test_non_sentinel_result_continues_loop() -> None:
    """Normal (non-sentinel) tool result is appended to history; loop continues."""
    llm = AsyncMock()
    registry = AsyncMock()

    llm.complete = AsyncMock(
        side_effect=[
            _llm_tool_response([_tool_call("safe_tool", {})]),
            _llm_message_response("all done"),
        ]
    )
    registry.execute = AsyncMock(return_value={"result": "done", "confirmation": "done"})

    planner = _make_planner(llm, registry)
    result = await planner.run(
        messages=[LLMMessage(role="user", content="do it")],
        tools=[],
        user_id=uuid.uuid4(),
        db=AsyncMock(),
    )

    assert result.pending_action is None
    assert result.content == "all done"
    assert llm.complete.call_count == 2
