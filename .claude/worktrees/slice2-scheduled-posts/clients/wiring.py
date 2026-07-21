"""Shared engine construction used by both the FastAPI app and the Telegram bot.

Both entry points need the same fully-wired CoreEngine + session factory.
This module provides a single function so they don't duplicate the wiring.
"""

from __future__ import annotations

import structlog
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from core.config import Settings
from core.engine import CoreEngine
from core.llm.openai_provider import OpenAIProvider
from core.memory.manager import MemoryManager
from core.tools.registry import ToolRegistry
from integrations.instagram import InstagramClient
from integrations.local_fs import LocalFsClient
from integrations.r2 import R2Client
from integrations.serper import SerperClient
from plugins.approval_test.plugin import ApprovalTestPlugin
from plugins.file_reader.plugin import FileReaderPlugin
from plugins.instagram_post.plugin import InstagramPostPlugin
from plugins.reminders.cancel import CancelReminderPlugin
from plugins.reminders.list import ListRemindersPlugin
from plugins.reminders.plugin import RemindersPlugin
from plugins.schedule_post.plugin import SchedulePostPlugin
from plugins.tasks.complete import CompleteTaskPlugin
from plugins.tasks.create import CreateTaskPlugin
from plugins.tasks.list import ListTasksPlugin
from plugins.web_search.plugin import WebSearchPlugin


async def build_engine(
    s: Settings,
) -> tuple[
    AsyncEngine, async_sessionmaker[AsyncSession], CoreEngine, SerperClient | None
]:  # noqa: E501
    """Construct and return (sql_engine, session_factory, core_engine, serper_client).

    The caller is responsible for calling sql_engine.dispose() and, if present,
    serper_client.aclose() on shutdown.
    """
    log = structlog.get_logger()

    sql_engine = create_async_engine(
        str(s.database_url),
        pool_size=s.db_pool_size,
        max_overflow=s.db_max_overflow,
    )
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        sql_engine, expire_on_commit=False
    )

    llm = OpenAIProvider(
        api_key=s.openai_api_key.get_secret_value(),
        default_model=s.openai_default_model,
        fast_model=s.openai_fast_model,
        timeout=s.openai_timeout_seconds,
        max_retries=0,
    )
    memory = MemoryManager(llm=llm, settings=s)
    registry = ToolRegistry()
    registry.register(ApprovalTestPlugin())
    registry.register(RemindersPlugin(tz_name=s.default_timezone))
    registry.register(ListRemindersPlugin(tz_name=s.default_timezone))
    registry.register(CancelReminderPlugin())
    registry.register(CreateTaskPlugin(tz_name=s.default_timezone))
    registry.register(ListTasksPlugin())
    registry.register(CompleteTaskPlugin())

    serper_client: SerperClient | None = None
    if s.serper_api_key is not None:
        serper_client = SerperClient(api_key=s.serper_api_key.get_secret_value())
        registry.register(WebSearchPlugin(client=serper_client))
    else:
        log.warning(
            "serper.key_missing", detail="SERPER_API_KEY not set; web_search plugin disabled"
        )

    if s.file_reader_root is not None:
        fs_client = LocalFsClient(root=s.file_reader_root, max_bytes=s.file_reader_max_bytes)
        registry.register(
            FileReaderPlugin(client=fs_client, llm=llm, fast_model=s.openai_fast_model)
        )
    else:
        log.warning(
            "file_reader.root_missing",
            detail="FILE_READER_ROOT not set; file_reader plugin disabled",
        )

    r2_client: R2Client | None = None
    r2_ready = all(
        [
            s.r2_account_id,
            s.r2_access_key_id,
            s.r2_secret_access_key,
            s.r2_bucket,
            s.r2_public_base_url,
        ]
    )
    if r2_ready:
        assert s.r2_account_id is not None
        assert s.r2_access_key_id is not None
        assert s.r2_secret_access_key is not None
        assert s.r2_bucket is not None
        assert s.r2_public_base_url is not None
        r2_client = R2Client(
            account_id=s.r2_account_id,
            access_key_id=s.r2_access_key_id.get_secret_value(),
            secret_access_key=s.r2_secret_access_key.get_secret_value(),
            bucket=s.r2_bucket,
            public_base_url=s.r2_public_base_url,
        )
        if s.instagram_access_token and s.instagram_user_id:
            ig_client = InstagramClient(
                access_token=s.instagram_access_token.get_secret_value(),
                ig_user_id=s.instagram_user_id,
            )
            registry.register(InstagramPostPlugin(client=ig_client))
            registry.register(SchedulePostPlugin(tz_name=s.default_timezone))
        else:
            log.warning(
                "instagram.not_configured",
                detail="INSTAGRAM_ACCESS_TOKEN or INSTAGRAM_USER_ID not set; instagram_post and schedule_post disabled",
            )
    else:
        log.warning(
            "r2.not_configured",
            detail="R2_* vars not fully set; image upload + instagram_post disabled",
        )

    core = CoreEngine(
        llm=llm,
        memory=memory,
        registry=registry,
        session_factory=factory,
        settings=s,
        r2=r2_client,
    )
    return sql_engine, factory, core, serper_client
