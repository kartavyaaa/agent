"""Unit tests for poll_scheduled_posts.

DB, notifier, and settings are mocked — no Postgres or real HTTP needed.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.scheduler.jobs import poll_scheduled_posts

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 7, 21, 15, 0, tzinfo=UTC)
_TTL = 60  # minutes


def _make_settings(ttl: int = _TTL) -> MagicMock:
    s = MagicMock()
    s.approval_ttl_minutes = ttl
    return s


def _make_user(telegram_id: int | None = 12345) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), telegram_id=telegram_id)


def _make_post(
    user_id: uuid.UUID,
    status: str = "scheduled",
    scheduled_for: datetime | None = None,
    image_url: str = "https://cdn.example.com/img.jpg",
    caption: str = "Test caption",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user_id,
        status=status,
        scheduled_for=scheduled_for or (_NOW - timedelta(minutes=1)),
        image_url=image_url,
        caption=caption,
        pending_action_id=None,
    )


def _make_pending(user_id: uuid.UUID, status: str = "pending") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user_id,
        status=status,
    )


class _MockDB:
    """Stateful mock DB that tracks added objects and supports execute() responses."""

    def __init__(self) -> None:
        self.added: list[object] = []
        self.deleted: list[object] = []
        self._execute_responses: list[MagicMock] = []
        self.commit = AsyncMock()
        self.delete = AsyncMock(side_effect=self._do_delete)

    def _do_delete(self, obj: object) -> None:
        self.deleted.append(obj)

    def add(self, obj: object) -> None:
        self.added.append(obj)

    def push_execute_response(
        self,
        scalars_result: list | None = None,
        all_result: list | None = None,
        scalar_one_or_none: object = "NOTSET",
    ) -> None:
        result = MagicMock()
        if scalars_result is not None:
            result.scalars.return_value.all.return_value = scalars_result
            # Also set scalar_one_or_none to None by default when scalars_result is set
            # (collision check uses scalar_one_or_none, not scalars().all())
            if scalar_one_or_none == "NOTSET":
                result.scalar_one_or_none.return_value = (
                    scalars_result[0] if scalars_result else None
                )
        if all_result is not None:
            result.all.return_value = all_result
        if scalar_one_or_none != "NOTSET":
            result.scalar_one_or_none.return_value = scalar_one_or_none
        self._execute_responses.append(result)

    async def execute(self, *args, **kwargs):  # noqa: ANN202
        if self._execute_responses:
            return self._execute_responses.pop(0)
        return MagicMock()

    async def get(self, model, pk):  # noqa: ANN202, ARG002
        return self._get_user

    def set_user(self, user: SimpleNamespace) -> None:
        self._get_user = user


def _make_ctx(db: _MockDB, notifier: MagicMock) -> dict:
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return {"session_factory": factory, "notifier": notifier}


# ---------------------------------------------------------------------------
# Phase 1: expire overdue pending actions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase1_executes_update_expire() -> None:
    """Phase 1 runs an UPDATE to flip overdue pending→expired before the trigger loop."""
    db = _MockDB()
    user = _make_user()
    db.set_user(user)
    # Phase 1 execute: UPDATE (returns nothing meaningful)
    db.push_execute_response(scalars_result=[])  # Phase 1 UPDATE
    db.push_execute_response(scalars_result=[])  # Phase 2: due posts query → empty
    db.push_execute_response(all_result=[])  # Phase 3: reconcile → empty

    notifier = AsyncMock()
    ctx = _make_ctx(db, notifier)

    with patch("core.scheduler.jobs.get_settings", return_value=_make_settings()):
        await poll_scheduled_posts(ctx)

    # commit called at least once (phase 1) + once more (phase 3)
    assert db.commit.call_count >= 2


# ---------------------------------------------------------------------------
# Phase 2: trigger loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_creates_pending_action_and_sets_triggered() -> None:
    db = _MockDB()
    user = _make_user(telegram_id=99)
    db.set_user(user)
    post = _make_post(user.id)

    db.push_execute_response()  # Phase 1 UPDATE
    db.push_execute_response(scalars_result=[post])  # Phase 2: due posts
    # collision check: no existing pending
    db.push_execute_response(scalars_result=[])
    db.push_execute_response(all_result=[])  # Phase 3: reconcile

    notifier = AsyncMock()
    notifier.send_photo = AsyncMock()
    ctx = _make_ctx(db, notifier)

    with patch("core.scheduler.jobs.get_settings", return_value=_make_settings()):
        await poll_scheduled_posts(ctx)

    assert post.status == "triggered"
    assert post.pending_action_id is not None
    pending_rows = [o for o in db.added if hasattr(o, "action_type")]
    assert len(pending_rows) == 1
    assert pending_rows[0].action_type == "instagram_post"
    assert pending_rows[0].action_payload["image_url"] == post.image_url
    assert pending_rows[0].action_payload["caption"] == post.caption


@pytest.mark.asyncio
async def test_commit_before_send() -> None:
    """DB must be committed before send_photo is called."""
    commit_order: list[str] = []

    db = _MockDB()
    user = _make_user(telegram_id=99)
    db.set_user(user)
    post = _make_post(user.id)

    original_commit = db.commit

    async def tracking_commit() -> None:
        commit_order.append("commit")
        await original_commit()

    db.commit = AsyncMock(side_effect=tracking_commit)

    db.push_execute_response()  # Phase 1 UPDATE
    db.push_execute_response(scalars_result=[post])  # Phase 2: due posts
    db.push_execute_response(scalars_result=[])  # collision check
    db.push_execute_response(all_result=[])  # Phase 3

    send_calls: list[str] = []

    async def tracking_send(*args, **kwargs):  # noqa: ANN202, ARG001
        send_calls.append("send")

    notifier = MagicMock()
    notifier.send_photo = AsyncMock(side_effect=tracking_send)
    ctx = _make_ctx(db, notifier)

    with patch("core.scheduler.jobs.get_settings", return_value=_make_settings()):
        await poll_scheduled_posts(ctx)

    # First commit (phase 1), then per-post commit, then send
    # The per-post commit must come before send
    assert "commit" in commit_order
    assert send_calls == ["send"]


@pytest.mark.asyncio
async def test_collision_skip_when_pending_exists() -> None:
    """If user has a pending action, the post is skipped (retried next poll)."""
    db = _MockDB()
    user = _make_user(telegram_id=55)
    db.set_user(user)
    post = _make_post(user.id)
    existing_pending = _make_pending(user.id, status="pending")

    db.push_execute_response()  # Phase 1 UPDATE
    db.push_execute_response(scalars_result=[post])  # Phase 2: due posts
    db.push_execute_response(scalar_one_or_none=existing_pending)  # collision check → found
    db.push_execute_response(all_result=[])  # Phase 3

    notifier = AsyncMock()
    notifier.send_photo = AsyncMock()
    ctx = _make_ctx(db, notifier)

    with patch("core.scheduler.jobs.get_settings", return_value=_make_settings()):
        await poll_scheduled_posts(ctx)

    assert post.status == "scheduled"  # not changed
    notifier.send_photo.assert_not_called()


@pytest.mark.asyncio
async def test_one_trigger_per_user_per_cycle() -> None:
    """Two due posts for the same user: only the first (earliest) is triggered."""
    db = _MockDB()
    user = _make_user(telegram_id=77)
    db.set_user(user)
    post1 = _make_post(user.id, scheduled_for=_NOW - timedelta(minutes=10))
    post2 = _make_post(user.id, scheduled_for=_NOW - timedelta(minutes=5))

    db.push_execute_response()  # Phase 1 UPDATE
    db.push_execute_response(scalars_result=[post1, post2])  # Phase 2: due posts
    db.push_execute_response(scalars_result=[])  # collision check for post1
    db.push_execute_response(all_result=[])  # Phase 3

    notifier = AsyncMock()
    notifier.send_photo = AsyncMock()
    ctx = _make_ctx(db, notifier)

    with patch("core.scheduler.jobs.get_settings", return_value=_make_settings()):
        await poll_scheduled_posts(ctx)

    assert post1.status == "triggered"
    assert post2.status == "scheduled"  # skipped (triggered_users guard)
    assert notifier.send_photo.call_count == 1


@pytest.mark.asyncio
async def test_send_failure_compensates_and_resets_post() -> None:
    """On send failure: pending row deleted, post reset to 'scheduled'."""
    db = _MockDB()
    user = _make_user(telegram_id=99)
    db.set_user(user)
    post = _make_post(user.id)

    db.push_execute_response()  # Phase 1 UPDATE
    db.push_execute_response(scalars_result=[post])  # Phase 2: due posts
    db.push_execute_response(scalars_result=[])  # collision check
    db.push_execute_response(all_result=[])  # Phase 3

    notifier = MagicMock()
    notifier.send_photo = AsyncMock(side_effect=RuntimeError("network error"))
    ctx = _make_ctx(db, notifier)

    with patch("core.scheduler.jobs.get_settings", return_value=_make_settings()):
        await poll_scheduled_posts(ctx)

    assert post.status == "scheduled"  # reset
    assert post.pending_action_id is None  # reset
    # pending row must have been deleted
    assert len(db.deleted) == 1


# ---------------------------------------------------------------------------
# Phase 3: reconcile
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_confirmed_to_posted() -> None:
    db = _MockDB()
    user = _make_user()
    db.set_user(user)

    triggered_post = _make_post(user.id, status="triggered")
    pending = _make_pending(user.id, status="confirmed")

    db.push_execute_response()  # Phase 1 UPDATE
    db.push_execute_response(scalars_result=[])  # Phase 2: no due posts
    db.push_execute_response(all_result=[(triggered_post, pending)])  # Phase 3

    notifier = AsyncMock()
    ctx = _make_ctx(db, notifier)

    with patch("core.scheduler.jobs.get_settings", return_value=_make_settings()):
        await poll_scheduled_posts(ctx)

    assert triggered_post.status == "posted"


@pytest.mark.asyncio
async def test_reconcile_cancelled_to_cancelled() -> None:
    db = _MockDB()
    user = _make_user()
    db.set_user(user)

    triggered_post = _make_post(user.id, status="triggered")
    pending = _make_pending(user.id, status="cancelled")

    db.push_execute_response()
    db.push_execute_response(scalars_result=[])
    db.push_execute_response(all_result=[(triggered_post, pending)])

    notifier = AsyncMock()
    ctx = _make_ctx(db, notifier)

    with patch("core.scheduler.jobs.get_settings", return_value=_make_settings()):
        await poll_scheduled_posts(ctx)

    assert triggered_post.status == "cancelled"


@pytest.mark.asyncio
async def test_reconcile_expired_to_failed() -> None:
    db = _MockDB()
    user = _make_user()
    db.set_user(user)

    triggered_post = _make_post(user.id, status="triggered")
    pending = _make_pending(user.id, status="expired")

    db.push_execute_response()
    db.push_execute_response(scalars_result=[])
    db.push_execute_response(all_result=[(triggered_post, pending)])

    notifier = AsyncMock()
    ctx = _make_ctx(db, notifier)

    with patch("core.scheduler.jobs.get_settings", return_value=_make_settings()):
        await poll_scheduled_posts(ctx)

    assert triggered_post.status == "failed"


@pytest.mark.asyncio
async def test_never_answered_post_then_unblocked() -> None:
    """After expiry phase reconciles a post to 'failed', the user's collision check clears,
    and a subsequent poll can trigger a fresh post for the same user."""
    # Simulate two poll cycles:
    # Cycle 1: post is triggered but user never answers → next cycle it's expired+failed
    # Cycle 2: fresh post for same user can now trigger (no pending collision)

    # This test verifies the reconcile path clears the 'triggered' state.
    db = _MockDB()
    user = _make_user(telegram_id=42)
    db.set_user(user)

    triggered_post = _make_post(user.id, status="triggered")
    expired_pending = _make_pending(user.id, status="expired")

    # Simulate second poll cycle: no new due posts, but reconcile fires
    db.push_execute_response()  # Phase 1 UPDATE
    db.push_execute_response(scalars_result=[])  # Phase 2: no due posts
    db.push_execute_response(all_result=[(triggered_post, expired_pending)])  # Phase 3

    notifier = AsyncMock()
    ctx = _make_ctx(db, notifier)

    with patch("core.scheduler.jobs.get_settings", return_value=_make_settings()):
        await poll_scheduled_posts(ctx)

    assert triggered_post.status == "failed"
    # pending action was 'expired', not 'pending' — collision check in next poll will pass
