"""Unit tests for clients/telegram/handlers.py.

No real Telegram connection. Engine and DB are mocked.
get_or_create_user_by_telegram_id is patched at its imported name in handlers.
"""

from __future__ import annotations

import asyncio
import base64
import io
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip(
    "telegramify_markdown",
    reason="telegramify-markdown not installed (PC-gate item; run pip install -e '.[dev]' on PC)",
)

from clients.telegram.handlers import (  # noqa: E402
    _DEFAULT_PLAN_PROMPT,
    _flush_media_group,
    _media_group_buffer,
    _media_group_tasks,
    handle_message,
    handle_photo,
)
from core.schemas import CoreResponse  # noqa: E402

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

    # Entity path: positional arg is text, parse_mode=None passed explicitly
    call_args = msg.answer.call_args
    assert call_args.args[0] == "Hi there!"
    assert call_args.kwargs.get("parse_mode") is None


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
        chunk_text = call_args.args[0]
        utf16_len = len(chunk_text.encode("utf-16-le")) // 2
        assert utf16_len <= 4096, f"chunk exceeds 4096 UTF-16 units: {utf16_len}"


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


# ---------------------------------------------------------------------------
# Fallback for empty / whitespace engine response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_response_sends_fallback() -> None:
    """Empty LLM output must produce the fallback message, never an empty send."""
    uid = uuid.uuid4()
    msg = _make_message(text="hello")
    engine = _make_engine(content="")
    factory = _make_session_factory(uid)

    with patch(
        "clients.telegram.handlers.get_or_create_user_by_telegram_id",
        new=AsyncMock(return_value=uid),
    ):
        await handle_message(msg, engine, factory, allowed_user_ids=_ALLOWED)

    msg.answer.assert_awaited_once()
    sent_text = msg.answer.call_args.args[0]
    assert sent_text == "(No response.)"
    assert sent_text  # never empty


@pytest.mark.asyncio
async def test_whitespace_response_sends_fallback() -> None:
    uid = uuid.uuid4()
    msg = _make_message(text="hello")
    engine = _make_engine(content="   ")
    factory = _make_session_factory(uid)

    with patch(
        "clients.telegram.handlers.get_or_create_user_by_telegram_id",
        new=AsyncMock(return_value=uid),
    ):
        await handle_message(msg, engine, factory, allowed_user_ids=_ALLOWED)

    msg.answer.assert_awaited_once()
    assert msg.answer.call_args.args[0] == "(No response.)"


# ---------------------------------------------------------------------------
# Photo handler tests
# ---------------------------------------------------------------------------

_FAKE_IMAGE_BYTES = b"FAKE_IMAGE_BYTES"
_FAKE_B64 = base64.b64encode(_FAKE_IMAGE_BYTES).decode()


def _make_photo_message(
    caption: str | None = "Nice shot",
    from_user_id: int | None = 12345,
) -> MagicMock:
    msg = MagicMock()
    msg.caption = caption
    msg.answer = AsyncMock()
    msg.media_group_id = None  # lone photo (not part of an album)
    if from_user_id is None:
        msg.from_user = None
    else:
        msg.from_user = MagicMock()
        msg.from_user.id = from_user_id
    # Telegram photo list — highest res is last
    photo_size = MagicMock()
    photo_size.file_id = "test_file_id"
    msg.photo = [photo_size]
    # bot accessible via message.bot (aiogram injects from update context)
    msg.bot = MagicMock()
    msg.bot.get_file = AsyncMock(return_value=MagicMock(file_path="photos/test.jpg"))
    msg.bot.download_file = AsyncMock(return_value=io.BytesIO(_FAKE_IMAGE_BYTES))
    return msg


@pytest.mark.asyncio
async def test_handle_photo_builds_correct_core_request() -> None:
    uid = uuid.uuid4()
    msg = _make_photo_message(caption="Nice shot")
    engine = _make_engine()
    factory = _make_session_factory(uid)

    with patch(
        "clients.telegram.handlers.get_or_create_user_by_telegram_id",
        new=AsyncMock(return_value=uid),
    ):
        await handle_photo(msg, engine, factory, allowed_user_ids=frozenset({12345}))

    engine.handle_request.assert_awaited_once()
    req = engine.handle_request.call_args[0][0]
    assert req.user_id == uid
    assert req.content == "Nice shot"
    assert req.image_base64 == _FAKE_B64
    assert req.image_mime == "image/jpeg"


