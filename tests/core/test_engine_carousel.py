"""Unit tests for the engine's needs_hosted_images (carousel) proposal branch."""

from __future__ import annotations

import uuid
from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.engine import CoreEngine
from core.llm.base import LLMResponse, TokenUsage
from core.planner.base import PendingActionProposal, PlannerResult
from core.schemas import CoreRequest, ImageAttachment
from core.tools.registry import ToolRegistry
from plugins.base import HealthStatus, PluginBase

# ---------------------------------------------------------------------------
# Helpers (mirrors test_engine_approval.py structure)
# ---------------------------------------------------------------------------


def _usage() -> TokenUsage:
    return TokenUsage(input_tokens=1, output_tokens=1, total_tokens=2)


def _message_response(text: str) -> LLMResponse:
    return LLMResponse(
        response_type="message",
        content=text,
        tool_calls=[],
        model="gpt-5.4",
        usage=_usage(),
        raw_response_id="r-001",
    )


def _make_db() -> MagicMock:
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


def _make_r2_client(url_template: str = "https://cdn.example.com/{n}.jpg") -> MagicMock:
    r2 = MagicMock()
    call_count = [0]

    async def _upload(data: bytes, *, key: str, content_type: str) -> str:
        n = call_count[0]
        call_count[0] += 1
        return url_template.format(n=n)

    r2.upload = AsyncMock(side_effect=_upload)
    return r2


def _make_engine(
    mock_db: MagicMock,
    *,
    r2: MagicMock | None = None,
    registry: ToolRegistry | MagicMock | None = None,
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
        registry.get_plugin = MagicMock(return_value=None)

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
    mock_settings.approval_ttl_minutes = 60

    return CoreEngine(
        llm=mock_llm,
        memory=mock_memory,
        registry=registry,
        session_factory=mock_factory,
        settings=mock_settings,
        r2=r2,
    )


# ---------------------------------------------------------------------------
# Fake carousel plugin
# ---------------------------------------------------------------------------


class _FakeCarouselInput(BaseModel):
    caption: str


class _FakeCarouselOutput(BaseModel):
    media_id: str
    confirmation: str = "ok"


class _FakeCarouselConfig(BaseModel):
    pass


class _CarouselPlugin(PluginBase):
    """Test plugin with needs_hosted_images=True."""

    name: ClassVar[str] = "carousel_plugin"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Carousel plugin"
    capabilities: ClassVar[list[str]] = []
    permissions: ClassVar[list[str]] = []
    dependencies: ClassVar[list[str]] = []
    input_schema = _FakeCarouselInput
    output_schema = _FakeCarouselOutput
    config_schema = _FakeCarouselConfig
    requires_approval: ClassVar[bool] = True
    needs_hosted_image: ClassVar[bool] = False
    needs_hosted_images: ClassVar[bool] = True

    async def execute(
        self, input: BaseModel, *, user_id: uuid.UUID, db: AsyncSession, **kwargs: Any
    ) -> _FakeCarouselOutput:
        assert isinstance(input, _FakeCarouselInput)
        return _FakeCarouselOutput(media_id="fake-id")

    async def health_check(self) -> HealthStatus:
        from datetime import UTC, datetime

        return HealthStatus(status="healthy", message="ok", checked_at=datetime.now(UTC))


def _make_carousel_registry() -> ToolRegistry:
    real_registry = ToolRegistry()
    real_registry.register(_CarouselPlugin())
    return real_registry


def _carousel_proposal() -> PendingActionProposal:
    return PendingActionProposal(
        action_type="carousel_plugin",
        action_payload={"caption": "Sunset series"},
        preview_text="Post these photos as a carousel?",
    )


def _make_attachments(n: int) -> list[ImageAttachment]:
    import base64

    return [
        ImageAttachment(
            data=base64.b64encode(f"fake-image-{i}".encode()).decode(),
            mime="image/jpeg",
        )
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_carousel_proposal_uploads_each_image_to_r2() -> None:
    """needs_hosted_images + 2 request.images → 2 r2.upload calls, image_urls list in payload."""
    mock_db = _make_db()
    r2 = _make_r2_client()
    real_registry = _make_carousel_registry()
    engine = _make_engine(mock_db, r2=r2, registry=real_registry)

    plan_result = PlannerResult(
        content="",
        tool_calls_made=["carousel_plugin"],
        iterations=1,
        pending_action=_carousel_proposal(),
    )

    attachments = _make_attachments(2)

    with patch("core.planner.react.ReActPlanner.run", new=AsyncMock(return_value=plan_result)):
        response = await engine.handle_request(
            CoreRequest(
                user_id=uuid.uuid4(),
                content="post as carousel",
                images=attachments,
            )
        )

    assert response.proposal is not None
    assert r2.upload.call_count == 2
    added_obj = mock_db.add.call_args[0][0]
    assert "image_urls" in added_obj.action_payload
    assert len(added_obj.action_payload["image_urls"]) == 2
    assert added_obj.action_payload["caption"] == "Sunset series"


@pytest.mark.asyncio
async def test_carousel_proposal_without_images_returns_friendly_refusal() -> None:
    """needs_hosted_images + no request.images → early CoreResponse, no R2, no pending row."""
    mock_db = _make_db()
    r2 = _make_r2_client()
    real_registry = _make_carousel_registry()
    engine = _make_engine(mock_db, r2=r2, registry=real_registry)

    plan_result = PlannerResult(
        content="",
        tool_calls_made=["carousel_plugin"],
        iterations=1,
        pending_action=_carousel_proposal(),
    )

    with patch("core.planner.react.ReActPlanner.run", new=AsyncMock(return_value=plan_result)):
        response = await engine.handle_request(
            CoreRequest(user_id=uuid.uuid4(), content="post as carousel")
            # no images
        )

    r2.upload.assert_not_called()
    mock_db.add.assert_not_called()
    assert "photos" in response.content.lower() or "carousel" in response.content.lower()
    assert response.proposal is None


@pytest.mark.asyncio
async def test_carousel_proposal_without_r2_returns_error() -> None:
    """needs_hosted_images + no R2 configured → friendly error, no pending row."""
    mock_db = _make_db()
    real_registry = _make_carousel_registry()
    engine = _make_engine(mock_db, r2=None, registry=real_registry)

    plan_result = PlannerResult(
        content="",
        tool_calls_made=["carousel_plugin"],
        iterations=1,
        pending_action=_carousel_proposal(),
    )

    with patch("core.planner.react.ReActPlanner.run", new=AsyncMock(return_value=plan_result)):
        response = await engine.handle_request(
            CoreRequest(
                user_id=uuid.uuid4(),
                content="post as carousel",
                images=_make_attachments(2),
            )
        )

    mock_db.add.assert_not_called()
    assert "not configured" in response.content.lower()
    assert response.proposal is None
