from __future__ import annotations

import uuid
from typing import ClassVar
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.tools.registry import ToolRegistry
from plugins.base import HealthStatus, PluginBase


class _FakeInput(BaseModel):
    value: str


class _FakeOutput(BaseModel):
    result: str
    confirmation: str = "ok"


class _FakeConfig(BaseModel):
    pass


class _NormalPlugin(PluginBase):
    name: ClassVar[str] = "normal_plugin"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Normal plugin, no approval required."
    capabilities: ClassVar[list[str]] = []
    permissions: ClassVar[list[str]] = []
    dependencies: ClassVar[list[str]] = []
    input_schema = _FakeInput
    output_schema = _FakeOutput
    config_schema = _FakeConfig
    requires_approval: ClassVar[bool] = False

    async def execute(
        self, input: BaseModel, *, user_id: uuid.UUID, db: AsyncSession
    ) -> _FakeOutput:
        assert isinstance(input, _FakeInput)
        return _FakeOutput(result=input.value)

    async def health_check(self) -> HealthStatus:
        from datetime import UTC, datetime

        return HealthStatus(status="healthy", message="ok", checked_at=datetime.now(UTC))


class _ApprovalPlugin(_NormalPlugin):
    name: ClassVar[str] = "approval_plugin"
    description: ClassVar[str] = "Requires approval."
    requires_approval: ClassVar[bool] = True


def _make_registry(plugin: PluginBase) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(plugin)
    return reg


def _make_db() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


@pytest.mark.asyncio
async def test_approval_plugin_not_approved_returns_sentinel() -> None:
    reg = _make_registry(_ApprovalPlugin())
    uid = uuid.uuid4()
    result = await reg.execute("approval_plugin", {"value": "x"}, user_id=uid, db=_make_db())
    assert result.get("__approval_required__") is True
    assert result["tool"] == "approval_plugin"
    assert result["args"] == {"value": "x"}


@pytest.mark.asyncio
async def test_approval_plugin_with_approved_flag_executes() -> None:
    plugin = _ApprovalPlugin()
    reg = _make_registry(plugin)
    uid = uuid.uuid4()
    result = await reg.execute(
        "approval_plugin", {"value": "hello"}, user_id=uid, db=_make_db(), _approved=True
    )
    assert result["result"] == "hello"
    assert "__approval_required__" not in result


@pytest.mark.asyncio
async def test_normal_plugin_executes_without_approval() -> None:
    reg = _make_registry(_NormalPlugin())
    uid = uuid.uuid4()
    result = await reg.execute("normal_plugin", {"value": "world"}, user_id=uid, db=_make_db())
    assert result["result"] == "world"
    assert "__approval_required__" not in result


@pytest.mark.asyncio
async def test_normal_plugin_with_approved_flag_still_executes() -> None:
    reg = _make_registry(_NormalPlugin())
    uid = uuid.uuid4()
    result = await reg.execute(
        "normal_plugin", {"value": "test"}, user_id=uid, db=_make_db(), _approved=True
    )
    assert result["result"] == "test"
