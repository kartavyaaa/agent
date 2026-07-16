from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clients.user_helper import get_or_create_user
from core.config import Settings
from core.llm.base import LLMConfig, LLMMessage, LLMProvider
from core.memory.manager import MemoryManager
from core.planner.react import ReActPlanner
from core.schemas import CoreRequest, CoreResponse
from core.timeutil import format_local
from core.tools.registry import ToolRegistry

_SYSTEM_PROMPT = (
    "You are a personal AI assistant. "
    "The user's timezone is {tz}. "
    "The current local time in the user's timezone is {now_local}. "
    "You have access to the following tools: {tools}. "
    "Use them whenever they help fulfill the user's request. "
    "When the user mentions a time, interpret it in their timezone ({tz}). "
    "When calling create_reminder, always emit remind_at as an absolute UTC ISO timestamp "
    "(e.g. 2026-07-14T03:30:00Z). "
    "When the user sends a photo, provide a thoughtful critique covering composition, "
    "lighting, subject, and suggestions for improvement. "
    "When all necessary actions are complete, reply directly to the user."
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

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        return self._session_factory

    async def handle_request(self, request: CoreRequest) -> CoreResponse:
        log = structlog.get_logger().bind(
            user_id=str(request.user_id),
            session_id=str(request.session_id),
        )
        async with self._session_factory() as db:
            try:
                await get_or_create_user(db, request.user_id)
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
        tz_name = self._settings.default_timezone
        now_local = format_local(datetime.now(UTC), tz_name)
        tools = self._registry.get_tools_for_llm()
        tool_names = ", ".join(t.name for t in tools) or "none"

        recent = await self._memory.get_recent(
            db,
            user_id=request.user_id,
            limit=self._settings.conversation_history_turns,
        )
        history_block = ""
        if recent:
            lines = "\n".join(m.content for m in recent)
            history_block = f"\n\nRecent conversation history (oldest to newest):\n{lines}"

        recall_block = ""
        if self._settings.semantic_recall_enabled:
            scored = await self._memory.semantic_search(
                db,
                user_id=request.user_id,
                query=request.content,  # always a plain string (caption or default prompt)
                top_k=self._settings.semantic_recall_top_k,
                memory_types=["episodic"],
            )
            log.debug(
                "recall.distances",
                distances=[round(s.distance, 4) for s in scored],
            )
            recent_contents = {m.content for m in recent}
            hits = [
                s
                for s in scored
                if s.distance <= self._settings.semantic_recall_max_distance
                and s.memory.content not in recent_contents
            ][: self._settings.semantic_recall_inject_count]
            if hits:
                lines = "\n".join(h.memory.content for h in hits)
                recall_block = "\n\nRelevant past context (may or may not be useful):\n" + lines

        system_msg = LLMMessage(
            role="system",
            content=_SYSTEM_PROMPT.format(now_local=now_local, tz=tz_name, tools=tool_names)
            + recall_block
            + history_block,
        )
        if request.image_base64:
            user_content: str | list[dict] = [  # type: ignore[type-arg]
                {
                    "type": "input_image",
                    "image_url": f"data:{request.image_mime};base64,{request.image_base64}",
                },
                {
                    "type": "input_text",
                    "text": request.content,
                },
            ]
        else:
            user_content = request.content
        user_msg = LLMMessage(role="user", content=user_content)

        planner = ReActPlanner(
            llm=self._llm,
            registry=self._registry,
            config=LLMConfig(
                model=self._settings.openai_default_model,
                temperature=self._settings.planner_default_temperature,
            ),
            max_iterations=self._settings.planner_max_iterations,
        )
        plan_result = await planner.run(
            messages=[system_msg, user_msg],
            tools=tools,
            user_id=request.user_id,
            db=db,
        )

        await self._memory.write(
            db,
            user_id=request.user_id,
            content=f"User: {request.content}\nAssistant: {plan_result.content}",  # content is always str
            memory_type="episodic",
            metadata={
                "session_id": str(request.session_id),
                "tools": plan_result.tool_calls_made,
            },
        )
        memories_written = 1

        log.info(
            "engine.processed",
            tools=plan_result.tool_calls_made,
            iterations=plan_result.iterations,
            memories_written=memories_written,
        )
        return CoreResponse(
            content=plan_result.content,
            memories_written=memories_written,
            tool_calls_made=plan_result.tool_calls_made,
        )
