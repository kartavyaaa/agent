"""Tests for ToolRegistry, including OpenAI strict-mode schema compliance."""

from __future__ import annotations

import uuid
from typing import Any, ClassVar
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.tools.registry import _INJECTED_CONTEXT_KEYS, ToolRegistry
from plugins.base import HealthStatus, PluginBase
from plugins.file_reader.plugin import FileReaderPlugin
from plugins.reminders.plugin import RemindersPlugin
from plugins.web_search.plugin import WebSearchPlugin

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _all_object_nodes(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect every dict node in the schema that is an object or has properties."""
    nodes: list[dict[str, Any]] = []
    if schema.get("type") == "object" or "properties" in schema:
        nodes.append(schema)
    for sub in schema.get("properties", {}).values():
        if isinstance(sub, dict):
            nodes.extend(_all_object_nodes(sub))
    for sub in schema.get("$defs", {}).values():
        if isinstance(sub, dict):
            nodes.extend(_all_object_nodes(sub))
    if isinstance(schema.get("items"), dict):
        nodes.extend(_all_object_nodes(schema["items"]))
    return nodes


def _make_web_search_plugin() -> WebSearchPlugin:
    client = MagicMock()
    client.health_check = MagicMock(return_value=True)
    return WebSearchPlugin(client=client)


def _make_file_reader_plugin() -> FileReaderPlugin:
    client = MagicMock()
    llm = MagicMock()
    client.health_check = MagicMock(return_value=True)
    return FileReaderPlugin(client=client, llm=llm, fast_model="gpt-5.4-nano")


# ---------------------------------------------------------------------------
# OpenAI strict-mode requirements: additionalProperties=false + required=all keys
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "plugin",
    [
        RemindersPlugin(),
        _make_web_search_plugin(),
        _make_file_reader_plugin(),
    ],
    ids=["create_reminder", "web_search", "read_file"],
)
def test_tool_schema_openai_strict_compliance(plugin: Any) -> None:
    """Every object node must have additionalProperties=false and required=list(properties.keys()).

    Both are required by OpenAI strict function-calling.  Pydantic emits neither by default
    (additionalProperties is absent; defaulted fields are omitted from required).
    """
    registry = ToolRegistry()
    registry.register(plugin)
    tools = registry.get_tools_for_llm()
    assert len(tools) == 1
    schema = tools[0].parameters

    object_nodes = _all_object_nodes(schema)
    assert object_nodes, f"No object nodes found in schema for {tools[0].name}"
    for node in object_nodes:
        assert node.get("additionalProperties") is False, (
            f"Tool '{tools[0].name}': object node missing additionalProperties=false.\n"
            f"Node: {node}"
        )
        props = node.get("properties", {})
        if props:
            assert set(node.get("required", [])) == set(props.keys()), (
                f"Tool '{tools[0].name}': required does not cover all properties.\n"
                f"properties keys: {set(props.keys())}\n"
                f"required: {node.get('required')}"
            )


def test_tool_schema_user_id_excluded() -> None:
    """user_id must never appear in the LLM-visible schema."""
    registry = ToolRegistry()
    registry.register(RemindersPlugin())
    tools = registry.get_tools_for_llm()
    schema = tools[0].parameters
    assert "user_id" not in schema.get("properties", {})
    assert "user_id" not in schema.get("required", [])


def test_get_tools_for_llm_returns_one_tool_per_plugin() -> None:
    registry = ToolRegistry()
    registry.register(RemindersPlugin())
    registry.register(_make_web_search_plugin())
    tools = registry.get_tools_for_llm()
    names = {t.name for t in tools}
    assert names == {"create_reminder", "web_search"}


# ---------------------------------------------------------------------------
# _INJECTED_CONTEXT_KEYS — image_urls (plural) support
# ---------------------------------------------------------------------------


def test_image_urls_in_injected_context_keys() -> None:
    assert "image_urls" in _INJECTED_CONTEXT_KEYS


def test_image_url_still_in_injected_context_keys() -> None:
    assert "image_url" in _INJECTED_CONTEXT_KEYS


# ---------------------------------------------------------------------------
# execute() injection of image_urls list
# ---------------------------------------------------------------------------


class _UrlsInput(BaseModel):
    caption: str


class _UrlsOutput(BaseModel):
    result: str
    confirmation: str = "ok"


class _UrlsConfig(BaseModel):
    pass


class _PluginWithImageUrls(PluginBase):
    """Plugin that declares image_urls: list[str] in execute() — should receive the injected list."""

    name: ClassVar[str] = "plugin_with_image_urls"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "test"
    capabilities: ClassVar[list[str]] = []
    permissions: ClassVar[list[str]] = []
    dependencies: ClassVar[list[str]] = []
    input_schema = _UrlsInput
    output_schema = _UrlsOutput
    config_schema = _UrlsConfig

    received_image_urls: list[str] | None = None

    async def execute(
        self,
        input: BaseModel,  # noqa: A002
        *,
        user_id: uuid.UUID,
        db: AsyncSession,
        image_urls: list[str] | None = None,
        **kwargs: Any,
    ) -> _UrlsOutput:
        assert isinstance(input, _UrlsInput)
        _PluginWithImageUrls.received_image_urls = image_urls
        return _UrlsOutput(result="done")

    async def health_check(self) -> HealthStatus:
        from datetime import UTC, datetime

        return HealthStatus(status="healthy", message="ok", checked_at=datetime.now(UTC))


class _PluginWithoutImageUrls(PluginBase):
    """Plugin that does NOT declare image_urls — should not receive it."""

    name: ClassVar[str] = "plugin_without_image_urls"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "test"
    capabilities: ClassVar[list[str]] = []
    permissions: ClassVar[list[str]] = []
    dependencies: ClassVar[list[str]] = []
    input_schema = _UrlsInput
    output_schema = _UrlsOutput
    config_schema = _UrlsConfig

    received_kwargs: dict[str, Any] = {}

    async def execute(
        self,
        input: BaseModel,  # noqa: A002
        *,
        user_id: uuid.UUID,
        db: AsyncSession,
        **kwargs: Any,
    ) -> _UrlsOutput:
        assert isinstance(input, _UrlsInput)
        _PluginWithoutImageUrls.received_kwargs = dict(kwargs)
        return _UrlsOutput(result="done")

    async def health_check(self) -> HealthStatus:
        from datetime import UTC, datetime

        return HealthStatus(status="healthy", message="ok", checked_at=datetime.now(UTC))


@pytest.mark.asyncio
async def test_image_urls_stripped_from_llm_args_and_forwarded_to_execute() -> None:
    registry = ToolRegistry()
    registry.register(_PluginWithImageUrls())
    _PluginWithImageUrls.received_image_urls = None

    urls = ["https://r2.example.com/a.jpg", "https://r2.example.com/b.jpg"]
    raw_args = {"caption": "hello", "image_urls": urls}

    await registry.execute(
        "plugin_with_image_urls",
        raw_args,
        user_id=uuid.uuid4(),
        db=MagicMock(),
        _approved=True,
    )

    assert _PluginWithImageUrls.received_image_urls == urls


@pytest.mark.asyncio
async def test_image_urls_not_forwarded_to_plugin_without_param() -> None:
    registry = ToolRegistry()
    registry.register(_PluginWithoutImageUrls())
    _PluginWithoutImageUrls.received_kwargs = {}

    urls = ["https://r2.example.com/a.jpg"]
    raw_args = {"caption": "hello", "image_urls": urls}

    await registry.execute(
        "plugin_without_image_urls",
        raw_args,
        user_id=uuid.uuid4(),
        db=MagicMock(),
        _approved=True,
    )

    assert "image_urls" not in _PluginWithoutImageUrls.received_kwargs


@pytest.mark.asyncio
async def test_image_url_singular_still_forwarded() -> None:
    """Regression: singular image_url injection still works after adding image_urls."""

    class _SingularPlugin(PluginBase):
        name: ClassVar[str] = "singular_plugin"
        version: ClassVar[str] = "1.0.0"
        description: ClassVar[str] = "test"
        capabilities: ClassVar[list[str]] = []
        permissions: ClassVar[list[str]] = []
        dependencies: ClassVar[list[str]] = []
        input_schema = _UrlsInput
        output_schema = _UrlsOutput
        config_schema = _UrlsConfig
        received_url: str | None = None

        async def execute(
            self,
            input: BaseModel,  # noqa: A002
            *,
            user_id: uuid.UUID,
            db: AsyncSession,
            image_url: str | None = None,
            **kwargs: Any,
        ) -> _UrlsOutput:
            _SingularPlugin.received_url = image_url
            return _UrlsOutput(result="done")

        async def health_check(self) -> HealthStatus:
            from datetime import UTC, datetime

            return HealthStatus(status="healthy", message="ok", checked_at=datetime.now(UTC))

    registry = ToolRegistry()
    registry.register(_SingularPlugin())
    _SingularPlugin.received_url = None

    raw_args = {"caption": "hello", "image_url": "https://r2.example.com/x.jpg"}
    await registry.execute(
        "singular_plugin",
        raw_args,
        user_id=uuid.uuid4(),
        db=MagicMock(),
        _approved=True,
    )
    assert _SingularPlugin.received_url == "https://r2.example.com/x.jpg"
