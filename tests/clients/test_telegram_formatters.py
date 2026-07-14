"""Unit tests for clients/telegram/formatters.py.

Pure unit tests — no mocking, no external dependencies.
"""

from __future__ import annotations

from clients.telegram.formatters import escape_html, format_response, split_message

# ---------------------------------------------------------------------------
# escape_html
# ---------------------------------------------------------------------------


def test_escape_html_ampersand() -> None:
    assert escape_html("a & b") == "a &amp; b"


def test_escape_html_angle_brackets() -> None:
    assert escape_html("<tag>") == "&lt;tag&gt;"


def test_escape_html_noop_plain_text() -> None:
    assert escape_html("hello world") == "hello world"


def test_escape_html_all_entities() -> None:
    assert escape_html("a & b < c > d") == "a &amp; b &lt; c &gt; d"


def test_escape_html_ampersand_not_double_escaped() -> None:
    # The function escapes what it receives; it does not try to be idempotent.
    # Input "&amp;" (6 chars) → "&amp;amp;" (9 chars).
    assert escape_html("&amp;") == "&amp;amp;"


def test_escape_html_ampersand_before_angle_brackets() -> None:
    # If < were replaced first, "&" in "&lt;" would be double-escaped.
    # Correct order: & → &amp;, then < → &lt;.
    assert escape_html("a < b & c > d") == "a &lt; b &amp; c &gt; d"


# ---------------------------------------------------------------------------
# split_message
# ---------------------------------------------------------------------------


def test_split_short_no_split() -> None:
    text = "hello world"
    result = split_message(text)
    assert result == [text]


def test_split_exactly_at_limit_no_split() -> None:
    text = "x" * 4096
    result = split_message(text)
    assert result == [text]


def test_split_one_over_limit() -> None:
    text = "x" * 4097
    result = split_message(text)
    assert len(result) == 2
    assert all(len(c) <= 4096 for c in result)


def test_split_long_message_all_chunks_within_limit() -> None:
    text = "A" * 9000
    result = split_message(text)
    assert len(result) >= 2
    assert all(len(c) <= 4096 for c in result)
    assert "".join(result) == text


def test_split_preserves_newline_boundaries() -> None:
    # 100 lines of 50 chars each = 5050 chars total → must split
    line = "B" * 49 + "\n"  # 50 chars per line
    text = line * 101  # 5050 chars
    result = split_message(text)
    assert len(result) >= 2
    # No chunk should end mid-word (all natural splits are at \n)
    for chunk in result:
        assert len(chunk) <= 4096


def test_split_hard_splits_single_overlong_line() -> None:
    # One line with no newline that exceeds the limit
    text = "Z" * 5000
    result = split_message(text, limit=100)
    assert all(len(c) <= 100 for c in result)
    assert "".join(result) == text


def test_split_custom_limit() -> None:
    text = "ab\ncd\nef"
    result = split_message(text, limit=5)
    assert all(len(c) <= 5 for c in result)


def test_split_empty_string() -> None:
    result = split_message("")
    assert result == [""]


# ---------------------------------------------------------------------------
# format_response
# ---------------------------------------------------------------------------


def test_format_response_escapes_and_splits() -> None:
    assert format_response("<b>") == ["&lt;b&gt;"]


def test_format_response_plain_short() -> None:
    assert format_response("hello") == ["hello"]


def test_format_response_escape_then_split_ordering() -> None:
    # 1024 '&' chars → after escaping: "&amp;" * 1024 = 5120 chars > 4096.
    # If splitting happened BEFORE escaping (wrong order), it would see 1024 chars
    # and return a single chunk — violating Telegram's limit after escaping.
    text = "&" * 1024
    result = format_response(text)
    assert (
        len(result) > 1
    ), "escape-then-split: escaped content (5120 chars) must be split into 2+ chunks"
    assert all(len(c) <= 4096 for c in result)
    # Reassembled content must equal the escaped form
    assert "".join(result) == "&amp;" * 1024
