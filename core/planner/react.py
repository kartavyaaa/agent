from __future__ import annotations

import json
import uuid
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import PlannerMaxIterationsError, PlannerStuckLoopError
from core.llm.base import LLMConfig, LLMMessage, LLMProvider, LLMTool, LLMToolCall
from core.planner.base import PlannerBase, PlannerResult
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

            # Append assistant turn (carries all tool calls for this step)
            history.append(
                LLMMessage(
                    role="assistant",
                    content=llm_resp.content or "",
                    tool_calls=llm_resp.tool_calls,
                )
            )

            # Execute every tool in this batch; append results to history
            for tc in llm_resp.tool_calls:
                tools_called.append(tc.name)
                log.info("planner.tool_call", tool=tc.name, iteration=iteration)
                out = await self._registry.execute(
                    tc.name,
                    tc.arguments,
                    user_id=user_id,
                    db=db,
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
