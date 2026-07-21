"""Unit tests for lazy R2 upload via _image_url_provider in registry.execute().

Verifies that the provider is invoked exactly when expected:
- critique / no-tool-call turn → 0 invocations
- schedule_post (needs_hosted_image=True, requires_approval=False) → exactly 1 invocation
- instagram_post (requires_approval=True) → 0 invocations (approval sentinel fires first)

Pure logic test: mock provider + registry stubs, no real R2, DB, or HTTP.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.tools.registry import ToolRegistry
from plugins.base import HealthStatus, PluginBase

# ---------------------------------------------------------------------------
# Stub plugins
# ---------------------------------------------------------------------------


class _StubInput(BaseModel):
    value: str = "x"


class _StubOutput(BaseModel):
    result: str = "ok"
    confirmation: str = "ok"


class _StubConfig(BaseModel):
    pass


def _make_health() -> HealthStatus:
    return HealthStatus(status="healthy", message="ok", checked_at=datetime.now(UTC))


class _NormalPlugin(PluginBase):
    name: ClassVar[str] = "normal_plugin"
    version: ClassVar[str] = "1.0"
    description: ClassVar[str] = "normal"
    capabilities: ClassVar[list[str]] = []
    permissions: ClassVar[list[str]] = []
    dependencies: ClassVar[list[str]] = []
    input_schema = _StubInput
    output_schema = _StubOutput
    config_schema = _StubConfig
    requires_approval: ClassVar[bool] = False
    needs_hosted_image: ClassVar[bool] = False

    async def execute(
        self, input: BaseModel, *, user_id: uuid.UUID, db: AsyncSession, **kwargs: Any
    ) -> _StubOutput:  # noqa: A002
        return _StubOutput()

    async def health_check(self) -> HealthStatus:
        return _make_health()


class _HostedImagePlugin(PluginBase):
    """Simulates schedule_post: needs_hosted_image=True, requires_approval=False."""

    name: ClassVar[str] = "hosted_image_plugin"
    version: ClassVar[str] = "1.0"
    description: ClassVar[str] = "hosted image"
    capabilities: ClassVar[list[str]] = []
    permissions: ClassVar[list[str]] = []
    dependencies: ClassVar[list[str]] = []
    input_schema = _StubInput
    output_schema = _StubOutput
    config_schema = _StubConfig
    requires_approval: ClassVar[bool] = False
    needs_hosted_image: ClassVar[bool] = True

    received_image_url: str | None = None

    async def execute(
        self,
        input: BaseModel,
        *,
        user_id: uuid.UUID,
        db: AsyncSession,
        image_url: str | None = None,
        **kwargs: Any,
    ) -> _StubOutput:  # noqa: A002
        _HostedImagePlugin.received_image_url = image_url
        if image_url is None:
            from core.exceptions import PluginError

            raise PluginError("schedule_post requires image_url")
        return _StubOutput()

    async def health_check(self) -> HealthStatus:
        return _make_health()


class _ApprovalPlugin(PluginBase):
    """Simulates instagram_post: requires_approval=True, needs_hosted_image=True."""

    name: ClassVar[str] = "approval_plugin"
    version: ClassVar[str] = "1.0"
    description: ClassVar[str] = "approval"
    capabilities: ClassVar[list[str]] = []
    permissions: ClassVar[list[str]] = []
    dependencies: ClassVar[list[str]] = []
    input_schema = _StubInput
    output_schema = _StubOutput
    config_schema = _StubConfig
    requires_approval: ClassVar[bool] = True
    needs_hosted_image: ClassVar[bool] = True

    async def execute(
        self, input: BaseModel, *, user_id: uuid.UUID, db: AsyncSession, **kwargs: Any
    ) -> _StubOutput:  # noqa: A002
        return _StubOutput()

    async def health_check(self) -> HealthStatus:
        return _make_health()


def _make_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_NormalPlugin())
    registry.register(_HostedImagePlugin())
    registry.register(_ApprovalPlugin())
    return registry


def _make_provider(url: str = "https://cdn.example.com/photo.jpg") -> tuple[AsyncMock, list[int]]:
    call_count: list[int] = [0]

    async def provider() -> str:
        call_count[0] += 1
        return url

    return AsyncMock(side_effect=provider), call_count


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normal_plugin_does_not_invoke_provider() -> None:
    registry = _make_registry()
    provider_mock, call_count = _make_provider()
    db = MagicMock(spec=AsyncSession)
    uid = uuid.uuid4()

    await registry.execute(
        "normal_plugin",
        {"value": "hello"},
        user_id=uid,
        db=db,
        _image_url_provider=provider_mock,
    )

    assert (
        call_count[0] == 0
    ), "Provider must NOT be invoked for a plugin with needs_hosted_image=False"


@pytest.mark.asyncio
async def test_hosted_image_plugin_invokes_provider_exactly_once() -> None:
    registry = _make_registry()
    provider_mock, call_count = _make_provider("https://cdn.example.com/photo.jpg")
    db = MagicMock(spec=AsyncSession)
    uid = uuid.uuid4()
    _HostedImagePlugin.received_image_url = None

    await registry.execute(
        "hosted_image_plugin",
        {"value": "caption text"},
        user_id=uid,
        db=db,
        _image_url_provider=provider_mock,
    )

    assert (
        call_count[0] == 1
    ), "Provider must be invoked exactly once for needs_hosted_image=True plugin"
    assert _HostedImagePlugin.received_image_url == "https://cdn.example.com/photo.jpg"


@pytest.mark.asyncio
async def test_approval_plugin_does_not_invoke_provider() -> None:
    """Approval sentinel fires before the needs_hosted_image check — provider never called."""
    registry = _make_registry()
    provider_mock, call_count = _make_provider()
    db = MagicMock(spec=AsyncSession)
    uid = uuid.uuid4()

    result = await registry.execute(
        "approval_plugin",
        {"value": "caption"},
        user_id=uid,
        db=db,
        _image_url_provider=provider_mock,
    )

    assert result.get("__approval_required__") is True
    assert call_count[0] == 0, "Provider must NOT be invoked when approval sentinel fires"


@pytest.mark.asyncio
async def test_no_provider_does_not_inject_image_url() -> None:
    """Without a provider, needs_hosted_image plugin receives image_url=None (raises PluginError)."""
    from core.exceptions import PluginError

    registry = _make_registry()
    db = MagicMock(spec=AsyncSession)
    uid = uuid.uuid4()
    _HostedImagePlugin.received_image_url = "untouched"

    # Plugin raises PluginError when image_url is None
    with pytest.raises(PluginError):
        await registry.execute(
            "hosted_image_plugin",
            {"value": "caption"},
            user_id=uid,
            db=db,
            _image_url_provider=None,
        )
