from __future__ import annotations

import base64
import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clients.user_helper import get_or_create_user
from core.config import Settings
from core.llm.base import LLMConfig, LLMMessage, LLMProvider
from core.memory.manager import MemoryManager
from core.planner.base import PendingActionProposal
from core.planner.react import ReActPlanner
from core.schemas import CoreRequest, CoreResponse, ProposalPayload
from core.timeutil import format_local
from core.tools.registry import ToolRegistry
from models.pending_action import PendingAction

if TYPE_CHECKING:
    from integrations.r2 import R2Client

_SYSTEM_PROMPT = (
    "You are a personal AI assistant. "
    "The user's timezone is {tz}. "
    "The current local time in the user's timezone is {now_local}. "
    "You have access to the following tools: {tools}. "
    "Use them whenever they help fulfill the user's request. "
    "When the user mentions a time, interpret it in their timezone ({tz}). "
    "When calling create_reminder, emit remind_at as the user's LOCAL time with NO timezone suffix "
    "(e.g. if the user says '5pm' and their timezone is Asia/Kolkata, emit 2026-07-14T17:00:00 — "
    "no Z, no +05:30). The system converts it to UTC. "
    "When the user sends a single photo: "
    "if they ask to post or share it to Instagram immediately, call instagram_post with the caption; "
    "if they ask to post or share it at a specific future time, call schedule_post with the caption "
    "and scheduled_for as the user's LOCAL time with NO timezone suffix "
    "(e.g. if the user says '5:15am' in Asia/Kolkata, emit 2026-07-22T05:15:00 — no Z, no +05:30); "
    "otherwise provide a thoughtful critique covering composition, lighting, subject, and suggestions. "
    "When the user sends multiple photos as a batch: produce a structured Instagram content plan "
    "covering groupings (carousel vs standalone with reasons), a caption and hashtags per group, "
    "a suggested posting order, and your take on which shots are strongest — presented as a "
    "suggestion, not a verdict; the human is the final judge on framing and selection. "
    "Some tools (like instagram_post) require user approval before they run. "
    "For these, call the tool directly with the required arguments — do NOT ask the user for "
    "confirmation in text first. The system automatically presents a confirmation prompt with "
    "buttons before the action executes. Asking for confirmation yourself is redundant and "
    "breaks the flow because photo context is not available in a later reply. "
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
        r2: R2Client | None = None,
    ) -> None:
        self._llm = llm
        self._memory = memory
        self._registry = registry
        self._session_factory = session_factory
        self._settings = settings
        self._r2 = r2

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
        if request.images:
            # Batch path: N images → content-plan. detail="high" mirrors probe/probe_multi_image.py.
            user_content: str | list[dict] = [  # type: ignore[type-arg]
                {
                    "type": "input_image",
                    "image_url": f"data:{img.mime};base64,{img.data}",
                    "detail": "high",
                }
                for img in request.images
            ] + [{"type": "input_text", "text": request.content}]
        elif request.image_base64:
            user_content = [
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

        # Memoizing provider for lazy R2 upload — used by non-approval needs_hosted_image plugins
        # (e.g. schedule_post). Only invoked if the planner actually calls such a plugin.
        # Photo-critique turns never call registry.execute() for a matching plugin, so this
        # closure is never invoked and zero R2 uploads occur. Upload is idempotent: same image
        # bytes always produce the same key (sha256 prefix) and overwrite the same R2 object.
        _upload_cache: list[str] = []  # single-element list as mutable cell

        async def _provide_image_url() -> str:
            if _upload_cache:
                return _upload_cache[0]
            if not request.image_base64:
                from core.exceptions import PluginError

                raise PluginError("This plugin requires a photo. Please send one.")
            img_bytes = base64.b64decode(request.image_base64)
            key = f"{request.user_id}/{hashlib.sha256(img_bytes).hexdigest()[:32]}.jpg"
            assert self._r2 is not None
            url = await self._r2.upload(
                img_bytes, key=key, content_type=request.image_mime or "image/jpeg"
            )
            _upload_cache.append(url)
            log.info("engine.r2_upload_lazy", key=key)
            return url

        image_url_provider = _provide_image_url if self._r2 is not None else None

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
            image_url_provider=image_url_provider,
        )

        if plan_result.pending_action is not None:
            pa = plan_result.pending_action
            plugin = self._registry.get_plugin(pa.action_type)

            # If the action needs a hosted image, upload now (bytes are available
            # in request.image_base64). This happens only in the proposal branch,
            # so ordinary photo critiques never touch R2.
            if getattr(plugin, "needs_hosted_image", False):
                if not request.image_base64:
                    # User requested posting without sending a photo — refuse early
                    # so no incomplete payload gets stored in pending_actions.
                    return CoreResponse(content="Please send a photo to post to Instagram.")
                if self._r2 is None:
                    # Defensive guard: wiring only registers instagram_post when R2
                    # is configured, so this branch is normally unreachable.
                    return CoreResponse(
                        content="Image hosting is not configured; cannot post to Instagram."
                    )
                img_bytes = base64.b64decode(request.image_base64)
                r2_key = f"{request.user_id}/{uuid.uuid4()}.jpg"
                r2_url = await self._r2.upload(
                    img_bytes,
                    key=r2_key,
                    content_type=request.image_mime or "image/jpeg",
                )
                pa = PendingActionProposal(
                    action_type=pa.action_type,
                    action_payload={**pa.action_payload, "image_url": r2_url},
                    preview_text=pa.preview_text,
                )
                log.info("engine.r2_upload", key=r2_key, action_type=pa.action_type)

            # Single-pending enforcement: cancel any existing pending action for
            # this user before inserting the new one (avoids partial-unique-index
            # violation).
            existing_result = await db.execute(
                select(PendingAction).where(
                    PendingAction.user_id == request.user_id,
                    PendingAction.status == "pending",
                )
            )
            existing_row = existing_result.scalar_one_or_none()
            if existing_row is not None:
                existing_row.status = "cancelled"
                await db.flush()

            new_id = uuid.uuid4()
            expires_at = datetime.now(UTC) + timedelta(minutes=self._settings.approval_ttl_minutes)
            pending_row = PendingAction(
                id=new_id,
                user_id=request.user_id,
                action_type=pa.action_type,
                action_payload=pa.action_payload,
                status="pending",
                preview_text=pa.preview_text,
                expires_at=expires_at,
            )
            db.add(pending_row)
            await db.flush()
            log.info(
                "engine.proposal",
                action_type=pa.action_type,
                pending_id=str(new_id),
            )
            return CoreResponse(
                content=pa.preview_text,
                tool_calls_made=plan_result.tool_calls_made,
                proposal=ProposalPayload(
                    pending_action_id=new_id,
                    preview_text=pa.preview_text,
                ),
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
