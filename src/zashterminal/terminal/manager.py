# zashterminal/terminal/manager.py

import json
import os
import pathlib
import signal
import subprocess
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Union
from urllib.parse import urlparse

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import Gdk, GLib, GObject, Gtk, Vte

from ..helpers import is_valid_url
from ..sessions.models import SessionItem
from ..settings.config import PROMPT_TERMINATOR_PATTERN
from ..settings.manager import SettingsManager
from ..utils.exceptions import (
    TerminalCreationError,
)
from ..utils.logger import get_logger, log_terminal_event
from ..utils.osc7_tracker import OSC7Info, get_osc7_tracker
from ..utils.platform import get_environment_manager, get_platform_info
from ..utils.security import validate_session_data
from ..utils.translation_utils import _
from .execute_command import pack_execute_user_data
from .manager_balabit import TerminalBalabitMixin
from .manager_execute import TerminalExecuteMixin
from .manager_ssh import TerminalSSHLifecycleMixin
from .manager_urls import TerminalUrlMixin
from .registry import (
    ManualSSHTracker,
    TerminalLifecycleManager,
    TerminalRegistry,
    TerminalState,
)

from ._lazy import (
    create_terminal_menu as _create_terminal_menu,
    get_highlight_manager as _get_highlight_manager,
    get_spawner as _get_spawner,
)




