from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.config import get_settings
from core.notifications.telegram_notifier import TelegramNotifier, _approval_keyboard_dict
from models.pending_action import PendingAction
from models.reminder import Reminder
from models.scheduled_post import ScheduledPost
from models.user import User

log = structlog.get_logger()


async def poll_reminders(ctx: dict[str, Any]) -> None:
    session_factory: async_sessionmaker[AsyncSession] = ctx["session_factory"]
    notifier: TelegramNotifier = ctx["notifier"]
    now = datetime.now(UTC)

    async with session_factory() as db:
        result = await db.execute(
            select(Reminder)
            .where(Reminder.remind_at <= now, Reminder.sent_at.is_(None))
            .with_for_update(skip_locked=True)
        )
        reminders = result.scalars().all()

        for reminder in reminders:
            user = await db.get(User, reminder.user_id)
            if user and user.telegram_id:
                try:
                    await notifier.send(user.telegram_id, reminder.message)
                except Exception:
                    log.warning("notify.failed", reminder_id=str(reminder.id))
                    continue
            reminder.sent_at = now

        await db.commit()


async def poll_scheduled_posts(ctx: dict[str, Any]) -> None:
    session_factory: async_sessionmaker[AsyncSession] = ctx["session_factory"]
    notifier: TelegramNotifier = ctx["notifier"]
    settings = get_settings()
    now = datetime.now(UTC)

    async with session_factory() as db:
        # --- Phase 1: expire overdue pending actions ---
        # Without this, pending_actions past their expires_at stay stuck at 'pending' forever,
        # permanently blocking the collision check for future scheduled posts.
        await db.execute(
            update(PendingAction)
            .where(PendingAction.status == "pending", PendingAction.expires_at < now)
            .values(status="expired")
        )
        await db.commit()

        # --- Phase 2: trigger due scheduled posts ---
        # SELECT ... FOR UPDATE SKIP LOCKED + per-post commit is safe under a single worker
        # process (confirmed: docker-compose.prod.yml has one worker service, no replicas).
        # Per-post commit is necessary so each trigger is visible to subsequent collision
        # SELECTs in the same batch. FOR UPDATE prevents two concurrent arq job instances
        # from processing the same post simultaneously.
        result = await db.execute(
            select(ScheduledPost)
            .where(ScheduledPost.scheduled_for <= now, ScheduledPost.status == "scheduled")
            .order_by(ScheduledPost.scheduled_for)
            .with_for_update(skip_locked=True)
        )
        posts = result.scalars().all()

        triggered_users: set[uuid.UUID] = set()
        for post in posts:
            if post.user_id in triggered_users:
                continue  # one trigger per user per cycle

            user = await db.get(User, post.user_id)
            if not (user and user.telegram_id):
                continue

            # Collision check: skip if user already has a pending action.
            # Phase 1 above cleared expired ones, so only truly in-flight approvals remain.
            existing = await db.execute(
                select(PendingAction).where(
                    PendingAction.user_id == post.user_id,
                    PendingAction.status == "pending",
                )
            )
            if existing.scalar_one_or_none() is not None:
                log.info("poll_scheduled_posts.collision_skip", post_id=str(post.id))
                continue  # retry next minute

            action_id = uuid.uuid4()
            if post.post_type == "carousel":
                action_type = "instagram_carousel"
                action_payload: dict[str, object] = {
                    "caption": post.caption,
                    "image_urls": post.image_urls,
                }
                assert post.image_urls, "carousel post must have image_urls"
                photo_url = post.image_urls[0]
                notify_caption = f"Scheduled carousel ready:\n\n{post.caption}"
            else:
                action_type = "instagram_post"
                assert post.image_url, "single post must have image_url"
                action_payload = {"caption": post.caption, "image_url": post.image_url}
                photo_url = post.image_url
                notify_caption = f"Scheduled post ready:\n\n{post.caption}"

            pending = PendingAction(
                id=action_id,
                user_id=post.user_id,
                action_type=action_type,
                action_payload=action_payload,
                status="pending",
                preview_text=f"Post to Instagram: {post.caption[:80]}",
                expires_at=now + timedelta(minutes=settings.approval_ttl_minutes),
            )
            db.add(pending)
            post.status = "triggered"
            post.pending_action_id = action_id
            await db.commit()  # commit BEFORE send → tap always finds the row
            triggered_users.add(post.user_id)

            try:
                await notifier.send_photo(
                    user.telegram_id,
                    photo_url=photo_url,
                    caption=notify_caption,
                    reply_markup=_approval_keyboard_dict(action_id),
                )
            except Exception:
                log.warning("poll_scheduled_posts.notify_failed", post_id=str(post.id))
                await db.delete(pending)  # coroutine — MUST await
                post.status = "scheduled"
                post.pending_action_id = None
                await db.commit()
                triggered_users.discard(post.user_id)
                continue

        # --- Phase 3: reconcile triggered posts from terminal pending_action statuses ---
        # Catches BOTH worker-expired actions (set by Phase 1 this or a prior cycle)
        # AND callback-driven terminals (confirmed/cancelled written by handle_callback
        # between polls). handle_callback writes: "confirmed" on success, "cancelled" on
        # cancel, "expired" on late tap, "failed" on error.
        _RECONCILE_MAP: dict[str, str] = {
            "confirmed": "posted",
            "cancelled": "cancelled",
            "expired": "failed",
            "failed": "failed",
        }
        reconcile_result = await db.execute(
            select(ScheduledPost, PendingAction)
            .join(PendingAction, ScheduledPost.pending_action_id == PendingAction.id)
            .where(
                ScheduledPost.status == "triggered",
                PendingAction.status.in_(list(_RECONCILE_MAP.keys())),
            )
        )
        for sp, pa in reconcile_result.all():
            sp.status = _RECONCILE_MAP[pa.status]
        await db.commit()
