"""Unit tests for clients/telegram/handlers.py.

No real Telegram connection. Engine and DB are mocked.
get_or_create_user_by_telegram_id is patched at its imported name in handlers.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clients.telegram.handlers import handle_message
from core.schemas import CoreResponse

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_message(text: str | None = "hello", from_user_id: int | None = 12345) -> MagicMock:
    msg = MagicMock()
    msg.text = text
    msg.answer = AsyncMock()
    if from_user_id is None:
        msg.from_user = None
    else:
        msg.from_user = MagicMock()
        msg.from_user.id = from_user_id
    return msg


def _make_session_factory(uid: uuid.UUID) -> MagicMock:
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory


def _make_engine(content: str = "Hi!") -> MagicMock:
    engine = MagicMock()
    engine.handle_request = AsyncMock(
        return_value=CoreResponse(content=content, memories_written=0, tool_calls_made=[])
    )
    return engine


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


_ALLOWED: frozenset[int] = frozenset({12345})  # matches _make_message default from_user_id


@pytest.mark.asyncio
async def test_handle_message_no_text_skips() -> None:
    msg = _make_message(text=None)
    engine = _make_engine()
    factory = _make_session_factory(uuid.uuid4())

    await handle_message(msg, engine, factory, allowed_user_ids=_ALLOWED)

    engine.handle_request.assert_not_awaited()
    msg.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_message_no_from_user_skips() -> None:
    msg = _make_message(from_user_id=None)
    engine = _make_engine()
    factory = _make_session_factory(uuid.uuid4())

    await handle_message(msg, engine, factory, allowed_user_ids=_ALLOWED)

    engine.handle_request.assert_not_awaited()
    msg.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_message_routes_to_engine() -> None:
    uid = uuid.uuid4()
    msg = _make_message(text="Hello bot", from_user_id=42)
    engine = _make_engine(content="Hi there!")
    factory = _make_session_factory(uid)

    with patch(
        "clients.telegram.handlers.get_or_create_user_by_telegram_id",
        new=AsyncMock(return_value=uid),
    ) as mock_lookup:
        await handle_message(msg, engine, factory, allowed_user_ids=frozenset({42}))

    mock_lookup.assert_awaited_once()
    assert mock_lookup.call_args[0][1] == 42  # telegram_id arg

    engine.handle_request.assert_awaited_once()
    req = engine.handle_request.call_args[0][0]
    assert req.user_id == uid
    assert req.content == "Hello bot"

    msg.answer.assert_awaited_once_with("Hi there!")


@pytest.mark.asyncio
async def test_handle_message_commits_before_engine_call() -> None:
    """The user upsert must be committed before engine.handle_request is called."""
    uid = uuid.uuid4()
    msg = _make_message()
    call_order: list[str] = []

    mock_db = MagicMock()

    async def record_commit() -> None:
        call_order.append("commit")

    mock_db.commit = AsyncMock(side_effect=record_commit)
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)

    async def record_handle(req: object) -> CoreResponse:
        call_order.append("engine")
        return CoreResponse(content="ok", memories_written=0, tool_calls_made=[])

    engine = MagicMock()
    engine.handle_request = AsyncMock(side_effect=record_handle)

    with patch(
        "clients.telegram.handlers.get_or_create_user_by_telegram_id",
        new=AsyncMock(return_value=uid),
    ):
        await handle_message(msg, engine, factory, allowed_user_ids=_ALLOWED)

    assert call_order == [
        "commit",
        "engine",
    ], "db.commit() must be called before engine.handle_request()"


@pytest.mark.asyncio
async def test_handle_message_long_response_multi_answer() -> None:
    uid = uuid.uuid4()
    msg = _make_message(text="big question")
    long_content = "A" * 5000  # over 4096 chars
    engine = _make_engine(content=long_content)
    factory = _make_session_factory(uid)

    with patch(
        "clients.telegram.handlers.get_or_create_user_by_telegram_id",
        new=AsyncMock(return_value=uid),
    ):
        await handle_message(msg, engine, factory, allowed_user_ids=_ALLOWED)

    assert msg.answer.await_count > 1, "long response must be sent in multiple chunks"
    for call_args in msg.answer.call_args_list:
        chunk = call_args[0][0]
        assert len(chunk) <= 4096, f"chunk length {len(chunk)} exceeds 4096"


# ---------------------------------------------------------------------------
# Allowlist tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_allowed_user_passes() -> None:
    uid = uuid.uuid4()
    msg = _make_message(text="hello", from_user_id=42)
    engine = _make_engine()
    factory = _make_session_factory(uid)

    with patch(
        "clients.telegram.handlers.get_or_create_user_by_telegram_id",
        new=AsyncMock(return_value=uid),
    ):
        await handle_message(msg, engine, factory, allowed_user_ids=frozenset({42}))

    engine.handle_request.assert_awaited_once()


@pytest.mark.asyncio
async def test_non_allowed_user_silently_ignored() -> None:
    msg = _make_message(text="hello", from_user_id=99)
    engine = _make_engine()
    factory = _make_session_factory(uuid.uuid4())

    await handle_message(msg, engine, factory, allowed_user_ids=frozenset({42}))

    engine.handle_request.assert_not_awaited()
    msg.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_allowlist_blocks_all() -> None:
    msg = _make_message(text="hello", from_user_id=12345)
    engine = _make_engine()
    factory = _make_session_factory(uuid.uuid4())

    await handle_message(msg, engine, factory, allowed_user_ids=frozenset())

    engine.handle_request.assert_not_awaited()
    msg.answer.assert_not_awaited()
