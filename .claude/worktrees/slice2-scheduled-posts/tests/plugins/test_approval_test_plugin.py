from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from plugins.approval_test.plugin import ApprovalTestPlugin
from plugins.approval_test.schemas import ApprovalTestInput


def _make_db() -> MagicMock:
    return MagicMock()


@pytest.mark.asyncio
async def test_execute_returns_confirmation() -> None:
    plugin = ApprovalTestPlugin()
    db = _make_db()
    result = await plugin.execute(
        ApprovalTestInput(message="hello world"),
        user_id=uuid.uuid4(),
        db=db,
    )
    assert result.result == "hello world"
    assert "hello world" in result.confirmation
    assert "confirmed" in result.confirmation.lower()


@pytest.mark.asyncio
async def test_execute_echoes_any_message() -> None:
    plugin = ApprovalTestPlugin()
    result = await plugin.execute(
        ApprovalTestInput(message="test 123"),
        user_id=uuid.uuid4(),
        db=_make_db(),
    )
    assert result.result == "test 123"


def test_requires_approval_is_true() -> None:
    assert ApprovalTestPlugin.requires_approval is True


@pytest.mark.asyncio
async def test_health_check_returns_healthy() -> None:
    plugin = ApprovalTestPlugin()
    status = await plugin.health_check()
    assert status.status == "healthy"
