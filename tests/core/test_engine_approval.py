"""Unit tests for the engine's approval-flow proposal path."""

from __future__ import annotations

import base64
import uuid
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.engine import CoreEngine
from core.llm.base import LLMResponse, TokenUsage
from core.planner.base import PendingActionProposal, PlannerResult
from core.schemas import CoreRequest
from plugins.base import HealthStatus, PluginBase

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


def _make_r2_client(url: str = "https://cdn.example.com/img.jpg") -> MagicMock:
    r2 = MagicMock()
    r2.upload = AsyncMock(return_value=url)
    return r2


def _make_engine_with_db(
    mock_db: MagicMock,
    *,
    r2: MagicMock | None = None,
    registry: MagicMock | None = None,
) -> CoreEngine:
    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(return_value=_message_response("hi"))
    mock_llm.embed = AsyncMock(return_value=[[0.0] * 1536])

    if registry is None:
        registry = MagicMock()
        registry.get_tools_for_llm = MagicMock(return_value=[])
        registry.get_plugin = MagicMock(return_value=None)  # no needs_hosted_image plugins

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
        registry=registry,
        session_factory=mock_factory,
        settings=mock_settings,
        r2=r2,
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


# ---------------------------------------------------------------------------
# Helpers for needs_hosted_image tests
# ---------------------------------------------------------------------------


class _FakeInput(BaseModel):
    caption: str


class _FakeOutput(BaseModel):
    result: str
    confirmation: str = "ok"


class _FakeConfig(BaseModel):
    pass


class _ImagePlugin(PluginBase):
    """Test plugin with needs_hosted_image=True."""

    name: ClassVar[str] = "image_plugin"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Image plugin"
    capabilities: ClassVar[list[str]] = []
    permissions: ClassVar[list[str]] = []
    dependencies: ClassVar[list[str]] = []
    input_schema = _FakeInput
    output_schema = _FakeOutput
    config_schema = _FakeConfig
    requires_approval: ClassVar[bool] = True
    needs_hosted_image: ClassVar[bool] = True

    async def execute(
        self, input: BaseModel, *, user_id: uuid.UUID, db: AsyncSession
    ) -> _FakeOutput:
        assert isinstance(input, _FakeInput)
        return _FakeOutput(result=input.caption)

    async def health_check(self) -> HealthStatus:
        from datetime import UTC, datetime

        return HealthStatus(status="healthy", message="ok", checked_at=datetime.now(UTC))


def _make_registry_with_image_plugin() -> MagicMock:
    from core.tools.registry import ToolRegistry

    plugin = _ImagePlugin()
    real_registry = ToolRegistry()
    real_registry.register(plugin)
    return real_registry


def _image_proposal() -> PendingActionProposal:
    return PendingActionProposal(
        action_type="image_plugin",
        action_payload={"caption": "Sunset"},
        preview_text="Post this to Instagram",
    )


def _make_db_for_image_test() -> MagicMock:
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()
    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = MagicMock()
    pending_result = MagicMock()
    pending_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(side_effect=[user_result, pending_result])
    return mock_db


# ---------------------------------------------------------------------------
# R2 upload in proposal branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proposal_with_needs_hosted_image_uploads_to_r2() -> None:
    mock_db = _make_db_for_image_test()
    r2 = _make_r2_client("https://cdn.example.com/user1/abc.jpg")
    real_registry = _make_registry_with_image_plugin()
    engine = _make_engine_with_db(mock_db, r2=r2, registry=real_registry)

    image_bytes = b"\xff\xd8\xff"  # minimal JPEG header
    image_b64 = base64.b64encode(image_bytes).decode()

    plan_result = PlannerResult(
        content="",
        tool_calls_made=["image_plugin"],
        iterations=1,
        pending_action=_image_proposal(),
    )

    with patch("core.planner.react.ReActPlanner.run", new=AsyncMock(return_value=plan_result)):
        response = await engine.handle_request(
            CoreRequest(
                user_id=uuid.uuid4(),
                content="post this",
                image_base64=image_b64,
                image_mime="image/jpeg",
            )
        )

    # R2 upload was called
    r2.upload.assert_called_once()
    call_kwargs = r2.upload.call_args
    assert call_kwargs.args[0] == image_bytes or call_kwargs.kwargs.get("data") == image_bytes
    # The stored payload includes image_url
    assert response.proposal is not None
    added_obj = mock_db.add.call_args[0][0]
    assert "image_url" in added_obj.action_payload
    assert added_obj.action_payload["image_url"] == "https://cdn.example.com/user1/abc.jpg"
    assert added_obj.action_payload["caption"] == "Sunset"


@pytest.mark.asyncio
async def test_proposal_without_image_returns_early_when_needs_hosted_image() -> None:
    mock_db = _make_db_for_image_test()
    r2 = _make_r2_client()
    real_registry = _make_registry_with_image_plugin()
    engine = _make_engine_with_db(mock_db, r2=r2, registry=real_registry)

    plan_result = PlannerResult(
        content="",
        tool_calls_made=["image_plugin"],
        iterations=1,
        pending_action=_image_proposal(),
    )

    with patch("core.planner.react.ReActPlanner.run", new=AsyncMock(return_value=plan_result)):
        response = await engine.handle_request(
            CoreRequest(user_id=uuid.uuid4(), content="post to instagram")
            # no image_base64
        )

    # R2 upload NOT called; friendly error returned; no pending row added
    r2.upload.assert_not_called()
    mock_db.add.assert_not_called()
    assert "photo" in response.content.lower()
    assert response.proposal is None


@pytest.mark.asyncio
async def test_proposal_without_r2_configured_returns_error() -> None:
    mock_db = _make_db_for_image_test()
    real_registry = _make_registry_with_image_plugin()
    engine = _make_engine_with_db(mock_db, r2=None, registry=real_registry)

    image_b64 = base64.b64encode(b"\xff\xd8\xff").decode()
    plan_result = PlannerResult(
        content="",
        tool_calls_made=["image_plugin"],
        iterations=1,
        pending_action=_image_proposal(),
    )

    with patch("core.planner.react.ReActPlanner.run", new=AsyncMock(return_value=plan_result)):
        response = await engine.handle_request(
            CoreRequest(
                user_id=uuid.uuid4(),
                content="post this",
                image_base64=image_b64,
            )
        )

    # No pending row; error response
    mock_db.add.assert_not_called()
    assert "not configured" in response.content.lower()
    assert response.proposal is None


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
