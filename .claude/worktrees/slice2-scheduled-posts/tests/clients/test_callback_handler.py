"""Unit tests for the Telegram callback_query handler (approval flow)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import Message

from clients.telegram.handlers import handle_callback

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_callback(
    data: str = "",
    from_user_id: int = 12345,
) -> MagicMock:
    cb = MagicMock()
    cb.data = data
    cb.from_user = MagicMock()
    cb.from_user.id = from_user_id
    cb.answer = AsyncMock()
    # Use spec=Message so isinstance(callback.message, Message) returns True in the handler.
    msg = MagicMock(spec=Message)
    msg.edit_text = AsyncMock()
    msg.edit_reply_markup = AsyncMock()
    cb.message = msg
    return cb


def _make_pending_row(
    *,
    user_id: uuid.UUID,
    action_type: str = "dummy_confirm_action",
    action_payload: dict | None = None,  # type: ignore[type-arg]
    status: str = "pending",
    expired: bool = False,
) -> MagicMock:
    row = MagicMock()
    row.user_id = user_id
    row.action_type = action_type
    row.action_payload = action_payload or {"message": "hi"}
    row.status = status
    row.expires_at = (
        datetime.now(UTC) - timedelta(seconds=1)
        if expired
        else datetime.now(UTC) + timedelta(hours=1)
    )
    return row


def _make_db(row: object | None) -> MagicMock:
    db = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = row
    db.execute = AsyncMock(return_value=execute_result)
    return db


def _make_session_factory(db: MagicMock) -> MagicMock:
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory


def _make_registry(*, raises: Exception | None = None) -> MagicMock:
    reg = MagicMock()
    if raises:
        reg.execute = AsyncMock(side_effect=raises)
    else:
        reg.execute = AsyncMock(return_value={"result": "ok", "confirmation": "done"})
    return reg


ALLOWED: frozenset[int] = frozenset({12345})
UID = uuid.uuid4()
_PATCH_USER = "clients.telegram.handlers.get_or_create_user_by_telegram_id"


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_allowlist_block_ignores_callback() -> None:
    cb = _make_callback(data=f"ok:{uuid.uuid4()}", from_user_id=99999)
    db = _make_db(row=None)
    with patch(_PATCH_USER, new=AsyncMock(return_value=UID)):
        await handle_callback(
            cb,
            session_factory=_make_session_factory(db),
            registry=_make_registry(),
            allowed_user_ids=frozenset({12345}),
        )
    cb.answer.assert_called_once_with()
    db.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Malformed callback_data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_malformed_data_empty_string() -> None:
    cb = _make_callback(data="")
    db = _make_db(row=None)
    with patch(_PATCH_USER, new=AsyncMock(return_value=UID)):
        await handle_callback(
            cb,
            session_factory=_make_session_factory(db),
            registry=_make_registry(),
            allowed_user_ids=ALLOWED,
        )
    cb.answer.assert_called_once_with()
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_malformed_data_wrong_prefix() -> None:
    cb = _make_callback(data=f"maybe:{uuid.uuid4()}")
    db = _make_db(row=None)
    with patch(_PATCH_USER, new=AsyncMock(return_value=UID)):
        await handle_callback(
            cb,
            session_factory=_make_session_factory(db),
            registry=_make_registry(),
            allowed_user_ids=ALLOWED,
        )
    cb.answer.assert_called_once_with()
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_malformed_data_no_colon() -> None:
    cb = _make_callback(data="oksome-uuid-no-colon")
    db = _make_db(row=None)
    with patch(_PATCH_USER, new=AsyncMock(return_value=UID)):
        await handle_callback(
            cb,
            session_factory=_make_session_factory(db),
            registry=_make_registry(),
            allowed_user_ids=ALLOWED,
        )
    cb.answer.assert_called_once_with()
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_malformed_data_invalid_uuid() -> None:
    cb = _make_callback(data="ok:not-a-uuid")
    db = _make_db(row=None)
    with patch(_PATCH_USER, new=AsyncMock(return_value=UID)):
        await handle_callback(
            cb,
            session_factory=_make_session_factory(db),
            registry=_make_registry(),
            allowed_user_ids=ALLOWED,
        )
    cb.answer.assert_called_once()
    assert "Invalid" in cb.answer.call_args[0][0]
    db.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Row not found / wrong user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_row_not_found_returns_friendly_message() -> None:
    cb = _make_callback(data=f"ok:{uuid.uuid4()}")
    db = _make_db(row=None)
    with patch(_PATCH_USER, new=AsyncMock(return_value=UID)):
        await handle_callback(
            cb,
            session_factory=_make_session_factory(db),
            registry=_make_registry(),
            allowed_user_ids=ALLOWED,
        )
    cb.answer.assert_called_once()
    assert "not found" in cb.answer.call_args[0][0].lower()
    _make_registry().execute.assert_not_called()


@pytest.mark.asyncio
async def test_wrong_user_returns_not_found() -> None:
    other_user_id = uuid.uuid4()
    row = _make_pending_row(user_id=other_user_id)
    cb = _make_callback(data=f"ok:{uuid.uuid4()}")
    db = _make_db(row=row)
    with patch(_PATCH_USER, new=AsyncMock(return_value=UID)):
        await handle_callback(
            cb,
            session_factory=_make_session_factory(db),
            registry=_make_registry(),
            allowed_user_ids=ALLOWED,
        )
    cb.answer.assert_called_once()
    assert "not found" in cb.answer.call_args[0][0].lower()


# ---------------------------------------------------------------------------
# Status guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_already_confirmed_row_is_rejected() -> None:
    row = _make_pending_row(user_id=UID, status="confirmed")
    pending_id = uuid.uuid4()
    cb = _make_callback(data=f"ok:{pending_id}")
    db = _make_db(row=row)
    registry = _make_registry()
    with patch(_PATCH_USER, new=AsyncMock(return_value=UID)):
        await handle_callback(
            cb,
            session_factory=_make_session_factory(db),
            registry=registry,
            allowed_user_ids=ALLOWED,
        )
    cb.answer.assert_called_once()
    assert "already handled" in cb.answer.call_args[0][0].lower()
    registry.execute.assert_not_called()


@pytest.mark.asyncio
async def test_executing_row_is_rejected_by_status_guard() -> None:
    """Claiming pattern: 'executing' row should be rejected like any non-pending status."""
    row = _make_pending_row(user_id=UID, status="executing")
    cb = _make_callback(data=f"ok:{uuid.uuid4()}")
    db = _make_db(row=row)
    registry = _make_registry()
    with patch(_PATCH_USER, new=AsyncMock(return_value=UID)):
        await handle_callback(
            cb,
            session_factory=_make_session_factory(db),
            registry=registry,
            allowed_user_ids=ALLOWED,
        )
    cb.answer.assert_called_once()
    assert "already handled" in cb.answer.call_args[0][0].lower()
    registry.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expired_row_is_rejected_and_marked_expired() -> None:
    row = _make_pending_row(user_id=UID, expired=True)
    cb = _make_callback(data=f"ok:{uuid.uuid4()}")
    db = _make_db(row=row)
    registry = _make_registry()
    with patch(_PATCH_USER, new=AsyncMock(return_value=UID)):
        await handle_callback(
            cb,
            session_factory=_make_session_factory(db),
            registry=registry,
            allowed_user_ids=ALLOWED,
        )
    assert row.status == "expired"
    db.flush.assert_called()
    db.commit.assert_called()
    registry.execute.assert_not_called()
    cb.answer.assert_called_once()
    assert "expired" in cb.answer.call_args[0][0].lower()


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_choice_sets_cancelled_status() -> None:
    row = _make_pending_row(user_id=UID)
    cb = _make_callback(data=f"no:{uuid.uuid4()}")
    db = _make_db(row=row)
    registry = _make_registry()
    with patch(_PATCH_USER, new=AsyncMock(return_value=UID)):
        await handle_callback(
            cb,
            session_factory=_make_session_factory(db),
            registry=registry,
            allowed_user_ids=ALLOWED,
        )
    assert row.status == "cancelled"
    registry.execute.assert_not_called()
    cb.answer.assert_called_once()
    assert "cancelled" in cb.answer.call_args[0][0].lower()


# ---------------------------------------------------------------------------
# Confirm (ok) — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ok_choice_claims_then_executes_and_confirms() -> None:
    row = _make_pending_row(user_id=UID)
    cb = _make_callback(data=f"ok:{uuid.uuid4()}")
    db = _make_db(row=row)
    registry = _make_registry()

    statuses: list[str] = []

    original_flush = db.flush

    async def track_flush() -> None:
        statuses.append(row.status)
        await original_flush()

    db.flush = track_flush

    with patch(_PATCH_USER, new=AsyncMock(return_value=UID)):
        await handle_callback(
            cb,
            session_factory=_make_session_factory(db),
            registry=registry,
            allowed_user_ids=ALLOWED,
        )

    # Claiming: first flush must see "executing"
    assert statuses[0] == "executing"
    # Final status is "confirmed"
    assert row.status == "confirmed"
    # Registry called with _approved=True
    registry.execute.assert_called_once()
    call_kwargs = registry.execute.call_args.kwargs
    assert call_kwargs["_approved"] is True
    assert call_kwargs["user_id"] == UID
    assert registry.execute.call_args.args[0] == "dummy_confirm_action"


# ---------------------------------------------------------------------------
# Execution failure on ok
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execution_failure_sets_failed_status() -> None:
    from core.exceptions import PluginError

    row = _make_pending_row(user_id=UID)
    cb = _make_callback(data=f"ok:{uuid.uuid4()}")
    db = _make_db(row=row)
    registry = _make_registry(raises=PluginError("boom"))

    with patch(_PATCH_USER, new=AsyncMock(return_value=UID)):
        await handle_callback(
            cb,
            session_factory=_make_session_factory(db),
            registry=registry,
            allowed_user_ids=ALLOWED,
        )

    assert row.status == "failed"
    cb.answer.assert_called()
    assert "failed" in cb.answer.call_args[0][0].lower()
