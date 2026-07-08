from __future__ import annotations

import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import PluginNotFoundError, PluginValidationError
from core.llm.base import LLMTool
from plugins.base import PluginBase


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
    ) -> dict[str, Any]:
        plugin = self._plugins.get(name)
        if plugin is None:
            raise PluginNotFoundError(f"No plugin registered under name '{name}'")
        try:
            validated = plugin.input_schema(**raw_args)
        except ValidationError as exc:
            raise PluginValidationError(str(exc)) from exc
        result = await plugin.execute(validated, user_id=user_id, db=db)
        return result.model_dump()
