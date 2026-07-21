from __future__ import annotations

import uuid
from typing import Any, ClassVar
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
        self, input: BaseModel, *, user_id: uuid.UUID, db: AsyncSession, **kwargs: Any
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


# ---------------------------------------------------------------------------
# Injected-context extraction (image_url separation)
# ---------------------------------------------------------------------------


class _ImageAwareInput(BaseModel):
    caption: str


class _ImageAwareOutput(BaseModel):
    result: str
    confirmation: str = "ok"


class _ImageAwarePlugin(PluginBase):
    """Plugin that accepts image_url as an injected kwarg (not in input_schema)."""

    name: ClassVar[str] = "image_aware_plugin"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Test plugin that uses injected image_url."
    capabilities: ClassVar[list[str]] = []
    permissions: ClassVar[list[str]] = []
    dependencies: ClassVar[list[str]] = []
    input_schema = _ImageAwareInput
    output_schema = _ImageAwareOutput
    config_schema = _FakeConfig
    requires_approval: ClassVar[bool] = False

    received_image_url: str | None = None

    async def execute(
        self,
        input: BaseModel,
        *,
        user_id: uuid.UUID,
        db: AsyncSession,
        image_url: str | None = None,
        **kwargs: Any,
    ) -> _ImageAwareOutput:
        assert isinstance(input, _ImageAwareInput)
        self.received_image_url = image_url
        return _ImageAwareOutput(result=input.caption)

    async def health_check(self) -> HealthStatus:
        from datetime import UTC, datetime

        return HealthStatus(status="healthy", message="ok", checked_at=datetime.now(UTC))


@pytest.mark.asyncio
async def test_image_url_in_raw_args_is_extracted_and_passed_to_execute() -> None:
    plugin = _ImageAwarePlugin()
    reg = _make_registry(plugin)
    uid = uuid.uuid4()

    result = await reg.execute(
        "image_aware_plugin",
        {"caption": "hello", "image_url": "https://cdn.example.com/img.jpg"},
        user_id=uid,
        db=_make_db(),
    )

    # caption validated by input_schema, image_url passed as kwarg
    assert result["result"] == "hello"
    assert plugin.received_image_url == "https://cdn.example.com/img.jpg"


@pytest.mark.asyncio
async def test_image_url_not_passed_to_plugin_without_param() -> None:
    """Plugins that don't declare image_url in execute() should not receive it."""
    reg = _make_registry(_NormalPlugin())
    uid = uuid.uuid4()

    # image_url in raw_args but _NormalPlugin.execute() has no image_url param — no error
    result = await reg.execute(
        "normal_plugin",
        {"value": "test", "image_url": "https://cdn.example.com/img.jpg"},
        user_id=uid,
        db=_make_db(),
    )

    assert result["result"] == "test"


@pytest.mark.asyncio
async def test_image_url_absent_from_raw_args_passes_none_to_plugin() -> None:
    plugin = _ImageAwarePlugin()
    reg = _make_registry(plugin)
    uid = uuid.uuid4()

    await reg.execute(
        "image_aware_plugin",
        {"caption": "no image"},
        user_id=uid,
        db=_make_db(),
    )

    assert plugin.received_image_url is None


@pytest.mark.asyncio
async def test_existing_user_id_db_injection_unchanged() -> None:
    """Confirm user_id and db still flow correctly after the injected-context split."""
    received: dict[str, object] = {}

    class _TrackingPlugin(_NormalPlugin):
        name: ClassVar[str] = "tracking_plugin"

        async def execute(
            self, input: BaseModel, *, user_id: uuid.UUID, db: AsyncSession, **kwargs: Any
        ) -> _FakeOutput:
            received["user_id"] = user_id
            received["db"] = db
            assert isinstance(input, _FakeInput)
            return _FakeOutput(result=input.value)

    uid = uuid.uuid4()
    db = _make_db()
    reg = _make_registry(_TrackingPlugin())
    await reg.execute("tracking_plugin", {"value": "x"}, user_id=uid, db=db)

    assert received["user_id"] == uid
    assert received["db"] is db
