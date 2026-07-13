from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database
    database_url: PostgresDsn
    db_pool_size: int = 10
    db_max_overflow: int = 5

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    session_ttl_seconds: int = 1800

    # OpenAI
    openai_api_key: SecretStr
    # gpt-5.5: complex planning, final synthesis, memory scoring
    # Provisional: re-verify snapshot ID + pricing at Phase 1 implementation time.
    openai_default_model: str = "gpt-5.5"
    # gpt-5.4-nano: cheap/fast steps — classification, tool-result summarisation
    # Provisional: re-verify snapshot ID + pricing at Phase 1 implementation time.
    openai_fast_model: str = "gpt-5.4-nano"
    openai_embedding_model: str = "text-embedding-3-small"
    openai_max_retries: int = 3
    openai_timeout_seconds: float = 60.0

    # Planner
    planner_max_iterations: int = 8
    planner_default_temperature: float = 0.7

    # Telegram
    telegram_bot_token: SecretStr
    telegram_webhook_secret: SecretStr
    telegram_webhook_url: str | None = None  # None → long-polling (dev)

    # Integrations
    serper_api_key: SecretStr | None = None
    google_credentials_json: str | None = None  # JSON string of service account creds

    # File reader
    file_reader_root: Path | None = None
    file_reader_max_bytes: int = 1_048_576  # 1 MB default

    # Worker (arq)
    worker_queue_name: str = "arq:queue"
    reminder_poll_interval_seconds: int = 60

    # App
    environment: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"
    cors_origins: list[str] = []


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
