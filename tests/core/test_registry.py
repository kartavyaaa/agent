"""Tests for ToolRegistry, including OpenAI strict-mode schema compliance."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from core.tools.registry import ToolRegistry
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
