from __future__ import annotations

import inspect
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import PluginNotFoundError, PluginValidationError
from core.llm.base import LLMTool
from plugins.base import PluginBase

# Keys that the engine injects into action_payload at proposal time (trusted server context).
# These are never LLM-supplied and must be separated from raw_args before input_schema validation,
# then forwarded to plugin.execute() only if the plugin's signature declares the parameter.
_INJECTED_CONTEXT_KEYS: frozenset[str] = frozenset({"image_url"})


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

    def get_plugin(self, name: str) -> PluginBase | None:
        return self._plugins.get(name)

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
        _image_url_provider: Callable[[], Awaitable[str]] | None = None,
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
        # Separate engine-injected context (image_url, etc.) from LLM-supplied args.
        # Injected keys are stored in action_payload by the engine at proposal time —
        # they must not be validated by input_schema (which only knows LLM-facing fields).
        injected: dict[str, Any] = {}
        llm_args: dict[str, Any] = {}
        for k, v in raw_args.items():
            (injected if k in _INJECTED_CONTEXT_KEYS else llm_args)[k] = v

        # Lazy R2 upload for non-approval plugins that need a hosted image (e.g. schedule_post).
        # Inject image_url directly into the injected dict — never into raw_args or llm_args —
        # so input_schema validation never sees it. Same accepted_injected channel as the
        # approval path. The provider is a memoizing closure supplied by the engine; it uploads
        # at most once per request and only when this branch is actually reached.
        if (
            getattr(plugin, "needs_hosted_image", False)
            and not getattr(plugin, "requires_approval", False)
            and _image_url_provider is not None
            and "image_url" not in injected
        ):
            injected["image_url"] = await _image_url_provider()

        # Filter injected to only params the plugin's execute() actually accepts,
        # so plugins without image_url in their signature don't receive it.
        sig = inspect.signature(plugin.execute)
        accepted_injected = {k: v for k, v in injected.items() if k in sig.parameters}

        try:
            validated = plugin.input_schema(**llm_args)
        except ValidationError as exc:
            raise PluginValidationError(str(exc)) from exc
        result = await plugin.execute(validated, user_id=user_id, db=db, **accepted_injected)
        return result.model_dump()
