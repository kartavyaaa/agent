"""Tests for the draft_block injection in CoreEngine._process().

The draft_block is appended to system_msg.content when the user has an active
draft ContentPlan. This ensures the LLM knows to call edit/approve/discard.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.engine import CoreEngine
from core.llm.base import LLMMessage, LLMResponse, TokenUsage
from core.schemas import CoreRequest

# ---------------------------------------------------------------------------
# Helpers mirrored from test_engine.py
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


def _make_engine_with_db_exec(
    exec_side_effect: list[Any],
) -> tuple[CoreEngine, MagicMock, MagicMock]:
    """Return (engine, mock_db, mock_llm).

    exec_side_effect is a list of return values for successive db.execute() calls.
    Order:
    - Call 1: get_or_create_user (returns a truthy user)
    - Call 2: draft_block ContentPlan query
    """
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=exec_side_effect)

    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(return_value=_message_response("hello"))
    mock_llm.embed = AsyncMock(return_value=[[0.0] * 1536])

    mock_registry = MagicMock()
    mock_registry.get_tools_for_llm = MagicMock(return_value=[])

    mock_memory = MagicMock()
    mock_memory.write = AsyncMock(return_value=MagicMock())
    mock_memory.get_recent = AsyncMock(return_value=[])
    mock_memory.semantic_search = AsyncMock(return_value=[])

    mock_settings = MagicMock()
    mock_settings.openai_default_model = "gpt-5.4"
    mock_settings.planner_max_iterations = 8
    mock_settings.planner_default_temperature = None
    mock_settings.default_timezone = "UTC"
    mock_settings.conversation_history_turns = 10
    mock_settings.semantic_recall_enabled = False
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
    return engine, mock_db, mock_llm


def _user_exec_result() -> MagicMock:
    """Simulate get_or_create_user finding an existing user."""
    r = MagicMock()
    r.scalar_one_or_none.return_value = MagicMock()  # truthy
    return r


def _draft_exec_result(draft_plan: MagicMock | None) -> MagicMock:
    r = MagicMock()
    r.scalar_one_or_none.return_value = draft_plan
    return r


def _make_draft_plan(plan_id: uuid.UUID, items: list[dict[str, Any]]) -> MagicMock:
    plan = MagicMock()
    plan.id = plan_id
    plan.status = "draft"
    plan.items = items
    return plan


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_draft_block_injected_when_active_draft() -> None:
    """system_msg.content contains plan_id and item count when draft exists."""
    plan_id = uuid.uuid4()
    items = [
        {"image_indices": [0], "caption": "Test post", "scheduled_for": "2026-07-28T18:00:00"},
        {"image_indices": [1, 2], "caption": "Carousel", "scheduled_for": "2026-07-29T18:00:00"},
    ]
    draft = _make_draft_plan(plan_id, items)

    engine, _, mock_llm = _make_engine_with_db_exec(
        exec_side_effect=[
            _user_exec_result(),
            _draft_exec_result(draft),
        ]
    )

    await engine.handle_request(CoreRequest(user_id=uuid.uuid4(), content="What should I do?"))

    # Inspect the system_msg passed to llm.complete
    call_args = mock_llm.complete.call_args
    messages: list[LLMMessage] = call_args.kwargs.get("messages") or call_args.args[0]
    system_content = next(m.content for m in messages if m.role == "system")

    assert str(plan_id) in system_content
    assert "2 item" in system_content
    assert "edit_draft_plan" in system_content
    assert "approve_draft_plan" in system_content
    assert "discard_draft_plan" in system_content


@pytest.mark.asyncio
async def test_no_draft_block_when_no_active_draft() -> None:
    """system_msg.content has no draft instructions when no active draft."""
    engine, _, mock_llm = _make_engine_with_db_exec(
        exec_side_effect=[
            _user_exec_result(),
            _draft_exec_result(None),  # No draft plan
        ]
    )

    await engine.handle_request(CoreRequest(user_id=uuid.uuid4(), content="Hello"))

    call_args = mock_llm.complete.call_args
    messages: list[LLMMessage] = call_args.kwargs.get("messages") or call_args.args[0]
    system_content = next(m.content for m in messages if m.role == "system")

    assert "draft content plan" not in system_content
    assert "edit_draft_plan" not in system_content


@pytest.mark.asyncio
async def test_no_draft_block_when_plan_has_no_items() -> None:
    """system_msg.content has no draft block when draft plan exists but items is empty/None."""
    plan_id = uuid.uuid4()
    draft = _make_draft_plan(plan_id, items=[])  # empty items

    engine, _, mock_llm = _make_engine_with_db_exec(
        exec_side_effect=[
            _user_exec_result(),
            _draft_exec_result(draft),
        ]
    )

    await engine.handle_request(CoreRequest(user_id=uuid.uuid4(), content="Hello"))

    call_args = mock_llm.complete.call_args
    messages: list[LLMMessage] = call_args.kwargs.get("messages") or call_args.args[0]
    system_content = next(m.content for m in messages if m.role == "system")

    assert "draft content plan" not in system_content
