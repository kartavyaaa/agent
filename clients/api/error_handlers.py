from __future__ import annotations

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from clients.errors import user_message
from core.exceptions import (
    ConfigurationError,
    FileDecodeError,
    FileNotFoundInSandboxError,
    FileReaderError,
    FileTooLargeError,
    IntegrationError,
    IntegrationRateLimitError,
    LLMRateLimitError,
    LLMTimeoutError,
    PathIsDirectoryError,
    PlannerMaxIterationsError,
    PlannerStuckLoopError,
    PlatformError,
    PluginNotImplementedError,
    PluginValidationError,
    SandboxViolationError,
)


class ErrorResponse(BaseModel):
    error: str
    detail: str


async def platform_error_handler(request: Request, exc: PlatformError) -> JSONResponse:
    log = structlog.get_logger()

    # Most-specific subclasses first to respect MRO.
    if isinstance(exc, PlannerMaxIterationsError):
        status, code = 422, "planner_too_complex"
    elif isinstance(exc, PlannerStuckLoopError):
        status, code = 422, "planner_stuck"
    elif isinstance(exc, LLMRateLimitError):
        status, code = 429, "llm_rate_limit"
    elif isinstance(exc, LLMTimeoutError):
        status, code = 504, "llm_timeout"
    elif isinstance(exc, PluginNotImplementedError):
        status, code = 501, "plugin_not_implemented"
    elif isinstance(exc, PluginValidationError):
        status, code = 422, "plugin_validation_error"
    elif isinstance(exc, SandboxViolationError):
        # Never include path in response or logs — prevents information disclosure.
        log.warning(
            "error_handler",
            exc_type=type(exc).__name__,
            status=403,
            sandbox_violation_suppressed=True,
        )
        return JSONResponse(
            status_code=403,
            content=ErrorResponse(error="access_denied", detail=user_message(exc)).model_dump(),
        )
    elif isinstance(exc, FileNotFoundInSandboxError):
        status, code = 422, "file_not_found"
    elif isinstance(exc, PathIsDirectoryError):
        status, code = 422, "path_is_directory"
    elif isinstance(exc, FileTooLargeError):
        status, code = 422, "file_too_large"
    elif isinstance(exc, FileDecodeError):
        status, code = 422, "file_decode_error"
    elif isinstance(exc, FileReaderError):
        status, code = 422, "file_reader_error"
    elif isinstance(exc, IntegrationRateLimitError):
        status, code = 429, "integration_rate_limit"
    elif isinstance(exc, IntegrationError):
        status, code = 502, "integration_error"
    elif isinstance(exc, ConfigurationError):
        status, code = 503, "configuration_error"
    else:
        status, code = 500, "internal_error"

    level = log.warning if status < 500 else log.error
    level("error_handler", exc_type=type(exc).__name__, status=status, code=code)

    return JSONResponse(
        status_code=status,
        content=ErrorResponse(error=code, detail=user_message(exc)).model_dump(),
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(PlatformError, platform_error_handler)  # type: ignore[arg-type]
