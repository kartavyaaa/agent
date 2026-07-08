"""OpenAI Responses API adapter.

IMPORTANT: This is the only file in the codebase that may import from `openai`.
All callers use the LLMProvider interface from core.llm.base.

Translation notes (Responses API):
  - LLMMessage list → input[] array of typed Items
  - role="tool_result" → {"type": "function_call_output", "call_id": ..., "output": str}
  - item.arguments is a JSON STRING — must json.loads() before use
  - output must always be a string (JSON-serialise dicts)
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import openai
from openai import NOT_GIVEN, AsyncOpenAI
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from core.exceptions import LLMError, LLMRateLimitError, LLMTimeoutError
from core.llm.base import (
    LLMChunk,
    LLMConfig,
    LLMMessage,
    LLMProvider,
    LLMResponse,
    LLMTool,
    LLMToolCall,
    TokenUsage,
)


class OpenAIProvider(LLMProvider):
    """Concrete LLMProvider backed by the OpenAI Responses API.

    Only this file imports openai. Embedding results are cached in-process;
    completion results are never cached (tool calls must not be cached).
    """

    def __init__(
        self,
        *,
        api_key: str,
        default_model: str,
        fast_model: str,
        timeout: float = 30.0,
        max_retries: int = 0,
    ) -> None:
        self._client = AsyncOpenAI(api_key=api_key, timeout=timeout, max_retries=max_retries)
        self._default_model = default_model
        self._fast_model = fast_model
        self._embed_cache: dict[str, list[float]] = {}

    @retry(
        retry=retry_if_exception_type(LLMRateLimitError),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    async def complete(
        self,
        messages: list[LLMMessage],
        tools: list[LLMTool] | None,
        config: LLMConfig,
    ) -> LLMResponse:
        input_items = [_to_item(m) for m in messages]
        tool_defs = _to_tool_defs(tools) if tools else NOT_GIVEN

        try:
            response = await self._client.responses.create(  # type: ignore[call-overload]
                model=config.model,
                input=input_items,
                tools=tool_defs,
                tool_choice=config.tool_choice if tools else NOT_GIVEN,
                temperature=config.temperature,
            )
        except openai.RateLimitError as exc:
            raise LLMRateLimitError(str(exc)) from exc
        except openai.APITimeoutError as exc:
            raise LLMTimeoutError(str(exc)) from exc

        tool_calls: list[LLMToolCall] = []
        content: str | None = None

        for item in response.output:
            if item.type == "function_call":
                try:
                    args: dict[str, object] = json.loads(item.arguments)
                except json.JSONDecodeError as exc:
                    raise LLMError(
                        f"malformed tool arguments from model: {item.arguments!r}"
                    ) from exc
                tool_calls.append(LLMToolCall(id=item.call_id, name=item.name, arguments=args))
            elif item.type == "message":
                content = "".join(c.text for c in item.content if hasattr(c, "text"))

        usage = TokenUsage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            total_tokens=response.usage.total_tokens,
            cached_tokens=getattr(response.usage, "cached_tokens", None),
        )
        return LLMResponse(
            response_type="tool_calls" if tool_calls else "message",
            content=content,
            tool_calls=tool_calls,
            model=response.model,
            usage=usage,
            raw_response_id=response.id,
        )

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        results: list[list[float]] = []
        uncached: list[str] = []
        uncached_idx: list[int] = []

        for i, text in enumerate(texts):
            if text in self._embed_cache:
                results.append(self._embed_cache[text])
            else:
                uncached.append(text)
                uncached_idx.append(i)
                results.append([])

        if uncached:
            r = await self._client.embeddings.create(model=model, input=uncached)
            for j, idx in enumerate(uncached_idx):
                vec: list[float] = r.data[j].embedding
                self._embed_cache[uncached[j]] = vec
                results[idx] = vec

        return results

    def stream(
        self,
        messages: list[LLMMessage],
        tools: list[LLMTool] | None,
        config: LLMConfig,
    ) -> AsyncIterator[LLMChunk]:
        raise NotImplementedError("streaming not implemented in this slice")

    async def count_tokens(self, messages: list[LLMMessage]) -> int:
        raise NotImplementedError

    def list_models(self) -> list[str]:
        return ["gpt-5.5", "gpt-5.4-nano", "gpt-5.4-mini"]


def _to_item(msg: LLMMessage) -> dict:  # type: ignore[type-arg]
    """Translate one LLMMessage to a Responses API input item."""
    if msg.role == "tool_result":
        output = msg.content if isinstance(msg.content, str) else json.dumps(msg.content)
        return {
            "type": "function_call_output",
            "call_id": msg.tool_call_id,
            "output": output,
        }
    if msg.role == "assistant" and msg.tool_calls:
        return {
            "role": "assistant",
            "content": [
                {
                    "type": "function_call",
                    "call_id": tc.id,
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments),
                }
                for tc in msg.tool_calls
            ],
        }
    return {"role": msg.role, "content": msg.content}


def _to_tool_defs(tools: list[LLMTool]) -> list[dict]:  # type: ignore[type-arg]
    return [
        {
            "type": "function",
            "name": t.name,
            "description": t.description,
            "parameters": t.parameters,
            "strict": t.strict,
        }
        for t in tools
    ]
