from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.config import Settings
from core.llm.base import LLMConfig, LLMMessage, LLMProvider
from core.memory.manager import MemoryManager
from core.schemas import CoreRequest, CoreResponse
from core.tools.registry import ToolRegistry

_SYSTEM_PROMPT = (
    "You are a personal AI assistant. "
    "Today's date and time (UTC) is {now}. "
    "When the user wants to set a reminder, call the create_reminder tool with an "
    "absolute UTC datetime. Otherwise respond directly."
)


class CoreEngine:
    """Handles one user request end-to-end.

    Owns the DB session lifecycle: opens, commits on success, rolls back on error.
    Clients call handle_request() and never touch the session directly.
    """

    def __init__(
        self,
        *,
        llm: LLMProvider,
        memory: MemoryManager,
        registry: ToolRegistry,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self._llm = llm
        self._memory = memory
        self._registry = registry
        self._session_factory = session_factory
        self._settings = settings

    async def handle_request(self, request: CoreRequest) -> CoreResponse:
        log = structlog.get_logger().bind(
            user_id=str(request.user_id),
            session_id=str(request.session_id),
        )
        async with self._session_factory() as db:
            try:
                result = await self._process(request, db, log)
                await db.commit()
                return result
            except Exception:
                await db.rollback()
                raise

    async def _process(
        self,
        request: CoreRequest,
        db: AsyncSession,
        log: Any,
    ) -> CoreResponse:
        now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        llm_resp = await self._llm.complete(
            messages=[
                LLMMessage(role="system", content=_SYSTEM_PROMPT.format(now=now_str)),
                LLMMessage(role="user", content=request.content),
            ],
            tools=self._registry.get_tools_for_llm(),
            config=LLMConfig(model=self._settings.openai_default_model),
        )

        tools_called: list[str] = []
        result_content = ""

        if llm_resp.response_type == "tool_calls":
            for tc in llm_resp.tool_calls:
                tools_called.append(tc.name)
                out = await self._registry.execute(
                    tc.name,
                    tc.arguments,
                    user_id=request.user_id,
                    db=db,
                )
                result_content = out.get("confirmation") or out.get("message", str(out))
                log.info("tool.executed", tool=tc.name)
        else:
            result_content = llm_resp.content or ""

        await self._memory.write(
            db,
            user_id=request.user_id,
            content=f"User: {request.content}\nAssistant: {result_content}",
            memory_type="episodic",
            metadata={"session_id": str(request.session_id), "tools": tools_called},
        )
        return CoreResponse(
            content=result_content,
            memories_written=1,
            tool_calls_made=tools_called,
        )
