"""Unit tests for TelegramNotifier.send_photo and _approval_keyboard_dict."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from core.notifications.telegram_notifier import TelegramNotifier, _approval_keyboard_dict


def _make_notifier(status_code: int = 200) -> tuple[TelegramNotifier, MagicMock]:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.raise_for_status = MagicMock(
        side_effect=(
            None
            if status_code < 400
            else httpx.HTTPStatusError("error", request=MagicMock(), response=response)
        )
    )
    client = MagicMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(return_value=response)
    notifier = TelegramNotifier(bot_token="test-token", http_client=client)
    return notifier, client


# ---------------------------------------------------------------------------
# _approval_keyboard_dict
# ---------------------------------------------------------------------------


def test_approval_keyboard_dict_prefixes() -> None:
    pid = uuid.uuid4()
    result = _approval_keyboard_dict(pid)
    buttons = result["inline_keyboard"][0]
    confirm_btn = buttons[0]
    cancel_btn = buttons[1]
    assert confirm_btn["callback_data"].startswith("ok:")
    assert cancel_btn["callback_data"].startswith("no:")


def test_approval_keyboard_dict_contains_uuid() -> None:
    pid = uuid.uuid4()
    result = _approval_keyboard_dict(pid)
    buttons = result["inline_keyboard"][0]
    assert str(pid) in buttons[0]["callback_data"]
    assert str(pid) in buttons[1]["callback_data"]


def test_approval_keyboard_dict_callback_data_within_64_bytes() -> None:
    pid = uuid.uuid4()
    result = _approval_keyboard_dict(pid)
    buttons = result["inline_keyboard"][0]
    # Telegram's 64-byte callback_data limit
    assert len(buttons[0]["callback_data"].encode()) <= 64
    assert len(buttons[1]["callback_data"].encode()) <= 64


# ---------------------------------------------------------------------------
# send_photo — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_photo_calls_sendphoto_endpoint() -> None:
    notifier, client = _make_notifier()
    pid = uuid.uuid4()
    keyboard = _approval_keyboard_dict(pid)

    await notifier.send_photo(
        telegram_id=12345,
        photo_url="https://cdn.example.com/photo.jpg",
        caption="Test caption",
        reply_markup=keyboard,
    )

    client.post.assert_called_once()
    url_arg = client.post.call_args[0][0]
    assert "sendPhoto" in url_arg
    assert "test-token" in url_arg


@pytest.mark.asyncio
async def test_send_photo_payload_shape() -> None:
    notifier, client = _make_notifier()
    pid = uuid.uuid4()
    keyboard = _approval_keyboard_dict(pid)

    await notifier.send_photo(
        telegram_id=99,
        photo_url="https://cdn.example.com/img.jpg",
        caption="Hello",
        reply_markup=keyboard,
    )

    payload = client.post.call_args[1]["json"]
    assert payload["chat_id"] == 99
    assert payload["photo"] == "https://cdn.example.com/img.jpg"
    assert payload["caption"] == "Hello"
    assert payload["reply_markup"] == keyboard


@pytest.mark.asyncio
async def test_send_photo_without_reply_markup() -> None:
    notifier, client = _make_notifier()

    await notifier.send_photo(
        telegram_id=42,
        photo_url="https://cdn.example.com/img.jpg",
        caption="No buttons",
    )

    payload = client.post.call_args[1]["json"]
    assert "reply_markup" not in payload


# ---------------------------------------------------------------------------
# send_photo — HTTP error propagates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_photo_raises_on_http_error() -> None:
    notifier, _ = _make_notifier(status_code=400)

    with pytest.raises(httpx.HTTPStatusError):
        await notifier.send_photo(
            telegram_id=42,
            photo_url="https://cdn.example.com/img.jpg",
            caption="Test",
        )
