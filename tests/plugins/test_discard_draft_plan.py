"""Unit tests for DiscardDraftPlanPlugin."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.exceptions import PluginError
from plugins.build_content_plan.discard import DiscardDraftPlanInput, DiscardDraftPlanPlugin

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_plugin() -> DiscardDraftPlanPlugin:
    return DiscardDraftPlanPlugin()


def _make_plan(status: str = "draft") -> MagicMock:
    plan = MagicMock()
    plan.id = uuid.uuid4()
    plan.user_id = uuid.uuid4()
    plan.status = status
    return plan


def _make_db(plan: MagicMock | None = None) -> MagicMock:
    db = MagicMock()
    db.flush = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = plan
    db.execute = AsyncMock(return_value=result)
    return db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discard_happy_path() -> None:
    plugin = _make_plugin()
    plan = _make_plan(status="draft")
    db = _make_db(plan)

    result = await plugin.execute(
        DiscardDraftPlanInput(plan_id=str(plan.id)),
        user_id=plan.user_id,
        db=db,
    )

    assert plan.status == "discarded"
    db.flush.assert_called_once()
    assert "discarded" in result.confirmation.lower()


@pytest.mark.asyncio
async def test_discard_not_found_raises() -> None:
    plugin = _make_plugin()
    db = _make_db(plan=None)
    with pytest.raises(PluginError, match="No active draft plan"):
        await plugin.execute(
            DiscardDraftPlanInput(plan_id=str(uuid.uuid4())),
            user_id=uuid.uuid4(),
            db=db,
        )


@pytest.mark.asyncio
async def test_discard_already_approved_raises() -> None:
    """An approved plan is not returned by the draft-filter query, so scalar_one_or_none=None."""
    plugin = _make_plugin()
    db = _make_db(plan=None)  # query filters status='draft', so approved plan → None
    with pytest.raises(PluginError, match="No active draft plan"):
        await plugin.execute(
            DiscardDraftPlanInput(plan_id=str(uuid.uuid4())),
            user_id=uuid.uuid4(),
            db=db,
        )
