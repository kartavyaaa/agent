from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from typing import ClassVar, Literal

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession


class HealthStatus(BaseModel):
    status: Literal["healthy", "degraded", "unhealthy"]
    message: str
    latency_ms: float | None = None
    checked_at: datetime


class PluginInfo(BaseModel):
    name: str
    version: str
    description: str
    capabilities: list[str]
    permissions: list[str]
    dependencies: list[str]
    health: HealthStatus | None = None


class PluginBase(ABC):
    """Contract every plugin must satisfy.

    Class-var fields are mandatory on every subclass. The ToolRegistry
    enforces their presence at registration time.
    """

    name: ClassVar[str]
    version: ClassVar[str]
    description: ClassVar[str]
    capabilities: ClassVar[list[str]]
    permissions: ClassVar[list[str]]
    dependencies: ClassVar[list[str]]
    input_schema: ClassVar[type[BaseModel]]
    output_schema: ClassVar[type[BaseModel]]
    config_schema: ClassVar[type[BaseModel]]
    requires_approval: ClassVar[bool] = False

    def get_info(self) -> PluginInfo:
        return PluginInfo(
            name=self.name,
            version=self.version,
            description=self.description,
            capabilities=self.capabilities,
            permissions=self.permissions,
            dependencies=self.dependencies,
        )

    @abstractmethod
    async def execute(
        self,
        input: BaseModel,
        *,
        user_id: uuid.UUID,
        db: AsyncSession,
    ) -> BaseModel:
        """Run the plugin. Raises PluginError on expected failures."""
        ...

    @abstractmethod
    async def health_check(self) -> HealthStatus:
        """Return current health. Must not raise; callers enforce a 5 s timeout."""
        ...
