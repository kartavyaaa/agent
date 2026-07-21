from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.engine import CoreEngine


def get_engine(request: Request) -> CoreEngine:
    return request.app.state.engine  # type: ignore[no-any-return]


def get_session_factory(
    engine: Annotated[CoreEngine, Depends(get_engine)],
) -> async_sessionmaker[AsyncSession]:
    return engine.session_factory
