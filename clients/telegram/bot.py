"""Telegram bot entry point — thin translator.

The engine and telegram_user_map are injected via Dispatcher kwargs so that
handlers receive them as typed parameters without importing from core directly.
"""

from __future__ import annotations

import uuid
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from clients.telegram.handlers import router
from core.config import get_settings


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(router)
    return dp


def build_bot() -> Bot:
    s = get_settings()
    return Bot(
        token=s.telegram_bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


async def run_polling(engine: Any, telegram_user_map: dict[int, uuid.UUID]) -> None:
    """Start long-polling (development / VM mode).

    engine and telegram_user_map are injected into every handler via
    Dispatcher's workflow_data kwargs.
    """
    bot = build_bot()
    dp = build_dispatcher()
    await dp.start_polling(bot, engine=engine, telegram_user_map=telegram_user_map)
