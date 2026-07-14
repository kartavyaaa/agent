"""Telegram message handlers — thin translator only.

Allowed imports from core: core.schemas.CoreRequest, core.schemas.CoreResponse.
No other core imports; no business logic here.
"""

from __future__ import annotations

from typing import Any

from aiogram import Router
from aiogram.types import Message

from clients.telegram.formatters import format_response
from clients.user_helper import get_or_create_user_by_telegram_id
from core.schemas import CoreRequest, CoreResponse  # only public types

router = Router()


@router.message()
async def handle_message(
    message: Message,
    engine: Any,
    session_factory: Any,
) -> None:
    if not message.text or not message.from_user:
        return

    async with session_factory() as db:
        user_id = await get_or_create_user_by_telegram_id(db, message.from_user.id)
        await db.commit()

    request = CoreRequest(user_id=user_id, content=message.text)
    response: CoreResponse = await engine.handle_request(request)
    for chunk in format_response(response.content):
        await message.answer(chunk)
