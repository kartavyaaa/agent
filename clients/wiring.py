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
from integrations.local_fs import LocalFsClient
from integrations.serper import SerperClient
from plugins.approval_test.plugin import ApprovalTestPlugin
from plugins.file_reader.plugin import FileReaderPlugin
from plugins.reminders.cancel import CancelReminderPlugin
from plugins.reminders.list import ListRemindersPlugin
from plugins.reminders.plugin import RemindersPlugin
from plugins.tasks.complete import CompleteTaskPlugin
from plugins.tasks.create import CreateTaskPlugin
from plugins.tasks.list import ListTasksPlugin
from plugins.web_search.plugin import WebSearchPlugin


async def build_engine(
    s: Settings,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession], CoreEngine, SerperClient | None]:
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

    core = CoreEngine(
        llm=llm,
        memory=memory,
        registry=registry,
        session_factory=factory,
        settings=s,
    )
    return sql_engine, factory, core, serper_client
