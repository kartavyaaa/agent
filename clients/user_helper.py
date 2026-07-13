from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from models.user import User


async def get_or_create_user(db: AsyncSession, user_id: uuid.UUID) -> User:
    """Ensure a User row exists for user_id and return it.

    Race-safe: uses INSERT … ON CONFLICT DO NOTHING so concurrent calls with the
    same new user_id both succeed without raising a UniqueViolation. Does not commit
    — the caller owns the session lifecycle.
    """
    log = structlog.get_logger()

    result = await db.execute(select(User).where(User.id == user_id))
    existing = result.scalar_one_or_none()
    if existing is not None:
        log.debug("user.get_or_create", user_id=str(user_id), created=False)
        return existing

    stmt = insert(User).values(id=user_id).on_conflict_do_nothing(index_elements=["id"])
    await db.execute(stmt)

    result2 = await db.execute(select(User).where(User.id == user_id))
    user = result2.scalar_one()
    log.debug("user.get_or_create", user_id=str(user_id), created=True)
    return user
