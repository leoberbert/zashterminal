# zashterminal/sessions/models.py

import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

from gi.repository import Gio, GObject

from ..utils.exceptions import SessionValidationError
from ..utils.logger import get_logger
from ..utils.platform import normalize_path
from ..utils.security import InputSanitizer
from ..utils.translation_utils import _


def _get_crypto():
    """Lazy loader for crypto module."""
    from ..utils import crypto

    return crypto


class BaseModel(GObject.GObject):
    """Base model for data items, providing common metadata and validation logic."""

    def __init__(self):
        super().__init__()
        self._created_at = time.time()
        self._modified_at = self._created_at
        self.logger = get_logger("zashterminal.sessions.basemodel")

    def _mark_modified(self):
        """Updates the modification timestamp."""
        self._modified_at = time.time()

    def get_validation_errors(self) -> List[str]:
        """
        Returns a list of validation error messages.
        Subclasses must override this method.
        """
        return []

    def validate(self) -> bool:
        """
        Performs basic validation on the item's configuration.
        Logs a warning if validation fails.
        """
        errors = self.get_validation_errors()
        if errors:
            # Use getattr to safely access 'name' which exists on subclasses
            item_name = getattr(self, "name", "Unknown Item")
            self.logger.warning(
                f"{self.__class__.__name__} validation failed for '{item_name}': {errors}"
            )
            return False
        return True


class LayoutItem(GObject.GObject):
    """Data model for a saved layout item in the tree view."""

    def __init__(self, name: str, folder_path: str = ""):
        super().__init__()
        self._name = name
        self._folder_path = str(normalize_path(folder_path)) if folder_path else ""

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, value: str):
        self._name = value

    @property
    def folder_path(self) -> str:
        return self._folder_path

    @folder_path.setter
    def folder_path(self, value: str):
        self._folder_path = str(normalize_path(value)) if value else ""

    @property
    def children(self) -> Optional[Gio.ListStore]:
        return None


