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
    planner_default_temperature: float | None = None

    # Telegram
    telegram_bot_token: SecretStr
    telegram_webhook_secret: SecretStr
    telegram_webhook_url: str | None = None  # None → long-polling (dev)
    telegram_allowed_user_ids: str = ""  # comma-separated numeric IDs; empty = block all

    @property
    def telegram_allowed_user_ids_set(self) -> frozenset[int]:
        raw = self.telegram_allowed_user_ids.strip()
        if not raw:
            return frozenset()
        return frozenset(int(part) for part in raw.split(",") if part.strip())

    # Integrations
    serper_api_key: SecretStr | None = None
    google_credentials_json: str | None = None  # JSON string of service account creds

    # File reader
    file_reader_root: Path | None = None
    file_reader_max_bytes: int = 1_048_576  # 1 MB default

    # Worker (arq)
    worker_queue_name: str = "arq:queue"
    reminder_poll_interval_seconds: int = 60

    # Memory / history
    conversation_history_turns: int = 10
    semantic_recall_enabled: bool = True
    semantic_recall_top_k: int = 5
    semantic_recall_max_distance: float = 0.35
    semantic_recall_inject_count: int = 3

    # App
    environment: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"
    cors_origins: list[str] = []
    default_timezone: str = "Asia/Kolkata"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
