from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from clients.api.error_handlers import register_error_handlers
from clients.api.routes.chat import router as chat_router
from clients.api.routes.health import router as health_router
from clients.api.routes.memories import router as memories_router
from clients.api.routes.reminders import router as reminders_router
from clients.wiring import build_engine
from core.config import get_settings
from core.logging import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    configure_logging(s.log_level, s.environment)
    log = structlog.get_logger()

    sql_engine, factory, core, serper_client = await build_engine(s)
    app.state.engine = core
    log.info("app.started")
    yield
    if serper_client is not None:
        await serper_client.aclose()
    await sql_engine.dispose()


app = FastAPI(title="Personal AI Platform", lifespan=lifespan)
register_error_handlers(app)
app.include_router(health_router)
app.include_router(reminders_router, prefix="/v1")
app.include_router(chat_router, prefix="/v1")
app.include_router(memories_router, prefix="/v1")
