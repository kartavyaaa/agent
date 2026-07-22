"""Telegram message handlers — thin translator only.

Allowed imports from core: core.schemas.CoreRequest, core.schemas.CoreResponse,
core.schemas.ProposalPayload, core.exceptions. No business logic here.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from clients.errors import _GENERIC_FALLBACK, user_message
from clients.user_helper import get_or_create_user_by_telegram_id
from core.exceptions import PlatformError
from core.schemas import CoreRequest, CoreResponse, ImageAttachment  # only public types

log = structlog.get_logger()
router = Router()

_FALLBACK = "(No response.)"

# Album default prompt — used when an album arrives with no caption.
_DEFAULT_PLAN_PROMPT = (
    "Create an Instagram content plan for these photos. "
    "Group them into carousels or standalone posts (with reasons), suggest a caption and "
    "hashtags for each, recommend a posting order, and share your take on which shots are "
    "strongest — treat it as a suggestion, the final call is yours."
)

# Module-level state for media-group (album) debounce collection.
# Keyed by media_group_id. asyncio is single-threaded so the append→cancel→restart
# sequence in handle_photo is atomic (no await between them).
_media_group_buffer: dict[str, list[Message]] = {}
_media_group_tasks: dict[str, asyncio.Task] = {}  # type: ignore[type-arg]


async def _edit_message_feedback(
    message: Message,
    text: str,
) -> None:
    """Edit an approval message to show final feedback, removing the inline keyboard.

    Worker-sent proposals arrive as photo messages (sent via sendPhoto).
    Telegram's Bot API does not allow editMessageText on a photo message — you
    must use editMessageCaption instead. Detect by checking message.photo.
    """
    if message.photo:
        await message.edit_caption(caption=text, reply_markup=None)
    else:
        await message.edit_text(text, reply_markup=None)


def _make_approval_keyboard(pending_action_id: uuid.UUID) -> InlineKeyboardMarkup:
    pid = str(pending_action_id)
    # "ok:{uuid}" = 39 bytes, "no:{uuid}" = 39 bytes — both within Telegram's 64-byte limit.
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Confirm", callback_data=f"ok:{pid}"),
                InlineKeyboardButton(text="❌ Cancel", callback_data=f"no:{pid}"),
            ]
        ]
    )


async def _send_response(message: Message, response: CoreResponse) -> None:
    """Send engine response to the user. Handles proposal (inline buttons) and normal text."""
    if response.proposal is not None:
        keyboard = _make_approval_keyboard(response.proposal.pending_action_id)
        await message.answer(response.proposal.preview_text, reply_markup=keyboard)
        return

    from clients.telegram.formatters import format_response  # lazy import

    chunks = format_response(response.content)
    if not chunks:
        await message.answer(_FALLBACK)
    else:
        for chunk_text, chunk_entities in chunks:
            await message.answer(chunk_text, entities=chunk_entities, parse_mode=None)


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

    mgid = message.media_group_id
    if mgid is not None:
        # Album photo — buffer and debounce. The block below has no await, so append →
        # cancel → restart is atomic from asyncio's perspective (single-threaded event loop).
        _media_group_buffer.setdefault(mgid, []).append(message)
        if mgid in _media_group_tasks:
            _media_group_tasks[mgid].cancel()
        _media_group_tasks[mgid] = asyncio.create_task(
            _flush_media_group(mgid, engine, session_factory)
        )
        return

    # Lone photo — existing single-photo path (critique / Instagram post).
    log.info("telegram.photo.received", telegram_user_id=message.from_user.id)

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
        await _send_response(message, response)
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


async def _flush_media_group(
    mgid: str,
    engine: Any,
    session_factory: Any,
) -> None:
    """Debounce coroutine: fires 1.5s after the last photo in an album arrives.

    Cancel safety: CancelledError is caught only during sleep. A superseded task
    (cancelled by a later photo) returns here without touching the shared dicts.
    The replacement task, which was not cancelled, owns the buffer and flushes it.

    Cleanup: both dicts are popped BEFORE any await that can fail, so entries
    never leak regardless of downstream errors.

    Straggler edge: a photo with the same mgid arriving after this flush popped
    starts a fresh buffer and produces a second plan. Realistically impossible
    (albums arrive within ~500ms; 1.5s window covers it) — accepted unguarded edge.
    """
    try:
        await asyncio.sleep(1.5)
    except asyncio.CancelledError:
        # Superseded — replacement task owns the buffer. Exit without touching shared state.
        return

    # Pop both entries BEFORE any await that can fail — no dict leaks on errors.
    msgs = _media_group_buffer.pop(mgid, [])
    _media_group_tasks.pop(mgid, None)

    if not msgs:
        return

    msgs.sort(key=lambda m: m.message_id)
    first = msgs[0]

    try:
        assert first.from_user is not None
        async with session_factory() as db:
            user_id = await get_or_create_user_by_telegram_id(db, first.from_user.id)
            await db.commit()

        assert first.bot is not None
        attachments: list[ImageAttachment] = []
        for msg in msgs:
            assert msg.photo is not None
            photo = msg.photo[-1]
            file = await first.bot.get_file(photo.file_id)
            assert file.file_path is not None
            buf = await first.bot.download_file(file.file_path)
            assert buf is not None
            attachments.append(
                ImageAttachment(
                    data=base64.b64encode(buf.read()).decode(),
                    mime="image/jpeg",
                )
            )

        caption = first.caption or _DEFAULT_PLAN_PROMPT
        request = CoreRequest(user_id=user_id, content=caption, images=attachments)
        log.info("telegram.album.flush", mgid=mgid, n=len(attachments))
        response: CoreResponse = await engine.handle_request(request)
        await _send_response(first, response)

    except PlatformError as exc:
        log.warning(
            "telegram.album.platform_error",
            mgid=mgid,
            exc_type=type(exc).__name__,
            error=str(exc),
        )
        await first.answer(user_message(exc))
    except Exception:
        log.exception("telegram.album.unexpected_error", mgid=mgid)
        await first.answer(_GENERIC_FALLBACK)


@router.message(F.text)
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
        await _send_response(message, response)
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


@router.callback_query()
async def handle_callback(
    callback: CallbackQuery,
    session_factory: Any,
    registry: Any,
    allowed_user_ids: frozenset[int],
) -> None:
    """Handle inline button presses for approval flow.

    Guards (in order): allowlist, data format, UUID validity, row existence,
    user ownership, status (non-pending rejected), expiry. On "ok": claim the
    row by setting status="executing" and committing BEFORE calling execute() —
    this prevents double-execution if the process crashes between execute() and
    the final commit.
    """
    if not callback.from_user:
        await callback.answer()
        return

    if callback.from_user.id not in allowed_user_ids:
        await callback.answer()
        return

    data = callback.data or ""
    if not (data.startswith("ok:") or data.startswith("no:")):
        await callback.answer()
        return

    choice = data[:2]  # "ok" or "no"
    pending_id_str = data[3:]
    try:
        pending_id = uuid.UUID(pending_id_str)
    except ValueError:
        await callback.answer("Invalid action ID.")
        return

    from sqlalchemy import select

    from models.pending_action import PendingAction

    async with session_factory() as db:
        try:
            result = await db.execute(select(PendingAction).where(PendingAction.id == pending_id))
            row = result.scalar_one_or_none()

            user_id = await get_or_create_user_by_telegram_id(db, callback.from_user.id)
            now = datetime.now(UTC)

            if row is None or row.user_id != user_id:
                await callback.answer("Action not found.")
                if isinstance(callback.message, Message):
                    await callback.message.edit_reply_markup(reply_markup=None)
                return

            if row.status != "pending":
                # Covers "executing" (crash-recovery claim), "confirmed", "cancelled",
                # "expired", "failed" — none should execute again.
                await callback.answer("This action was already handled.")
                if isinstance(callback.message, Message):
                    await callback.message.edit_reply_markup(reply_markup=None)
                return

            if row.expires_at <= now:
                row.status = "expired"
                await db.flush()
                await db.commit()
                await callback.answer("This action has expired.")
                if isinstance(callback.message, Message):
                    await _edit_message_feedback(callback.message, "⏰ Action expired.")
                return

            if choice == "no":
                row.status = "cancelled"
                await db.flush()
                await db.commit()
                await callback.answer("Cancelled.")
                if isinstance(callback.message, Message):
                    await _edit_message_feedback(callback.message, "❌ Cancelled.")
                return

            # choice == "ok"
            # Claim the row BEFORE executing to prevent double-execution on crash.
            # Any concurrent tap or post-crash re-tap sees "executing" (non-pending)
            # and is rejected by the status guard above.
            row.status = "executing"
            await db.flush()
            await db.commit()

            # Answer the callback IMMEDIATELY — Telegram requires a response within
            # ~15s or it shows "query is too old". Slow actions (carousel ~40s) would
            # miss this window if we answered after execute(). Message edits have no
            # time limit, so we answer now and edit the message after execution.
            await callback.answer("Posting…")

            feedback: str = "✅ Done."
            try:
                result = await registry.execute(
                    row.action_type,
                    row.action_payload,
                    user_id=user_id,
                    db=db,
                    _approved=True,
                )
                feedback = result.get("confirmation", "✅ Done.")
                row.status = "confirmed"
                await db.flush()
                await db.commit()
            except PlatformError as exc:
                row.status = "failed"
                await db.flush()
                await db.commit()
                log.warning(
                    "callback.execute_platform_error",
                    action_type=row.action_type,
                    exc_type=type(exc).__name__,
                )
                feedback = f"❌ {user_message(exc)}"
            except Exception:
                row.status = "failed"
                await db.flush()
                await db.commit()
                log.exception("callback.execute_unexpected_error", action_type=row.action_type)
                feedback = "❌ Execution failed."
            if isinstance(callback.message, Message):
                await _edit_message_feedback(callback.message, feedback)

        except Exception:
            log.exception("callback.unexpected_error")
            await callback.answer("Something went wrong.")
            if isinstance(callback.message, Message):
                with contextlib.suppress(Exception):
                    await _edit_message_feedback(callback.message, "❌ Something went wrong.")
