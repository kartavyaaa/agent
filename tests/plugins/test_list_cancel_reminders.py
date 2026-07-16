"""Unit tests for ListRemindersPlugin and CancelReminderPlugin.

DB is mocked — no Postgres needed.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.reminders.cancel import CancelReminderPlugin
from plugins.reminders.list import ListRemindersPlugin
from plugins.reminders.schemas import (
    CancelReminderInput,
    CancelReminderOutput,
    ListRemindersInput,
    ListRemindersOutput,
)


def _make_db_read(rows: list[MagicMock]) -> MagicMock:
    """Mock db for list queries: db.execute returns rows via scalars().all()."""
    db = MagicMock()
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = rows
    db.execute = AsyncMock(return_value=execute_result)
    return db


def _make_cancel_db(row: object | None) -> MagicMock:
    """Mock db for cancel: scalar_one_or_none + delete + flush."""
    db = MagicMock()
    db.flush = AsyncMock()
    db.delete = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = row
    db.execute = AsyncMock(return_value=execute_result)
    return db


def _make_mock_reminder(
    message: str = "test reminder",
    remind_at: datetime | None = None,
) -> MagicMock:
    r = MagicMock()
    r.id = uuid.uuid4()
    r.message = message
    r.remind_at = remind_at or datetime(2026, 7, 20, 9, 0, tzinfo=UTC)
    r.sent_at = None
    return r


# ---------------------------------------------------------------------------
# list_reminders — schema
# ---------------------------------------------------------------------------


def test_list_reminders_input_has_no_user_id() -> None:
    assert "user_id" not in ListRemindersInput.model_fields


def test_list_reminders_input_has_no_fields() -> None:
    # Zero LLM-supplied fields — LLM calls this tool with an empty argument object.
    assert len(ListRemindersInput.model_fields) == 0


# ---------------------------------------------------------------------------
# list_reminders — execute()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_reminders_returns_summaries_with_ids() -> None:
    plugin = ListRemindersPlugin(tz_name="UTC")
    mock_reminder = _make_mock_reminder(message="wake up")
    db = _make_db_read([mock_reminder])

    result = await plugin.execute(
        ListRemindersInput(),
        user_id=uuid.uuid4(),
        db=db,
    )

    assert isinstance(result, ListRemindersOutput)
    assert result.count == 1
    assert result.reminders[0].reminder_id == str(mock_reminder.id)
    assert result.reminders[0].message == "wake up"


@pytest.mark.asyncio
async def test_list_reminders_count_matches() -> None:
    plugin = ListRemindersPlugin(tz_name="UTC")
    reminders = [_make_mock_reminder(message=f"reminder {i}") for i in range(4)]
    db = _make_db_read(reminders)

    result = await plugin.execute(
        ListRemindersInput(),
        user_id=uuid.uuid4(),
        db=db,
    )

    assert result.count == len(result.reminders) == 4


@pytest.mark.asyncio
async def test_list_reminders_empty_when_none_pending() -> None:
    plugin = ListRemindersPlugin(tz_name="UTC")
    db = _make_db_read([])

    result = await plugin.execute(
        ListRemindersInput(),
        user_id=uuid.uuid4(),
        db=db,
    )

    assert result.count == 0
    assert result.reminders == []


@pytest.mark.asyncio
async def test_list_reminders_includes_utc_and_local_time() -> None:
    plugin = ListRemindersPlugin(tz_name="UTC")
    remind_at = datetime(2026, 7, 20, 9, 0, tzinfo=UTC)
    mock_reminder = _make_mock_reminder(remind_at=remind_at)
    db = _make_db_read([mock_reminder])

    result = await plugin.execute(
        ListRemindersInput(),
        user_id=uuid.uuid4(),
        db=db,
    )

    summary = result.reminders[0]
    assert "2026-07-20" in summary.remind_at_utc
    assert "2026-07-20" in summary.remind_at_local


@pytest.mark.asyncio
async def test_list_reminders_health_check() -> None:
    assert (await ListRemindersPlugin(tz_name="UTC").health_check()).status == "healthy"


# ---------------------------------------------------------------------------
# cancel_reminder — schema
# ---------------------------------------------------------------------------


def test_cancel_reminder_input_has_no_user_id() -> None:
    assert "user_id" not in CancelReminderInput.model_fields


# ---------------------------------------------------------------------------
# cancel_reminder — execute()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_reminder_found_deletes_row() -> None:
    plugin = CancelReminderPlugin()
    rid = uuid.uuid4()
    mock_reminder = _make_mock_reminder(message="wake up")
    mock_reminder.id = rid
    db = _make_cancel_db(mock_reminder)

    result = await plugin.execute(
        CancelReminderInput(reminder_id=str(rid)),
        user_id=uuid.uuid4(),
        db=db,
    )

    db.delete.assert_called_once_with(mock_reminder)
    db.flush.assert_called_once()
    assert isinstance(result, CancelReminderOutput)
    assert result.status == "cancelled"


@pytest.mark.asyncio
async def test_cancel_reminder_found_returns_message() -> None:
    plugin = CancelReminderPlugin()
    rid = uuid.uuid4()
    mock_reminder = _make_mock_reminder(message="check the oven")
    mock_reminder.id = rid
    db = _make_cancel_db(mock_reminder)

    result = await plugin.execute(
        CancelReminderInput(reminder_id=str(rid)),
        user_id=uuid.uuid4(),
        db=db,
    )

    assert result.message == "check the oven"
    assert "check the oven" in result.detail


@pytest.mark.asyncio
async def test_cancel_reminder_not_found_returns_not_found() -> None:
    plugin = CancelReminderPlugin()
    db = _make_cancel_db(None)

    result = await plugin.execute(
        CancelReminderInput(reminder_id=str(uuid.uuid4())),
        user_id=uuid.uuid4(),
        db=db,
    )

    assert result.status == "not_found"
    db.delete.assert_not_called()
    db.flush.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_reminder_invalid_uuid_returns_not_found() -> None:
    plugin = CancelReminderPlugin()
    db = _make_cancel_db(None)

    result = await plugin.execute(
        CancelReminderInput(reminder_id="not-a-valid-uuid"),
        user_id=uuid.uuid4(),
        db=db,
    )

    assert result.status == "not_found"
    # DB should not be queried for an unparseable id
    db.execute.assert_not_called()
    db.delete.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_reminder_cross_user_returns_not_found() -> None:
    """A reminder belonging to another user must not be cancellable.

    The DB query scopes by id AND user_id AND sent_at IS NULL.
    Simulate by returning None from the DB (user_id mismatch → no row found).
    """
    plugin = CancelReminderPlugin()
    db = _make_cancel_db(None)  # DB returns nothing: user_id doesn't match

    result = await plugin.execute(
        CancelReminderInput(reminder_id=str(uuid.uuid4())),
        user_id=uuid.uuid4(),
        db=db,
    )

    assert result.status == "not_found"
    db.delete.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_reminder_already_sent_returns_not_found() -> None:
    """A reminder with sent_at set must not be cancellable.

    Simulated by the DB returning None (sent_at IS NULL filter excluded the row).
    """
    plugin = CancelReminderPlugin()
    db = _make_cancel_db(None)  # WHERE sent_at IS NULL excluded the already-sent row

    result = await plugin.execute(
        CancelReminderInput(reminder_id=str(uuid.uuid4())),
        user_id=uuid.uuid4(),
        db=db,
    )

    assert result.status == "not_found"
    db.delete.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_reminder_health_check() -> None:
    assert (await CancelReminderPlugin().health_check()).status == "healthy"
