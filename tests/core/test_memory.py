"""Unit tests for MemoryManager.

DB and LLM are both mocked — no Postgres or OpenAI needed.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.memory.manager import MemoryManager, ScoredMemory, _heuristic

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manager() -> MemoryManager:
    llm = MagicMock()
    llm.embed = AsyncMock(return_value=[[0.1] * 1536])
    with patch("core.llm.openai_provider.AsyncOpenAI"):
        from core.config import Settings

        settings = MagicMock(spec=Settings)
        settings.openai_embedding_model = "text-embedding-3-small"
    return MemoryManager(llm=llm, settings=settings)


def _mock_llm(manager: MemoryManager) -> MagicMock:
    """Return the MagicMock standing in for the LLM on this manager."""
    return manager._llm  # type: ignore[return-value]


def _make_db() -> MagicMock:
    db = MagicMock()
    db.add = MagicMock()
    db.execute = AsyncMock()
    db.scalars = MagicMock()
    return db


# ---------------------------------------------------------------------------
# write()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_embeds_content() -> None:
    manager = _make_manager()
    db = _make_db()
    uid = uuid.uuid4()

    mem = await manager.write(db, user_id=uid, content="call Bob", memory_type="episodic")

    _mock_llm(manager).embed.assert_called_once_with(["call Bob"], model="text-embedding-3-small")
    assert mem.embedding == [0.1] * 1536


@pytest.mark.asyncio
async def test_write_stores_in_db() -> None:
    manager = _make_manager()
    db = _make_db()
    uid = uuid.uuid4()

    mem = await manager.write(db, user_id=uid, content="buy milk", memory_type="episodic")

    db.add.assert_called_once_with(mem)


@pytest.mark.asyncio
async def test_write_uses_provided_importance_score() -> None:
    manager = _make_manager()
    db = _make_db()

    mem = await manager.write(
        db,
        user_id=uuid.uuid4(),
        content="anything",
        memory_type="episodic",
        importance_score=0.99,
    )

    assert mem.importance_score == 0.99


@pytest.mark.asyncio
async def test_write_metadata_stored() -> None:
    manager = _make_manager()
    db = _make_db()

    mem = await manager.write(
        db,
        user_id=uuid.uuid4(),
        content="test",
        memory_type="episodic",
        metadata={"session_id": "abc", "tools": ["create_reminder"]},
    )

    assert mem.metadata_ == {"session_id": "abc", "tools": ["create_reminder"]}


# ---------------------------------------------------------------------------
# _heuristic importance scoring
# ---------------------------------------------------------------------------


def test_heuristic_reminder_keyword() -> None:
    assert _heuristic("Set a reminder for tomorrow", "episodic") >= 0.7


def test_heuristic_reminder_case_insensitive() -> None:
    assert _heuristic("REMINDER: call Bob", "episodic") >= 0.7


def test_heuristic_episodic_no_keyword() -> None:
    assert _heuristic("User asked about the weather", "episodic") == 0.6


def test_heuristic_semantic() -> None:
    assert _heuristic("Capital of France", "semantic") == 0.5


def test_heuristic_working() -> None:
    assert _heuristic("scratch pad content", "working") == 0.5


# ---------------------------------------------------------------------------
# semantic_search()
# ---------------------------------------------------------------------------


def _make_db_rows(*pairs: tuple[SimpleNamespace, float]) -> MagicMock:
    """Return a mock db whose execute().all() yields Row-like objects with .Memory and .dist."""
    fake_rows = [SimpleNamespace(Memory=mem, dist=dist) for mem, dist in pairs]
    mock_result = MagicMock()
    mock_result.all.return_value = fake_rows
    db = MagicMock()
    db.execute = AsyncMock(return_value=mock_result)
    return db


@pytest.mark.asyncio
async def test_semantic_search_embeds_query_once() -> None:
    manager = _make_manager()
    row_a = SimpleNamespace(last_accessed_at=None)
    row_b = SimpleNamespace(last_accessed_at=None)
    db = _make_db_rows((row_a, 0.2), (row_b, 0.4))

    await manager.semantic_search(db, user_id=uuid.uuid4(), query="remind me")

    _mock_llm(manager).embed.assert_called_once()
    call_args = _mock_llm(manager).embed.call_args
    assert call_args[0][0] == ["remind me"]


@pytest.mark.asyncio
async def test_semantic_search_updates_last_accessed() -> None:
    manager = _make_manager()
    row = SimpleNamespace(last_accessed_at=None)
    db = _make_db_rows((row, 0.3))

    result = await manager.semantic_search(db, user_id=uuid.uuid4(), query="test")

    assert result[0].memory.last_accessed_at is not None


@pytest.mark.asyncio
async def test_semantic_search_returns_scored_pairs() -> None:
    manager = _make_manager()
    row_a = SimpleNamespace(last_accessed_at=None)
    row_b = SimpleNamespace(last_accessed_at=None)
    db = _make_db_rows((row_a, 0.1), (row_b, 0.45))

    result = await manager.semantic_search(db, user_id=uuid.uuid4(), query="test")

    assert len(result) == 2
    assert all(isinstance(r, ScoredMemory) for r in result)
    assert result[0].distance == 0.1
    assert result[1].distance == 0.45
    assert result[0].memory is row_a  # type: ignore[comparison-overlap]
    assert result[1].memory is row_b  # type: ignore[comparison-overlap]


# ---------------------------------------------------------------------------
# get_recent()
# ---------------------------------------------------------------------------


def _mem(content: str) -> SimpleNamespace:
    return SimpleNamespace(content=content)


@pytest.mark.asyncio
async def test_get_recent_returns_oldest_first() -> None:
    manager = _make_manager()
    db = MagicMock()
    # DB returns DESC (newest first): turn3, turn2, turn1
    rows_desc = [_mem("turn3"), _mem("turn2"), _mem("turn1")]
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = rows_desc
    db.execute = AsyncMock(return_value=mock_result)

    result = await manager.get_recent(db, user_id=uuid.uuid4(), limit=3)

    assert [r.content for r in result] == ["turn1", "turn2", "turn3"]


@pytest.mark.asyncio
async def test_get_recent_empty_when_none() -> None:
    manager = _make_manager()
    db = MagicMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=mock_result)

    result = await manager.get_recent(db, user_id=uuid.uuid4(), limit=10)

    assert result == []


@pytest.mark.asyncio
async def test_get_recent_does_not_update_last_accessed() -> None:
    manager = _make_manager()
    db = MagicMock()
    row = SimpleNamespace(content="x", last_accessed_at=None)
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [row]
    db.execute = AsyncMock(return_value=mock_result)

    await manager.get_recent(db, user_id=uuid.uuid4(), limit=5)

    assert row.last_accessed_at is None


@pytest.mark.asyncio
async def test_get_recent_does_not_embed() -> None:
    manager = _make_manager()
    db = MagicMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=mock_result)

    await manager.get_recent(db, user_id=uuid.uuid4(), limit=5)

    _mock_llm(manager).embed.assert_not_called()
