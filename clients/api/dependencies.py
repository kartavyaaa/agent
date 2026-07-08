from __future__ import annotations

from fastapi import Request

from core.engine import CoreEngine


def get_engine(request: Request) -> CoreEngine:
    return request.app.state.engine  # type: ignore[no-any-return]
