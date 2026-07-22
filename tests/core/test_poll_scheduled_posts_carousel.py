"""Unit tests for poll_scheduled_posts carousel branch."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.scheduler.jobs import poll_scheduled_posts

_NOW = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
_TTL = 60


def _make_settings(ttl: int = _TTL) -> MagicMock:
    s = MagicMock()
    s.approval_ttl_minutes = ttl
    return s


def _make_user(telegram_id: int = 12345) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), telegram_id=telegram_id)


def _make_carousel_post(user_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user_id,
        status="scheduled",
        post_type="carousel",
        image_url=None,
        image_urls=["https://r2.example.com/a.jpg", "https://r2.example.com/b.jpg"],
        caption="Beach trip highlights",
        scheduled_for=_NOW - timedelta(minutes=1),
        pending_action_id=None,
    )


def _make_single_post(user_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user_id,
        status="scheduled",
        post_type="single",
        image_url="https://r2.example.com/img.jpg",
        image_urls=None,
        caption="Sunset shot",
        scheduled_for=_NOW - timedelta(minutes=1),
        pending_action_id=None,
    )


class _MockDB:
    def __init__(self) -> None:
        self.added: list[object] = []
        self._execute_responses: list[MagicMock] = []
        self.commit = AsyncMock()
        self.delete = AsyncMock()

    def add(self, obj: object) -> None:
        self.added.append(obj)

    def push_execute_response(
        self,
        scalars_result: list[object] | None = None,
        all_result: list[object] | None = None,
        scalar_one_or_none: object = "NOTSET",
    ) -> None:
        result = MagicMock()
        if scalars_result is not None:
            result.scalars.return_value.all.return_value = scalars_result
            if scalar_one_or_none == "NOTSET":
                result.scalar_one_or_none.return_value = (
                    scalars_result[0] if scalars_result else None
                )
        if all_result is not None:
            result.all.return_value = all_result
        if scalar_one_or_none != "NOTSET":
            result.scalar_one_or_none.return_value = scalar_one_or_none
        self._execute_responses.append(result)

    async def execute(self, *args: object, **kwargs: object) -> MagicMock:
        if self._execute_responses:
            return self._execute_responses.pop(0)
        return MagicMock()

    async def get(self, model: object, pk: object) -> object:  # noqa: ARG002
        return self._get_user

    def set_user(self, user: SimpleNamespace) -> None:
        self._get_user = user


def _make_ctx(db: _MockDB, notifier: MagicMock) -> dict[str, object]:
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return {"session_factory": factory, "notifier": notifier}


# ---------------------------------------------------------------------------
# Carousel branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_carousel_post_builds_instagram_carousel_pending_action() -> None:
    """A carousel ScheduledPost must produce action_type='instagram_carousel'."""
    db = _MockDB()
    user = _make_user()
    db.set_user(user)
    post = _make_carousel_post(user.id)

    db.push_execute_response()  # Phase 1 UPDATE
    db.push_execute_response(scalars_result=[post])  # Phase 2: due posts
    db.push_execute_response(scalars_result=[])  # collision check
    db.push_execute_response(all_result=[])  # Phase 3

    notifier = MagicMock()
    notifier.send_photo = AsyncMock()
    ctx = _make_ctx(db, notifier)

    with patch("core.scheduler.jobs.get_settings", return_value=_make_settings()):
        await poll_scheduled_posts(ctx)

    pending_rows = [o for o in db.added if hasattr(o, "action_type")]
    assert len(pending_rows) == 1
    row: SimpleNamespace = pending_rows[0]  # type: ignore[assignment]
    assert row.action_type == "instagram_carousel"
    payload: dict[str, object] = row.action_payload
    assert payload == {
        "caption": "Beach trip highlights",
        "image_urls": ["https://r2.example.com/a.jpg", "https://r2.example.com/b.jpg"],
    }


@pytest.mark.asyncio
async def test_carousel_post_notifies_with_first_image() -> None:
    """send_photo must use the first image URL as preview for a carousel."""
    db = _MockDB()
    user = _make_user(telegram_id=99)
    db.set_user(user)
    post = _make_carousel_post(user.id)

    db.push_execute_response()
    db.push_execute_response(scalars_result=[post])
    db.push_execute_response(scalars_result=[])
    db.push_execute_response(all_result=[])

    notifier = MagicMock()
    notifier.send_photo = AsyncMock()
    ctx = _make_ctx(db, notifier)

    with patch("core.scheduler.jobs.get_settings", return_value=_make_settings()):
        await poll_scheduled_posts(ctx)

    notifier.send_photo.assert_awaited_once()
    call_kwargs = notifier.send_photo.call_args.kwargs
    assert call_kwargs["photo_url"] == "https://r2.example.com/a.jpg"
    assert "carousel" in call_kwargs["caption"].lower()


@pytest.mark.asyncio
async def test_carousel_post_status_set_to_triggered() -> None:
    db = _MockDB()
    user = _make_user()
    db.set_user(user)
    post = _make_carousel_post(user.id)

    db.push_execute_response()
    db.push_execute_response(scalars_result=[post])
    db.push_execute_response(scalars_result=[])
    db.push_execute_response(all_result=[])

    notifier = MagicMock()
    notifier.send_photo = AsyncMock()
    ctx = _make_ctx(db, notifier)

    with patch("core.scheduler.jobs.get_settings", return_value=_make_settings()):
        await poll_scheduled_posts(ctx)

    assert post.status == "triggered"
    assert post.pending_action_id is not None


# ---------------------------------------------------------------------------
# Single branch (regression: existing behaviour unchanged)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_post_still_builds_instagram_post_pending_action() -> None:
    """Existing single-post path must be unaffected by the carousel branch."""
    db = _MockDB()
    user = _make_user()
    db.set_user(user)
    post = _make_single_post(user.id)

    db.push_execute_response()
    db.push_execute_response(scalars_result=[post])
    db.push_execute_response(scalars_result=[])
    db.push_execute_response(all_result=[])

    notifier = MagicMock()
    notifier.send_photo = AsyncMock()
    ctx = _make_ctx(db, notifier)

    with patch("core.scheduler.jobs.get_settings", return_value=_make_settings()):
        await poll_scheduled_posts(ctx)

    pending_rows = [o for o in db.added if hasattr(o, "action_type")]
    assert len(pending_rows) == 1
    row: SimpleNamespace = pending_rows[0]  # type: ignore[assignment]
    assert row.action_type == "instagram_post"
    payload: dict[str, object] = row.action_payload
    assert payload == {
        "caption": "Sunset shot",
        "image_url": "https://r2.example.com/img.jpg",
    }
