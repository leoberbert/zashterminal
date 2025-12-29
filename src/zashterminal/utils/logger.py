# zashterminal/utils/logger.py
"""
Logging utilities for Zashterminal.

Performance-optimized: Heavy imports and directory creation are deferred
until actually needed to minimize startup time.
"""

import logging
import os
import sys
import threading
from typing import TYPE_CHECKING, Dict, Optional

if TYPE_CHECKING:
    from pathlib import Path

# Lazy-loaded module references
_logging_handlers = None
_pathlib = None
_datetime = None


def _get_logging_handlers():
    """Lazy import of logging.handlers."""
    global _logging_handlers
    if _logging_handlers is None:
        import logging.handlers as lh

        _logging_handlers = lh
    return _logging_handlers


def _get_pathlib():
    """Lazy import of pathlib."""
    global _pathlib
    if _pathlib is None:
        import pathlib

        _pathlib = pathlib
    return _pathlib


def _get_datetime():
    """Lazy import of datetime."""
    global _datetime
    if _datetime is None:
        from datetime import datetime as dt

        _datetime = dt
    return _datetime


class LogLevel:
    """Log levels for the application (lightweight enum alternative)."""

    DEBUG = logging.DEBUG
    INFO = logging.INFO
    WARNING = logging.WARNING
    ERROR = logging.ERROR
    CRITICAL = logging.CRITICAL


class LoggerConfig:
    """
    Configuration for the logging system.

    Directory creation is deferred until file logging is actually enabled.
    """

    def __init__(self):
        self._log_dir = None  # Lazy initialization
        self.max_file_size = 10 * 1024 * 1024  # 10MB
        self.backup_count = 5
        self.log_to_file = False
        self.console_level = LogLevel.ERROR
        self.file_level = LogLevel.DEBUG
        self.error_file_level = LogLevel.ERROR

    @property
    def log_dir(self) -> "Path":
        """Get log directory, creating it if necessary."""
        if self._log_dir is None:
            Path = _get_pathlib().Path
            self._log_dir = Path.home() / ".config" / "zashterminal" / "logs"
            self._log_dir.mkdir(parents=True, exist_ok=True)
        return self._log_dir

    @property
    def main_log_file(self) -> "Path":
        """Get main log file path."""
        return self.log_dir / "zashterminal.log"

    @property
    def error_log_file(self) -> "Path":
        """Get error log file path."""
        return self.log_dir / "zashterminal_errors.log"


class ColoredFormatter(logging.Formatter):
    """Colored formatter for console output that preserves alignment."""

    COLORS = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[35m",
        "RESET": "\033[0m",
    }

    def format(self, record):
        """
        Applies color and padding to the log level name before formatting.
        This ensures that the ANSI escape codes do not break the alignment
        of the log columns.
        """
        levelname = record.levelname
        original_levelname = record.levelname

        # Standard padding width for log levels is 8 characters.
        padding_width = 8

        if levelname in self.COLORS:
            colored_levelname = (
                f"{self.COLORS[levelname]}{levelname}{self.COLORS['RESET']}"
            )
            # Manually pad the colored string to the correct visual width.
            padding = " " * (padding_width - len(levelname))
            record.levelname = f"{colored_levelname}{padding}"
        else:
            # Ensure non-colored levels are also padded for alignment.
            record.levelname = f"{levelname:<{padding_width}}"

        # Let the parent class handle the final formatting with our modified record.
        formatted_message = super().format(record)

        # Restore the original levelname on the record object in case other
        # handlers in the chain need it in its original state.
        record.levelname = original_levelname

        return formatted_message