class SessionItem(BaseModel):
    """Data model for a terminal session, either local or remote (SSH)."""

    def __init__(
        self,
        name: str,
        session_type: str = "ssh",
        host: str = "",
        user: str = "",
        auth_type: str = "key",
        auth_value: str = "",
        folder_path: str = "",
        port: int = 22,
        tab_color: Optional[str] = None,
        post_login_command_enabled: bool = False,
        post_login_command: str = "",
        sftp_session_enabled: bool = False,
        sftp_local_directory: str = "",
        sftp_remote_directory: str = "",
        port_forwardings: Optional[List[Dict[str, Any]]] = None,
        x11_forwarding: bool = False,
        source: str = "user",
        local_working_directory: str = "",
        local_startup_command: str = "",

        # Per-session highlighting overrides (tri-state)
        # None = Automatic (inherit global preferences)
        # True = Explicitly enabled for this session
        # False = Explicitly disabled for this session
        output_highlighting: Optional[bool] = None,
        command_specific_highlighting: Optional[bool] = None,
        cat_colorization: Optional[bool] = None,
        shell_input_highlighting: Optional[bool] = None,
    ):
        super().__init__()
        self.logger = get_logger("zashterminal.sessions.model")

        # Core properties
        self._name = InputSanitizer.sanitize_filename(name)
        self._session_type = session_type
        self._host = host
        self._user = user
        self._auth_type = auth_type
        self._auth_value = auth_value
        self._folder_path = str(normalize_path(folder_path)) if folder_path else ""
        self._port = port
        self._tab_color = tab_color
        self._post_login_command_enabled = bool(post_login_command_enabled)
        self._post_login_command = (
            post_login_command.strip() if post_login_command else ""
        )
        self._sftp_session_enabled = bool(sftp_session_enabled)
        self._sftp_local_directory = (
            str(normalize_path(sftp_local_directory))
            if sftp_local_directory
            else ""
        )
        self._sftp_remote_directory = (
            sftp_remote_directory.strip() if sftp_remote_directory else ""
        )
        self._port_forwardings: List[Dict[str, Any]] = []
        if port_forwardings:
            self.port_forwardings = port_forwardings
        self._x11_forwarding = bool(x11_forwarding)
        self._source = source or "user"
        # Local terminal specific properties
        self._local_working_directory = (
            str(normalize_path(local_working_directory))
            if local_working_directory
            else ""
        )
        self._local_startup_command = (
            local_startup_command.strip() if local_startup_command else ""
        )

        # Highlighting preferences (per-session overrides)
        self._output_highlighting: Optional[bool] = output_highlighting
        self._command_specific_highlighting: Optional[bool] = command_specific_highlighting
        self._cat_colorization: Optional[bool] = cat_colorization
        self._shell_input_highlighting: Optional[bool] = shell_input_highlighting

    @property
    def children(self) -> Optional[Gio.ListStore]:
        """Session items are leaf nodes and have no children."""
        return None

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, value: str):
        old_name = self._name
        new_name = InputSanitizer.sanitize_filename(value)
        if old_name != new_name and self.uses_password_auth():
            crypto = _get_crypto()
            password = crypto.lookup_password(old_name)
            if password:
                crypto.store_password(new_name, password)
                crypto.clear_password(old_name)
        self._name = new_name
        self._mark_modified()

    @property
    def session_type(self) -> str:
        return self._session_type

    @session_type.setter
    def session_type(self, value: str):
        if value not in ["local", "ssh"]:
            raise SessionValidationError(self.name, [f"Invalid session type: {value}"])
        self._session_type = value
        self._mark_modified()

    @property
    def host(self) -> str:
        return self._host

    @host.setter
    def host(self, value: str):
        self._host = value.strip()
        self._mark_modified()

    @property
    def user(self) -> str:
        return self._user

    @user.setter
    def user(self, value: str):
        self._user = value.strip()
        self._mark_modified()

    @property
    def auth_type(self) -> str:
        return self._auth_type

    @auth_type.setter
    def auth_type(self, value: str):
        if value not in ["key", "password", ""]:
            raise SessionValidationError(self.name, [f"Invalid auth type: {value}"])
        if self._auth_type == "password" and value != "password":
            _get_crypto().clear_password(self.name)
        self._auth_type = value
        self._mark_modified()

    @property
    def auth_value(self) -> str:
        """Returns the password from keyring or the raw key path."""
        if self.uses_password_auth():
            crypto = _get_crypto()
            if crypto.is_encryption_available():
                return crypto.lookup_password(self.name) or ""
            self.logger.warning(
                "Encryption is not available, cannot retrieve password."
            )
            return ""
        return self._auth_value

    @auth_value.setter
    def auth_value(self, value: str):
        """Sets the auth value, storing it in the keyring if it's a password."""
        if self.uses_password_auth():
            crypto = _get_crypto()
            if crypto.is_encryption_available():
                if value:
                    crypto.store_password(self.name, value)
                else:
                    crypto.clear_password(self.name)
            else:
                self.logger.error("Cannot store password: Encryption is not available.")
            self._auth_value = ""
        else:
            self._auth_value = value
        self._mark_modified()

    @property
    def folder_path(self) -> str:
        return self._folder_path

    @folder_path.setter
    def folder_path(self, value: str):
        self._folder_path = str(normalize_path(value)) if value else ""
        self._mark_modified()

    @property
    def port(self) -> int:
        return self._port

    @port.setter
    def port(self, value: int):
        try:
            port_val = int(value)
            if not (1 <= port_val <= 65535):
                raise ValueError("Port out of range")
            self._port = port_val
            self._mark_modified()
        except (ValueError, TypeError) as e:
            raise SessionValidationError(
                self.name, [_("Port must be a valid number between 1 and 65535")]
            ) from e

    @property
    def tab_color(self) -> Optional[str]:
        return self._tab_color

    @tab_color.setter
    def tab_color(self, value: Optional[str]):
        self._tab_color = value
        self._mark_modified()

    @property
    def post_login_command_enabled(self) -> bool:
        return self._post_login_command_enabled

    @post_login_command_enabled.setter
    def post_login_command_enabled(self, value: bool):
        new_value = bool(value)
        if self._post_login_command_enabled != new_value:
            self._post_login_command_enabled = new_value
            self._mark_modified()

    @property
    def post_login_command(self) -> str:
        return self._post_login_command

    @post_login_command.setter
    def post_login_command(self, value: str):
        new_value = value.strip() if value else ""
        if self._post_login_command != new_value:
            self._post_login_command = new_value
            self._mark_modified()

    @property
    def sftp_session_enabled(self) -> bool:
        return self._sftp_session_enabled

    @sftp_session_enabled.setter
    def sftp_session_enabled(self, value: bool):
        new_value = bool(value)
        if self._sftp_session_enabled != new_value:
            self._sftp_session_enabled = new_value
            self._mark_modified()

    @property
    def sftp_local_directory(self) -> str:
        return self._sftp_local_directory

    @sftp_local_directory.setter
    def sftp_local_directory(self, value: str):
        new_value = (
            str(normalize_path(value)) if value and value.strip() else ""
        )
        if self._sftp_local_directory != new_value:
            self._sftp_local_directory = new_value
            self._mark_modified()

    @property
    def sftp_remote_directory(self) -> str:
        return self._sftp_remote_directory

    @sftp_remote_directory.setter
    def sftp_remote_directory(self, value: str):
        new_value = value.strip() if value else ""
        if self._sftp_remote_directory != new_value:
            self._sftp_remote_directory = new_value
            self._mark_modified()

    @property
    def port_forwardings(self) -> List[Dict[str, Any]]:
        return deepcopy(self._port_forwardings)

    @port_forwardings.setter
    def port_forwardings(self, value: List[Dict[str, Any]]):
        normalized_list: List[Dict[str, Any]] = []
        if value:
            for item in value:
                normalized_list.append(self._normalize_port_forwarding(item))
        if self._port_forwardings != normalized_list:
            self._port_forwardings = normalized_list
            self._mark_modified()

    def _normalize_port_forwarding(self, item: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(item, dict):
            raise SessionValidationError(
                self.name, [_("Invalid port forwarding entry.")]
            )
        name = str(item.get("name", "")).strip() or _("Tunnel")
        local_host = str(item.get("local_host", "localhost")).strip() or "localhost"
        remote_host = str(item.get("remote_host", "")).strip()
        try:
            local_port = int(item.get("local_port", 0))
            remote_port = int(item.get("remote_port", 0))
        except (TypeError, ValueError):
            raise SessionValidationError(
                self.name, [_("Port forwarding entries must use numeric ports.")]
            ) from None

        return {
            "name": name,
            "local_host": local_host,
            "local_port": local_port,
            "remote_host": remote_host,
            "remote_port": remote_port,
        }

    @property
    def x11_forwarding(self) -> bool:
        return self._x11_forwarding

    @x11_forwarding.setter
    def x11_forwarding(self, value: bool):
        new_value = bool(value)
        if self._x11_forwarding != new_value:
            self._x11_forwarding = new_value
            self._mark_modified()

    @property
    def source(self) -> str:
        return self._source

    @source.setter
    def source(self, value: str):
        new_value = value or "user"
        if self._source != new_value:
            self._source = new_value
            self._mark_modified()

    @property
    def local_working_directory(self) -> str:
        return self._local_working_directory

    @local_working_directory.setter
    def local_working_directory(self, value: str):
        new_value = str(normalize_path(value)) if value and value.strip() else ""
        if self._local_working_directory != new_value:
            self._local_working_directory = new_value
            self._mark_modified()

    @property
    def local_startup_command(self) -> str:
        return self._local_startup_command

    @local_startup_command.setter
    def local_startup_command(self, value: str):
        new_value = value.strip() if value else ""
        if self._local_startup_command != new_value:
            self._local_startup_command = new_value
            self._mark_modified()

    @property
    def output_highlighting(self) -> Optional[bool]:
        """Per-session override for output highlighting.

        None means automatic (inherit global local/ssh output-highlighting preference).
        """

        return self._output_highlighting

    @output_highlighting.setter
    def output_highlighting(self, value: Optional[bool]):
        if value is not None and not isinstance(value, bool):
            raise SessionValidationError(self.name, ["Invalid output_highlighting value"])
        if self._output_highlighting != value:
            self._output_highlighting = value
            self._mark_modified()

    @property
    def command_specific_highlighting(self) -> Optional[bool]:
        """Per-session override for command-specific/context-aware output highlighting."""

        return self._command_specific_highlighting

    @command_specific_highlighting.setter
    def command_specific_highlighting(self, value: Optional[bool]):
        if value is not None and not isinstance(value, bool):
            raise SessionValidationError(
                self.name, ["Invalid command_specific_highlighting value"]
            )
        if self._command_specific_highlighting != value:
            self._command_specific_highlighting = value
            self._mark_modified()

    @property
    def cat_colorization(self) -> Optional[bool]:
        """Per-session override for Pygments-based cat output colorization."""

        return self._cat_colorization

    @cat_colorization.setter
    def cat_colorization(self, value: Optional[bool]):
        if value is not None and not isinstance(value, bool):
            raise SessionValidationError(self.name, ["Invalid cat_colorization value"])
        if self._cat_colorization != value:
            self._cat_colorization = value
            self._mark_modified()

    @property
    def shell_input_highlighting(self) -> Optional[bool]:
        """Per-session override for shell input highlighting."""

        return self._shell_input_highlighting

    @shell_input_highlighting.setter
    def shell_input_highlighting(self, value: Optional[bool]):
        if value is not None and not isinstance(value, bool):
            raise SessionValidationError(
                self.name, ["Invalid shell_input_highlighting value"]
            )
        if self._shell_input_highlighting != value:
            self._shell_input_highlighting = value
            self._mark_modified()

    def get_validation_errors(self) -> List[str]:
        """Returns a list of validation error messages."""
        errors = []
        if not self.name:
            errors.append(_("Session name is required."))
        if self.is_ssh():
            if not self.host:
                errors.append(_("Host is required for SSH sessions."))
            if not (1 <= self.port <= 65535):
                errors.append(_("Port must be between 1 and 65535."))
            if self.post_login_command_enabled and not self.post_login_command:
                errors.append(_("Post-login command cannot be empty when enabled."))
            if self.sftp_session_enabled and self.sftp_local_directory:
                try:
                    local_path = Path(self.sftp_local_directory).expanduser()
                    if not local_path.exists() or not local_path.is_dir():
                        errors.append(
                            _("SFTP local directory must exist and be a directory.")
                        )
                except Exception:
                    errors.append(
                        _("SFTP local directory must exist and be a directory.")
                    )
            for tunnel in self._port_forwardings:
                local_port = tunnel.get("local_port", 0)
                remote_port = tunnel.get("remote_port", 0)
                if not (1024 < int(local_port) <= 65535):
                    errors.append(
                        _(
                            "Port forward '{name}' has an invalid local port (must be between 1025 and 65535)."
                        ).format(name=tunnel.get("name", ""))
                    )
                if not (1 <= int(remote_port) <= 65535):
                    errors.append(
                        _("Port forward '{name}' has an invalid remote port.").format(
                            name=tunnel.get("name", "")
                        )
                    )
        return errors

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the session item to a dictionary."""
        auth_value_to_save = "" if self.uses_password_auth() else self._auth_value
        return {
            "name": self.name,
            "session_type": self._session_type,
            "host": self.host,
            "user": self.user,
            "auth_type": self._auth_type,
            "auth_value": auth_value_to_save,
            "folder_path": self._folder_path,
            "port": self.port,
            "tab_color": self.tab_color,
            "post_login_command_enabled": self.post_login_command_enabled,
            "post_login_command": self.post_login_command,
            "sftp_session_enabled": self.sftp_session_enabled,
            "sftp_local_directory": self.sftp_local_directory,
            "sftp_remote_directory": self.sftp_remote_directory,
            "port_forwardings": self.port_forwardings,
            "x11_forwarding": self.x11_forwarding,
            "local_working_directory": self.local_working_directory,
            "local_startup_command": self.local_startup_command,

            # Highlighting overrides (tri-state)
            "output_highlighting": self.output_highlighting,
            "command_specific_highlighting": self.command_specific_highlighting,
            "cat_colorization": self.cat_colorization,
            "shell_input_highlighting": self.shell_input_highlighting,

            "created_at": self._created_at,
            "modified_at": self._modified_at,
            "source": self._source,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionItem":
        """Deserializes a dictionary into a SessionItem instance."""
        session = cls(
            name=data.get("name", _("Unnamed Session")),
            session_type=data.get("session_type", "ssh"),
            host=data.get("host", ""),
            user=data.get("user", ""),
            auth_type=data.get("auth_type", "key"),
            folder_path=data.get("folder_path", ""),
            port=data.get("port", 22),
            post_login_command_enabled=data.get("post_login_command_enabled", False),
            post_login_command=data.get("post_login_command", ""),
            sftp_session_enabled=data.get("sftp_session_enabled", False),
            sftp_local_directory=data.get("sftp_local_directory", ""),
            sftp_remote_directory=data.get("sftp_remote_directory", ""),
            port_forwardings=data.get("port_forwardings", []),
            x11_forwarding=data.get("x11_forwarding", False),
            source=data.get("source", "user"),
            local_working_directory=data.get("local_working_directory", ""),
            local_startup_command=data.get("local_startup_command", ""),

            # Highlighting overrides (tri-state)
            output_highlighting=data.get("output_highlighting", None),
            command_specific_highlighting=data.get(
                "command_specific_highlighting", None
            ),
            cat_colorization=data.get("cat_colorization", None),
            shell_input_highlighting=data.get("shell_input_highlighting", None),
        )
        # __init__ sets default metadata; overwrite with loaded data
        session._auth_value = data.get("auth_value", "")
        session.tab_color = data.get("tab_color")
        session._created_at = data.get("created_at", time.time())
        session._modified_at = data.get("modified_at", time.time())
        return session

    def is_local(self) -> bool:
        return self._session_type == "local"

    def is_ssh(self) -> bool:
        return self._session_type == "ssh"

    def uses_key_auth(self) -> bool:
        return self.is_ssh() and self._auth_type == "key"

    def uses_password_auth(self) -> bool:
        return self.is_ssh() and self._auth_type == "password"

    def get_connection_string(self) -> str:
        if self.is_local():
            return _("Local Terminal")
        return f"{self.user}@{self.host}" if self.user else self.host

    def __str__(self) -> str:
        return f"SessionItem(name='{self.name}', type='{self._session_type}')"


class SessionFolder(BaseModel):
    """Data model for a folder used to organize sessions."""

    def __init__(self, name: str, path: str = "", parent_path: str = ""):
        super().__init__()
        self.logger = get_logger("zashterminal.sessions.folder")

        self._name = InputSanitizer.sanitize_filename(name)
        self._path = str(normalize_path(path)) if path else ""
        self._parent_path = str(normalize_path(parent_path)) if parent_path else ""
        self._children = Gio.ListStore.new(GObject.GObject)

    @property
    def children(self) -> Gio.ListStore:
        """Provides the list of children for Gtk.TreeListModel."""
        return self._children

    def add_child(self, item):
        """Add a child item (SessionItem or SessionFolder) to this folder."""
        self._children.append(item)

    def clear_children(self):
        """Remove all children from this folder."""
        self._children.remove_all()

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, value: str):
        self._name = InputSanitizer.sanitize_filename(value)
        self._mark_modified()

    @property
    def path(self) -> str:
        return self._path

    @path.setter
    def path(self, value: str):
        self._path = str(normalize_path(value)) if value else ""
        self._mark_modified()

    @property
    def parent_path(self) -> str:
        return self._parent_path

    @parent_path.setter
    def parent_path(self, value: str):
        self._parent_path = str(normalize_path(value)) if value else ""
        self._mark_modified()

    def get_validation_errors(self) -> List[str]:
        """Returns a list of validation error messages."""
        errors = []
        if not self.name:
            errors.append(_("Folder name is required."))
        if (
            self.path
            and self.parent_path
            and not self.path.startswith(self.parent_path + "/")
            and self.parent_path != ""
        ):
            errors.append(_("Folder path is not consistent with its parent path."))
        return errors

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the folder item to a dictionary."""
        return {
            "name": self.name,
            "path": self.path,
            "parent_path": self.parent_path,
            "created_at": self._created_at,
            "modified_at": self._modified_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionFolder":
        """Deserializes a dictionary into a SessionFolder instance."""
        folder = cls(
            name=data.get("name", _("Unnamed Folder")),
            path=data.get("path", ""),
            parent_path=data.get("parent_path", ""),
        )
        # __init__ sets default metadata; overwrite with loaded data
        folder._created_at = data.get("created_at", time.time())
        folder._modified_at = data.get("modified_at", time.time())
        return folder

    def __str__(self) -> str:
        return f"SessionFolder(name='{self.name}', path='{self.path}')"
