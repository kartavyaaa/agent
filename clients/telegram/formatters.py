from __future__ import annotations


def escape_html(text: str) -> str:
    """Escape characters that Telegram's HTML parser treats as markup.

    Order matters: & must be replaced first so the & in &lt;/&gt; isn't re-escaped.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def split_message(text: str, limit: int = 4096) -> list[str]:
    """Split text into chunks each at most `limit` characters.

    Prefers splitting at newline boundaries to preserve paragraph structure.
    Falls back to a hard split when a single line exceeds the limit.
    Always returns at least one chunk.
    """
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")

    return chunks


def format_response(content: str) -> list[str]:
    """Prepare engine output for Telegram: escape HTML, then split at 4096 chars.

    escape-then-split order is required: split boundaries must reflect the final
    escaped length (& → &amp; expands 1 char to 5), not the raw content length.
    """
    return split_message(escape_html(content))
