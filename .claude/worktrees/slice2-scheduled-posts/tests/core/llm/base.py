from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Literal

from pydantic import BaseModel


class LLMToolCall(BaseModel):
    id: str
    name: str
    arguments: dict  # type: ignore[type-arg]


class LLMMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool_result"]
    content: str | list[dict]  # type: ignore[type-arg]
    tool_call_id: str | None = None  # set when role=tool_result
    tool_calls: list[LLMToolCall] | None = None  # set when role=assistant with tool calls


class LLMTool(BaseModel):
    name: str
    description: str
    parameters: dict  # type: ignore[type-arg]  # JSON Schema object
    strict: bool = True


class LLMConfig(BaseModel):
    model: str
    # GPT-5 family and reasoning models on the Responses API reject temperature.
    # Default None means the parameter is omitted from the API call entirely.
    temperature: float | None = None
    max_tokens: int | None = None
    # reasoning.effort for GPT-5/o-series via Responses API: {"effort": "medium"} etc.
    # Not yet wired in the provider; placeholder for future use.
    reasoning_effort: Literal["none", "low", "medium", "high", "xhigh"] | None = None
    tool_choice: Literal["auto", "none", "required"] | str = "auto"
    parallel_tool_calls: bool = True


class TokenUsage(BaseModel):
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cached_tokens: int | None = None


class LLMResponse(BaseModel):
    response_type: Literal["message", "tool_calls"]
    content: str | None = None  # set when response_type=message
    tool_calls: list[LLMToolCall] = []  # set when response_type=tool_calls
    model: str
    usage: TokenUsage
    raw_response_id: str


class LLMChunk(BaseModel):
    delta_text: str | None = None
    delta_tool_call: LLMToolCall | None = None
    finish_reason: str | None = None


class LLMProvider(ABC):
    """Single seam for all model calls. Swap providers by swapping this implementation."""

    @abstractmethod
    async def complete(
        self,
        messages: list[LLMMessage],
        tools: list[LLMTool] | None,
        config: LLMConfig,
    ) -> LLMResponse: ...

    @abstractmethod
    def stream(
        self,
        messages: list[LLMMessage],
        tools: list[LLMTool] | None,
        config: LLMConfig,
    ) -> AsyncIterator[LLMChunk]: ...

    @abstractmethod
    async def embed(
        self,
        texts: list[str],
        model: str,
    ) -> list[list[float]]: ...

    @abstractmethod
    async def count_tokens(self, messages: list[LLMMessage]) -> int: ...

    @abstractmethod
    def list_models(self) -> list[str]: ...
