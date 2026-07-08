from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.notifications.telegram_notifier import TelegramNotifier
from models.reminder import Reminder
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
