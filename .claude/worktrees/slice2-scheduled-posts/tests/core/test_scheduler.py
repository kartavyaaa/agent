"""Unit tests for poll_reminders.

Postgres, Telegram, and the session factory are all mocked.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.scheduler.jobs import poll_reminders

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(telegram_id: int | None = 12345) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), telegram_id=telegram_id)


def _make_reminder(
    user_id: uuid.UUID,
    remind_at: datetime | None = None,
    sent_at: datetime | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user_id,
        message="call Bob",
        remind_at=remind_at or datetime.now(UTC) - timedelta(seconds=30),
        sent_at=sent_at,
    )


def _make_ctx(
    reminders: list[SimpleNamespace],
    user: SimpleNamespace,
    notifier: AsyncMock,
) -> dict[str, object]:
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = reminders

    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.get = AsyncMock(return_value=user)
    mock_db.commit = AsyncMock()

    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    return {"session_factory": mock_factory, "notifier": notifier}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_sends_due_reminder() -> None:
    user = _make_user(telegram_id=99)
    reminder = _make_reminder(user.id)
    notifier = AsyncMock()
    ctx = _make_ctx([reminder], user, notifier)

    await poll_reminders(ctx)

    notifier.send.assert_called_once_with(99, "call Bob")
    assert reminder.sent_at is not None


@pytest.mark.asyncio
async def test_poll_sets_sent_at_after_send() -> None:
    user = _make_user(telegram_id=77)
    reminder = _make_reminder(user.id)
    notifier = AsyncMock()
    ctx = _make_ctx([reminder], user, notifier)

    before = datetime.now(UTC)
    await poll_reminders(ctx)
    after = datetime.now(UTC)

    assert reminder.sent_at is not None
    assert before <= reminder.sent_at <= after


@pytest.mark.asyncio
async def test_poll_skips_no_telegram_id_but_marks_sent() -> None:
    user = _make_user(telegram_id=None)
    reminder = _make_reminder(user.id)
    notifier = AsyncMock()
    ctx = _make_ctx([reminder], user, notifier)

    await poll_reminders(ctx)

    notifier.send.assert_not_called()
    # reminder still marked sent so it won't be picked up again
    assert reminder.sent_at is not None


@pytest.mark.asyncio
async def test_poll_no_reminders_does_nothing() -> None:
    user = _make_user()
    notifier = AsyncMock()
    ctx = _make_ctx([], user, notifier)

    await poll_reminders(ctx)

    notifier.send.assert_not_called()


@pytest.mark.asyncio
async def test_poll_continues_after_notify_failure() -> None:
    """A send failure must not abort the loop — next reminder still processed."""
    user = _make_user(telegram_id=55)
    r1 = _make_reminder(user.id)
    r2 = _make_reminder(user.id)

    notifier = AsyncMock()
    notifier.send.side_effect = [RuntimeError("timeout"), None]

    # Two reminders; db.get always returns the same user
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [r1, r2]

    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.get = AsyncMock(return_value=user)
    mock_db.commit = AsyncMock()

    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    ctx: dict[str, object] = {"session_factory": mock_factory, "notifier": notifier}
    await poll_reminders(ctx)

    # r1 send failed → continue, r1.sent_at NOT set; r2 succeeds → sent_at set
    assert r1.sent_at is None
    assert r2.sent_at is not None
    assert notifier.send.call_count == 2


@pytest.mark.asyncio
async def test_poll_commits_session() -> None:
    user = _make_user()
    reminder = _make_reminder(user.id)
    notifier = AsyncMock()

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [reminder]

    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.get = AsyncMock(return_value=user)
    mock_db.commit = AsyncMock()

    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    ctx: dict[str, object] = {"session_factory": mock_factory, "notifier": notifier}
    await poll_reminders(ctx)

    mock_db.commit.assert_called_once()