class ThreadSafeLogger:
    """Thread-safe logger implementation."""

    def __init__(self, name: str, config: LoggerConfig):
        self.name = name
        self.config = config
        self._logger = logging.getLogger(name)
        self._lock = threading.Lock()
        self._setup_logger()

    def _setup_logger(self):
        """Set up the logger with handlers and formatters based on current config."""
        with self._lock:
            if self._logger.hasHandlers():
                self._logger.handlers.clear()

            self._logger.propagate = False
            self._logger.setLevel(logging.DEBUG)

            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(self.config.console_level)
            # The format string now uses the pre-formatted `levelname`.
            console_formatter = ColoredFormatter(
                fmt="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
                datefmt="%H:%M:%S",
            )
            console_handler.setFormatter(console_formatter)
            self._logger.addHandler(console_handler)

            if self.config.log_to_file:
                handlers_module = _get_logging_handlers()
                file_formatter = logging.Formatter(
                    fmt="%(asctime)s | %(name)s | %(levelname)-8s | %(funcName)s:%(lineno)d | %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )

                main_file_handler = handlers_module.RotatingFileHandler(
                    self.config.main_log_file,
                    maxBytes=self.config.max_file_size,
                    backupCount=self.config.backup_count,
                    encoding="utf-8",
                )
                main_file_handler.setLevel(self.config.file_level)
                main_file_handler.setFormatter(file_formatter)
                self._logger.addHandler(main_file_handler)

                error_file_handler = handlers_module.RotatingFileHandler(
                    self.config.error_log_file,
                    maxBytes=self.config.max_file_size,
                    backupCount=self.config.backup_count,
                    encoding="utf-8",
                )
                error_file_handler.setLevel(self.config.error_file_level)
                error_file_handler.setFormatter(file_formatter)
                self._logger.addHandler(error_file_handler)

    def debug(self, message: str, **kwargs):
        self._logger.debug(message, **kwargs)

    def info(self, message: str, **kwargs):
        self._logger.info(message, **kwargs)

    def warning(self, message: str, **kwargs):
        self._logger.warning(message, **kwargs)

    def error(self, message: str, exc_info: bool = False, **kwargs):
        self._logger.error(message, exc_info=exc_info, **kwargs)

    def critical(self, message: str, exc_info: bool = True, **kwargs):
        self._logger.critical(message, exc_info=exc_info, **kwargs)

    def exception(self, message: str, **kwargs):
        self._logger.exception(message, **kwargs)


class LoggerManager:
    """Centralized logger manager."""

    _instance: Optional["LoggerManager"] = None
    _lock = threading.RLock()

    def __new__(cls) -> "LoggerManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return
        self._initialized = True
        self.config = LoggerConfig()
        self._loggers: Dict[str, ThreadSafeLogger] = {}
        self._setup_root_logger()

    def _setup_root_logger(self):
        logging.getLogger("gi").setLevel(logging.WARNING)
        logging.getLogger("Vte").setLevel(logging.WARNING)
        logging.getLogger("Gtk").setLevel(logging.WARNING)

    def get_logger(self, name: str) -> ThreadSafeLogger:
        if name not in self._loggers:
            with self._lock:
                if name not in self._loggers:
                    self._loggers[name] = ThreadSafeLogger(name, self.config)
        return self._loggers[name]

    def reconfigure_all_loggers(self):
        """Re-applies configuration to all existing logger instances."""
        with self._lock:
            for logger in self._loggers.values():
                logger._setup_logger()

    def set_console_level(self, level: LogLevel):
        with self._lock:
            self.config.console_level = level
            self.reconfigure_all_loggers()

    def set_log_to_file_enabled(self, enabled: bool):
        with self._lock:
            if self.config.log_to_file != enabled:
                self.config.log_to_file = enabled
                self.reconfigure_all_loggers()

    def enable_debug_mode(self):
        self.set_console_level(LogLevel.DEBUG)
        os.environ["ZASHTERMINAL_DEBUG"] = "1"

    def disable_debug_mode(self):
        self.set_console_level(LogLevel.INFO)
        os.environ.pop("ZASHTERMINAL_DEBUG", None)

    def cleanup_old_logs(self, days_to_keep: int = 30):
        try:
            datetime_cls = _get_datetime()
            cutoff_time = datetime_cls.now().timestamp() - (days_to_keep * 24 * 60 * 60)
            for log_file in self.config.log_dir.glob("*.log*"):
                if log_file.stat().st_mtime < cutoff_time:
                    log_file.unlink()
        except Exception as e:
            print(f"Error cleaning up old logs: {e}")


_logger_manager = LoggerManager()


def get_logger(name: str = None) -> ThreadSafeLogger:
    """Get a logger instance."""
    if name is None:
        import inspect

        frame = inspect.currentframe()
        try:
            name = frame.f_back.f_globals.get("__name__", "unknown")
        finally:
            del frame
    return _logger_manager.get_logger(name)


def set_console_log_level(level_str: str):
    """Set console logging level globally from a string."""
    level_map = {
        "DEBUG": LogLevel.DEBUG,
        "INFO": LogLevel.INFO,
        "WARNING": LogLevel.WARNING,
        "ERROR": LogLevel.ERROR,
        "CRITICAL": LogLevel.CRITICAL,
    }
    level = level_map.get(level_str.upper())
    if level is not None:
        _logger_manager.set_console_level(level)
    else:
        get_logger().error(f"Invalid log level string: {level_str}")


def set_log_to_file_enabled(enabled: bool):
    """Enable or disable logging to files globally."""
    _logger_manager.set_log_to_file_enabled(enabled)


def enable_debug_mode():
    """Enable debug mode for all loggers."""
    _logger_manager.enable_debug_mode()


def disable_debug_mode():
    """Disable debug mode for all loggers."""
    _logger_manager.disable_debug_mode()


def cleanup_old_logs(days_to_keep: int = 30):
    """Clean up old log files."""
    _logger_manager.cleanup_old_logs(days_to_keep)


def log_app_start():
    """Log application startup."""
    logger = get_logger("zashterminal.startup")
    logger.info("Zashterminal starting up")
    cleanup_old_logs()


def log_app_shutdown():
    """Log application shutdown."""
    logger = get_logger("zashterminal.shutdown")
    logger.info("Zashterminal shutting down")


def log_terminal_event(event_type: str, terminal_name: str, details: str = ""):
    """Log terminal-related events."""
    logger = get_logger("zashterminal.terminal")
    message = f"Terminal '{terminal_name}' {event_type}"
    if details:
        message += f": {details}"
    logger.info(message)


def log_session_event(event_type: str, item_name: str, details: str = ""):
    """Log session or folder-related events."""
    logger = get_logger("zashterminal.sessions")
    item_type = "Folder" if "folder" in event_type else "Session"
    message = f"{item_type} '{item_name}' {event_type.replace('folder_', '')}"
    if details:
        message += f": {details}"
    logger.info(message)


def log_error_with_context(error: Exception, context: str, logger_name: str = None):
    """Log an error with context information."""
    logger = get_logger(logger_name)
    logger.error(f"Error in {context}: {str(error)}", exc_info=True)
