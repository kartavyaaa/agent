"""Route-level tests for the FastAPI application.

All external dependencies (engine, DB session) are mocked via FastAPI dependency overrides
and unittest.mock. No Docker required.
"""

from __future__ import annotations

import uuid
from datetime import UTC
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from clients.api.dependencies import get_engine, get_session_factory
from clients.api.main import app
from core.exceptions import (
    IntegrationError,
    LLMRateLimitError,
    PlannerMaxIterationsError,
    PlannerStuckLoopError,
    PlatformError,
    SandboxViolationError,
)
from core.schemas import CoreResponse
from models.memory import Memory

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_USER_ID = uuid.uuid4()


def _make_mock_engine(response: CoreResponse | None = None) -> MagicMock:
    engine = MagicMock()
    engine.handle_request = AsyncMock(
        return_value=response
        or CoreResponse(content="Hello!", memories_written=1, tool_calls_made=[])
    )
    return engine


def _make_mock_session_factory(rows: list[Memory] | None = None) -> MagicMock:
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()

    # Simulate db.execute().scalars().all()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = rows or []
    mock_db.execute = AsyncMock(return_value=mock_result)

    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory


def _make_memory(memory_type: str = "episodic") -> Memory:
    from datetime import datetime

    return Memory(
        id=uuid.uuid4(),
        user_id=_USER_ID,
        content="User asked something. Assistant replied.",
        embedding=None,
        importance_score=0.6,
        memory_type=memory_type,
        metadata_={},
        created_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Chat route — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_happy_path() -> None:
    mock_engine = _make_mock_engine()
    app.dependency_overrides[get_engine] = lambda: mock_engine
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/v1/chat", json={"user_id": str(_USER_ID), "content": "hello"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["content"] == "Hello!"
        mock_engine.handle_request.assert_awaited_once()
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Chat route — error handler dispatch (raised from engine, not injected directly)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_planner_max_iterations() -> None:
    mock_engine = _make_mock_engine()
    mock_engine.handle_request = AsyncMock(side_effect=PlannerMaxIterationsError())
    app.dependency_overrides[get_engine] = lambda: mock_engine
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/chat", json={"user_id": str(_USER_ID), "content": "loop forever"}
            )
        assert resp.status_code == 422
        assert resp.json()["error"] == "planner_too_complex"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_chat_planner_stuck() -> None:
    mock_engine = _make_mock_engine()
    mock_engine.handle_request = AsyncMock(side_effect=PlannerStuckLoopError())
    app.dependency_overrides[get_engine] = lambda: mock_engine
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/v1/chat", json={"user_id": str(_USER_ID), "content": "stuck"})
        assert resp.status_code == 422
        assert resp.json()["error"] == "planner_stuck"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_chat_llm_rate_limit() -> None:
    mock_engine = _make_mock_engine()
    mock_engine.handle_request = AsyncMock(side_effect=LLMRateLimitError())
    app.dependency_overrides[get_engine] = lambda: mock_engine
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/v1/chat", json={"user_id": str(_USER_ID), "content": "query"})
        assert resp.status_code == 429
        assert resp.json()["error"] == "llm_rate_limit"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_chat_sandbox_violation_returns_403_without_path() -> None:
    secret_path = "/etc/passwd"
    mock_engine = _make_mock_engine()
    mock_engine.handle_request = AsyncMock(side_effect=SandboxViolationError(secret_path))
    app.dependency_overrides[get_engine] = lambda: mock_engine
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/chat",
                json={"user_id": str(_USER_ID), "content": "read ../../../../etc/passwd"},
            )
        assert resp.status_code == 403
        body = resp.json()
        assert body["error"] == "access_denied"
        # Secret path must NOT appear anywhere in the response
        assert secret_path not in resp.text
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_chat_integration_error_returns_502() -> None:
    mock_engine = _make_mock_engine()
    mock_engine.handle_request = AsyncMock(side_effect=IntegrationError("service down"))
    app.dependency_overrides[get_engine] = lambda: mock_engine
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/chat", json={"user_id": str(_USER_ID), "content": "search something"}
            )
        assert resp.status_code == 502
        assert resp.json()["error"] == "integration_error"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_chat_platform_error_catchall_returns_500() -> None:
    mock_engine = _make_mock_engine()
    mock_engine.handle_request = AsyncMock(side_effect=PlatformError("unexpected"))
    app.dependency_overrides[get_engine] = lambda: mock_engine
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/v1/chat", json={"user_id": str(_USER_ID), "content": "anything"})
        assert resp.status_code == 500
        assert resp.json()["error"] == "internal_error"
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Memories route
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memories_happy_path() -> None:
    rows = [_make_memory(), _make_memory()]
    factory = _make_mock_session_factory(rows=rows)
    app.dependency_overrides[get_session_factory] = lambda: factory
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(f"/v1/memories?user_id={_USER_ID}")
        assert resp.status_code == 200
        assert len(resp.json()) == 2
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_memories_type_filter() -> None:
    rows = [_make_memory(memory_type="episodic")]
    factory = _make_mock_session_factory(rows=rows)
    app.dependency_overrides[get_session_factory] = lambda: factory
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(f"/v1/memories?user_id={_USER_ID}&memory_type=episodic")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["memory_type"] == "episodic"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_memories_embedding_excluded_from_response() -> None:
    rows = [_make_memory()]
    factory = _make_mock_session_factory(rows=rows)
    app.dependency_overrides[get_session_factory] = lambda: factory
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(f"/v1/memories?user_id={_USER_ID}")
        assert resp.status_code == 200
        for item in resp.json():
            assert "embedding" not in item
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_memories_invalid_type_returns_422() -> None:
    factory = _make_mock_session_factory()
    app.dependency_overrides[get_session_factory] = lambda: factory
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(f"/v1/memories?user_id={_USER_ID}&memory_type=invalid")
        # FastAPI query-param Literal validation returns 422 before DB is touched
        assert resp.status_code == 422
    finally:
        app.dependency_overrides.clear()
