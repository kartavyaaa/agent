from __future__ import annotations

import httpx


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
