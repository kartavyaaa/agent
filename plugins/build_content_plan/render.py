"""Shared rendering helpers for build_content_plan draft items."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def parse_preview_time(raw: Any) -> str:
    """Format a raw scheduled_for value as human-readable local string.

    The value may arrive as a datetime (Pydantic-parsed) or a string.
    Returns a human-readable local string like "Mon Jul 28, 6:00 PM".
    Uses explicit lstrip("0") for cross-platform compat (%-d/%-I are Linux-only).
    """
    try:
        dt = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw).replace("Z", ""))
        day = str(dt.day)
        hour = str(dt.hour % 12 or 12)
        ampm = "AM" if dt.hour < 12 else "PM"
        minute = dt.strftime("%M")
        return dt.strftime(f"%a %b {day}, {hour}:{minute} {ampm}")
    except Exception:
        return str(raw)


def render_items(items: list[dict[str, Any]], tz_name: str = "UTC") -> str:
    """Render a draft item list as a numbered human-readable string."""
    lines: list[str] = []
    for i, item in enumerate(items, 1):
        indices: list[int] = item.get("image_indices", [])
        n = len(indices)
        kind = "Carousel" if n > 1 else "Single"
        caption: str = item.get("caption", "")
        caption_preview = (caption[:57] + "…") if len(caption) > 60 else caption
        raw_time = item.get("scheduled_for", "")
        time_str = parse_preview_time(raw_time)
        lines.append(
            f"  {i}. {kind} ({n} photo{'s' if n != 1 else ''}) — \"{caption_preview}\" — {time_str}"
        )
    return "\n".join(lines)
