"""Run the Telegram bot in long-polling mode (development / VM).

Usage:
    python -m clients.telegram

Requires .env with at minimum:
    TELEGRAM_BOT_TOKEN, DATABASE_URL, OPENAI_API_KEY, TELEGRAM_WEBHOOK_SECRET

The bot is a third long-running process alongside uvicorn and the arq worker.
Containerising it in docker-compose is deferred to Phase 4 (webhook mode).
"""

from __future__ import annotations

import asyncio

import structlog

from clients.telegram.bot import run_polling
from clients.wiring import build_engine
from core.config import get_settings
from core.logging import configure_logging


async def main() -> None:
    s = get_settings()
    configure_logging(s.log_level, s.environment)
    log = structlog.get_logger()

    sql_engine, session_factory, core, serper_client = await build_engine(s)
    log.info("telegram_bot.starting", mode="long-polling")
    try:
        await run_polling(core, session_factory)
    finally:
        if serper_client is not None:
            await serper_client.aclose()
        await sql_engine.dispose()


asyncio.run(main())