@pytest.mark.asyncio
async def test_handle_photo_no_caption_uses_default_prompt() -> None:
    uid = uuid.uuid4()
    msg = _make_photo_message(caption=None)
    engine = _make_engine()
    factory = _make_session_factory(uid)

    with patch(
        "clients.telegram.handlers.get_or_create_user_by_telegram_id",
        new=AsyncMock(return_value=uid),
    ):
        await handle_photo(msg, engine, factory, allowed_user_ids=frozenset({12345}))

    req = engine.handle_request.call_args[0][0]
    assert req.content == "Please critique this photo."
    assert req.image_base64 == _FAKE_B64


@pytest.mark.asyncio
async def test_handle_photo_allowlist_blocks_non_allowed() -> None:
    msg = _make_photo_message(from_user_id=9999)
    engine = _make_engine()
    factory = _make_session_factory(uuid.uuid4())

    await handle_photo(msg, engine, factory, allowed_user_ids=frozenset({1}))

    engine.handle_request.assert_not_awaited()
    msg.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_photo_allowlist_passes_allowed_sender() -> None:
    uid = uuid.uuid4()
    msg = _make_photo_message(from_user_id=42)
    engine = _make_engine()
    factory = _make_session_factory(uid)

    with patch(
        "clients.telegram.handlers.get_or_create_user_by_telegram_id",
        new=AsyncMock(return_value=uid),
    ):
        await handle_photo(msg, engine, factory, allowed_user_ids=frozenset({42}))

    engine.handle_request.assert_awaited_once()


# ---------------------------------------------------------------------------
# Album / media-group path
# ---------------------------------------------------------------------------


def _make_album_message(
    mgid: str,
    msg_id: int,
    caption: str | None = None,
    from_user_id: int = 12345,
) -> MagicMock:
    msg = MagicMock()
    msg.media_group_id = mgid
    msg.message_id = msg_id
    msg.caption = caption
    msg.answer = AsyncMock()
    msg.from_user = MagicMock()
    msg.from_user.id = from_user_id
    photo = MagicMock()
    photo.file_id = f"fid_{msg_id}"
    msg.photo = [photo]
    msg.bot = MagicMock()
    msg.bot.get_file = AsyncMock(return_value=MagicMock(file_path=f"x/{msg_id}.jpg"))
    msg.bot.download_file = AsyncMock(return_value=io.BytesIO(_FAKE_IMAGE_BYTES))
    return msg


def _patch_sleep_and_user(uid: uuid.UUID) -> tuple:  # type: ignore[type-arg]
    """Context managers: patch asyncio.sleep to a no-op and user lookup to return uid."""
    return (
        patch("clients.telegram.handlers.asyncio.sleep", new_callable=AsyncMock),
        patch(
            "clients.telegram.handlers.get_or_create_user_by_telegram_id",
            new=AsyncMock(return_value=uid),
        ),
    )


@pytest.mark.asyncio
async def test_lone_photo_processes_immediately() -> None:
    """A lone photo (media_group_id=None) is processed immediately via the single-photo path."""
    uid = uuid.uuid4()
    msg = _make_photo_message(caption="lone shot")
    assert msg.media_group_id is None
    engine = _make_engine()
    factory = _make_session_factory(uid)

    with patch(
        "clients.telegram.handlers.get_or_create_user_by_telegram_id",
        new=AsyncMock(return_value=uid),
    ):
        await handle_photo(msg, engine, factory, allowed_user_ids=frozenset({12345}))

    engine.handle_request.assert_awaited_once()
    req = engine.handle_request.call_args[0][0]
    assert req.image_base64 == _FAKE_B64
    assert req.images is None


