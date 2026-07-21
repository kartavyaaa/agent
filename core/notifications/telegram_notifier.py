from __future__ import annotations

import uuid
from typing import Any

import httpx


def _approval_keyboard_dict(pending_action_id: uuid.UUID) -> dict[str, Any]:
    """Build the Bot API inline-keyboard payload for an approval flow.

    Prefixes "ok:" / "no:" match handle_callback's parser exactly (handlers.py:55,278).
    Any change here MUST be mirrored in _make_approval_keyboard in handlers.py.
    """
    pid = str(pending_action_id)
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Confirm", "callback_data": f"ok:{pid}"},
                {"text": "❌ Cancel", "callback_data": f"no:{pid}"},
            ]
        ]
    }


class TelegramNotifier:
    def __init__(
        self,
        bot_token: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token = bot_token
        # http_client injected for testing; worker startup creates a real one
        self._client = http_client or httpx.AsyncClient()

    async def send(self, telegram_id: int, message: str) -> None:
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        r = await self._client.post(
            url,
            json={"chat_id": telegram_id, "text": message},
            timeout=10.0,
        )
        r.raise_for_status()

    async def send_photo(
        self,
        telegram_id: int,
        photo_url: str,
        caption: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        """Send a photo with an optional inline keyboard via the Bot API.

        reply_markup must be a plain dict (Bot API JSON shape), not an aiogram object.
        Use _approval_keyboard_dict() to build the approval keyboard.
        """
        url = f"https://api.telegram.org/bot{self._token}/sendPhoto"
        payload: dict[str, Any] = {"chat_id": telegram_id, "photo": photo_url, "caption": caption}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        r = await self._client.post(url, json=payload, timeout=10.0)
        r.raise_for_status()
