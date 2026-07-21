"""Unit tests for Settings parsing helpers in core/config.py."""

from __future__ import annotations

from core.config import Settings


def _settings(**kwargs: str) -> Settings:
    """Construct a minimal Settings object with required fields faked out."""
    defaults = {
        "database_url": "postgresql+asyncpg://u:p@localhost/db",
        "openai_api_key": "sk-test",
        "telegram_bot_token": "123:ABC",
        "telegram_webhook_secret": "secret",
    }
    defaults.update(kwargs)
    return Settings.model_validate(defaults)


class TestTelegramAllowedUserIdsSet:
    def test_empty_string_returns_empty_frozenset(self) -> None:
        s = _settings(telegram_allowed_user_ids="")
        assert s.telegram_allowed_user_ids_set == frozenset()

    def test_unset_defaults_to_empty_frozenset(self) -> None:
        s = _settings()
        assert s.telegram_allowed_user_ids_set == frozenset()

    def test_single_id(self) -> None:
        s = _settings(telegram_allowed_user_ids="123456789")
        assert s.telegram_allowed_user_ids_set == frozenset({123456789})

    def test_comma_separated_ids(self) -> None:
        s = _settings(telegram_allowed_user_ids="123,456,789")
        assert s.telegram_allowed_user_ids_set == frozenset({123, 456, 789})

    def test_whitespace_around_ids(self) -> None:
        s = _settings(telegram_allowed_user_ids="123, 456 , 789")
        assert s.telegram_allowed_user_ids_set == frozenset({123, 456, 789})

    def test_whitespace_only_string_returns_empty(self) -> None:
        s = _settings(telegram_allowed_user_ids="   ")
        assert s.telegram_allowed_user_ids_set == frozenset()
