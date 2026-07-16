"""Telegram message handlers — thin translator only.

Allowed imports from core: core.schemas.CoreRequest, core.schemas.CoreResponse.
No other core imports; no business logic here.
"""

from __future__ import annotations

import base64
from typing import Any

import structlog
from aiogram import F, Router
from aiogram.types import Message

from clients.errors import _GENERIC_FALLBACK, user_message
from clients.user_helper import get_or_create_user_by_telegram_id
from core.exceptions import PlatformError
from core.schemas import CoreRequest, CoreResponse  # only public types

log = structlog.get_logger()
router = Router()

_FALLBACK = "(No response.)"


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
    try:
        response: CoreResponse = await engine.handle_request(request)
        from clients.telegram.formatters import (  # lazy: import only on success path; keeps error paths importable without telegramify_markdown
            format_response,
        )

        chunks = format_response(response.content)
        if not chunks:
            await message.answer(_FALLBACK)
        else:
            for chunk_text, chunk_entities in chunks:
                await message.answer(chunk_text, entities=chunk_entities, parse_mode=None)
    except PlatformError as exc:
        log.warning(
            "telegram.handle_message.platform_error",
            exc_type=type(exc).__name__,
            error=str(exc),
        )
        await message.answer(user_message(exc))
    except Exception:
        log.exception("telegram.handle_message.unexpected_error")
        await message.answer(_GENERIC_FALLBACK)


@router.message(F.photo)
async def handle_photo(
    message: Message,
    engine: Any,
    session_factory: Any,
    allowed_user_ids: frozenset[int],
) -> None:
    if not message.from_user:
        return

    if message.from_user.id not in allowed_user_ids:
        log.info("telegram.photo.ignored", telegram_user_id=message.from_user.id)
        return

    async with session_factory() as db:
        user_id = await get_or_create_user_by_telegram_id(db, message.from_user.id)
        await db.commit()

    # Highest-res photo is the last element; Telegram always delivers as JPEG.
    # message.photo and message.bot are guaranteed non-None when F.photo matches,
    # but mypy doesn't know that — assert to narrow the types.
    assert message.photo is not None and message.bot is not None
    photo = message.photo[-1]
    file = await message.bot.get_file(photo.file_id)
    assert file.file_path is not None
    buf = await message.bot.download_file(file.file_path)
    assert buf is not None
    image_b64 = base64.b64encode(buf.read()).decode()

    caption = message.caption or "Please critique this photo."
    request = CoreRequest(
        user_id=user_id,
        content=caption,
        image_base64=image_b64,
        image_mime="image/jpeg",
    )
    try:
        response: CoreResponse = await engine.handle_request(request)
        from clients.telegram.formatters import format_response

        chunks = format_response(response.content)
        if not chunks:
            await message.answer(_FALLBACK)
        else:
            for chunk_text, chunk_entities in chunks:
                await message.answer(chunk_text, entities=chunk_entities, parse_mode=None)
    except PlatformError as exc:
        log.warning(
            "telegram.handle_photo.platform_error",
            exc_type=type(exc).__name__,
            error=str(exc),
        )
        await message.answer(user_message(exc))
    except Exception:
        log.exception("telegram.handle_photo.unexpected_error")
        await message.answer(_GENERIC_FALLBACK)
