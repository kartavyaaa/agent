from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# Engine and session factory are initialised from settings at app startup.
# Do not import settings here to keep models free of application dependencies.
engine = None  # type: ignore[assignment]  # set by app lifespan
async_session_factory: async_sessionmaker[AsyncSession] | None = None
