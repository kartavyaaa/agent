from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base

HealthStatusEnum = Enum("healthy", "degraded", "unhealthy", "unknown", name="plugin_health_status")


class PluginRegistry(Base):
    __tablename__ = "plugin_registry"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plugin_name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    version: Mapped[str] = mapped_column(String(20), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")
    config: Mapped[dict] = mapped_column(JSONB, server_default="{}")  # type: ignore[type-arg]
    health_status: Mapped[str] = mapped_column(HealthStatusEnum, server_default="unknown")
    last_health_check_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
