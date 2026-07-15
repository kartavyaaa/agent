"""Telegram message handlers — thin translator only.

Allowed imports from core: core.schemas.CoreRequest, core.schemas.CoreResponse.
No other core imports; no business logic here.
"""

from __future__ import annotations

from typing import Any

import structlog
from aiogram import Router
from aiogram.types import Message

from clients.telegram.formatters import format_response
from clients.user_helper import get_or_create_user_by_telegram_id
from core.schemas import CoreRequest, CoreResponse  # only public types

log = structlog.get_logger()
router = Router()


@router.message()
async def handle_message(
    message: Message,
    engine: Any,
    session_factory: Any,
    allowed_user_ids: frozenset[int],
) -> None:
    if not message.text or not message.from_user:
        return

    if message.from_user.id not in allowed_user_ids:
        log.info("telegram.message.ignored", telegram_user_id=message.from_user.id)
        return

    async with session_factory() as db:
        user_id = await get_or_create_user_by_telegram_id(db, message.from_user.id)
        await db.commit()

    request = CoreRequest(user_id=user_id, content=message.text)
    response: CoreResponse = await engine.handle_request(request)
    for chunk in format_response(response.content):
        await message.answer(chunk)
