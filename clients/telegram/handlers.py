"""Telegram message handlers — thin translator only.

Allowed imports from core: core.schemas.CoreRequest, core.schemas.CoreResponse.
No other core imports; no business logic here.
"""

from __future__ import annotations

import uuid
from typing import Any

from aiogram import Router
from aiogram.types import Message

from core.schemas import CoreRequest, CoreResponse  # only public types

router = Router()


@router.message()
async def handle_message(
    message: Message, engine: Any, telegram_user_map: dict[int, uuid.UUID]
) -> None:
    if not message.text or not message.from_user:
        return

    user_id = telegram_user_map.get(message.from_user.id)
    if user_id is None:
        await message.answer("Your account is not linked. Please register first.")
        return

    request = CoreRequest(user_id=user_id, content=message.text)
    response: CoreResponse = await engine.handle_request(request)
    await message.answer(response.content)
