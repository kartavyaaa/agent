from __future__ import annotations

import telegramify_markdown
import telegramify_markdown.entity as tg_entity
from aiogram.types import MessageEntity

_UTF16_LIMIT = 4096


def _to_aiogram(lib_entity: tg_entity.MessageEntity) -> MessageEntity:
    return MessageEntity.model_validate(lib_entity.to_dict())


def format_response(content: str) -> list[tuple[str, list[MessageEntity]]]:
    """Convert raw LLM Markdown to (text, entities) chunks for Telegram.

    Uses convert() + split_entities() — the library's entity path.
    Text is plain UTF-8; entities carry all formatting. No parse_mode needed.
    Feed RAW LLM markdown; do not pre-escape.

    Returns [] for empty/whitespace content — caller must send a fallback
    rather than passing empty text to Telegram (which returns 400).
    """
    if not content or not content.strip():
        return []

    text, lib_entities = telegramify_markdown.convert(content)
    chunks = telegramify_markdown.split_entities(text, lib_entities, _UTF16_LIMIT)
    return [
        (chunk_text, [_to_aiogram(e) for e in chunk_entities])
        for chunk_text, chunk_entities in chunks
    ]
