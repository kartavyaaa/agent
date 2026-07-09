from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import ClassVar, cast

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from integrations.serper import SerperClient
from plugins.base import HealthStatus, PluginBase
from plugins.web_search.schemas import (
    SearchResult,
    WebSearchConfig,
    WebSearchInput,
    WebSearchOutput,
)


class WebSearchPlugin(PluginBase):
    name: ClassVar[str] = "web_search"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Search the web for current information using Google Search. "
        "Returns titles, URLs, and snippets for the top results. "
        "Use this tool when you need up-to-date information not in your training data."
    )
    capabilities: ClassVar[list[str]] = ["web_search"]
    permissions: ClassVar[list[str]] = ["network:read"]
    dependencies: ClassVar[list[str]] = ["serper"]
    input_schema: ClassVar[type[BaseModel]] = WebSearchInput
    output_schema: ClassVar[type[BaseModel]] = WebSearchOutput
    config_schema: ClassVar[type[BaseModel]] = WebSearchConfig

    def __init__(self, client: SerperClient) -> None:
        self._client = client

    async def execute(
        self,
        input: BaseModel,  # noqa: A002
        *,
        user_id: uuid.UUID,  # noqa: ARG002
        db: AsyncSession,  # noqa: ARG002
    ) -> WebSearchOutput:
        data = cast(WebSearchInput, input)
        raw = await self._client.search(data.query, num_results=data.max_results)
        results = [SearchResult(title=r.title, link=r.link, snippet=r.snippet) for r in raw]
        return WebSearchOutput(query=data.query, results=results, result_count=len(results))

    async def health_check(self) -> HealthStatus:
        ok = await self._client.health_check()
        return HealthStatus(
            status="healthy" if ok else "unhealthy",
            message="ok" if ok else "Serper API key not configured",
            checked_at=datetime.now(UTC),
        )
