# zashterminal/sessions/operations.py

import os
import threading
from functools import partial
from pathlib import Path
from typing import Callable, List, Optional, Tuple, Union

from gi.repository import Gio

from ..core.signals import AppSignals
from ..helpers import generate_unique_name
from ..utils.logger import get_logger
from ..utils.ssh_config_parser import SSHConfigParser
from ..utils.translation_utils import _
from .models import SessionFolder, SessionItem
from .results import OperationResult
from .storage import get_storage_manager
from .validation import validate_folder_for_add, validate_session_for_add


class SessionOperations:
    """Handles CRUD and organizational operations for sessions and folders."""

    def __init__(
        self,
        session_store: Gio.ListStore,
        folder_store: Gio.ListStore,
        settings_manager,
    ):
        self.logger = get_logger("zashterminal.sessions.operations")
        self.session_store = session_store
        self.folder_store = folder_store
        self.settings_manager = settings_manager
        self._operation_lock = threading.RLock()
        self.storage_manager = get_storage_manager()
        ignored_list = self.settings_manager.get("ignored_ssh_config_hosts", []) or []
        self._ignored_ssh_config_hosts = set(ignored_list)

    def _add_item(
        self,
        item: Union[SessionItem, SessionFolder],
        store: Gio.ListStore,
        validator: Callable[[Union[SessionItem, SessionFolder]], OperationResult],
        item_type_name: str,
    ) -> OperationResult:
        """Generic method to add an item to a store with validation and rollback."""
        validation_result = validator(item)
        if not validation_result.success:
            return validation_result

        store.append(item)
        if not self._save_changes():
            self._remove_item_from_store(item)  # Rollback
            return OperationResult(
                False,
                _("Failed to save {item_type} data.").format(item_type=item_type_name),
            )

        self.logger.info(
            f"{item_type_name.capitalize()} added successfully: '{item.name}'"
        )
        return OperationResult(
            True,
            _("{item_type} '{name}' added successfully.").format(
                item_type=item_type_name.capitalize(), name=item.name
            ),
            item,
        )

    def add_session(self, session: SessionItem) -> OperationResult:
        """Adds a new session to the store with validation."""
        with self._operation_lock:
            validator = partial(
                validate_session_for_add,
                session_store=self.session_store,
                folder_store=self.folder_store,
            )
            result = self._add_item(session, self.session_store, validator, "session")
            if result.success:
                AppSignals.get().emit("session-created", session)
            return result

    def update_session(
        self, position: int, updated_session: SessionItem
    ) -> OperationResult:
        """Updates an existing session in the store."""
        with self._operation_lock:
            original_session = self.session_store.get_item(position)
            if not isinstance(original_session, SessionItem):
                return OperationResult(False, _("Item at position is not a session."))

            # Store original data for rollback
            original_data = original_session.to_dict()

            # Apply updates
            original_session.name = updated_session.name
            original_session.session_type = updated_session.session_type
            original_session.host = updated_session.host
            original_session.user = updated_session.user
            original_session.port = updated_session.port
            original_session.auth_type = updated_session.auth_type
            # The auth_value setter handles the keyring
            original_session.auth_value = updated_session.auth_value
            original_session.folder_path = updated_session.folder_path
            original_session.tab_color = updated_session.tab_color
            original_session.post_login_command_enabled = (
                updated_session.post_login_command_enabled
            )
            original_session.post_login_command = (
                updated_session.post_login_command
            )
            original_session.sftp_session_enabled = (
                updated_session.sftp_session_enabled
            )
            original_session.sftp_local_directory = (
                updated_session.sftp_local_directory
            )
            original_session.sftp_remote_directory = (
                updated_session.sftp_remote_directory
            )
            original_session.port_forwardings = updated_session.port_forwardings
            original_session.x11_forwarding = updated_session.x11_forwarding
            # Local terminal options
            original_session.local_working_directory = (
                updated_session.local_working_directory
            )
            original_session.local_startup_command = (
                updated_session.local_startup_command
            )

            # Per-session highlighting overrides (tri-state)
            original_session.output_highlighting = updated_session.output_highlighting
            original_session.command_specific_highlighting = (
                updated_session.command_specific_highlighting
            )
            original_session.cat_colorization = updated_session.cat_colorization
            original_session.shell_input_highlighting = (
                updated_session.shell_input_highlighting
            )

            if not self._save_changes():
                # Rollback changes on failure by recreating the item from original data
                rolled_back_session = SessionItem.from_dict(original_data)
                self.session_store.remove(position)
                self.session_store.insert(position, rolled_back_session)
                return OperationResult(False, _("Failed to save updated session data."))

            self.logger.info(f"Session updated successfully: '{original_session.name}'")
            result = OperationResult(
                True,
                _("Session '{name}' updated successfully.").format(
                    name=original_session.name
                ),
                original_session,
            )
            AppSignals.get().emit("session-updated", original_session.name)
            return result

    def remove_session(self, session: SessionItem) -> OperationResult:
        """Removes a session from the store."""
        with self._operation_lock:
            session_name = session.name  # Capture name before removal
            position = self._find_item_position(session)
            if position == -1:
                return OperationResult(False, _("Session not found."))

            self.session_store.remove(position)
            if not self._save_changes():
                self.session_store.insert(position, session)  # Rollback
                return OperationResult(
                    False, _("Failed to save after session removal.")
                )

            if getattr(session, "source", "user") == "ssh_config":
                key = self._make_ssh_config_key(session.user, session.host, session.port)
                if key not in self._ignored_ssh_config_hosts:
                    self._ignored_ssh_config_hosts.add(key)
                    self._persist_ignored_hosts()

            self.logger.info(f"Session removed successfully: '{session_name}'")
            result = OperationResult(
                True,
                _("Session '{name}' removed successfully.").format(name=session_name),
            )
            AppSignals.get().emit("session-deleted", session_name)
            return result

    def add_folder(self, folder: SessionFolder) -> OperationResult:
        """Adds a new folder to the store."""
        with self._operation_lock:
            validator = partial(validate_folder_for_add, folder_store=self.folder_store)
            result = self._add_item(folder, self.folder_store, validator, "folder")
            if result.success:
                AppSignals.get().emit("folder-created", folder)
            return result

    def update_folder(
        self, position: int, updated_folder: SessionFolder
    ) -> OperationResult:
        """Updates an existing folder and handles path changes for children."""
        with self._operation_lock:
            original_folder = self.folder_store.get_item(position)
            if not isinstance(original_folder, SessionFolder):
                return OperationResult(False, _("Item at position is not a folder."))

            old_path = original_folder.path
            new_path = updated_folder.path

            original_folder.name = updated_folder.name
            original_folder.parent_path = updated_folder.parent_path
            original_folder.path = new_path

            if old_path != new_path:
                self._update_child_paths(old_path, new_path)

            if not self._save_changes():
                return OperationResult(False, _("Failed to save updated folder data."))

            self.logger.info(f"Folder updated successfully: '{original_folder.name}'")
            result = OperationResult(
                True,
                _("Folder '{name}' updated successfully.").format(
                    name=original_folder.name
                ),
                original_folder,
            )
            AppSignals.get().emit("folder-updated", original_folder.name)
            return result

    def remove_folder(
        self, folder: SessionFolder, force: bool = False
    ) -> OperationResult:
        """Removes a folder, and optionally its contents."""
        with self._operation_lock:
            folder_name = folder.name  # Capture name before removal
            if not force and self._folder_has_children(folder.path):
                return OperationResult(False, _("Cannot remove a non-empty folder."))

            if force:
                self._remove_folder_children(folder.path)

            position = self._find_item_position(folder)
            if position == -1:
                return OperationResult(False, _("Folder not found."))

            self.folder_store.remove(position)
            if not self._save_changes():
                return OperationResult(False, _("Failed to save after folder removal."))

            self.logger.info(f"Folder removed successfully: '{folder_name}'")
            result = OperationResult(
                True,
                _("Folder '{name}' removed successfully.").format(name=folder_name),
            )
            AppSignals.get().emit("folder-deleted", folder_name)
            return result

    def move_session_to_folder(
        self, session: SessionItem, target_folder_path: str
    ) -> OperationResult:
        """Moves a session to a different folder."""
        with self._operation_lock:
            if session.folder_path == target_folder_path:
                return OperationResult(True, "Session already in target folder.")

            original_folder = session.folder_path
            session.folder_path = target_folder_path

            if not self._save_changes():
                session.folder_path = original_folder  # Rollback
                return OperationResult(False, _("Failed to save after moving session."))

            self.logger.info(
                f"Session '{session.name}' moved to '{target_folder_path or 'root'}'"
            )
            result = OperationResult(True, _("Session moved successfully."), session)
            AppSignals.get().emit("session-updated", session.name)
            return result

    def move_folder(
        self, folder: SessionFolder, target_parent_path: str
    ) -> OperationResult:
        """Moves a folder to a new parent folder."""
        with self._operation_lock:
            if folder.parent_path == target_parent_path:
                return OperationResult(True, "Folder already in target parent.")
            if target_parent_path.startswith(folder.path + "/"):
                return OperationResult(False, _("Cannot move a folder into itself."))

            _found, position = self.find_folder_by_path(folder.path)
            if position == -1:
                return OperationResult(False, _("Folder not found."))

            updated_folder = SessionFolder.from_dict(folder.to_dict())
            updated_folder.parent_path = target_parent_path
            updated_folder.path = (
                f"{target_parent_path}/{updated_folder.name}"
                if target_parent_path
                else f"/{updated_folder.name}"
            )
            return self.update_folder(position, updated_folder)

    def duplicate_session(self, session: SessionItem) -> OperationResult:
        """Duplicates a session, giving it a unique name."""
        with self._operation_lock:
            new_item = SessionItem.from_dict(session.to_dict())
            existing_names = self._get_session_names_in_folder(session.folder_path)
            new_item.name = generate_unique_name(new_item.name, existing_names)
            return self.add_session(new_item)

    def import_sessions_from_ssh_config(
        self, config_path: Optional[Union[str, Path]] = None
    ) -> OperationResult:
        """Imports SSH sessions from an OpenSSH-style config file."""
        with self._operation_lock:
            default_path = Path.home() / ".ssh" / "config"
            target_path = Path(config_path).expanduser() if config_path else default_path

            if not target_path.exists():
                message = _("SSH config file not found at {path}").format(
                    path=str(target_path)
                )
                self.logger.warning(message)
                return OperationResult(False, message)

            parser = SSHConfigParser()
            try:
                entries = parser.parse(target_path)
            except Exception as exc:  # pragma: no cover - defensive
                error_msg = _("Failed to parse SSH config: {error}").format(error=exc)
                self.logger.error(error_msg)
                return OperationResult(False, error_msg)

            if not entries:
                message = _("No host entries found in SSH config.")
                self.logger.info(message)
                return OperationResult(False, message)

            imported_count = 0
            warnings: List[str] = []
            existing_names = self._get_session_names_in_folder("")

            for entry in entries:
                hostname = entry.hostname or entry.alias
                if not hostname:
                    warnings.append(
                        _("Skipped host '{alias}': missing hostname.").format(
                            alias=entry.alias
                        )
                    )
                    continue

                user = entry.user or ""
                port = entry.port or 22
                entry_key = self._make_ssh_config_key(user, hostname, port)

                if entry_key in self._ignored_ssh_config_hosts:
                    self.logger.debug(
                        f"Skipping ignored SSH config host: {entry_key}"
                    )
                    continue

                existing_session = self._find_existing_ssh_session(hostname, user, port)
                if existing_session:
                    continue

                session_name = generate_unique_name(entry.alias, existing_names)
                candidate_identity = (
                    os.path.expanduser(entry.identity_file)
                    if entry.identity_file
                    else ""
                )

                session = SessionItem(
                    name=session_name,
                    session_type="ssh",
                    host=hostname,
                    user=user,
                    port=port,
                    auth_type="key",
                    auth_value=candidate_identity,
                    x11_forwarding=bool(entry.forward_x11),
                    source="ssh_config",
                )

                result = self.add_session(session)
                if result.success:
                    if entry_key in self._ignored_ssh_config_hosts:
                        self._ignored_ssh_config_hosts.remove(entry_key)
                        self._persist_ignored_hosts()
                    imported_count += 1
                    existing_names.add(session_name)
                else:
                    warning_text = result.message or _(
                        "Failed to import host '{alias}'."
                    ).format(alias=entry.alias)
                    warnings.append(warning_text)

            if imported_count == 0:
                message = _("No sessions were imported from {path}.").format(
                    path=str(target_path)
                )
                self.logger.info(message)
                return OperationResult(False, message, warnings=warnings)

            success_message = _("Imported {count} session(s) from {path}.").format(
                count=imported_count, path=str(target_path)
            )
            self.logger.info(success_message)
            return OperationResult(True, success_message, warnings=warnings)

    def paste_item(
        self,
        item_to_paste: Union[SessionItem, SessionFolder],
        target_folder_path: str,
        is_cut: bool,
    ) -> OperationResult:
        """Pastes an item from clipboard logic (cut/copy)."""
        with self._operation_lock:
            if is_cut:
                if isinstance(item_to_paste, SessionItem):
                    return self.move_session_to_folder(
                        item_to_paste, target_folder_path
                    )
                elif isinstance(item_to_paste, SessionFolder):
                    return self.move_folder(item_to_paste, target_folder_path)
            else:  # Is copy
                if isinstance(item_to_paste, SessionItem):
                    new_item = SessionItem.from_dict(item_to_paste.to_dict())
                    new_item.folder_path = target_folder_path
                    return self.duplicate_session(new_item)

            return OperationResult(
                False, _("Unsupported item type for paste operation.")
            )

    def find_session_by_name_and_path(
        self, name: str, path: str
    ) -> Optional[Tuple[SessionItem, int]]:
        """Finds a session by its name and folder path."""
        for i in range(self.session_store.get_n_items()):
            session = self.session_store.get_item(i)
            if session.name == name and session.folder_path == path:
                return session, i
        return None, -1

    def find_folder_by_path(self, path: str) -> Optional[Tuple[SessionFolder, int]]:
        """Finds a folder by its full path."""
        for i in range(self.folder_store.get_n_items()):
            folder = self.folder_store.get_item(i)
            if folder.path == path:
                return folder, i
        return None, -1

    def _save_changes(self) -> bool:
        """Saves all session and folder data."""
        return self.storage_manager.save_sessions_and_folders_safe(
            self.session_store, self.folder_store
        )

    def _update_child_paths(self, old_path: str, new_path: str):
        """Updates the paths of all children when a folder is moved or renamed."""
        for i in range(self.session_store.get_n_items()):
            session = self.session_store.get_item(i)
            if session.folder_path == old_path:
                session.folder_path = new_path

        for i in range(self.folder_store.get_n_items()):
            folder = self.folder_store.get_item(i)
            if folder.parent_path == old_path:
                folder.parent_path = new_path
                # Recursively update paths of sub-folders
                old_sub_path = folder.path
                new_sub_path = f"{new_path}/{folder.name}"
                folder.path = new_sub_path
                self._update_child_paths(old_sub_path, new_sub_path)

    def _folder_has_children(self, folder_path: str) -> bool:
        """Checks if a folder contains any sessions or subfolders."""
        for i in range(self.session_store.get_n_items()):
            if self.session_store.get_item(i).folder_path == folder_path:
                return True
        for i in range(self.folder_store.get_n_items()):
            if self.folder_store.get_item(i).parent_path == folder_path:
                return True
        return False

    def _remove_folder_children(self, folder_path: str):
        """Recursively removes all sessions and subfolders within a given path."""
        for i in range(self.session_store.get_n_items() - 1, -1, -1):
            if self.session_store.get_item(i).folder_path == folder_path:
                self.session_store.remove(i)

        for i in range(self.folder_store.get_n_items() - 1, -1, -1):
            folder = self.folder_store.get_item(i)
            if folder.parent_path == folder_path:
                self._remove_folder_children(folder.path)
                self.folder_store.remove(i)

    def _find_item_position(
        self, item_to_find: Union[SessionItem, SessionFolder]
    ) -> int:
        """Finds the position of a session or folder in its respective store."""
        store = (
            self.session_store
            if isinstance(item_to_find, SessionItem)
            else self.folder_store
        )
        for i in range(store.get_n_items()):
            if store.get_item(i) == item_to_find:
                return i
        return -1

    def _remove_item_from_store(
        self, item_to_remove: Union[SessionItem, SessionFolder]
    ):
        """Removes an item from its store, used for rolling back failed saves."""
        position = self._find_item_position(item_to_remove)
        if position != -1:
            store = (
                self.session_store
                if isinstance(item_to_remove, SessionItem)
                else self.folder_store
            )
            store.remove(position)

    def _get_session_names_in_folder(self, folder_path: str) -> set:
        """Gets a set of session names within a specific folder."""
        return {s.name for s in self.session_store if s.folder_path == folder_path}

    def _find_existing_ssh_session(
        self, hostname: str, user: str, port: int
    ) -> Optional[SessionItem]:
        for session in self.session_store:
            if not isinstance(session, SessionItem):
                continue
            if not session.is_ssh():
                continue
            if (
                session.host == hostname
                and session.user == user
                and session.port == port
            ):
                return session
        return None

    def _make_ssh_config_key(self, user: str, host: str, port: int) -> str:
        user_part = user or ""
        return f"{user_part}@{host}:{port}"

    def _persist_ignored_hosts(self) -> None:
        try:
            self.settings_manager.set(
                "ignored_ssh_config_hosts", sorted(self._ignored_ssh_config_hosts)
            )
        except Exception as exc:  # pragma: no cover - defensive
            self.logger.warning(f"Failed to persist ignored SSH config hosts: {exc}")
