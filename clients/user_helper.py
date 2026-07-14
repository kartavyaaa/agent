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


async def get_or_create_user_by_telegram_id(db: AsyncSession, telegram_id: int) -> uuid.UUID:
    """Ensure a User row exists for telegram_id and return its UUID.

    Race-safe: uses INSERT … ON CONFLICT DO NOTHING. The re-SELECT queries by
    telegram_id (not the locally-generated new_id) so that if this request lost
    the insert race, we still find the row committed by the winner.
    Does not commit — caller owns the session lifecycle.
    """
    log = structlog.get_logger()

    result = await db.execute(select(User).where(User.telegram_id == telegram_id))
    existing = result.scalar_one_or_none()
    if existing is not None:
        log.debug("user.get_or_create_by_telegram_id", telegram_id=telegram_id, created=False)
        return existing.id

    new_id = uuid.uuid4()
    stmt = (
        insert(User)
        .values(id=new_id, telegram_id=telegram_id)
        .on_conflict_do_nothing(index_elements=["telegram_id"])
    )
    await db.execute(stmt)

    result2 = await db.execute(select(User).where(User.telegram_id == telegram_id))
    user = result2.scalar_one()
    log.debug("user.get_or_create_by_telegram_id", telegram_id=telegram_id, created=True)
    return user.id
