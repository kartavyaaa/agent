"""Unit tests for RemindersPlugin.

DB is mocked — no Postgres needed.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.reminders.plugin import RemindersPlugin
from plugins.reminders.schemas import ReminderInput, ReminderOutput


def _make_db() -> MagicMock:
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# Schema correctness
# ---------------------------------------------------------------------------


def test_reminder_output_distinct_fields() -> None:
    fields = set(ReminderOutput.model_fields.keys())
    assert "message" in fields
    assert "confirmation" in fields
    # They must be distinct — guard against future accidental merge
    assert len(fields) == len(ReminderOutput.model_fields)


def test_reminder_input_has_no_user_id() -> None:
    assert "user_id" not in ReminderInput.model_fields


# ---------------------------------------------------------------------------
# execute()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_creates_reminder_row() -> None:
    plugin = RemindersPlugin()
    db = _make_db()
    uid = uuid.uuid4()
    remind_at = datetime(2026, 7, 8, 9, 0, tzinfo=UTC)

    await plugin.execute(
        ReminderInput(message="call Bob", remind_at=remind_at),
        user_id=uid,
        db=db,
    )

    db.add.assert_called_once()
    added = db.add.call_args[0][0]
    assert added.user_id == uid
    assert added.message == "call Bob"
    assert added.remind_at == remind_at


@pytest.mark.asyncio
async def test_execute_returns_correct_output() -> None:
    plugin = RemindersPlugin()
    db = _make_db()
    remind_at = datetime(2026, 7, 8, 9, 0, tzinfo=UTC)

    result = await plugin.execute(
        ReminderInput(message="call Bob", remind_at=remind_at),
        user_id=uuid.uuid4(),
        db=db,
    )

    assert isinstance(result, ReminderOutput)
    assert result.message == "call Bob"
    assert "2026-07-08" in result.confirmation
    assert result.remind_at == remind_at


@pytest.mark.asyncio
async def test_execute_message_and_confirmation_are_distinct() -> None:
    plugin = RemindersPlugin()
    db = _make_db()
    remind_at = datetime(2026, 7, 9, 14, 30, tzinfo=UTC)

    result = await plugin.execute(
        ReminderInput(message="buy milk", remind_at=remind_at),
        user_id=uuid.uuid4(),
        db=db,
    )

    # message is the reminder text; confirmation is the human-readable ack
    assert result.message == "buy milk"
    assert result.message != result.confirmation
    assert "2026-07-09" in result.confirmation


@pytest.mark.asyncio
async def test_execute_naive_datetime_gets_utc() -> None:
    """A naive datetime from the LLM must be treated as UTC."""
    plugin = RemindersPlugin()
    db = _make_db()
    naive_dt = datetime(2026, 7, 8, 9, 0)  # no tzinfo

    result = await plugin.execute(
        ReminderInput(message="test", remind_at=naive_dt),
        user_id=uuid.uuid4(),
        db=db,
    )

    assert result.remind_at.tzinfo is not None


@pytest.mark.asyncio
async def test_execute_flushes_to_get_id() -> None:
    plugin = RemindersPlugin()
    db = _make_db()

    await plugin.execute(
        ReminderInput(
            message="test flush",
            remind_at=datetime(2026, 7, 8, 9, 0, tzinfo=UTC),
        ),
        user_id=uuid.uuid4(),
        db=db,
    )

    db.flush.assert_called_once()


# ---------------------------------------------------------------------------
# health_check()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_healthy() -> None:
    plugin = RemindersPlugin()
    status = await plugin.health_check()
    assert status.status == "healthy"
    assert status.checked_at is not None
