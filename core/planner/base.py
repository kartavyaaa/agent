from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from core.llm.base import LLMMessage, LLMTool


@dataclass
class PlannerResult:
    content: str
    tool_calls_made: list[str] = field(default_factory=list)
    iterations: int = 0


class PlannerBase(ABC):
    @abstractmethod
    async def run(
        self,
        *,
        messages: list[LLMMessage],
        tools: list[LLMTool],
        user_id: uuid.UUID,
        db: AsyncSession,
    ) -> PlannerResult: ...
