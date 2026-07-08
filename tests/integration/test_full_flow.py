"""Integration test: full reminder round-trip against real Postgres.
 
Requires Docker. Skipped automatically when Docker is unavailable (conftest.py
detects this and marks the test).
 
What is mocked:
- LLM (embed returns fixed vector; complete returns create_reminder tool call)
- TelegramNotifier.send (AsyncMock — no real HTTP)
 
What is real:
- Postgres (pgvector/pgvector:pg16 via testcontainers)
- SQLAlchemy session / ORM writes
- Alembic migration (upgrade head against the container)
- CoreEngine.handle_request → RemindersPlugin → Reminder row
- MemoryManager.write → Memory row
- poll_reminders → marks sent_at
"""
 
from __future__ import annotations
 
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
 
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
 
from core.engine import CoreEngine
from core.llm.base import LLMResponse, LLMToolCall, TokenUsage
from core.memory.manager import MemoryManager
from core.notifications.telegram_notifier import TelegramNotifier
from core.scheduler.jobs import poll_reminders
from core.schemas import CoreRequest
from core.tools.registry import ToolRegistry
from models.memory import Memory
from models.reminder import Reminder
from models.user import User
from plugins.reminders.plugin import RemindersPlugin
 
pytestmark = pytest.mark.integration
 
# Repo root: this file is tests/integration/test_full_flow.py → up 2 levels.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
 
 
# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
 
 
def _base_env(database_url: str) -> dict[str, str]:
    """Environment for the alembic subprocess: inherit, then set required vars."""
    env = dict(os.environ)
    env["DATABASE_URL"] = database_url  # keep +asyncpg — alembic/env.py uses create_async_engine
    env.setdefault("OPENAI_API_KEY", "sk-test")
    env.setdefault("TELEGRAM_BOT_TOKEN", "0:test")
    env.setdefault("TELEGRAM_WEBHOOK_SECRET", "test")
    env.setdefault("REDIS_URL", "redis://localhost:6379/0")
    return env
 
 
@pytest.fixture(scope="module")
def pg_url() -> Generator[str, None, None]:
    """Start a pgvector Postgres container, run migrations, yield the async URL."""
    from testcontainers.postgres import PostgresContainer
 
    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        # testcontainers returns a psycopg2 URL; swap the driver to asyncpg.
        url = pg.get_connection_url().replace("psycopg2", "asyncpg", 1)
 
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic.config",
                "-c",
                str(_ALEMBIC_INI),
                "upgrade",
                "head",
            ],
            cwd=str(_REPO_ROOT),
            env=_base_env(url),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "alembic upgrade failed "
                f"(exit {result.returncode}):\n"
                f"--- stdout ---\n{result.stdout}\n"
                f"--- stderr ---\n{result.stderr}"
            )
 
        yield url
 
 
@pytest_asyncio.fixture
async def session_factory(pg_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(pg_url, echo=False)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    yield factory
    await engine.dispose()
 
 
@pytest_asyncio.fixture
async def db_user(session_factory: async_sessionmaker[AsyncSession]) -> User:
    user = User(id=uuid.uuid4(), telegram_id=888888)
    async with session_factory() as db:
        db.add(user)
        await db.commit()
        await db.refresh(user)
    return user
 
 
def _mock_llm() -> MagicMock:
    """LLM that returns a create_reminder tool call and fixed embeddings."""
    remind_at = (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
 
    llm = MagicMock()
    llm.embed = AsyncMock(return_value=[[0.0] * 1536])
    llm.complete = AsyncMock(
        return_value=LLMResponse(
            response_type="tool_calls",
            content=None,
            tool_calls=[
                LLMToolCall(
                    id="call-integration-1",
                    name="create_reminder",
                    arguments={"message": "call Bob", "remind_at": remind_at},
                )
            ],
            model="gpt-5.5",
            usage=TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15),
            raw_response_id="resp-integration-1",
        )
    )
    return llm
 
 
# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------
 
 
@pytest.mark.asyncio
async def test_reminder_round_trip(
    session_factory: async_sessionmaker[AsyncSession],
    db_user: User,
) -> None:
    mock_llm = _mock_llm()
 
    settings = MagicMock()
    settings.openai_default_model = "gpt-5.5"
    settings.openai_embedding_model = "text-embedding-3-small"
 
    memory = MemoryManager(llm=mock_llm, settings=settings)
    registry = ToolRegistry()
    registry.register(RemindersPlugin())
 
    engine = CoreEngine(
        llm=mock_llm,
        memory=memory,
        registry=registry,
        session_factory=session_factory,
        settings=settings,
    )
 
    # --- Step 1: handle request ---
    response = await engine.handle_request(
        CoreRequest(user_id=db_user.id, content="remind me tomorrow to call Bob")
    )
 
    assert response.tool_calls_made == ["create_reminder"]
    assert "call Bob" in response.content or "Reminder set" in response.content
 
    # --- Step 2: Memory row written ---
    async with session_factory() as db:
        mem_result = await db.execute(select(Memory).where(Memory.user_id == db_user.id))
        memories = mem_result.scalars().all()
 
    assert len(memories) == 1
    assert "call Bob" in memories[0].content
    assert memories[0].memory_type == "episodic"
 
    # --- Step 3: Reminder row written ---
    async with session_factory() as db:
        rem_result = await db.execute(select(Reminder).where(Reminder.user_id == db_user.id))
        reminders = rem_result.scalars().all()
 
    assert len(reminders) == 1
    reminder = reminders[0]
    assert reminder.message == "call Bob"
    assert reminder.sent_at is None
 
    # --- Step 4: backdate remind_at so the poller picks it up ---
    async with session_factory() as db:
        r = await db.get(Reminder, reminder.id)
        assert r is not None
        r.remind_at = datetime.now(UTC) - timedelta(seconds=5)
        await db.commit()
 
    # --- Step 5: run poll_reminders ---
    mock_notifier = MagicMock(spec=TelegramNotifier)
    mock_notifier.send = AsyncMock()
 
    await poll_reminders({"session_factory": session_factory, "notifier": mock_notifier})
 
    # --- Step 6: notifier called with user's telegram_id ---
    mock_notifier.send.assert_called_once_with(db_user.telegram_id, "call Bob")
 
    # --- Step 7: reminder now marked sent ---
    async with session_factory() as db:
        updated = await db.get(Reminder, reminder.id)
        assert updated is not None
        assert updated.sent_at is not None