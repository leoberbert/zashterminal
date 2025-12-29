# zashterminal/utils/exceptions.py

from enum import Enum
from typing import Any, Dict, Optional

from .translation_utils import _


class ErrorSeverity(Enum):
    """Error severity levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ErrorCategory(Enum):
    """Error categories for classification."""

    TERMINAL = "terminal"
    SESSION = "session"
    SSH = "ssh"
    UI = "ui"
    STORAGE = "storage"
    CONFIG = "config"
    PERMISSION = "permission"
    NETWORK = "network"
    SYSTEM = "system"
    VALIDATION = "validation"


class ZashterminalError(Exception):
    """Base exception class for all Zashterminal errors."""

    def __init__(
        self,
        message: str,
        category: ErrorCategory = ErrorCategory.SYSTEM,
        severity: ErrorSeverity = ErrorSeverity.MEDIUM,
        details: Optional[Dict[str, Any]] = None,
        user_message: Optional[str] = None,
    ):
        super().__init__(message)
        self.message = message
        self.category = category
        self.severity = severity
        self.details = details or {}
        self.user_message = user_message or self._generate_user_message()

    def _generate_user_message(self) -> str:
        """Generate a user-friendly message based on the category."""
        category_messages = {
            ErrorCategory.TERMINAL: _("A terminal error occurred"),
            ErrorCategory.SESSION: _("A session error occurred"),
            ErrorCategory.SSH: _("An SSH connection error occurred"),
            ErrorCategory.UI: _("A user interface error occurred"),
            ErrorCategory.STORAGE: _("A data storage error occurred"),
            ErrorCategory.CONFIG: _("A configuration error occurred"),
            ErrorCategory.PERMISSION: _("A permission error occurred"),
            ErrorCategory.NETWORK: _("A network error occurred"),
            ErrorCategory.SYSTEM: _("A system error occurred"),
            ErrorCategory.VALIDATION: _("A validation error occurred"),
        }
        return category_messages.get(self.category, _("An unexpected error occurred"))

    def __str__(self) -> str:
        return f"[{self.category.value.upper()}:{self.severity.value.upper()}] {self.message}"


class TerminalError(ZashterminalError):
    """Base class for terminal-related errors."""

    def __init__(self, message: str, **kwargs):
        kwargs.setdefault("category", ErrorCategory.TERMINAL)
        super().__init__(message, **kwargs)


class TerminalCreationError(TerminalError):
    """Raised when terminal creation fails."""

    def __init__(self, reason: str, terminal_type: str = "unknown", **kwargs):
        message = _("Failed to create {} terminal: {}").format(terminal_type, reason)
        kwargs.setdefault("severity", ErrorSeverity.HIGH)
        kwargs.setdefault("details", {"terminal_type": terminal_type, "reason": reason})
        kwargs.setdefault(
            "user_message", _("Could not create terminal. {}").format(reason)
        )
        super().__init__(message, **kwargs)


class SSHError(ZashterminalError):
    """Base class for SSH-related errors."""

    def __init__(self, message: str, **kwargs):
        kwargs.setdefault("category", ErrorCategory.SSH)
        super().__init__(message, **kwargs)


class SSHConnectionError(SSHError):
    """Raised when SSH connection fails."""

    def __init__(self, host: str, reason: str, **kwargs):
        message = _("SSH connection to '{}' failed: {}").format(host, reason)
        kwargs.setdefault("severity", ErrorSeverity.HIGH)
        kwargs.setdefault("details", {"host": host, "reason": reason})
        kwargs.setdefault(
            "user_message", _("Could not connect to {}. {}").format(host, reason)
        )
        super().__init__(message, **kwargs)


class SSHKeyError(SSHError):
    """Raised when SSH key is invalid or not found."""

    def __init__(self, key_path: str, reason: str, **kwargs):
        message = _("SSH key error for '{}': {}").format(key_path, reason)
        kwargs.setdefault("severity", ErrorSeverity.MEDIUM)
        kwargs.setdefault("details", {"key_path": key_path, "reason": reason})
        kwargs.setdefault("user_message", _("SSH key problem: {}").format(reason))
        super().__init__(message, **kwargs)


class SessionError(ZashterminalError):
    """Base class for session-related errors."""

    def __init__(self, message: str, **kwargs):
        kwargs.setdefault("category", ErrorCategory.SESSION)
        super().__init__(message, **kwargs)


class SessionValidationError(SessionError):
    """Raised when session validation fails."""

    def __init__(self, session_name: str, validation_errors: list, **kwargs):
        message = _("Session '{}' validation failed: {}").format(
            session_name, ", ".join(validation_errors)
        )
        kwargs.setdefault("severity", ErrorSeverity.MEDIUM)
        kwargs.setdefault(
            "details", {"session_name": session_name, "errors": validation_errors}
        )
        kwargs.setdefault(
            "user_message",
            _("Session configuration is invalid: {}").format(validation_errors[0]),
        )
        super().__init__(message, **kwargs)


class StorageError(ZashterminalError):
    """Base class for storage-related errors."""

    def __init__(self, message: str, **kwargs):
        kwargs.setdefault("category", ErrorCategory.STORAGE)
        super().__init__(message, **kwargs)


class StorageReadError(StorageError):
    """Raised when reading from storage fails."""

    def __init__(self, file_path: str, reason: str, **kwargs):
        message = _("Failed to read from '{}': {}").format(file_path, reason)
        kwargs.setdefault("severity", ErrorSeverity.HIGH)
        kwargs.setdefault("details", {"file_path": file_path, "reason": reason})
        kwargs.setdefault("user_message", _("Could not load saved data"))
        super().__init__(message, **kwargs)


class StorageWriteError(StorageError):
    """Raised when writing to storage fails."""

    def __init__(self, file_path: str, reason: str, **kwargs):
        message = _("Failed to write to '{}': {}").format(file_path, reason)
        kwargs.setdefault("severity", ErrorSeverity.HIGH)
        kwargs.setdefault("details", {"file_path": file_path, "reason": reason})
        kwargs.setdefault("user_message", _("Could not save data"))
        super().__init__(message, **kwargs)


class StorageCorruptedError(StorageError):
    """Raised when storage data is corrupted."""

    def __init__(self, file_path: str, details: str = "", **kwargs):
        message = _("Storage file '{}' is corrupted").format(file_path)
        if details:
            message += _(": {}").format(details)
        kwargs.setdefault("severity", ErrorSeverity.HIGH)
        kwargs.setdefault(
            "details", {"file_path": file_path, "corruption_details": details}
        )
        kwargs.setdefault("user_message", _("Saved data appears to be corrupted"))
        super().__init__(message, **kwargs)


class ConfigError(ZashterminalError):
    """Base class for configuration-related errors."""

    def __init__(self, message: str, **kwargs):
        kwargs.setdefault("category", ErrorCategory.CONFIG)
        super().__init__(message, **kwargs)


class ConfigValidationError(ConfigError):
    """Raised when configuration validation fails."""

    def __init__(self, config_key: str, value: Any, reason: str, **kwargs):
        message = _("Invalid configuration for '{}' (value: {}): {}").format(
            config_key, value, reason
        )
        kwargs.setdefault("severity", ErrorSeverity.MEDIUM)
        kwargs.setdefault(
            "details", {"config_key": config_key, "value": value, "reason": reason}
        )
        kwargs.setdefault("user_message", _("Configuration error: {}").format(reason))
        super().__init__(message, **kwargs)


class UIError(ZashterminalError):
    """Base class for UI-related errors."""

    def __init__(self, component: str, message: str = None, **kwargs):
        error_message = (
            _("UI error in {}: {}").format(component, message)
            if message
            else _("UI error in component: {}").format(component)
        )
        kwargs.setdefault("category", ErrorCategory.UI)
        kwargs.setdefault("details", {}).update({"component": component})
        super().__init__(error_message, **kwargs)


class ValidationError(ZashterminalError):
    """Base class for validation errors."""

    def __init__(
        self,
        message: str,
        category=None,
        severity=None,
        field: str = None,
        value: Any = None,
        reason: str = None,
        **kwargs,
    ):
        if category is not None:
            kwargs.setdefault("category", category)
        else:
            kwargs.setdefault("category", ErrorCategory.VALIDATION)
        if severity is not None:
            kwargs.setdefault("severity", severity)
        else:
            kwargs.setdefault("severity", ErrorSeverity.MEDIUM)
        if field and reason:
            error_message = _("Validation failed for '{}': {}").format(field, reason)
            if message and message != reason:
                error_message = f"{message} - {error_message}"
        elif field:
            error_message = _("Validation failed for '{}': {}").format(field, message)
        else:
            error_message = message
        kwargs.setdefault("details", {}).update({
            "field": field,
            "value": value,
            "reason": reason,
        })
        super().__init__(error_message, **kwargs)


class HostnameValidationError(ValidationError):
    """Raised when hostname validation fails."""

    def __init__(self, hostname: str, reason: str, **kwargs):
        message = _("Invalid hostname '{}': {}").format(hostname, reason)
        kwargs.setdefault("severity", ErrorSeverity.MEDIUM)
        kwargs.setdefault("details", {"hostname": hostname, "reason": reason})
        kwargs.setdefault("user_message", _("Invalid hostname: {}").format(reason))
        super().__init__(message, **kwargs)


class PathValidationError(ValidationError):
    """Raised when path validation fails."""

    def __init__(self, path: str, reason: str, **kwargs):
        message = _("Invalid path '{}': {}").format(path, reason)
        kwargs.setdefault("severity", ErrorSeverity.MEDIUM)
        kwargs.setdefault("details", {"path": path, "reason": reason})
        kwargs.setdefault("user_message", _("Invalid path: {}").format(reason))
        super().__init__(message, **kwargs)


class ZashterminalPermissionError(ZashterminalError):
    """Base class for permission-related errors."""

    def __init__(self, message: str, **kwargs):
        kwargs.setdefault("category", ErrorCategory.PERMISSION)
        super().__init__(message, **kwargs)


# Alias for backwards compatibility - use ZashterminalPermissionError in new code
PermissionError = ZashterminalPermissionError


class FilePermissionError(ZashterminalPermissionError):
    """Raised when file permission is denied."""

    def __init__(self, file_path: str, operation: str, **kwargs):
        message = _("Permission denied for {} operation on '{}'").format(
            operation, file_path
        )
        kwargs.setdefault("severity", ErrorSeverity.HIGH)
        kwargs.setdefault("details", {"file_path": file_path, "operation": operation})
        kwargs.setdefault(
            "user_message", _("Permission denied accessing {}").format(file_path)
        )
        super().__init__(message, **kwargs)


class DirectoryPermissionError(ZashterminalPermissionError):
    """Raised when directory permission is denied."""

    def __init__(self, directory_path: str, operation: str, **kwargs):
        message = _("Permission denied for {} operation on directory '{}'").format(
            operation, directory_path
        )
        kwargs.setdefault("severity", ErrorSeverity.HIGH)
        kwargs.setdefault(
            "details", {"directory_path": directory_path, "operation": operation}
        )
        kwargs.setdefault(
            "user_message",
            _("Permission denied accessing directory {}").format(directory_path),
        )
        super().__init__(message, **kwargs)


def handle_exception(
    exception: Exception,
    context: str = "",
    logger_name: str = None,
    reraise: bool = False,
) -> Optional[ZashterminalError]:
    """Handle an exception by logging it and optionally converting to ZashterminalError."""
    from .logger import log_error_with_context

    log_error_with_context(exception, context, logger_name)
    converted_exception = (
        exception
        if isinstance(exception, ZashterminalError)
        else ZashterminalError(
            message=str(exception),
            details={"original_type": type(exception).__name__, "context": context},
        )
    )
    if reraise:
        raise converted_exception
    return converted_exception
