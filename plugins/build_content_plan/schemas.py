from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class PlanItem(BaseModel):
    # 0-based indices into the flat image_urls list injected by the engine.
    # URLs are trusted server context and never appear in the LLM-facing schema.
    image_indices: list[int]
    caption: str
    scheduled_for: datetime  # user's local wall-clock; localize_to_utc applied in execute()


class BuildContentPlanInput(BaseModel):
    items: list[PlanItem]


class BuildContentPlanOutput(BaseModel):
    content_plan_id: str
    scheduled_post_ids: list[str]
    confirmation: str


class BuildContentPlanConfig(BaseModel):
    pass


# ---------------------------------------------------------------------------
# list_content_plans
# ---------------------------------------------------------------------------


class ContentPlanSummary(BaseModel):
    content_plan_id: str
    created_at_local: str
    total_items: int
    scheduled_items: int
    next_scheduled_local: str | None


class ListContentPlansInput(BaseModel):
    pass


class ListContentPlansOutput(BaseModel):
    plans: list[ContentPlanSummary]
    count: int


class ListContentPlansConfig(BaseModel):
    pass


# ---------------------------------------------------------------------------
# cancel_content_plan
# ---------------------------------------------------------------------------


class CancelContentPlanInput(BaseModel):
    content_plan_id: str  # UUID str from a list_content_plans call


class CancelContentPlanOutput(BaseModel):
    content_plan_id: str
    cancelled_count: int
    detail: str


class CancelContentPlanConfig(BaseModel):
    pass
