from __future__ import annotations

import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import PluginNotFoundError, PluginValidationError
from core.llm.base import LLMTool
from plugins.base import PluginBase


def _normalize_for_openai_strict(schema: dict[str, Any]) -> None:
    """Recursively enforce OpenAI strict function-calling requirements on all object nodes.

    Two rules applied at every object node:
    - additionalProperties=false (Pydantic never emits this)
    - required must list every key in properties (Pydantic omits defaulted fields)

    Both rules apply to nested $defs and items so future plugins with nested
    objects don't silently produce malformed schemas.

    Must be called AFTER user_id has been stripped from properties so it is
    not re-introduced into required.
    """
    if schema.get("type") == "object" or "properties" in schema:
        schema.setdefault("additionalProperties", False)
        props = schema.get("properties", {})
        if props:
            schema["required"] = list(props.keys())
    for sub in schema.get("properties", {}).values():
        if isinstance(sub, dict):
            _normalize_for_openai_strict(sub)
    for sub in schema.get("$defs", {}).values():
        if isinstance(sub, dict):
            _normalize_for_openai_strict(sub)
    if isinstance(schema.get("items"), dict):
        _normalize_for_openai_strict(schema["items"])


class ToolRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, PluginBase] = {}

    def register(self, plugin: PluginBase) -> None:
        self._plugins[plugin.name] = plugin

    def get_tools_for_llm(self) -> list[LLMTool]:
        tools: list[LLMTool] = []
        for plugin in self._plugins.values():
            schema = plugin.input_schema.model_json_schema()
            # user_id must never be exposed to the model — it is a trusted context
            # value injected by execute(), not sourced from LLM output.
            props = schema.get("properties", {})
            props.pop("user_id", None)
            required = schema.get("required", [])
            if "user_id" in required:
                required.remove("user_id")
            _normalize_for_openai_strict(schema)
            tools.append(
                LLMTool(
                    name=plugin.name,
                    description=plugin.description,
                    parameters=schema,
                )
            )
        return tools

    async def execute(
        self,
        name: str,
        raw_args: dict[str, Any],
        *,
        user_id: uuid.UUID,
        db: AsyncSession,
        _approved: bool = False,
    ) -> dict[str, Any]:
        plugin = self._plugins.get(name)
        if plugin is None:
            raise PluginNotFoundError(f"No plugin registered under name '{name}'")
        # Approval gate: intercept before validation and execution.
        # _approved=True bypasses this (set by the callback handler on confirm).
        if getattr(plugin, "requires_approval", False) and not _approved:
            return {
                "__approval_required__": True,
                "tool": name,
                "args": raw_args,
            }
        try:
            validated = plugin.input_schema(**raw_args)
        except ValidationError as exc:
            raise PluginValidationError(str(exc)) from exc
        result = await plugin.execute(validated, user_id=user_id, db=db)
        return result.model_dump()
