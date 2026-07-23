from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import PluginError
from models.content_plan import ContentPlan
from plugins.base import HealthStatus, PluginBase


class DiscardDraftPlanInput(BaseModel):
    plan_id: str


class DiscardDraftPlanOutput(BaseModel):
    confirmation: str


class DiscardDraftPlanConfig(BaseModel):
    pass


class DiscardDraftPlanPlugin(PluginBase):
    name: ClassVar[str] = "discard_draft_plan"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Discard an active draft content plan without scheduling anything. "
        "Call this when the user cancels, says 'forget it', 'never mind', or explicitly "
        "wants to abandon the current draft plan."
    )
    capabilities: ClassVar[list[str]] = ["discard_draft_plan"]
    permissions: ClassVar[list[str]] = ["db:write"]
    dependencies: ClassVar[list[str]] = []
    input_schema: ClassVar[type[BaseModel]] = DiscardDraftPlanInput
    output_schema: ClassVar[type[BaseModel]] = DiscardDraftPlanOutput
    config_schema: ClassVar[type[BaseModel]] = DiscardDraftPlanConfig
    requires_approval: ClassVar[bool] = False
    needs_hosted_image: ClassVar[bool] = False
    needs_hosted_images: ClassVar[bool] = False

    async def execute(
        self,
        input: BaseModel,  # noqa: A002
        *,
        user_id: uuid.UUID,
        db: AsyncSession,
        **kwargs: Any,
    ) -> DiscardDraftPlanOutput:
        assert isinstance(input, DiscardDraftPlanInput)

        result = await db.execute(
            select(ContentPlan).where(
                ContentPlan.id == uuid.UUID(input.plan_id),
                ContentPlan.user_id == user_id,
                ContentPlan.status == "draft",
            )
        )
        plan = result.scalar_one_or_none()
        if plan is None:
            raise PluginError(f"No active draft plan found with id={input.plan_id}.")

        plan.status = "discarded"
        await db.flush()
        return DiscardDraftPlanOutput(confirmation="Draft plan discarded.")

    async def health_check(self) -> HealthStatus:
        return HealthStatus(status="healthy", message="ok", checked_at=datetime.now(UTC))
