# zashterminal/utils/security.py

import ipaddress
import os
import re
import socket
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .exceptions import (
    DirectoryPermissionError,
    FilePermissionError,
    HostnameValidationError,
    PathValidationError,
    SSHKeyError,
)
from .logger import get_logger
from .translation_utils import _

# Pre-compiled patterns for hostname validation/sanitization
_HOSTNAME_SANITIZE_PATTERN = re.compile(r"[^a-z0-9.-]")
_HOSTNAME_VALID_PATTERN = re.compile(r"^[a-zA-Z0-9.-]+$")


class SecurityConfig:
    """Security configuration and limits."""

    MAX_HOSTNAME_LENGTH = 253
    MAX_USERNAME_LENGTH = 32
    MAX_SSH_KEY_SIZE = 16384
    MAX_PATH_LENGTH = 4096
    FORBIDDEN_PATH_CHARS = ["<", ">", ":", '"', "|", "?", "*", "\0"]
    FORBIDDEN_PATH_SEQUENCES = ["../", "..\\"]
    MAX_SESSION_NAME_LENGTH = 128
    SECURE_FILE_PERMISSIONS = 0o600
    SECURE_DIR_PERMISSIONS = 0o700


class InputSanitizer:
    """Input sanitization utilities."""

    @staticmethod
    def sanitize_filename(filename: str, replacement: str = "_") -> str:
        if not filename:
            return _("unnamed")
        forbidden_chars = '<>:"/\\|?*\0'
        sanitized = filename
        for char in forbidden_chars:
            sanitized = sanitized.replace(char, replacement)
        sanitized = "".join(char for char in sanitized if ord(char) >= 32)
        sanitized = sanitized.strip(" .")
        if not sanitized:
            sanitized = _("unnamed")
        if len(sanitized) > SecurityConfig.MAX_SESSION_NAME_LENGTH:
            sanitized = sanitized[: SecurityConfig.MAX_SESSION_NAME_LENGTH]
        return sanitized

    @staticmethod
    def sanitize_hostname(hostname: str) -> str:
        if not hostname:
            return ""
        sanitized = hostname.strip().lower()
        sanitized = _HOSTNAME_SANITIZE_PATTERN.sub("", sanitized)
        if len(sanitized) > SecurityConfig.MAX_HOSTNAME_LENGTH:
            sanitized = sanitized[: SecurityConfig.MAX_HOSTNAME_LENGTH]
        return sanitized


class HostnameValidator:
    """Hostname validation utilities."""

    @staticmethod
    def is_valid_hostname(hostname: str) -> bool:
        if not hostname or len(hostname) > SecurityConfig.MAX_HOSTNAME_LENGTH:
            return False
        if not _HOSTNAME_VALID_PATTERN.match(hostname):
            return False
        labels = hostname.split(".")
        for label in labels:
            if (
                not label
                or len(label) > 63
                or label.startswith("-")
                or label.endswith("-")
            ):
                return False
        return True

    @staticmethod
    def is_private_ip(ip_str: str) -> bool:
        try:
            return ipaddress.ip_address(ip_str).is_private
        except ValueError:
            return False

    @staticmethod
    def resolve_hostname(hostname: str, timeout: float = 5.0) -> Optional[str]:
        """Resolve a hostname to an IP address.

        Uses a separate socket with timeout instead of socket.setdefaulttimeout()
        to avoid affecting global socket behavior for other parts of the application.

        Args:
            hostname: The hostname to resolve
            timeout: Resolution timeout in seconds

        Returns:
            The resolved IP address or None if resolution fails
        """
        logger = get_logger("zashterminal.security")
        try:
            # Use getaddrinfo with a timeout via socket options instead of
            # setdefaulttimeout() which affects ALL sockets globally
            import signal

            def timeout_handler(signum, frame):
                raise socket.timeout(f"Hostname resolution timed out for {hostname}")

            # Set up signal-based timeout (works on Unix)
            old_handler = signal.signal(signal.SIGALRM, timeout_handler)
            signal.setitimer(signal.ITIMER_REAL, timeout)

            try:
                result = socket.gethostbyname(hostname)
                return result
            finally:
                # Cancel the timer and restore old handler
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, old_handler)

        except (socket.gaierror, socket.timeout) as e:
            logger.debug(f"Hostname resolution failed for {hostname}: {e}")
            return None
        except Exception as e:
            logger.debug(f"Unexpected error resolving hostname {hostname}: {e}")
            return None


