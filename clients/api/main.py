from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from clients.api.routes.health import router as health_router
from clients.api.routes.reminders import router as reminders_router
from core.config import get_settings
from core.engine import CoreEngine
from core.llm.openai_provider import OpenAIProvider
from core.logging import configure_logging
from core.memory.manager import MemoryManager
from core.tools.registry import ToolRegistry
from plugins.reminders.plugin import RemindersPlugin


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    configure_logging(s.log_level, s.environment)

    engine = create_async_engine(
        str(s.database_url),
        pool_size=s.db_pool_size,
        max_overflow=s.db_max_overflow,
    )
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)

    llm = OpenAIProvider(
        api_key=s.openai_api_key.get_secret_value(),
        default_model=s.openai_default_model,
        fast_model=s.openai_fast_model,
        timeout=s.openai_timeout_seconds,
        max_retries=0,  # tenacity handles retries
    )
    memory = MemoryManager(llm=llm, settings=s)
    registry = ToolRegistry()
    registry.register(RemindersPlugin())
    core = CoreEngine(
        llm=llm,
        memory=memory,
        registry=registry,
        session_factory=factory,
        settings=s,
    )
    app.state.engine = core
    yield
    await engine.dispose()


app = FastAPI(title="Personal AI Platform", lifespan=lifespan)
app.include_router(health_router)
app.include_router(reminders_router, prefix="/v1")