@pytest.mark.asyncio
async def test_album_photo_buffered_not_processed_immediately() -> None:
    """An album photo is buffered; engine is NOT called until debounce fires."""
    uid = uuid.uuid4()
    mgid = "group-001"
    msg = _make_album_message(mgid, msg_id=1)
    engine = _make_engine()
    factory = _make_session_factory(uid)

    # Patch sleep so the debounce task never actually fires during this test.
    with patch("clients.telegram.handlers.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        mock_sleep.side_effect = asyncio.CancelledError  # keep task suspended

        with patch(
            "clients.telegram.handlers.get_or_create_user_by_telegram_id",
            new=AsyncMock(return_value=uid),
        ):
            await handle_photo(msg, engine, factory, allowed_user_ids=frozenset({12345}))

    engine.handle_request.assert_not_awaited()
    # Clean up task to avoid asyncio warnings
    task = _media_group_tasks.pop(mgid, None)
    _media_group_buffer.pop(mgid, None)
    if task and not task.done():
        task.cancel()


@pytest.mark.asyncio
async def test_album_cancel_restart_on_second_photo() -> None:
    """Sending a second album photo cancels the first task and creates a new one."""
    uid = uuid.uuid4()
    mgid = "group-002"
    msg1 = _make_album_message(mgid, msg_id=1)
    msg2 = _make_album_message(mgid, msg_id=2)
    engine = _make_engine()
    factory = _make_session_factory(uid)

    with patch("clients.telegram.handlers.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        mock_sleep.side_effect = asyncio.CancelledError

        with patch(
            "clients.telegram.handlers.get_or_create_user_by_telegram_id",
            new=AsyncMock(return_value=uid),
        ):
            await handle_photo(msg1, engine, factory, allowed_user_ids=frozenset({12345}))
            task1 = _media_group_tasks[mgid]
            await handle_photo(msg2, engine, factory, allowed_user_ids=frozenset({12345}))
            task2 = _media_group_tasks[mgid]

    assert task1 is not task2
    # task.cancel() only schedules CancelledError; await task1 so the event loop
    # runs it to cancellation completion before asserting .cancelled().
    with pytest.raises(asyncio.CancelledError):
        await task1
    assert task1.cancelled()

    # Clean up
    _media_group_tasks.pop(mgid, None)
    _media_group_buffer.pop(mgid, None)
    if task2 and not task2.done():
        task2.cancel()


@pytest.mark.asyncio
async def test_album_flush_engine_called_once_with_image_list() -> None:
    """After debounce, engine is called once with a CoreRequest carrying an images list."""
    uid = uuid.uuid4()
    mgid = "group-003"
    msgs = [_make_album_message(mgid, msg_id=i) for i in range(1, 4)]
    _media_group_buffer[mgid] = list(msgs)

    engine = _make_engine()
    factory = _make_session_factory(uid)

    sleep_patch, user_patch = _patch_sleep_and_user(uid)
    with sleep_patch, user_patch:
        await _flush_media_group(mgid, engine, factory)

    engine.handle_request.assert_awaited_once()
    req = engine.handle_request.call_args[0][0]
    assert req.images is not None
    assert len(req.images) == 3
    assert req.image_base64 is None


@pytest.mark.asyncio
async def test_album_flush_uses_first_message_caption() -> None:
    """When the first album message has a caption it becomes CoreRequest.content."""
    uid = uuid.uuid4()
    mgid = "group-004"
    msgs = [
        _make_album_message(mgid, msg_id=1, caption="plan these"),
        _make_album_message(mgid, msg_id=2, caption=None),
    ]
    _media_group_buffer[mgid] = list(msgs)

    engine = _make_engine()
    factory = _make_session_factory(uid)

    sleep_patch, user_patch = _patch_sleep_and_user(uid)
    with sleep_patch, user_patch:
        await _flush_media_group(mgid, engine, factory)

    req = engine.handle_request.call_args[0][0]
    assert req.content == "plan these"


@pytest.mark.asyncio
async def test_album_flush_uses_default_prompt_when_no_caption() -> None:
    """When no caption is present, the default plan prompt is used."""
    uid = uuid.uuid4()
    mgid = "group-005"
    msgs = [_make_album_message(mgid, msg_id=i, caption=None) for i in range(1, 3)]
    _media_group_buffer[mgid] = list(msgs)

    engine = _make_engine()
    factory = _make_session_factory(uid)

    sleep_patch, user_patch = _patch_sleep_and_user(uid)
    with sleep_patch, user_patch:
        await _flush_media_group(mgid, engine, factory)

    req = engine.handle_request.call_args[0][0]
    assert req.content == _DEFAULT_PLAN_PROMPT


@pytest.mark.asyncio
async def test_album_flush_cleanup_on_engine_error() -> None:
    """On engine error: buffer+tasks are cleaned up and the user gets a fallback message."""
    uid = uuid.uuid4()
    mgid = "group-006"
    msgs = [_make_album_message(mgid, msg_id=1)]
    _media_group_buffer[mgid] = list(msgs)

    engine = MagicMock()
    engine.handle_request = AsyncMock(side_effect=RuntimeError("boom"))
    factory = _make_session_factory(uid)

    sleep_patch, user_patch = _patch_sleep_and_user(uid)
    with sleep_patch, user_patch:
        await _flush_media_group(mgid, engine, factory)

    assert mgid not in _media_group_buffer
    assert mgid not in _media_group_tasks
    msgs[0].answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancelled_task_does_not_process() -> None:
    """A cancelled (superseded) task exits during sleep without touching the buffer."""
    uid = uuid.uuid4()
    mgid = "group-007"
    msgs = [_make_album_message(mgid, msg_id=1)]
    _media_group_buffer[mgid] = list(msgs)

    engine = _make_engine()
    factory = _make_session_factory(uid)

    # Patch sleep to raise CancelledError (simulates task cancellation mid-sleep)
    with (
        patch(
            "clients.telegram.handlers.asyncio.sleep",
            new=AsyncMock(side_effect=asyncio.CancelledError),
        ),
        patch(
            "clients.telegram.handlers.get_or_create_user_by_telegram_id",
            new=AsyncMock(return_value=uid),
        ),
    ):
        await _flush_media_group(mgid, engine, factory)

    engine.handle_request.assert_not_awaited()
    # Buffer must be untouched by the cancelled task
    assert mgid in _media_group_buffer
    # Clean up
    _media_group_buffer.pop(mgid, None)


@pytest.mark.asyncio
async def test_album_allowlist_enforced() -> None:
    """Album photos from non-allowed users are not buffered."""
    mgid = "group-008"
    msg = _make_album_message(mgid, msg_id=1, from_user_id=9999)
    engine = _make_engine()
    factory = _make_session_factory(uuid.uuid4())

    await handle_photo(msg, engine, factory, allowed_user_ids=frozenset({1}))

    assert mgid not in _media_group_buffer
    engine.handle_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_albums_no_cross_contamination() -> None:
    """Two concurrent albums (different mgids) each get their own buffer entry."""
    uid = uuid.uuid4()
    mgid_a = "group-009a"
    mgid_b = "group-009b"
    msg_a = _make_album_message(mgid_a, msg_id=1)
    msg_b = _make_album_message(mgid_b, msg_id=1)
    engine = _make_engine()
    factory = _make_session_factory(uid)

    with patch("clients.telegram.handlers.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        mock_sleep.side_effect = asyncio.CancelledError

        with patch(
            "clients.telegram.handlers.get_or_create_user_by_telegram_id",
            new=AsyncMock(return_value=uid),
        ):
            await handle_photo(msg_a, engine, factory, allowed_user_ids=frozenset({12345}))
            await handle_photo(msg_b, engine, factory, allowed_user_ids=frozenset({12345}))

    assert mgid_a in _media_group_buffer
    assert mgid_b in _media_group_buffer
    assert _media_group_buffer[mgid_a][0] is msg_a
    assert _media_group_buffer[mgid_b][0] is msg_b

    # Clean up
    for mgid in (mgid_a, mgid_b):
        task = _media_group_tasks.pop(mgid, None)
        _media_group_buffer.pop(mgid, None)
        if task and not task.done():
            task.cancel()
