from __future__ import annotations

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

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
        status, code, msg = 422, "planner_too_complex", "Request is too complex to complete."
    elif isinstance(exc, PlannerStuckLoopError):
        status, code, msg = 422, "planner_stuck", "Request caused a planning loop."
    elif isinstance(exc, LLMRateLimitError):
        status, code, msg = (
            429,
            "llm_rate_limit",
            "AI provider rate limit reached. Try again later.",
        )
    elif isinstance(exc, LLMTimeoutError):
        status, code, msg = 504, "llm_timeout", "AI provider timed out. Try again later."
    elif isinstance(exc, PluginNotImplementedError):
        status, code, msg = (
            501,
            "plugin_not_implemented",
            "Requested capability is not yet implemented.",
        )
    elif isinstance(exc, PluginValidationError):
        status, code, msg = (
            422,
            "plugin_validation_error",
            "Plugin input or output failed validation.",
        )
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
            content=ErrorResponse(error="access_denied", detail="Access denied.").model_dump(),
        )
    elif isinstance(exc, FileNotFoundInSandboxError):
        status, code, msg = 422, "file_not_found", "Requested file was not found."
    elif isinstance(exc, PathIsDirectoryError):
        status, code, msg = 422, "path_is_directory", "Requested path is a directory, not a file."
    elif isinstance(exc, FileTooLargeError):
        status, code, msg = 422, "file_too_large", "Requested file exceeds the size limit."
    elif isinstance(exc, FileDecodeError):
        status, code, msg = 422, "file_decode_error", "File content could not be decoded."
    elif isinstance(exc, FileReaderError):
        status, code, msg = 422, "file_reader_error", "File read failed."
    elif isinstance(exc, IntegrationRateLimitError):
        status, code, msg = (
            429,
            "integration_rate_limit",
            "External service rate limit reached. Try again later.",
        )
    elif isinstance(exc, IntegrationError):
        status, code, msg = 502, "integration_error", "External service error."
    elif isinstance(exc, ConfigurationError):
        status, code, msg = 503, "configuration_error", "Service is misconfigured. Contact support."
    else:
        status, code, msg = 500, "internal_error", "An unexpected error occurred."

    level = log.warning if status < 500 else log.error
    level("error_handler", exc_type=type(exc).__name__, status=status, code=code)

    return JSONResponse(
        status_code=status,
        content=ErrorResponse(error=code, detail=msg).model_dump(),
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(PlatformError, platform_error_handler)  # type: ignore[arg-type]
