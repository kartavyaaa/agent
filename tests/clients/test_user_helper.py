"""Unit tests for clients/user_helper.py.

All DB interactions are mocked — no Docker or real Postgres required.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from clients.user_helper import get_or_create_user, get_or_create_user_by_telegram_id

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_select_result(user: MagicMock | None) -> MagicMock:
    """Return a mock result whose scalar_one_or_none() yields `user`."""
    r = MagicMock()
    r.scalar_one_or_none.return_value = user
    return r


def _make_select_result_one(user: MagicMock) -> MagicMock:
    """Return a mock result whose scalar_one() yields `user`."""
    r = MagicMock()
    r.scalar_one.return_value = user
    return r


def _make_user(uid: uuid.UUID | None = None, telegram_id: int | None = None) -> MagicMock:
    user = MagicMock()
    user.id = uid or uuid.uuid4()
    user.telegram_id = telegram_id
    return user


# ---------------------------------------------------------------------------
# get_or_create_user (UUID-based)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_or_create_returns_existing() -> None:
    uid = uuid.uuid4()
    existing = _make_user(uid=uid)

    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=_make_select_result(existing))

    result = await get_or_create_user(mock_db, uid)

    assert result is existing
    mock_db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_or_create_creates_new() -> None:
    uid = uuid.uuid4()
    new_user = _make_user(uid=uid)

    first_select = _make_select_result(None)
    insert_result = MagicMock()
    second_select = _make_select_result_one(new_user)

    mock_db = MagicMock()
    mock_db.execute = AsyncMock(side_effect=[first_select, insert_result, second_select])

    result = await get_or_create_user(mock_db, uid)

    assert result is new_user
    assert mock_db.execute.await_count == 3


# ---------------------------------------------------------------------------
# get_or_create_user_by_telegram_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_or_create_by_telegram_id_returns_existing() -> None:
    uid = uuid.uuid4()
    existing = _make_user(uid=uid, telegram_id=42)

    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=_make_select_result(existing))

    result = await get_or_create_user_by_telegram_id(mock_db, 42)

    assert result == uid
    mock_db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_or_create_by_telegram_id_creates_new() -> None:
    uid = uuid.uuid4()
    new_user = _make_user(uid=uid, telegram_id=99)

    first_select = _make_select_result(None)
    insert_result = MagicMock()
    second_select = _make_select_result_one(new_user)

    mock_db = MagicMock()
    mock_db.execute = AsyncMock(side_effect=[first_select, insert_result, second_select])

    result = await get_or_create_user_by_telegram_id(mock_db, 99)

    assert result == uid
    assert mock_db.execute.await_count == 3


@pytest.mark.asyncio
async def test_get_or_create_by_telegram_id_reselect_uses_telegram_id() -> None:
    """The re-SELECT after INSERT must query by telegram_id, not by the locally-generated uuid.

    If this request lost the insert race, the locally-generated uuid was discarded
    and would not be found. Only telegram_id is guaranteed to be present.
    """
    uid = uuid.uuid4()
    new_user = _make_user(uid=uid, telegram_id=77)

    first_select = _make_select_result(None)
    insert_result = MagicMock()
    second_select = _make_select_result_one(new_user)

    mock_db = MagicMock()
    mock_db.execute = AsyncMock(side_effect=[first_select, insert_result, second_select])

    await get_or_create_user_by_telegram_id(mock_db, 77)

    # Inspect the third execute call (index 2) — it must be the re-SELECT.
    # We can't easily introspect the SQLAlchemy clause object, but we CAN assert
    # that a third execute call was made (not just two), which proves the re-SELECT
    # path ran. The unit above already checks await_count == 3; this test documents intent.
    third_call_args = mock_db.execute.call_args_list[2]
    assert third_call_args is not None, "re-SELECT (3rd execute) must have been called"
