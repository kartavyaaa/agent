"""Unit tests for the new _image_urls_provider lazy injection in ToolRegistry."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.tools.registry import ToolRegistry

# ---------------------------------------------------------------------------
# Fake plugin helpers
# ---------------------------------------------------------------------------


class _FakeOutput(BaseModel):
    confirmation: str = "done"


class _FakeInput(BaseModel):
    pass


def _make_fake_plugin(
    *,
    needs_hosted_images: bool = False,
    needs_hosted_image: bool = False,
    requires_approval: bool = False,
    name: str = "fake_plugin",
) -> Any:
    """Build a minimal fake plugin with the right attributes and a proper execute signature."""
    execute_calls: list[dict[str, Any]] = []

    async def _execute(
        input: BaseModel,
        *,
        user_id: uuid.UUID,
        db: AsyncSession,
        image_url: str | None = None,
        image_urls: list[str] | None = None,
        **kwargs: Any,
    ) -> _FakeOutput:
        execute_calls.append({"image_url": image_url, "image_urls": image_urls})
        return _FakeOutput()

    plugin: Any = MagicMock()
    plugin.name = name
    plugin.needs_hosted_images = needs_hosted_images
    plugin.needs_hosted_image = needs_hosted_image
    plugin.requires_approval = requires_approval
    plugin.input_schema = _FakeInput
    plugin.execute = _execute
    plugin._execute_calls = execute_calls

    return plugin


def _make_registry_with_plugin(plugin: Any) -> ToolRegistry:
    registry = ToolRegistry()
    registry._plugins[plugin.name] = plugin
    return registry


# ---------------------------------------------------------------------------
# Tests: _image_urls_provider injection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_image_urls_provider_called_when_needs_hosted_images() -> None:
    """Provider is called when needs_hosted_images=True, requires_approval=False."""
    plugin = _make_fake_plugin(needs_hosted_images=True, requires_approval=False)
    registry = _make_registry_with_plugin(plugin)

    uploaded_urls = ["https://r2.example.com/a.jpg", "https://r2.example.com/b.jpg"]
    provider = AsyncMock(return_value=uploaded_urls)

    await registry.execute(
        "fake_plugin",
        {},
        user_id=uuid.uuid4(),
        db=MagicMock(),
        _image_urls_provider=provider,
    )

    provider.assert_called_once()
    assert len(plugin._execute_calls) == 1
    assert plugin._execute_calls[0]["image_urls"] == uploaded_urls


@pytest.mark.asyncio
async def test_image_urls_provider_not_called_when_needs_hosted_images_false() -> None:
    """Provider is NOT called when needs_hosted_images=False."""
    plugin = _make_fake_plugin(needs_hosted_images=False, requires_approval=False)
    registry = _make_registry_with_plugin(plugin)

    provider = AsyncMock(return_value=["url"])

    await registry.execute(
        "fake_plugin",
        {},
        user_id=uuid.uuid4(),
        db=MagicMock(),
        _image_urls_provider=provider,
    )

    provider.assert_not_called()
    assert plugin._execute_calls[0]["image_urls"] is None


@pytest.mark.asyncio
async def test_image_urls_provider_not_called_when_requires_approval() -> None:
    """Provider is NOT called when requires_approval=True (uses old approval path)."""
    plugin = _make_fake_plugin(needs_hosted_images=True, requires_approval=True)
    registry = _make_registry_with_plugin(plugin)

    provider = AsyncMock(return_value=["url"])

    result = await registry.execute(
        "fake_plugin",
        {},
        user_id=uuid.uuid4(),
        db=MagicMock(),
        _image_urls_provider=provider,
    )

    # Approval gate fires first — provider never reached, execute never called
    provider.assert_not_called()
    assert result.get("__approval_required__") is True
    assert len(plugin._execute_calls) == 0


@pytest.mark.asyncio
async def test_image_urls_provider_not_called_when_already_injected() -> None:
    """Provider is NOT called if image_urls is already in raw_args (already injected)."""
    plugin = _make_fake_plugin(needs_hosted_images=True, requires_approval=False)
    registry = _make_registry_with_plugin(plugin)

    provider = AsyncMock(return_value=["new_url"])
    pre_injected = ["already_here.jpg"]

    await registry.execute(
        "fake_plugin",
        {"image_urls": pre_injected},
        user_id=uuid.uuid4(),
        db=MagicMock(),
        _image_urls_provider=provider,
    )

    provider.assert_not_called()
    assert plugin._execute_calls[0]["image_urls"] == pre_injected


@pytest.mark.asyncio
async def test_image_urls_provider_not_called_when_none() -> None:
    """Provider is NOT called if _image_urls_provider is None."""
    plugin = _make_fake_plugin(needs_hosted_images=True, requires_approval=False)
    registry = _make_registry_with_plugin(plugin)

    await registry.execute(
        "fake_plugin",
        {},
        user_id=uuid.uuid4(),
        db=MagicMock(),
        _image_urls_provider=None,
    )

    # No exception — just no injection (image_urls stays None)
    assert plugin._execute_calls[0]["image_urls"] is None
