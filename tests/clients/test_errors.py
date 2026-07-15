"""Unit tests for clients.errors.user_message shared helper."""

from __future__ import annotations

from clients.errors import user_message
from core.exceptions import (
    LLMRateLimitError,
    LLMTimeoutError,
    PlatformError,
    SandboxViolationError,
)


def test_user_message_llm_timeout() -> None:
    assert user_message(LLMTimeoutError()) == "AI provider timed out. Try again later."


def test_user_message_llm_rate_limit() -> None:
    assert user_message(LLMRateLimitError()) == "AI provider rate limit reached. Try again later."


def test_user_message_fallback_for_base_platform_error() -> None:
    assert user_message(PlatformError()) == "An unexpected error occurred."


def test_api_and_helper_agree_llm_timeout() -> None:
    # Guard against the API handler and user_message drifting apart.
    exc = LLMTimeoutError()
    assert user_message(exc) == "AI provider timed out. Try again later."


def test_api_and_helper_agree_sandbox_violation() -> None:
    # SandboxViolationError → FileReaderError → IntegrationError: ordering-fragile branch.
    # If IntegrationError is ever checked before SandboxViolationError, this returns
    # "External service error." instead of "Access denied." and this test catches it.
    exc = SandboxViolationError()
    assert user_message(exc) == "Access denied."
