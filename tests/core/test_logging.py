"""Tests that configure_logging actually emits exception tracebacks.

These tests exercise the real processor chain end-to-end (no mocks of the
logging internals) by capturing stdout. A JSON log event produced by
log.exception() must contain the traceback text — if format_exc_info is
missing from the chain it is silently dropped and these tests fail.
"""

from __future__ import annotations

import io
import json
import sys

import structlog

from core.logging import configure_logging


def _capture_log_exception(environment: str) -> str:
    """Configure logging for the given environment, fire log.exception() inside
    a real except block, and return all captured stdout as a string."""
    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        # Reset structlog so a fresh configure_logging() takes effect.
        structlog.reset_defaults()
        configure_logging(log_level="DEBUG", environment=environment)
        log = structlog.get_logger()
        try:
            raise ValueError("boom — intentional test error")
        except ValueError:
            log.exception("test.error_event", context="unit_test")
    finally:
        sys.stdout = old_stdout
        structlog.reset_defaults()

    return buf.getvalue()


def test_production_logging_emits_traceback() -> None:
    """In production (JSON) mode, log.exception() must include traceback text."""
    output = _capture_log_exception("production")
    assert output.strip(), "Expected at least one log line, got nothing"

    # Each line is a JSON object
    for line in output.strip().splitlines():
        event = json.loads(line)
        if event.get("event") == "test.error_event":
            exc_info = event.get("exception") or event.get("exc_info") or ""
            assert (
                "ValueError" in exc_info
            ), f"Traceback not found in JSON output. Got event keys: {list(event.keys())}"
            assert "boom" in exc_info, "Exception message not in traceback"
            return

    raise AssertionError("test.error_event not found in captured log output")


def test_development_logging_emits_traceback() -> None:
    """In development (console) mode, log.exception() must include traceback text."""
    output = _capture_log_exception("development")
    assert output.strip(), "Expected at least one log line, got nothing"
    assert "ValueError" in output, "Traceback not found in development console output"
    assert "boom" in output, "Exception message not in traceback"
    assert "test.error_event" in output, "Event name not in development console output"