class TerminalManager(
    TerminalBalabitMixin,
    TerminalSSHLifecycleMixin,
    TerminalExecuteMixin,
    TerminalUrlMixin,
):
    def __init__(self, parent_window, settings_manager: SettingsManager):
        self.logger = get_logger("zashterminal.terminal.manager")
        self.parent_window = parent_window
        self.settings_manager = settings_manager
        self.platform_info = get_platform_info()
        self.environment_manager = get_environment_manager()
        self.registry = TerminalRegistry()
        self.spawner = _get_spawner()
        self.lifecycle_manager = TerminalLifecycleManager(self.registry, self.logger)
        self.osc7_tracker = get_osc7_tracker(settings_manager)
        self.manual_ssh_tracker = ManualSSHTracker(
            self.registry, self._on_manual_ssh_state_changed
        )
        self._creation_lock = threading.Lock()
        self._cleanup_lock = threading.Lock()
        self._pending_kill_timers: Dict[int, int] = {}
        self.tab_manager = None
        self.on_terminal_focus_changed: Optional[Callable] = None
        self.terminal_exit_handler: Optional[Callable] = None
        self._stats = {
            "terminals_created": 0,
            "terminals_failed": 0,
            "terminals_closed": 0,
        }
        self._highlight_proxies: Dict[
            int, Any
        ] = {}  # Dict[int, HighlightedTerminalProxy]
        self._highlight_manager = None
        self._balabit_gateway_prompt_shown: set[int] = set()
        self._balabit_gateway_prompt_submitted: set[int] = set()
        self._balabit_gateway_pending_auth: Dict[int, Dict[str, str]] = {}
        self._selection_copy_timers: Dict[int, int] = {}
        # Process check timer runs every 1 second for responsive context detection
        self._process_check_timer_id = GLib.timeout_add_seconds(
            1, self._periodic_process_check
        )
        self.logger.info("Terminal manager initialized")

    def _is_highlighting_preload_needed(self) -> bool:
        """
        Check if highlighting resources should be preloaded at startup.

        To reduce baseline memory usage, we only preload when highlighting
        features are likely to be used based on user configuration.
        """
        try:
            # Shell input highlighting has its own setting and should trigger preload.
            if self.settings_manager.get("shell_input_highlighting_enabled", False):
                return True

            # Cat colorization only matters when output highlighting is enabled.
            if not self.settings_manager.get("cat_colorization_enabled", True):
                return False

            # Read lightweight highlight activation flags without instantiating
            # HighlightManager (which loads/compiles many JSON rules).
            highlights_file = (
                self.settings_manager.config_paths.CONFIG_DIR / "highlights_settings.json"
            )
            if not highlights_file.exists():
                return False

            with open(highlights_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            return bool(data.get("enabled_for_local") or data.get("enabled_for_ssh"))
        except Exception as e:
            self.logger.debug(f"Could not evaluate highlight preload need: {e}")
            return False

    def prepare_initial_terminal(self) -> None:
        """
        Pre-create the base terminal widget and prepare shell environment in background.
        This allows the terminal to be ready faster when the first tab is created.
        Call this early during window initialization for best results.
        """
        self._precreated_terminal = None
        self._precreated_env_ready = threading.Event()
        self._precreated_env_data = None
        self._highlights_ready = threading.Event()

        # Create base terminal widget immediately (must be on main thread)
        # Note: Don't apply settings yet since window UI may not be fully ready
        try:
            self._precreated_terminal = self._create_base_terminal(apply_settings=False)
            if self._precreated_terminal:
                self.logger.info("Pre-created base terminal widget for faster startup")
        except Exception as e:
            self.logger.warning(f"Failed to pre-create terminal: {e}")
            self._precreated_terminal = None

        # Prepare shell environment and highlights in background thread
        def prepare_background():
            try:
                # Prepare shell environment
                cmd, env, temp_dir_path = self.spawner._prepare_shell_environment(None)
                self._precreated_env_data = (cmd, env, temp_dir_path)
                self.logger.debug("Pre-prepared shell environment in background")
            except Exception as e:
                self.logger.warning(f"Failed to pre-prepare shell environment: {e}")
                self._precreated_env_data = None
            finally:
                self._precreated_env_ready.set()

            if self._is_highlighting_preload_needed():
                # Pre-load the HighlightManager (loads 50+ JSON files)
                try:
                    from ..settings.highlights import get_highlight_manager

                    self._highlight_manager = get_highlight_manager()
                    self.logger.debug(
                        "Pre-loaded HighlightManager (JSON rules) in background"
                    )
                except Exception as e:
                    self.logger.warning(f"Failed to pre-load HighlightManager: {e}")

                # Pre-load highlight modules (output, shell_input, and proxy implementation)
                try:
                    from .highlighter.output import get_output_highlighter
                    from .highlighter.shell_input import get_shell_input_highlighter

                    get_output_highlighter()
                    get_shell_input_highlighter()
                    # Pre-import the proxy implementation to warm up GTK stack
                    from ._highlighter_impl import (
                        HighlightedTerminalProxy as _,  # noqa: F401
                    )

                    self.logger.debug("Pre-loaded highlighting modules in background")
                except Exception as e:
                    self.logger.warning(f"Failed to pre-load highlights: {e}")
            else:
                self.logger.debug(
                    "Skipping highlight preload (features disabled by configuration)"
                )

            self._highlights_ready.set()

        bg_thread = threading.Thread(target=prepare_background, daemon=True)
        bg_thread.start()

    def get_precreated_terminal(self) -> "Optional[Vte.Terminal]":
        """
        Get the pre-created terminal if available.
        Returns None if no terminal was pre-created or it was already consumed.
        """
        terminal = getattr(self, "_precreated_terminal", None)
        self._precreated_terminal = None
        return terminal

    def get_precreated_env_data(self, timeout: float = 0.1) -> "Optional[tuple]":
        """
        Get the pre-prepared shell environment data if ready.
        Args:
            timeout: Max time to wait for env preparation (default 100ms)
        Returns:
            Tuple of (cmd, env, temp_dir_path) or None if not ready/failed
        """
        ready_event = getattr(self, "_precreated_env_ready", None)
        if ready_event and ready_event.wait(timeout):
            data = getattr(self, "_precreated_env_data", None)
            self._precreated_env_data = None
            return data
        return None

    def _get_highlight_manager(self):
        if self._highlight_manager is None:
            self._highlight_manager = _get_highlight_manager()
        return self._highlight_manager

    def _cleanup_highlight_proxy(self, terminal_id: int):
        proxy = self._highlight_proxies.pop(terminal_id, None)
        if proxy:
            try:
                # Pass from_destroy=False by default as we don't know if it's from destroy here.
                # But usually this is called from cleanup logic, so widget might be dying.
                # Safe to just call stop(), it handles internal state.
                proxy.stop()
                self.logger.debug(f"Stopped highlight proxy for terminal {terminal_id}")
            except Exception as e:
                self.logger.error(f"Error stopping highlight proxy: {e}")

    def apply_settings_to_all_terminals(self):
        self.logger.info("Applying settings to all active terminals.")
        for terminal_id in self.registry.get_all_terminal_ids():
            terminal = self.registry.get_terminal(terminal_id)
            if terminal:
                try:
                    self.settings_manager.apply_terminal_settings(
                        terminal, self.parent_window
                    )
                except Exception as e:
                    self.logger.error(
                        f"Failed to apply settings to terminal {terminal_id}: {e}"
                    )

    def set_tab_manager(self, tab_manager):
        self.tab_manager = tab_manager

    def set_terminal_exit_handler(self, handler: Callable):
        self.terminal_exit_handler = handler

    def _periodic_process_check(self) -> bool:
        """
        Periodic check to detect manual SSH sessions in local terminals.

        This runs every 2 seconds and checks for manual SSH sessions.
        Note: Context-aware highlighting is now handled by CommandDetector
        which parses the terminal output stream in real-time.
        """
        try:
            if self.parent_window and hasattr(self.parent_window, "tab_manager"):
                active_terminal = self.parent_window.tab_manager.get_selected_terminal()

                if active_terminal:
                    terminal_id = getattr(active_terminal, "terminal_id", None)
                    if terminal_id is not None:
                        # Check for manual SSH sessions
                        self.manual_ssh_tracker.check_process_tree(terminal_id)
        except Exception as e:
            self.logger.debug(f"Periodic check error: {e}")
        return True

    def _on_manual_ssh_state_changed(self, terminal: Vte.Terminal):
        self._update_title(terminal)
        return False

    def _resolve_working_directory(
        self, working_directory: Optional[str]
    ) -> Optional[str]:
        if not working_directory:
            return None
        try:
            expanded_path = os.path.expanduser(os.path.expandvars(working_directory))
            resolved_path = os.path.abspath(expanded_path)
            path_obj = pathlib.Path(resolved_path)
            if (
                path_obj.exists()
                and path_obj.is_dir()
                and os.access(resolved_path, os.R_OK | os.X_OK)
            ):
                return resolved_path
            else:
                self.logger.warning(
                    f"Working directory not accessible: {working_directory}"
                )
                return None
        except Exception as e:
            self.logger.error(
                f"Error resolving working directory '{working_directory}': {e}"
            )
            return None

    def _on_directory_uri_changed(self, terminal: Vte.Terminal, _param_spec):
        try:
            uri = terminal.get_current_directory_uri()
            if not uri:
                return
            from urllib.parse import unquote, urlparse

            parsed_uri = urlparse(uri)
            if parsed_uri.scheme != "file":
                return
            path = unquote(parsed_uri.path)
            hostname = parsed_uri.hostname or "localhost"
            display_path = self.osc7_tracker.parser._create_display_path(path)
            osc7_info = OSC7Info(
                hostname=hostname, path=path, display_path=display_path
            )
            self._update_title(terminal, osc7_info)
            self._run_pending_execute_command(terminal)
        except Exception as e:
            self.logger.error(f"Directory URI change handling failed: {e}")

    def _update_title(
        self, terminal: Vte.Terminal, osc7_info: Optional[OSC7Info] = None
    ):
        terminal_id = getattr(terminal, "terminal_id", None)
        if terminal_id is None:
            return
        terminal_info = self.registry.get_terminal_info(terminal_id)
        if not terminal_info:
            return

        if osc7_info is None:
            uri = terminal.get_current_directory_uri()
            if uri:
                from urllib.parse import unquote, urlparse

                parsed_uri = urlparse(uri)
                if parsed_uri.scheme == "file":
                    path = unquote(parsed_uri.path)
                    hostname = parsed_uri.hostname or "localhost"
                    display_path = self.osc7_tracker.parser._create_display_path(path)
                    osc7_info = OSC7Info(
                        hostname=hostname, path=path, display_path=display_path
                    )

        new_title = "Terminal"
        if terminal_info.get("type") == "ssh":
            session = terminal_info.get("identifier")
            if isinstance(session, SessionItem):
                new_title = (
                    f"{session.name}:{osc7_info.display_path}"
                    if osc7_info
                    else session.name
                )
        elif terminal_info.get("type") == "local":
            ssh_target = self.manual_ssh_tracker.get_ssh_target(terminal_id)
            if ssh_target:
                new_title = (
                    f"{ssh_target}:{osc7_info.display_path}"
                    if osc7_info
                    else ssh_target
                )
            elif osc7_info:
                new_title = osc7_info.display_path
            else:
                identifier = terminal_info.get("identifier")
                if isinstance(identifier, SessionItem):
                    new_title = identifier.name
                else:
                    new_title = str(identifier)
        elif terminal_info.get("type") == "sftp":
            session = terminal_info.get("identifier")
            if isinstance(session, SessionItem):
                new_title = self._get_sftp_display_title(session, terminal)

        if self.tab_manager:
            self.tab_manager.update_titles_for_terminal(terminal, new_title, osc7_info)

    def _get_sftp_display_title(
        self, session: SessionItem, terminal: Vte.Terminal
    ) -> str:
        if self.tab_manager:
            page = self.tab_manager.get_page_for_terminal(terminal)
            if page:
                for tab in self.tab_manager.tabs:
                    if self.tab_manager.pages.get(tab) == page:
                        return getattr(tab, "_base_title", f"SFTP-{session.name}")
        return f"SFTP-{session.name}"

    def create_local_terminal(
        self,
        session: Optional[SessionItem] = None,
        title: str = "Local Terminal",
        working_directory: Optional[str] = None,
        execute_command: Optional[str] = None,
        close_after_execute: bool = False,
    ):
        # Try to use pre-created terminal for faster initial startup
        terminal = self.get_precreated_terminal()
        if terminal:
            self.logger.debug("Using pre-created terminal for faster startup")
            # Apply settings now that window UI is ready
            self.settings_manager.apply_terminal_settings(terminal, self.parent_window)
            self.logger.debug("Applied terminal settings to pre-created terminal")
        else:
            terminal = self._create_base_terminal()
        if not terminal:
            raise TerminalCreationError("base terminal creation failed", "local")

        identifier = session if session else title
        terminal_id = self.registry.register_terminal(terminal, "local", identifier)
        self._setup_terminal_events(terminal, identifier, terminal_id)

        try:
            resolved_working_dir = self._resolve_working_directory(working_directory)
            if working_directory and not resolved_working_dir:
                self.logger.warning(
                    f"Invalid working directory '{working_directory}', using default"
                )

            # Try to get pre-prepared environment for faster spawn (only if no custom working dir)
            precreated_env = None
            if not working_directory:
                precreated_env = self.get_precreated_env_data(timeout=0.05)

            user_data_for_spawn = pack_execute_user_data(
                terminal_id, execute_command, close_after_execute
            )

            highlight_manager = self._get_highlight_manager()

            # Decide whether to spawn a highlighted proxy.
            # Highlighted terminals are required for:
            # - output highlighting
            # - cat colorization
            # - shell input highlighting
            # Each can be overridden per-session (tri-state: None/True/False).
            # Note: cat colorization and shell input highlighting only work
            # when output highlighting is enabled (Local/SSH activation).

            output_highlighting_enabled = highlight_manager.enabled_for_local
            if session and session.output_highlighting is not None:
                output_highlighting_enabled = session.output_highlighting

            # Cat and shell input highlighting depend on output highlighting being enabled
            cat_colorization_enabled = (
                output_highlighting_enabled
                and self.settings_manager.get("cat_colorization_enabled", True)
            )
            shell_input_enabled = (
                output_highlighting_enabled
                and self.settings_manager.get("shell_input_highlighting_enabled", False)
            )

            # Per-session overrides can further enable/disable these features
            if session and session.cat_colorization is not None:
                cat_colorization_enabled = (
                    output_highlighting_enabled and session.cat_colorization
                )
            if session and session.shell_input_highlighting is not None:
                shell_input_enabled = (
                    output_highlighting_enabled and session.shell_input_highlighting
                )

            should_spawn_highlighted = (
                output_highlighting_enabled
                or cat_colorization_enabled
                or shell_input_enabled
            )

            if should_spawn_highlighted:
                # Check if highlight modules are ready (non-blocking)
                # If not ready yet, spawn_highlighted_local_terminal will
                # import them synchronously (slightly slower first time only)
                highlights_ready = getattr(self, "_highlights_ready", None)
                if highlights_ready is not None:
                    # Only wait a very short time - if not ready, import will happen sync
                    highlights_ready.wait(timeout=0.05)  # Max 50ms wait

                proxy = self.spawner.spawn_highlighted_local_terminal(
                    terminal,
                    session=session,
                    callback=self._on_spawn_callback,
                    user_data=user_data_for_spawn,
                    working_directory=resolved_working_dir,
                    terminal_id=terminal_id,
                )
                if proxy:
                    self._highlight_proxies[terminal_id] = proxy
                    self.logger.info(
                        f"Highlighted local terminal spawned (ID: {terminal_id})"
                    )
                else:
                    self.logger.warning(
                        "Highlighted spawn failed, falling back to standard spawning"
                    )
                    self.spawner.spawn_local_terminal(
                        terminal,
                        callback=self._on_spawn_callback,
                        user_data=user_data_for_spawn,
                        working_directory=resolved_working_dir,
                        precreated_env=precreated_env,
                    )
            else:
                self.spawner.spawn_local_terminal(
                    terminal,
                    callback=self._on_spawn_callback,
                    user_data=user_data_for_spawn,
                    working_directory=resolved_working_dir,
                    precreated_env=precreated_env,
                )

            log_title = session.name if session else title
            self.logger.info(
                f"Local terminal created successfully: '{log_title}' (ID: {terminal_id})"
            )
            log_terminal_event("created", log_title, "local terminal")
            self._stats["terminals_created"] += 1
            return terminal
        except TerminalCreationError:
            self.registry.unregister_terminal(terminal_id)
            self._cleanup_highlight_proxy(terminal_id)
            self._stats["terminals_failed"] += 1
            raise

    def _create_remote_terminal(
        self,
        session: SessionItem,
        terminal_type: str,
        initial_command: Optional[str] = None,
        sftp_remote_path: Optional[str] = None,
        sftp_local_directory: Optional[str] = None,
    ) -> Optional[Vte.Terminal]:
        with self._creation_lock:
            session_data = session.to_dict()
            is_valid, errors = validate_session_data(session_data)
            if not is_valid:
                error_msg = f"Session validation failed for {terminal_type.upper()}: {', '.join(errors)}"
                raise TerminalCreationError(error_msg, terminal_type)

            terminal = self._create_base_terminal()
            if not terminal:
                raise TerminalCreationError(
                    f"base terminal creation failed for {terminal_type.upper()}",
                    terminal_type,
                )

            terminal_id = self.registry.register_terminal(
                terminal, terminal_type, session
            )
            self._setup_terminal_events(terminal, session, terminal_id)
            user_data_for_spawn = (terminal_id, session)

            try:
                if terminal_type == "ssh":
                    highlight_manager = self._get_highlight_manager()

                    # Decide whether to spawn a highlighted proxy.
                    # Note: cat colorization and shell input highlighting only work
                    # when output highlighting is enabled (Local/SSH activation).
                    output_highlighting_enabled = highlight_manager.enabled_for_ssh
                    if session.output_highlighting is not None:
                        output_highlighting_enabled = session.output_highlighting

                    # Cat and shell input highlighting depend on output highlighting being enabled
                    cat_colorization_enabled = (
                        output_highlighting_enabled
                        and self.settings_manager.get("cat_colorization_enabled", True)
                    )
                    shell_input_enabled = (
                        output_highlighting_enabled
                        and self.settings_manager.get(
                            "shell_input_highlighting_enabled", False
                        )
                    )

                    # Per-session overrides can further enable/disable these features
                    if session.cat_colorization is not None:
                        cat_colorization_enabled = (
                            output_highlighting_enabled and session.cat_colorization
                        )
                    if session.shell_input_highlighting is not None:
                        shell_input_enabled = (
                            output_highlighting_enabled
                            and session.shell_input_highlighting
                        )

                    should_spawn_highlighted = (
                        output_highlighting_enabled
                        or cat_colorization_enabled
                        or shell_input_enabled
                    )

                    if should_spawn_highlighted:
                        proxy = self.spawner.spawn_highlighted_ssh_session(
                            terminal,
                            session,
                            callback=self._on_spawn_callback,
                            user_data=user_data_for_spawn,
                            initial_command=initial_command,
                            terminal_id=terminal_id,
                        )
                        if proxy:
                            self._highlight_proxies[terminal_id] = proxy
                            self.logger.info(
                                f"Highlighted SSH terminal spawned (ID: {terminal_id})"
                            )
                        else:
                            self.logger.warning(
                                "Highlighted SSH spawn failed, falling back to standard spawning"
                            )
                            self.spawner.spawn_ssh_session(
                                terminal,
                                session,
                                callback=self._on_spawn_callback,
                                user_data=user_data_for_spawn,
                                initial_command=initial_command,
                            )
                    else:
                        self.spawner.spawn_ssh_session(
                            terminal,
                            session,
                            callback=self._on_spawn_callback,
                            user_data=user_data_for_spawn,
                            initial_command=initial_command,
                        )
                    # Setup drag-and-drop for SSH terminal uploads
                    self._setup_ssh_drag_and_drop(terminal, terminal_id)
                elif terminal_type == "sftp":
                    self._setup_sftp_drag_and_drop(terminal)
                    self.spawner.spawn_sftp_session(
                        terminal,
                        session,
                        callback=self._on_spawn_callback,
                        user_data=user_data_for_spawn,
                        local_directory=sftp_local_directory,
                        remote_path=sftp_remote_path,
                    )
                else:
                    raise ValueError(
                        f"Unsupported remote terminal type: {terminal_type}"
                    )

                self.logger.info(
                    f"{terminal_type.upper()} terminal created successfully: '{session.name}' (ID: {terminal_id})"
                )
                log_terminal_event(
                    "created",
                    session.name,
                    f"{terminal_type.upper()} to {session.get_connection_string()}",
                )
                self._stats["terminals_created"] += 1
                return terminal
            except TerminalCreationError:
                self.registry.unregister_terminal(terminal_id)
                self._cleanup_highlight_proxy(terminal_id)
                self._stats["terminals_failed"] += 1
                raise

    def create_ssh_terminal(
        self, session: SessionItem, initial_command: Optional[str] = None
    ) -> Optional[Vte.Terminal]:
        commands: List[str] = []
        if initial_command:
            commands.append(initial_command)
        if session.post_login_command_enabled and session.post_login_command:
            commands.append(session.post_login_command)
        combined_command = "; ".join(commands) if commands else None
        return self._create_remote_terminal(session, "ssh", combined_command)

    def create_sftp_terminal(self, session: SessionItem) -> Optional[Vte.Terminal]:
        remote_path = None
        local_directory = None
        if session.sftp_session_enabled:
            remote_path = session.sftp_remote_directory or None
            local_directory = session.sftp_local_directory or None
        return self._create_remote_terminal(
            session,
            "sftp",
            sftp_remote_path=remote_path,
            sftp_local_directory=local_directory,
        )

    def _create_base_terminal(
        self, apply_settings: bool = True
    ) -> Optional[Vte.Terminal]:
        try:
            terminal = Vte.Terminal()
            terminal.set_vexpand(True)
            terminal.set_hexpand(True)
            terminal.set_mouse_autohide(True)
            terminal.set_cursor_blink_mode(Vte.CursorBlinkMode.ON)
            terminal.set_scroll_on_output(False)
            terminal.set_scroll_on_keystroke(True)
            terminal.set_scroll_unit_is_pixels(True)
            if hasattr(terminal, "set_search_highlight_enabled"):
                terminal.set_search_highlight_enabled(True)
            if apply_settings:
                self.settings_manager.apply_terminal_settings(
                    terminal, self.parent_window
                )
            self._setup_context_menu(terminal)
            self._setup_url_patterns(terminal)
            return terminal
        except Exception as e:
            self.logger.error(f"Base terminal creation failed: {e}")
            return None

    def _setup_sftp_drag_and_drop(self, terminal: Vte.Terminal):
        drop_target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop_target.connect("drop", self._on_file_drop, terminal)
        terminal.add_controller(drop_target)

    def _setup_ssh_drag_and_drop(self, terminal: Vte.Terminal, terminal_id: int):
        """Setup drag-and-drop for SSH terminals to upload files."""
        drop_target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop_target.connect("drop", self._on_ssh_file_drop, terminal, terminal_id)
        terminal.add_controller(drop_target)

    def _on_file_drop(self, drop_target, value, x, y, terminal: Vte.Terminal) -> bool:
        try:
            files = value.get_files()
            for file in files:
                local_path = file.get_path()
                if local_path:
                    command_to_send = f'put -r "{local_path}"\n'
                    self.logger.info(
                        f"File dropped on SFTP terminal. Sending command: {command_to_send.strip()}"
                    )
                    terminal.feed_child(command_to_send.encode("utf-8"))
            return True
        except Exception as e:
            self.logger.error(f"Error handling file drop for SFTP: {e}")
            return False

    def _on_ssh_file_drop(
        self, drop_target, value, x, y, terminal: Vte.Terminal, terminal_id: int
    ) -> bool:
        """Handle file drop on SSH terminal to initiate upload via file manager."""
        try:
            files = value.get_files()
            if not files:
                return False

            # Get session info for this terminal
            info = self.registry.get_terminal_info(terminal_id)
            if not info:
                self.logger.warning(f"No terminal info for ID {terminal_id}")
                return False

            session = info.get("identifier")

            # Check if terminal is in SSH session (either session-based or manual SSH)
            ssh_target = self.manual_ssh_tracker.get_ssh_target(terminal_id)
            if not ssh_target and not (
                isinstance(session, SessionItem) and session.is_ssh()
            ):
                self.logger.info("Drop target is not an SSH session, ignoring")
                return False

            # Get file paths
            local_paths = []
            for file in files:
                path = file.get_path()
                if path:
                    local_paths.append(path)

            if not local_paths:
                return False

            # Signal to show upload confirmation dialog
            # This will be handled by the window to show the file manager dialog
            self.logger.info(
                f"Files dropped on SSH terminal. Requesting upload dialog for {len(local_paths)} files."
            )

            # Emit signal to notify the window about the file drop
            GLib.idle_add(
                self._emit_ssh_file_drop_signal,
                terminal_id,
                local_paths,
                session,
                ssh_target,
            )
            return True

        except Exception as e:
            self.logger.error(f"Error handling file drop for SSH: {e}")
            return False

    def _emit_ssh_file_drop_signal(
        self, terminal_id: int, local_paths: list, session, ssh_target: str
    ):
        """Emit signal to notify about SSH file drop (runs on main thread)."""
        # Store the dropped files info for the window to pick up
        self._pending_ssh_upload = {
            "terminal_id": terminal_id,
            "local_paths": local_paths,
            "session": session,
            "ssh_target": ssh_target,
        }
        # Notify via the terminal-focus-changed signal mechanism
        # The window can check for pending uploads when handling focus
        if hasattr(self, "_ssh_file_drop_callback") and self._ssh_file_drop_callback:
            self._ssh_file_drop_callback(terminal_id, local_paths, session, ssh_target)
        return False

    def set_ssh_file_drop_callback(self, callback):
        """Set callback for SSH file drop events."""
        self._ssh_file_drop_callback = callback

    def _setup_terminal_events(
        self,
        terminal: Vte.Terminal,
        identifier: Union[str, SessionItem],
        terminal_id: int,
    ) -> None:
        try:
            terminal.zashterminal_handler_ids = []
            terminal.zashterminal_controllers = []

            handler_id = terminal.connect(
                "child-exited", self._on_child_exited, identifier, terminal_id
            )
            terminal.zashterminal_handler_ids.append(handler_id)

            handler_id = terminal.connect("eof", self._on_eof, identifier, terminal_id)
            terminal.zashterminal_handler_ids.append(handler_id)

            handler_id = terminal.connect(
                "notify::current-directory-uri", self._on_directory_uri_changed
            )
            terminal.zashterminal_handler_ids.append(handler_id)

            handler_id = terminal.connect(
                "contents-changed",
                self._on_terminal_contents_changed_for_gateway_auth,
                terminal_id,
            )
            terminal.zashterminal_handler_ids.append(handler_id)

            handler_id = terminal.connect(
                "selection-changed",
                self._on_terminal_selection_changed,
                terminal_id,
            )
            terminal.zashterminal_handler_ids.append(handler_id)

            self.manual_ssh_tracker.track(terminal_id, terminal)

            focus_controller = Gtk.EventControllerFocus()
            focus_controller.connect(
                "enter", self._on_terminal_focus_in, terminal, terminal_id
            )
            terminal.add_controller(focus_controller)
            terminal.zashterminal_controllers.append(focus_controller)

            click_controller = Gtk.GestureClick()
            click_controller.set_button(1)
            click_controller.set_propagation_phase(Gtk.PropagationPhase.BUBBLE)
            click_controller.connect(
                "pressed", self._on_terminal_clicked, terminal, terminal_id
            )
            terminal.add_controller(click_controller)
            terminal.zashterminal_controllers.append(click_controller)

            right_click_controller = Gtk.GestureClick()
            right_click_controller.set_button(3)
            right_click_controller.set_propagation_phase(Gtk.PropagationPhase.BUBBLE)
            right_click_controller.connect(
                "pressed", self._on_terminal_right_clicked, terminal, terminal_id
            )
            terminal.add_controller(right_click_controller)
            terminal.zashterminal_controllers.append(right_click_controller)

            # Key event controller for command detection via screen scraping
            # Captures Enter key press to read the current command line from VTE
            key_controller = Gtk.EventControllerKey()
            key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            key_controller.connect(
                "key-pressed",
                self._on_terminal_key_pressed_for_detection,
                terminal,
                terminal_id,
            )
            terminal.add_controller(key_controller)
            terminal.zashterminal_controllers.append(key_controller)

            terminal.terminal_id = terminal_id
        except Exception as e:
            self.logger.error(
                f"Failed to configure terminal events for ID {terminal_id}: {e}"
            )

    def _setup_context_menu(self, terminal: Vte.Terminal) -> None:
        try:
            menu_model = _create_terminal_menu(
                terminal, settings_manager=self.settings_manager
            )
            terminal.set_context_menu_model(menu_model)
        except Exception as e:
            self.logger.error(f"Context menu setup failed: {e}")

    def _update_context_menu_with_url(
        self, terminal: Vte.Terminal, x: float, y: float
    ) -> None:
        try:
            menu_model = _create_terminal_menu(
                terminal, x, y, settings_manager=self.settings_manager
            )
            terminal.set_context_menu_model(menu_model)
        except Exception as e:
            self.logger.error(f"Context menu URL update failed: {e}")

    def _on_terminal_focus_in(self, _controller, terminal, terminal_id):
        try:
            self.registry.update_terminal_status(terminal_id, "focused")
            if self.on_terminal_focus_changed:
                self.on_terminal_focus_changed(terminal, False)
        except Exception as e:
            self.logger.error(f"Terminal focus in handling failed: {e}")

    def _on_child_exited(
        self,
        terminal: Vte.Terminal,
        child_status: int,
        identifier: Union[str, SessionItem],
        terminal_id: int,
    ) -> None:
        """Handle terminal child process exit."""
        if not self.lifecycle_manager.mark_terminal_closing(terminal_id):
            return

        try:
            # Clean up connection monitor and retry flag
            self._cleanup_connection_monitor(terminal)
            terminal._retry_in_progress = False

            terminal_info = self.registry.get_terminal_info(terminal_id)
            if not terminal_info:
                self.lifecycle_manager.unmark_terminal_closing(terminal_id)
                return

            terminal_name = (
                identifier.name if isinstance(identifier, SessionItem) else identifier
            )

            if terminal_id in self._pending_kill_timers:
                GLib.source_remove(self._pending_kill_timers.pop(terminal_id))

            closed_by_user = getattr(terminal, "_closed_by_user", False)
            auto_reconnect_active = getattr(terminal, "_auto_reconnect_active", False)

            # Handle SSH/SFTP failure
            is_ssh = terminal_info.get("type") in ["ssh", "sftp"]
            ssh_failed = is_ssh and child_status != 0 and not closed_by_user

            if ssh_failed:
                self.lifecycle_manager.transition_state(
                    terminal_id, TerminalState.SPAWN_FAILED
                )
                self.logger.warning(
                    f"SSH failed for '{terminal_name}' (status: {child_status})"
                )

                # Stop auto-reconnect on auth errors
                is_auth_error = self._check_ssh_auth_error(terminal, child_status)
                if is_auth_error and auto_reconnect_active:
                    self.cancel_auto_reconnect(terminal)
                    terminal.feed(
                        b"\r\n\x1b[31m[Auth error - auto-reconnect stopped]\x1b[0m\r\n"
                    )

                # Show banner unless auto-reconnect handles it
                if auto_reconnect_active and not is_auth_error:
                    self.lifecycle_manager.unmark_terminal_closing(terminal_id)
                else:
                    GLib.idle_add(
                        self._show_ssh_connection_error_dialog,
                        terminal_name,
                        identifier,
                        terminal,
                        terminal_id,
                        child_status,
                    )
            else:
                # Normal/successful exit
                # Hide banner if exists (connection was successful and user closed it)
                if is_ssh and child_status == 0 and self.tab_manager:
                    self.tab_manager.hide_error_banner_for_terminal(terminal)

                if not self.lifecycle_manager.transition_state(
                    terminal_id, TerminalState.EXITED
                ):
                    self.lifecycle_manager.unmark_terminal_closing(terminal_id)
                    return
                self.logger.info(
                    f"Terminal '{terminal_name}' exited (status: {child_status})"
                )
                log_terminal_event("exited", terminal_name, f"status {child_status}")
                GLib.idle_add(
                    self._cleanup_terminal_ui,
                    terminal,
                    terminal_id,
                    child_status,
                    identifier,
                )

        except Exception as e:
            self.logger.error(f"Child exit handling failed: {e}")
            self.lifecycle_manager.unmark_terminal_closing(terminal_id)

    def _on_eof(
        self,
        terminal: Vte.Terminal,
        identifier: Union[str, SessionItem],
        terminal_id: int,
    ) -> None:
        self._on_child_exited(terminal, 0, identifier, terminal_id)

    def _cleanup_terminal_ui(
        self, terminal: Vte.Terminal, terminal_id: int, child_status: int, identifier
    ) -> bool:
        # Safety check: Don't cleanup if auto-reconnect is active
        if self.is_auto_reconnect_active(terminal):
            self.logger.warning(
                f"[CLEANUP_UI] Blocked cleanup for terminal {terminal_id} - auto-reconnect is active"
            )
            return False

        try:
            if self.terminal_exit_handler:
                self.terminal_exit_handler(terminal, child_status, identifier)
            if self.tab_manager:
                self.tab_manager._on_terminal_process_exited(
                    terminal, child_status, identifier
                )
            else:
                self._cleanup_terminal(terminal, terminal_id)
        except Exception as e:
            self.logger.error(f"Terminal UI cleanup failed: {e}")
        finally:
            self.lifecycle_manager.unmark_terminal_closing(terminal_id)
        return False

    def _cleanup_terminal(self, terminal: Vte.Terminal, terminal_id: int) -> None:
        # Safety check: Don't cleanup terminal if auto-reconnect is active
        if self.is_auto_reconnect_active(terminal):
            self.logger.warning(
                f"[CLEANUP] Blocked cleanup for terminal {terminal_id} - auto-reconnect is active"
            )
            return

        with self._cleanup_lock:
            if not self.registry.get_terminal_info(terminal_id):
                return
            terminal_info = self.registry.get_terminal_info(terminal_id)

            # Clean up highlight proxy FIRST to stop GLib watches
            self._cleanup_highlight_proxy(terminal_id)

            pid = terminal_info.get("process_id")
            if pid:
                self.spawner.process_tracker.unregister_process(pid)
            identifier = terminal_info.get("identifier", "Unknown")
            terminal_name = (
                identifier
                if isinstance(identifier, str)
                else getattr(identifier, "name", "Unknown")
            )
            self.logger.info(
                f"Cleaning up resources for terminal '{terminal_name}' (ID: {terminal_id})"
            )
            self.osc7_tracker.untrack_terminal(terminal)
            self.manual_ssh_tracker.untrack(terminal_id)
            self._balabit_gateway_pending_auth.pop(terminal_id, None)
            self._balabit_gateway_prompt_shown.discard(terminal_id)
            self._balabit_gateway_prompt_submitted.discard(terminal_id)
            if terminal_id in self._selection_copy_timers:
                GLib.source_remove(self._selection_copy_timers.pop(terminal_id))

            if hasattr(terminal, "zashterminal_handler_ids"):
                for handler_id in terminal.zashterminal_handler_ids:
                    if GObject.signal_handler_is_connected(terminal, handler_id):
                        terminal.disconnect(handler_id)
                terminal.zashterminal_handler_ids.clear()

            if hasattr(terminal, "zashterminal_controllers"):
                for controller in terminal.zashterminal_controllers:
                    terminal.remove_controller(controller)
                terminal.zashterminal_controllers.clear()

            if hasattr(terminal, "_osc8_hovered_uri"):
                try:
                    delattr(terminal, "_osc8_hovered_uri")
                except Exception as e:
                    self.logger.debug(f"Could not delete _osc8_hovered_uri attr: {e}")

            if hasattr(terminal, "_closed_by_user"):
                try:
                    delattr(terminal, "_closed_by_user")
                except Exception as e:
                    self.logger.debug(f"Could not delete _closed_by_user attr: {e}")
            if self.registry.unregister_terminal(terminal_id):
                self._stats["terminals_closed"] += 1
                log_terminal_event(
                    "removed", terminal_name, "terminal resources cleaned"
                )
            if terminal_id in self._pending_kill_timers:
                GLib.source_remove(self._pending_kill_timers.pop(terminal_id))

    def _on_spawn_callback(
        self,
        terminal: Vte.Terminal,
        pid: int,
        error: Optional[GLib.Error],
        user_data: Any,
    ) -> None:
        """
        Called when terminal spawn completes.

        For SSH: spawn success just means process started.
        We monitor process status to detect actual connection success.
        """
        try:
            final_user_data = (
                user_data[0] if isinstance(user_data, tuple) else user_data
            )
            user_data_tuple = final_user_data.get("original_user_data")
            terminal_id, user_data = user_data_tuple

            if error:
                self.logger.error(
                    f"Spawn failed for terminal {terminal_id}: {error.message}"
                )
                self.registry.update_terminal_status(terminal_id, "spawn_failed")
                return

            self.registry.update_terminal_process(terminal_id, pid)

            # For retry/auto-reconnect: wait for process exit to determine success/failure
            # If process exits quickly (< 3s), it failed. If still running, it's connected.
            has_banner = self.tab_manager and self.tab_manager.has_error_banner(
                terminal
            )
            is_auto_reconnect = getattr(terminal, "_auto_reconnect_active", False)
            is_retry = getattr(terminal, "_retry_in_progress", False)

            if has_banner or is_auto_reconnect or is_retry:
                self._monitor_connection_status(terminal, terminal_id, pid)

            # Handle execute command
            if (
                isinstance(user_data, dict)
                and user_data.get("execute_command")
                and pid > 0
            ):
                self._schedule_execute_command(
                    terminal,
                    user_data["execute_command"],
                    user_data.get("close_after_execute", False),
                )

        except Exception as e:
            self.logger.error(f"Spawn callback failed: {e}")

    def _ensure_process_terminated(
        self, pid: int, terminal_name: str, terminal_id: int
    ) -> bool:
        try:
            self._pending_kill_timers.pop(terminal_id, None)
            os.kill(pid, 0)
            self.logger.warning(
                f"Process {pid} ('{terminal_name}') did not exit gracefully. Sending SIGKILL."
            )
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception as e:
            self.logger.error(f"Error during final check for PID {pid}: {e}")
        return False

    def remove_terminal(
        self, terminal: Vte.Terminal, force_kill_group: bool = False
    ) -> bool:
        # Cancel auto-reconnect FIRST before any other cleanup
        # This ensures we stop reconnection attempts immediately when closing
        if self.is_auto_reconnect_active(terminal):
            self.cancel_auto_reconnect(terminal)

        with self._cleanup_lock:
            terminal_id = getattr(terminal, "terminal_id", None)
            if terminal_id is None:
                return False
            info = self.registry.get_terminal_info(terminal_id)
            if not info:
                return False

            identifier = info.get("identifier", "Unknown")

            # Mark terminal as closed by user
            try:
                setattr(terminal, "_closed_by_user", True)
            except Exception:
                pass

            # If terminal already exited or spawn failed, just do UI cleanup
            if info.get("status") in [
                TerminalState.EXITED.value,
                TerminalState.SPAWN_FAILED.value,
            ]:
                # Need full UI cleanup to close the tab
                GLib.idle_add(
                    self._cleanup_terminal_ui,
                    terminal,
                    terminal_id,
                    0,  # exit status
                    identifier,
                )
                return True

            pid = info.get("process_id")
            if not pid or pid == -1:
                # No process to kill, just do UI cleanup
                GLib.idle_add(
                    self._cleanup_terminal_ui,
                    terminal,
                    terminal_id,
                    0,  # exit status
                    identifier,
                )
                return True

            terminal_name = (
                identifier.name
                if isinstance(identifier, SessionItem)
                else str(identifier)
            )

            try:
                target_id = os.getpgid(pid) if force_kill_group else pid
                os.kill(target_id, signal.SIGHUP)
            except (ProcessLookupError, PermissionError) as e:
                self.logger.warning(
                    f"Could not send signal to PID {pid}, likely already exited: {e}"
                )
                # Process already exited, do UI cleanup
                GLib.idle_add(
                    self._cleanup_terminal_ui,
                    terminal,
                    terminal_id,
                    0,  # exit status
                    identifier,
                )
                return True

            timeout_id = GLib.timeout_add(
                5000, self._ensure_process_terminated, pid, terminal_name, terminal_id
            )
            self._pending_kill_timers[terminal_id] = timeout_id
            return True

    def copy_selection(self, terminal: Vte.Terminal):
        self._copy_terminal_selection_to_clipboard(terminal)

    def _on_terminal_selection_changed(
        self, terminal: Vte.Terminal, terminal_id: int
    ) -> None:
        if terminal_id in self._selection_copy_timers:
            GLib.source_remove(self._selection_copy_timers.pop(terminal_id))
        self._selection_copy_timers[terminal_id] = GLib.timeout_add(
            80, self._copy_selection_after_change, terminal, terminal_id
        )

    def _copy_selection_after_change(
        self, terminal: Vte.Terminal, terminal_id: int
    ) -> bool:
        self._selection_copy_timers.pop(terminal_id, None)
        try:
            self._copy_terminal_selection_to_clipboard(terminal)
        except Exception as e:
            self.logger.debug(
                f"Selection auto-copy failed for terminal {terminal_id}: {e}"
            )
        return GLib.SOURCE_REMOVE

    def _copy_terminal_selection_to_clipboard(self, terminal: Vte.Terminal) -> bool:
        if terminal.get_has_selection():
            terminal.copy_clipboard_format(Vte.Format.TEXT)
            return True

        if not hasattr(terminal, "get_text_selected"):
            return False

        selected_text = terminal.get_text_selected(Vte.Format.TEXT)
        if not selected_text:
            return False

        clipboard = (
            terminal.get_clipboard()
            if hasattr(terminal, "get_clipboard")
            else terminal.get_display().get_clipboard()
        )
        clipboard.set(selected_text)
        return True

    def paste_clipboard(self, terminal: Vte.Terminal):
        terminal.paste_clipboard()

    def select_all(self, terminal: Vte.Terminal):
        terminal.select_all()

    def clear_terminal(self, terminal: Vte.Terminal):
        try:
            terminal.reset(True, True)

            def _send_newline():
                try:
                    if hasattr(terminal, "feed_child_binary"):
                        terminal.feed_child_binary(b"\n")
                    else:
                        terminal.feed_child("\n", -1)
                except Exception as exc:
                    self.logger.debug(f"Failed to send newline after clear: {exc}")
                return GLib.SOURCE_REMOVE

            GLib.timeout_add(120, _send_newline)
            terminal_id = getattr(terminal, "terminal_id", None)
            terminal_name = "terminal"
            if terminal_id is not None:
                info = self.registry.get_terminal_info(terminal_id) or {}
                terminal_name = (
                    info.get("title")
                    or info.get("session_name")
                    or info.get("type")
                    or f"terminal-{terminal_id}"
                )
                log_terminal_event(
                    "cleared", terminal_name, "screen and scrollback cleared"
                )
            self.logger.info(f"Cleared terminal output for {terminal_name}")
        except Exception as e:
            self.logger.error(f"Failed to clear terminal output: {e}")

    def cleanup_all_terminals(self):
        """
        Force closes all terminals managed by this window instance.
        Corrected to only kill processes owned by this window, avoiding global app shutdown.
        """
        if self._process_check_timer_id:
            GLib.source_remove(self._process_check_timer_id)
            self._process_check_timer_id = None

        # Clean up all highlight proxies
        for terminal_id in list(self._highlight_proxies.keys()):
            self._cleanup_highlight_proxy(terminal_id)

        # KILL ONLY LOCAL PROCESSES BELONGING TO THIS WINDOW
        spawner = _get_spawner()
        all_ids = self.registry.get_all_terminal_ids()

        count_killed = 0
        for t_id in all_ids:
            info = self.registry.get_terminal_info(t_id)
            if info and info.get("process_id"):
                pid = info["process_id"]
                # Use the new targeted kill method
                spawner.process_tracker.terminate_process(pid)
                count_killed += 1

        self.logger.info(
            f"cleanup_all_terminals: Terminated {count_killed} processes for this window."
        )

