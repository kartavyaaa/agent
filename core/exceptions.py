from __future__ import annotations


class PlatformError(Exception):
    """Base exception for all platform errors."""


class EngineError(PlatformError):
    """Raised by the Core Engine for request-handling failures."""


class PlannerError(EngineError):
    """Raised by the ReAct planner."""


class PlannerMaxIterationsError(PlannerError):
    """Planner exceeded the configured max_iterations limit."""


class PlannerStuckLoopError(PlannerError):
    """Planner detected the same tool call with identical args repeated N times."""


class MemoryError(PlatformError):
    """Raised by the memory subsystem."""


class PluginError(PlatformError):
    """Raised by a plugin or the tool registry."""


class PluginNotFoundError(PluginError):
    """No plugin registered under the requested name."""


class PluginNotImplementedError(PluginError):
    """Plugin is a stub; execute() is not yet implemented."""


class PluginValidationError(PluginError):
    """Plugin input or output failed schema validation."""


class LLMError(PlatformError):
    """Raised by the LLM provider adapter."""


class LLMRateLimitError(LLMError):
    """Provider returned a rate-limit response."""


class LLMTimeoutError(LLMError):
    """Provider call exceeded the configured timeout."""


class IntegrationError(PlatformError):
    """Raised by an integration client (Serper, GCal, etc.)."""


class IntegrationRateLimitError(IntegrationError):
    """Integration returned HTTP 429 — do not retry."""


class ConfigurationError(PlatformError):
    """Raised when required configuration is missing or invalid."""


class FileReaderError(IntegrationError):
    """Base for all file-reader failures."""


class SandboxViolationError(FileReaderError):
    """Requested path resolves outside the configured sandbox root."""


class FileNotFoundInSandboxError(FileReaderError):
    """File does not exist within the sandbox."""


class PathIsDirectoryError(FileReaderError):
    """Requested path is a directory, not a file."""


class FileTooLargeError(FileReaderError):
    """File exceeds the configured size limit."""


class FileDecodeError(FileReaderError):
    """File content is not valid UTF-8."""
