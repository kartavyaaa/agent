"""Unit tests for clients/telegram/formatters.py.

These tests require telegramify-markdown to be installed (PC-gate item).
On the VM (no network), they are skipped via importorskip.
"""

from __future__ import annotations

import pytest

pytest.importorskip(
    "telegramify_markdown",
    reason="telegramify-markdown not installed (PC-gate item; run pip install -e '.[dev]' on PC)",
)

from aiogram.types import MessageEntity  # noqa: E402

from clients.telegram.formatters import _to_aiogram, format_response  # noqa: E402

# ---------------------------------------------------------------------------
# Entity conversion: lib → aiogram
# ---------------------------------------------------------------------------


def test_entity_conversion_preserves_fields() -> None:
    import telegramify_markdown.entity as tg_entity

    lib_ent = tg_entity.MessageEntity(type="bold", offset=0, length=4)
    result = _to_aiogram(lib_ent)
    assert isinstance(result, MessageEntity)
    assert result.type == "bold"
    assert result.offset == 0
    assert result.length == 4


# ---------------------------------------------------------------------------
# format_response: Markdown → entity output
# ---------------------------------------------------------------------------


def test_bold_produces_entity() -> None:
    chunks = format_response("**bold**")
    assert len(chunks) == 1
    text, entities = chunks[0]
    assert "bold" in text
    assert "**" not in text
    bold_entities = [e for e in entities if e.type == "bold"]
    assert len(bold_entities) >= 1


def test_inline_code_produces_entity() -> None:
    chunks = format_response("`code`")
    assert len(chunks) == 1
    text, entities = chunks[0]
    assert "code" in text
    code_entities = [e for e in entities if e.type == "code"]
    assert len(code_entities) >= 1


def test_code_block_produces_pre_entity() -> None:
    md = "```python\nprint('hi')\n```"
    chunks = format_response(md)
    assert len(chunks) >= 1
    all_entities = [e for _, ents in chunks for e in ents]
    pre_entities = [e for e in all_entities if e.type == "pre"]
    assert len(pre_entities) >= 1
    # language field should be set
    assert any(e.language == "python" for e in pre_entities)


def test_plain_text_no_bold_entities() -> None:
    chunks = format_response("Hello world. This is a plain sentence.")
    assert len(chunks) == 1
    text, entities = chunks[0]
    assert "Hello world" in text
    # plain text must not produce bold or italic entities
    assert not any(e.type in ("bold", "italic") for e in entities)


def test_bullet_list_text_preserved() -> None:
    md = "- item one\n- item two\n- item three"
    chunks = format_response(md)
    combined = "".join(t for t, _ in chunks)
    assert "item one" in combined
    assert "item two" in combined


# ---------------------------------------------------------------------------
# format_response: empty / whitespace
# ---------------------------------------------------------------------------


def test_empty_returns_no_chunks() -> None:
    assert format_response("") == []


def test_whitespace_returns_no_chunks() -> None:
    assert format_response("   ") == []


def test_newline_only_returns_no_chunks() -> None:
    assert format_response("\n\n") == []


# ---------------------------------------------------------------------------
# format_response: return type
# ---------------------------------------------------------------------------


def test_returns_list_of_tuples() -> None:
    result = format_response("Hello")
    assert isinstance(result, list)
    assert len(result) >= 1
    text, entities = result[0]
    assert isinstance(text, str)
    assert isinstance(entities, list)


def test_short_message_single_chunk() -> None:
    result = format_response("Short message.")
    assert len(result) == 1


# ---------------------------------------------------------------------------
# format_response: splitting — UTF-16 limit enforced
# ---------------------------------------------------------------------------


def test_long_text_multiple_chunks_within_utf16_limit() -> None:
    # Plain text well over 4096 chars
    text = ("This is a sentence. " * 300).strip()
    result = format_response(text)
    assert len(result) >= 2
    for chunk_text, _ in result:
        utf16_len = len(chunk_text.encode("utf-16-le")) // 2
        assert utf16_len <= 4096, f"chunk exceeds 4096 UTF-16 units: {utf16_len}"


def test_long_formatted_text_within_limit() -> None:
    block = "**Important:** here is a point to remember.\n\n"
    text = block * 100
    result = format_response(text)
    for chunk_text, _ in result:
        utf16_len = len(chunk_text.encode("utf-16-le")) // 2
        assert utf16_len <= 4096


def test_long_code_block_entity_offsets_valid() -> None:
    # Large code block — split_entities handles the boundary; entity offsets must be in range
    code_body = "x = 1\n" * 700  # ~4900 chars
    md = "Introduction.\n\n```python\n" + code_body + "```"
    result = format_response(md)
    assert len(result) >= 1
    for chunk_text, chunk_entities in result:
        for e in chunk_entities:
            assert e.offset >= 0
            assert e.offset + e.length <= len(chunk_text), (
                f"entity {e.type} out of range: offset={e.offset} length={e.length} "
                f"text_len={len(chunk_text)}"
            )
