from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import PlannerMaxIterationsError, PlannerStuckLoopError
from core.llm.base import LLMConfig, LLMMessage, LLMProvider, LLMTool, LLMToolCall
from core.planner.base import PendingActionProposal, PlannerBase, PlannerResult
from core.tools.registry import ToolRegistry


def _call_signature(tool_calls: list[LLMToolCall]) -> str:
    """Stable string fingerprint of a tool-call batch for stuck-loop detection.

    Uses JSON serialisation to handle nested dict/list argument values safely.
    frozenset(items()) would crash on unhashable nested values.
    """
    return json.dumps(
        sorted((tc.name, json.dumps(tc.arguments, sort_keys=True)) for tc in tool_calls)
    )


def _format_tool_result(out: dict[str, Any]) -> str:
    return str(out.get("confirmation") or out.get("message") or out)


class ReActPlanner(PlannerBase):
    """ReAct-style planner: Reason → Act → Observe loop.

    Each iteration calls the LLM. If it returns tool calls, all are executed
    and their results are appended to the message history before the next call.
    The loop ends when the LLM returns a plain message, the iteration cap is
    reached, or a stuck loop is detected.

    The planner never commits the DB session — the engine owns the transaction.
    """

    def __init__(
        self,
        *,
        llm: LLMProvider,
        registry: ToolRegistry,
        config: LLMConfig,
        max_iterations: int,
    ) -> None:
        self._llm = llm
        self._registry = registry
        self._config = config
        self._max_iterations = max_iterations

    async def run(
        self,
        *,
        messages: list[LLMMessage],
        tools: list[LLMTool],
        user_id: uuid.UUID,
        db: AsyncSession,
        image_url_provider: Callable[[], Awaitable[str]] | None = None,
        image_urls_provider: Callable[[], Awaitable[list[str]]] | None = None,
    ) -> PlannerResult:
        log = structlog.get_logger().bind(user_id=str(user_id))
        history = list(messages)
        tools_called: list[str] = []
        last_call_sig: str | None = None

        for iteration in range(self._max_iterations):
            llm_resp = await self._llm.complete(
                messages=history,
                tools=tools or None,
                config=self._config,
            )

            if llm_resp.response_type == "message":
                log.info("planner.done", iterations=iteration + 1)
                return PlannerResult(
                    content=llm_resp.content or "",
                    tool_calls_made=tools_called,
                    iterations=iteration + 1,
                )

            # Stuck-loop detection: same batch of tool calls with identical args
            current_sig = _call_signature(llm_resp.tool_calls)
            if current_sig == last_call_sig:
                raise PlannerStuckLoopError(
                    f"Identical tool batch repeated on iteration {iteration}"
                )
            last_call_sig = current_sig

            # Echo any reasoning items back before the assistant tool-call items
            # so the model preserves chain-of-thought context on the next turn.
            for raw in llm_resp.reasoning_items:
                history.append(LLMMessage(role="reasoning", content="", raw_item=raw))

            # Append assistant turn (carries all tool calls for this step)
            history.append(
                LLMMessage(
                    role="assistant",
                    content=llm_resp.content or "",
                    tool_calls=llm_resp.tool_calls,
                )
            )

            # Execute every tool in this batch; append results to history.
            # If any tool returns an approval sentinel, halt immediately —
            # the sentinel is NOT appended to history (it must never become an
            # LLM observation). Remaining batch tools are discarded; the engine
            # will surface a proposal instead of continuing the plan.
            for tc in llm_resp.tool_calls:
                tools_called.append(tc.name)
                log.info("planner.tool_call", tool=tc.name, iteration=iteration)
                out = await self._registry.execute(
                    tc.name,
                    tc.arguments,
                    user_id=user_id,
                    db=db,
                    _image_url_provider=image_url_provider,
                    _image_urls_provider=image_urls_provider,
                )
                if out.get("__approval_required__"):
                    plugin = self._registry.get_plugin(out["tool"])
                    if plugin is not None:
                        preview = plugin.build_preview(out["args"])
                    else:
                        preview = (
                            f"I'd like to run '{out['tool']}' with these parameters: {out['args']}"
                        )
                    log.info("planner.approval_required", tool=out["tool"])
                    return PlannerResult(
                        content="",
                        tool_calls_made=tools_called,
                        iterations=iteration + 1,
                        pending_action=PendingActionProposal(
                            action_type=out["tool"],
                            action_payload=out["args"],
                            preview_text=preview,
                        ),
                    )
                result_str = _format_tool_result(out)
                history.append(
                    LLMMessage(
                        role="tool_result",
                        content=result_str,
                        tool_call_id=tc.id,
                    )
                )

        raise PlannerMaxIterationsError(
            f"Planner exceeded {self._max_iterations} iterations without a final answer"
        )
