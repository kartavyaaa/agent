"""Error-path tests for clients/telegram/handlers.py.

No importorskip — format_response is patched so telegramify_markdown is never
imported, meaning these run on the VM even without the full dev install.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clients.errors import _GENERIC_FALLBACK
from clients.telegram.handlers import handle_message
from core.exceptions import LLMRateLimitError, LLMTimeoutError

# ---------------------------------------------------------------------------
# Helpers (duplicated from test_telegram_handlers.py — no shared fixture file)
# ---------------------------------------------------------------------------

_ALLOWED: frozenset[int] = frozenset({12345})


def _make_message(text: str = "hello", from_user_id: int = 12345) -> MagicMock:
    msg = MagicMock()
    msg.text = text
    msg.answer = AsyncMock()
    msg.from_user = MagicMock()
    msg.from_user.id = from_user_id
    return msg


def _make_session_factory() -> MagicMock:
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory


def _make_engine_raising(exc: Exception) -> MagicMock:
    engine = MagicMock()
    engine.handle_request = AsyncMock(side_effect=exc)
    return engine


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_platform_error_llm_timeout() -> None:
    msg = _make_message()
    engine = _make_engine_raising(LLMTimeoutError("upstream timed out"))
    factory = _make_session_factory()

    with patch(
        "clients.telegram.handlers.get_or_create_user_by_telegram_id",
        new=AsyncMock(return_value=uuid.uuid4()),
    ):
        await handle_message(msg, engine, factory, allowed_user_ids=_ALLOWED)

    msg.answer.assert_awaited_once_with("AI provider timed out. Try again later.")


@pytest.mark.asyncio
async def test_platform_error_llm_rate_limit() -> None:
    msg = _make_message()
    engine = _make_engine_raising(LLMRateLimitError("rate limited"))
    factory = _make_session_factory()

    with patch(
        "clients.telegram.handlers.get_or_create_user_by_telegram_id",
        new=AsyncMock(return_value=uuid.uuid4()),
    ):
        await handle_message(msg, engine, factory, allowed_user_ids=_ALLOWED)

    msg.answer.assert_awaited_once_with("AI provider rate limit reached. Try again later.")


@pytest.mark.asyncio
async def test_unexpected_error_sends_generic_fallback() -> None:
    msg = _make_message()
    engine = _make_engine_raising(ValueError("boom"))
    factory = _make_session_factory()

    with patch(
        "clients.telegram.handlers.get_or_create_user_by_telegram_id",
        new=AsyncMock(return_value=uuid.uuid4()),
    ):
        await handle_message(msg, engine, factory, allowed_user_ids=_ALLOWED)

    msg.answer.assert_awaited_once_with(_GENERIC_FALLBACK)


@pytest.mark.asyncio
async def test_unexpected_error_logs_exception() -> None:
    msg = _make_message()
    engine = _make_engine_raising(ValueError("boom"))
    factory = _make_session_factory()

    with (
        patch(
            "clients.telegram.handlers.get_or_create_user_by_telegram_id",
            new=AsyncMock(return_value=uuid.uuid4()),
        ),
        patch("clients.telegram.handlers.log") as mock_log,
    ):
        await handle_message(msg, engine, factory, allowed_user_ids=_ALLOWED)

    mock_log.exception.assert_called_once_with("telegram.handle_message.unexpected_error")
