from __future__ import annotations

from typing import Any

import httpx
from arq import cron
from arq.connections import RedisSettings
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.config import get_settings
from core.logging import configure_logging
from core.notifications.telegram_notifier import TelegramNotifier
from core.scheduler.jobs import poll_reminders, poll_scheduled_posts


async def startup(ctx: dict[str, Any]) -> None:
    s = get_settings()
    configure_logging(log_level=s.log_level, environment=s.environment)
    engine = create_async_engine(str(s.database_url))
    ctx["session_factory"] = async_sessionmaker(engine, expire_on_commit=False)
    ctx["http_client"] = httpx.AsyncClient()
    ctx["notifier"] = TelegramNotifier(
        bot_token=s.telegram_bot_token.get_secret_value(),
        http_client=ctx["http_client"],
    )


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["http_client"].aclose()


class WorkerSettings:
    functions = [poll_reminders, poll_scheduled_posts]
    cron_jobs = [cron(poll_reminders, second={0}), cron(poll_scheduled_posts, second={0})]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(str(get_settings().redis_url))
    max_jobs = 10
    job_timeout = 55
