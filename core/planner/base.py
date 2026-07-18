from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.llm.base import LLMMessage, LLMTool


@dataclass
class PendingActionProposal:
    action_type: str
    action_payload: dict[str, Any]
    preview_text: str


@dataclass
class PlannerResult:
    content: str
    tool_calls_made: list[str] = field(default_factory=list)
    iterations: int = 0
    pending_action: PendingActionProposal | None = None


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
