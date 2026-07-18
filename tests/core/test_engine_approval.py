"""Unit tests for the engine's approval-flow proposal path."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.engine import CoreEngine
from core.llm.base import LLMResponse, TokenUsage
from core.planner.base import PendingActionProposal, PlannerResult
from core.schemas import CoreRequest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _usage() -> TokenUsage:
    return TokenUsage(input_tokens=1, output_tokens=1, total_tokens=2)


def _message_response(text: str) -> LLMResponse:
    return LLMResponse(
        response_type="message",
        content=text,
        tool_calls=[],
        model="gpt-5.5",
        usage=_usage(),
        raw_response_id="r-001",
    )


def _make_db(has_existing_pending: bool = False) -> MagicMock:
    """Build a mock DB session.

    db.execute is called twice in the approval path:
      1. get_or_create_user (returns a truthy user)
      2. select(PendingAction) (returns existing row or None)
    """
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()

    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = MagicMock()  # user found

    existing_row = MagicMock() if has_existing_pending else None
    pending_result = MagicMock()
    pending_result.scalar_one_or_none.return_value = existing_row

    mock_db.execute = AsyncMock(side_effect=[user_result, pending_result])
    return mock_db


def _make_engine_with_db(mock_db: MagicMock) -> CoreEngine:
    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(return_value=_message_response("hi"))
    mock_llm.embed = AsyncMock(return_value=[[0.0] * 1536])

    mock_registry = MagicMock()
    mock_registry.get_tools_for_llm = MagicMock(return_value=[])

    mock_memory = MagicMock()
    mock_memory.write = AsyncMock(return_value=MagicMock())
    mock_memory.get_recent = AsyncMock(return_value=[])
    mock_memory.semantic_search = AsyncMock(return_value=[])

    mock_settings = MagicMock()
    mock_settings.openai_default_model = "gpt-5.5"
    mock_settings.planner_max_iterations = 8
    mock_settings.planner_default_temperature = None
    mock_settings.default_timezone = "UTC"
    mock_settings.conversation_history_turns = 10
    mock_settings.semantic_recall_enabled = False
    mock_settings.semantic_recall_top_k = 5
    mock_settings.semantic_recall_max_distance = 0.35
    mock_settings.semantic_recall_inject_count = 3
    mock_settings.approval_ttl_minutes = 60

    return CoreEngine(
        llm=mock_llm,
        memory=mock_memory,
        registry=mock_registry,
        session_factory=mock_factory,
        settings=mock_settings,
    )


def _pending_action_proposal() -> PendingActionProposal:
    return PendingActionProposal(
        action_type="dummy_confirm_action",
        action_payload={"message": "hi"},
        preview_text="I'd like to run 'dummy_confirm_action' with these parameters: {'message': 'hi'}",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proposal_path_writes_pending_row_and_returns_proposal() -> None:
    mock_db = _make_db(has_existing_pending=False)
    engine = _make_engine_with_db(mock_db)

    proposal = _pending_action_proposal()
    plan_result = PlannerResult(
        content="",
        tool_calls_made=["dummy_confirm_action"],
        iterations=1,
        pending_action=proposal,
    )

    with patch("core.planner.react.ReActPlanner.run", new=AsyncMock(return_value=plan_result)):
        response = await engine.handle_request(
            CoreRequest(user_id=uuid.uuid4(), content="run the dummy thing")
        )

    assert response.proposal is not None
    assert response.proposal.preview_text == proposal.preview_text
    assert isinstance(response.proposal.pending_action_id, uuid.UUID)
    # PendingAction row was added to the DB
    mock_db.add.assert_called_once()
    added_obj = mock_db.add.call_args[0][0]
    assert added_obj.action_type == "dummy_confirm_action"
    assert added_obj.action_payload == {"message": "hi"}
    assert added_obj.status == "pending"


@pytest.mark.asyncio
async def test_proposal_path_cancels_existing_pending_before_insert() -> None:
    mock_db = _make_db(has_existing_pending=True)
    engine = _make_engine_with_db(mock_db)

    plan_result = PlannerResult(
        content="",
        tool_calls_made=["dummy_confirm_action"],
        iterations=1,
        pending_action=_pending_action_proposal(),
    )

    with patch("core.planner.react.ReActPlanner.run", new=AsyncMock(return_value=plan_result)):
        response = await engine.handle_request(
            CoreRequest(user_id=uuid.uuid4(), content="do it again")
        )

    # flush called twice: once for cancel, once for the new row
    assert mock_db.flush.call_count == 2
    assert response.proposal is not None


@pytest.mark.asyncio
async def test_proposal_path_does_not_write_episodic_memory() -> None:
    mock_db = _make_db(has_existing_pending=False)
    engine = _make_engine_with_db(mock_db)

    plan_result = PlannerResult(
        content="",
        tool_calls_made=["dummy_confirm_action"],
        iterations=1,
        pending_action=_pending_action_proposal(),
    )

    with patch("core.planner.react.ReActPlanner.run", new=AsyncMock(return_value=plan_result)):
        await engine.handle_request(CoreRequest(user_id=uuid.uuid4(), content="do the thing"))

    engine._memory.write.assert_not_called()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_normal_path_unchanged_when_no_pending_action() -> None:
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()
    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = MagicMock()
    mock_db.execute = AsyncMock(return_value=user_result)

    engine = _make_engine_with_db(mock_db)

    plan_result = PlannerResult(
        content="all done",
        tool_calls_made=[],
        iterations=1,
        pending_action=None,
    )

    with patch("core.planner.react.ReActPlanner.run", new=AsyncMock(return_value=plan_result)):
        response = await engine.handle_request(CoreRequest(user_id=uuid.uuid4(), content="hello"))

    assert response.proposal is None
    assert response.content == "all done"
    assert response.memories_written == 1
    engine._memory.write.assert_called_once()  # type: ignore[attr-defined]