class SSHKeyValidator:
    """SSH key validation utilities."""

    @staticmethod
    def validate_ssh_key_path(key_path: str) -> Tuple[bool, Optional[str]]:
        if not key_path:
            return False, _("Key path is empty")
        try:
            path = Path(key_path)
            if not path.exists():
                return False, _("Key file does not exist: {}").format(key_path)
            if not path.is_file():
                return False, _("Key path is not a file: {}").format(key_path)
            file_size = path.stat().st_size
            if file_size > SecurityConfig.MAX_SSH_KEY_SIZE:
                return False, _("Key file too large: {} bytes").format(file_size)
            if file_size == 0:
                return False, _("Key file is empty")
            if path.stat().st_mode & 0o077:
                return False, _("Key file has insecure permissions (should be 600)")
            if not os.access(path, os.R_OK):
                return False, _("Key file is not readable")
            return True, None
        except OSError as e:
            return False, _("Error accessing key file: {}").format(e)

    @staticmethod
    def read_and_validate_ssh_key(
        key_path: str,
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        path_valid, path_error = SSHKeyValidator.validate_ssh_key_path(key_path)
        if not path_valid:
            return False, path_error, None
        return True, None, None


class PathValidator:
    """File path validation utilities."""

    @staticmethod
    def is_safe_path(path: str, base_path: Optional[str] = None) -> bool:
        if not path:
            return False
        try:
            normalized = os.path.normpath(path)
            if ".." in normalized.split(os.sep):
                return False
            for char in SecurityConfig.FORBIDDEN_PATH_CHARS:
                if char in normalized:
                    return False
            if base_path and not normalized.startswith(os.path.normpath(base_path)):
                return False
            if len(normalized) > SecurityConfig.MAX_PATH_LENGTH:
                return False
            return True
        except Exception:
            return False


class SecurityAuditor:
    """Security auditing utilities."""

    def __init__(self):
        self.logger = get_logger("zashterminal.security.audit")

    def audit_ssh_session(
        self, session_data: Dict[str, Any], resolve_dns: bool = False
    ) -> List[Dict[str, Any]]:
        """Audit SSH session configuration for security issues.

        Args:
            session_data: Session configuration dictionary
            resolve_dns: Whether to perform DNS resolution (can be slow/blocking).
                         Should only be True when explicitly testing connection.

        Returns:
            List of security findings
        """
        findings = []
        hostname = session_data.get("host", "")
        if hostname:
            if not HostnameValidator.is_valid_hostname(hostname):
                findings.append({
                    "severity": "medium",
                    "type": "invalid_hostname",
                    "message": _("Invalid hostname format: {}").format(hostname),
                    "recommendation": _("Use a valid hostname or IP address"),
                })
            elif resolve_dns:
                # Only resolve hostname when explicitly requested (e.g., test connection)
                # to avoid blocking the UI during startup
                if (
                    ip := HostnameValidator.resolve_hostname(hostname)
                ) and HostnameValidator.is_private_ip(ip):
                    findings.append({
                        "severity": "low",
                        "type": "private_ip",
                        "message": _("Connecting to private IP: {}").format(ip),
                        "recommendation": _("Ensure this is intentional"),
                    })

        auth_type = session_data.get("auth_type", "")
        auth_value = session_data.get("auth_value", "")
        if auth_type == "key" and auth_value:
            is_valid, error = SSHKeyValidator.validate_ssh_key_path(auth_value)
            if not is_valid:
                findings.append({
                    "severity": "high",
                    "type": "invalid_ssh_key",
                    "message": _("SSH key validation failed: {}").format(error),
                    "recommendation": _("Fix SSH key configuration"),
                })
        elif auth_type == "password":
            findings.append({
                "severity": "medium",
                "type": "password_auth",
                "message": _("Using password authentication"),
                "recommendation": _(
                    "Consider using SSH key authentication for better security"
                ),
            })

        username = session_data.get("user", "")
        if username == "root":
            findings.append({
                "severity": "medium",
                "type": "root_user",
                "message": _("Connecting as root user"),
                "recommendation": _("Use a regular user account when possible"),
            })

        return findings


def validate_ssh_hostname(hostname: str) -> None:
    if not hostname:
        raise HostnameValidationError("", _("Hostname cannot be empty"))
    sanitized = InputSanitizer.sanitize_hostname(hostname)
    if not HostnameValidator.is_valid_hostname(sanitized):
        raise HostnameValidationError(hostname, _("Invalid hostname format"))


def validate_ssh_key_file(key_path: str) -> None:
    is_valid, error, _ = SSHKeyValidator.read_and_validate_ssh_key(key_path)
    if not is_valid:
        raise SSHKeyError(key_path, error or _("Unknown validation error"))


def validate_file_path(file_path: str, base_path: Optional[str] = None) -> None:
    if not PathValidator.is_safe_path(file_path, base_path):
        raise PathValidationError(file_path, _("Path contains unsafe elements"))


def ensure_secure_file_permissions(file_path: str) -> None:
    try:
        Path(file_path).chmod(SecurityConfig.SECURE_FILE_PERMISSIONS)
    except OSError as e:
        raise FilePermissionError(
            file_path, _("set secure permissions"), details={"reason": str(e)}
        )


def ensure_secure_directory_permissions(dir_path: str) -> None:
    try:
        Path(dir_path).chmod(SecurityConfig.SECURE_DIR_PERMISSIONS)
    except OSError as e:
        raise DirectoryPermissionError(
            dir_path, _("set secure permissions"), details={"reason": str(e)}
        )


def create_security_auditor() -> SecurityAuditor:
    """Create a new security auditor instance."""
    return SecurityAuditor()


def validate_session_data(session_data: Dict[str, Any]) -> Tuple[bool, List[str]]:
    errors = []
    try:
        name = session_data.get("name", "")
        if not name or not name.strip():
            errors.append(_("Session name cannot be empty"))
        elif len(name) > SecurityConfig.MAX_SESSION_NAME_LENGTH:
            errors.append(
                _("Session name too long (max {} characters)").format(
                    SecurityConfig.MAX_SESSION_NAME_LENGTH
                )
            )
        host = session_data.get("host", "")
        if host:
            if not host.strip():
                errors.append(_("Hostname cannot be empty for SSH sessions"))
            elif not HostnameValidator.is_valid_hostname(host.strip()):
                errors.append(_("Invalid hostname format: {}").format(host))
        username = session_data.get("user", "")
        if host and not username:
            errors.append(_("Username is required for SSH sessions"))
        elif username and len(username) > SecurityConfig.MAX_USERNAME_LENGTH:
            errors.append(
                _("Username too long (max {} characters)").format(
                    SecurityConfig.MAX_USERNAME_LENGTH
                )
            )
        port = session_data.get("port", 22)
        if port is not None:
            try:
                if not (1 <= int(port) <= 65535):
                    errors.append(_("Port must be between 1 and 65535"))
            except (ValueError, TypeError):
                errors.append(_("Port must be a valid number"))
        auth_type = session_data.get("auth_type", "")
        auth_value = session_data.get("auth_value", "")
        if host:
            if auth_type == "key":
                # CORRECTED LOGIC: Only validate the key path if one is provided.
                # An empty path is valid for agent-based authentication.
                if auth_value:
                    is_key_valid, key_error = SSHKeyValidator.validate_ssh_key_path(
                        auth_value
                    )
                    if not is_key_valid:
                        errors.append(
                            _("SSH key validation failed: {}").format(key_error)
                        )
            elif auth_type not in ["key", "password", ""]:
                errors.append(_("Invalid authentication type: {}").format(auth_type))
        if folder_path := session_data.get("folder_path", ""):
            if not PathValidator.is_safe_path(folder_path):
                errors.append(_("Invalid or unsafe folder path"))
        return len(errors) == 0, errors
    except Exception as e:
        logger = get_logger("zashterminal.security.validation")
        logger.error(f"Session validation error: {e}")
        return False, [_("Validation error: {}").format(e)]
