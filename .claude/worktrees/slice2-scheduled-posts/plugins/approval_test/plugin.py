from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from plugins.approval_test.schemas import (
    ApprovalTestConfig,
    ApprovalTestInput,
    ApprovalTestOutput,
)
from plugins.base import HealthStatus, PluginBase


class ApprovalTestPlugin(PluginBase):
    """Trivial approval-requiring plugin used to prove the approval flow end-to-end.

    Requires explicit user confirmation before executing — exercises the full
    pause → propose → confirm → execute cycle without any external side effects.
    Replace or remove once a real approval-requiring plugin (e.g. Instagram) is wired.
    """

    name = "dummy_confirm_action"
    version = "1.0.0"
    description = (
        "A test action that requires your confirmation before running. "
        "Echoes a message back after you approve it."
    )
    capabilities: ClassVar[list[str]] = ["approval_test"]
    permissions: ClassVar[list[str]] = []
    dependencies: ClassVar[list[str]] = []
    requires_approval: ClassVar[bool] = True
    input_schema = ApprovalTestInput
    output_schema = ApprovalTestOutput
    config_schema = ApprovalTestConfig

    async def execute(
        self,
        input: BaseModel,
        *,
        user_id: uuid.UUID,
        db: AsyncSession,
        **kwargs: Any,
    ) -> ApprovalTestOutput:
        assert isinstance(input, ApprovalTestInput)
        return ApprovalTestOutput(
            result=input.message,
            confirmation=f"Dummy action confirmed: '{input.message}'",
        )

    async def health_check(self) -> HealthStatus:
        return HealthStatus(
            status="healthy",
            message="ok",
            checked_at=datetime.now(UTC),
        )
