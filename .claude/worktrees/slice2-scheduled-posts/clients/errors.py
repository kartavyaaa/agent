from __future__ import annotations

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

_GENERIC_FALLBACK = "Something went wrong on my end. Please try again."


def user_message(exc: PlatformError) -> str:
    """Return a short, user-facing string for any PlatformError.

    Order matters: check subclasses before their parent classes
    (FileReaderError/IntegrationError families — reordering silently breaks these).
    """
    if isinstance(exc, PlannerMaxIterationsError):
        return "Request is too complex to complete."
    elif isinstance(exc, PlannerStuckLoopError):
        return "Request caused a planning loop."
    elif isinstance(exc, LLMRateLimitError):
        return "AI provider rate limit reached. Try again later."
    elif isinstance(exc, LLMTimeoutError):
        return "AI provider timed out. Try again later."
    elif isinstance(exc, PluginNotImplementedError):
        return "Requested capability is not yet implemented."
    elif isinstance(exc, PluginValidationError):
        return "Plugin input or output failed validation."
    elif isinstance(exc, SandboxViolationError):
        return "Access denied."
    elif isinstance(exc, FileNotFoundInSandboxError):
        return "Requested file was not found."
    elif isinstance(exc, PathIsDirectoryError):
        return "Requested path is a directory, not a file."
    elif isinstance(exc, FileTooLargeError):
        return "Requested file exceeds the size limit."
    elif isinstance(exc, FileDecodeError):
        return "File content could not be decoded."
    elif isinstance(exc, FileReaderError):
        return "File read failed."
    elif isinstance(exc, IntegrationRateLimitError):
        return "External service rate limit reached. Try again later."
    elif isinstance(exc, IntegrationError):
        return "External service error."
    elif isinstance(exc, ConfigurationError):
        return "Service is misconfigured. Contact support."
    return "An unexpected error occurred."
