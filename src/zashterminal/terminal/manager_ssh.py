# zashterminal/terminal/manager_ssh.py
"""SSH reconnect, connection monitoring, and session lifecycle helpers."""

import os
import signal
import time
from datetime import datetime
from typing import Any, Dict, Optional, Union

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import Adw, GLib, Gtk, Vte

from ..sessions.models import SessionItem
from ..utils.logger import log_terminal_event
from ..utils.translation_utils import _
from .registry import TerminalState


class TerminalSSHLifecycleMixin:
    def _check_ssh_auth_error(self, terminal: Vte.Terminal, child_status: int) -> bool:
        """Check if SSH failure is due to authentication error."""
        import os as os_module

        # Decode exit code
        if os_module.WIFEXITED(child_status):
            exit_code = os_module.WEXITSTATUS(child_status)
        else:
            exit_code = child_status

        # Exit codes 5, 6 are common SSH auth failure codes
        if exit_code in (5, 6):
            return True

        # Check terminal text for auth patterns
        try:
            col_count = terminal.get_column_count()
            row_count = terminal.get_row_count()
            start_row = max(0, row_count - 20)
            result = terminal.get_text_range_format(
                0,
                start_row,
                0,
                row_count - 1,
                col_count - 1,
            )
            if result and len(result) > 0 and result[0]:
                text_lower = result[0].lower()
                auth_patterns = [
                    "permission denied",
                    "authentication failed",
                    "incorrect password",
                    "invalid password",
                    "too many authentication failures",
                ]
                for pattern in auth_patterns:
                    if pattern in text_lower:
                        return True
        except Exception:
            pass

        return False

    def _show_ssh_connection_error_dialog(
        self, session_name, identifier, terminal, terminal_id, child_status
    ) -> None:
        """
        Show SSH connection error using non-blocking inline banner.

        Uses an inline banner above the terminal instead of a modal dialog,
        allowing users to continue using other tabs while deciding how to handle
        the connection failure.
        """
        # Safety check: Verify terminal widget is still valid
        # This is especially important on XFCE where widget destruction timing
        # can differ from Wayland compositors
        try:
            if terminal is None or not terminal.get_realized():
                self.logger.debug(
                    f"Skipping error dialog - terminal not realized for '{session_name}'"
                )
                self.lifecycle_manager.unmark_terminal_closing(terminal_id)
                return
            if terminal.get_parent() is None:
                self.logger.debug(
                    f"Skipping error dialog - terminal orphaned for '{session_name}'"
                )
                self.lifecycle_manager.unmark_terminal_closing(terminal_id)
                return
        except Exception as e:
            self.logger.debug(f"Terminal widget check failed: {e}")
            self.lifecycle_manager.unmark_terminal_closing(terminal_id)
            return

        # Skip if a retry is in progress - avoid showing banner during retry
        if getattr(terminal, "_retry_in_progress", False):
            self.logger.debug(
                f"Skipping error banner - retry in progress for '{session_name}'"
            )
            self.lifecycle_manager.unmark_terminal_closing(terminal_id)
            return

        # Skip if banner is already showing
        if self.tab_manager and self.tab_manager.has_error_banner(terminal):
            self.logger.debug(
                f"Skipping error banner - banner already showing for '{session_name}'"
            )
            self.lifecycle_manager.unmark_terminal_closing(terminal_id)
            return

        try:
            # Decode the wait status to get the actual exit code
            import os as os_module

            from ..ui.ssh_dialogs import get_error_info

            if os_module.WIFEXITED(child_status):
                exit_code = os_module.WEXITSTATUS(child_status)
            elif os_module.WIFSIGNALED(child_status):
                exit_code = 128 + os_module.WTERMSIG(child_status)
            else:
                exit_code = child_status

            self.logger.debug(
                f"SSH error: raw status={child_status}, decoded exit_code={exit_code}"
            )

            # Extract terminal text for error analysis
            terminal_text = None
            try:
                col_count = terminal.get_column_count()
                row_count = terminal.get_row_count()
                start_row = max(0, row_count - 50)
                # Use Vte.Format.TEXT constant
                from gi.repository import Vte as VteLib

                result = terminal.get_text_range_format(
                    VteLib.Format.TEXT,
                    start_row,
                    0,
                    row_count - 1,
                    col_count - 1,
                )
                if result and len(result) > 0 and result[0]:
                    terminal_text = result[0]
            except Exception as text_err:
                self.logger.debug(f"Could not extract terminal text: {text_err}")

            # Get error description and type
            error_type, _, error_description = get_error_info(exit_code, terminal_text)

            # Check if this is an authentication error
            is_auth_error = error_type in (
                "auth_failed",
                "auth_multi_failed",
                "key_rejected",
                "key_format_error",
                "key_permissions",
            )

            # Check if this is a host key error
            is_host_key_error = error_type in (
                "host_key_failed",
                "host_key_changed",
            )

            # Get session for retry functionality
            session = identifier if isinstance(identifier, SessionItem) else None

            # Show inline banner (non-blocking)
            if self.tab_manager:
                banner_shown = self.tab_manager.show_error_banner_for_terminal(
                    terminal=terminal,
                    session_name=session_name,
                    error_message=error_description,
                    session=session,
                    is_auth_error=is_auth_error,
                    is_host_key_error=is_host_key_error,
                )

                if banner_shown:
                    self.logger.info(
                        f"Showed inline error banner for '{session_name}' (auth_error={is_auth_error})"
                    )
                else:
                    self.logger.warning(
                        f"Could not show inline banner for '{session_name}'"
                    )

            # Unmark terminal as closing - the banner will handle cleanup
            self.lifecycle_manager.unmark_terminal_closing(terminal_id)

        except Exception as e:
            self.logger.error(f"Failed to show SSH error: {e}")
            import traceback

            self.logger.debug(traceback.format_exc())
            # In case of error, still unmark so the terminal stays open
            self.lifecycle_manager.unmark_terminal_closing(terminal_id)

    def _retry_ssh_connection_with_timeout(
        self, session: SessionItem, timeout: int
    ) -> bool:
        """
        Retry SSH connection with extended timeout.
        Creates a new tab (single retry mode).
        """
        try:
            original_timeout = self.settings_manager.get("ssh_connect_timeout", 30)
            self.settings_manager.set(
                "ssh_connect_timeout", timeout, save_immediately=False
            )

            if self.tab_manager:
                self.tab_manager.create_ssh_tab(session)

            def restore_timeout():
                self.settings_manager.set(
                    "ssh_connect_timeout", original_timeout, save_immediately=False
                )
                return False

            GLib.timeout_add(1000, restore_timeout)

            self.logger.info(
                f"Retried SSH connection to '{session.name}' with {timeout}s timeout"
            )
        except Exception as e:
            self.logger.error(f"Failed to retry SSH connection: {e}")

        return False

    def start_auto_reconnect(
        self,
        terminal: Vte.Terminal,
        terminal_id: int,
        session: SessionItem,
        duration_mins: int,
        interval_secs: int,
        timeout_secs: int,
    ) -> None:
        """
        Start automatic reconnection attempts for a failed SSH terminal.

        This keeps the same terminal tab and re-spawns SSH sessions in it.
        Progress is displayed inline in the terminal itself.
        """
        import time
        from datetime import datetime

        # Store auto-reconnect state on the terminal
        terminal._auto_reconnect_active = True
        terminal._auto_reconnect_cancelled = False
        terminal._auto_reconnect_timer_id = None

        end_time = time.time() + (duration_mins * 60)
        max_attempts = (duration_mins * 60) // interval_secs

        state = {
            "attempt": 0,
        }

        def get_timestamp() -> str:
            """Get current timestamp string."""
            return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        def display_status(message: str, is_error: bool = False) -> None:
            """Display status message in the terminal with timestamp."""
            if not terminal.get_realized():
                return
            color = "\x1b[33m" if not is_error else "\x1b[31m"  # Yellow or Red
            reset = "\x1b[0m"
            dim = "\x1b[2m"
            timestamp = get_timestamp()
            terminal.feed(
                f"\r\n{dim}[{timestamp}]{reset} {color}[Auto-Reconnect] {message}{reset}\r\n".encode(
                    "utf-8"
                )
            )

        def show_connection_options() -> None:
            """Show connection error dialog with options when auto-reconnect exhausted."""
            terminal._auto_reconnect_active = False
            terminal._auto_reconnect_timer_id = None

            # Show the connection error dialog to give user options
            def _present_ssh_error():
                self._show_ssh_connection_error_dialog(
                    session.name,
                    session,
                    terminal,
                    terminal_id,
                    1,  # Non-zero status to indicate failure
                )
                return GLib.SOURCE_REMOVE

            GLib.idle_add(_present_ssh_error)

        def _perform_reconnect_attempt() -> None:
            """Run one reconnect attempt; next tick is scheduled separately."""
            terminal._auto_reconnect_timer_id = None

            if getattr(terminal, "_auto_reconnect_cancelled", False):
                display_status(_("Cancelled by user."))
                terminal._auto_reconnect_active = False
                return

            now = time.time()
            if now >= end_time:
                display_status(_("Time limit reached. Giving up."), is_error=True)
                display_status(_("Showing connection options..."))
                show_connection_options()
                return

            state["attempt"] += 1
            remaining = int(end_time - now)
            remaining_mins = remaining // 60
            remaining_secs = remaining % 60

            display_status(
                _("Attempt {n}/{max} - Time remaining: {mins}m {secs}s").format(
                    n=state["attempt"],
                    max=max_attempts,
                    mins=remaining_mins,
                    secs=remaining_secs,
                )
            )

            try:
                original_timeout = self.settings_manager.get("ssh_connect_timeout", 30)
                self.settings_manager.set(
                    "ssh_connect_timeout", timeout_secs, save_immediately=False
                )
                self._respawn_ssh_in_terminal(terminal, terminal_id, session)

                def _restore_timeout():
                    self.settings_manager.set(
                        "ssh_connect_timeout",
                        original_timeout,
                        save_immediately=False,
                    )
                    return GLib.SOURCE_REMOVE

                GLib.timeout_add(1000, _restore_timeout)
            except Exception as e:
                self.logger.error(f"Auto-reconnect spawn error: {e}")
                display_status(
                    _("Spawn error: {error}").format(error=str(e)), is_error=True
                )

            if now + interval_secs < end_time:
                timer_id = GLib.timeout_add_seconds(
                    interval_secs, _attempt_reconnect_idle
                )
                terminal._auto_reconnect_timer_id = timer_id
            else:
                display_status(_("Maximum attempts reached."), is_error=True)
                display_status(_("Showing connection options..."))
                show_connection_options()

        def _attempt_reconnect_idle() -> bool:
            _perform_reconnect_attempt()
            return GLib.SOURCE_REMOVE

        display_status(
            _(
                "Starting auto-reconnect: {attempts} attempts over {mins} minute(s), every {secs}s"
            ).format(
                attempts=max_attempts,
                mins=duration_mins,
                secs=interval_secs,
            )
        )
        display_status(_("Close this tab to cancel."))

        timer_id = GLib.timeout_add(500, _attempt_reconnect_idle)
        terminal._auto_reconnect_timer_id = timer_id

    def _respawn_ssh_in_terminal(
        self,
        terminal: Vte.Terminal,
        terminal_id: int,
        session: SessionItem,
    ) -> None:
        """
        Re-spawn an SSH session in an existing terminal.
        This is used for auto-reconnect to avoid creating new tabs.
        """
        try:
            # Update registry to show we're spawning
            self.registry.update_terminal_status(terminal_id, "spawning")

            # Check if we should use highlighted SSH
            highlight_manager = self._get_highlight_manager()
            output_highlighting_enabled = highlight_manager.enabled_for_ssh
            if session.output_highlighting is not None:
                output_highlighting_enabled = session.output_highlighting

            should_spawn_highlighted = output_highlighting_enabled

            user_data_for_spawn = (terminal_id, session)

            if should_spawn_highlighted:
                proxy = self.spawner.spawn_highlighted_ssh_session(
                    terminal,
                    session,
                    callback=self._on_spawn_callback,
                    user_data=user_data_for_spawn,
                    terminal_id=terminal_id,
                )
                if proxy:
                    self._highlight_proxies[terminal_id] = proxy
                else:
                    # Fallback to standard
                    self.spawner.spawn_ssh_session(
                        terminal,
                        session,
                        callback=self._on_spawn_callback,
                        user_data=user_data_for_spawn,
                    )
            else:
                self.spawner.spawn_ssh_session(
                    terminal,
                    session,
                    callback=self._on_spawn_callback,
                    user_data=user_data_for_spawn,
                )

            self.logger.info(f"Re-spawned SSH session in terminal {terminal_id}")

        except Exception as e:
            self.logger.error(f"Failed to re-spawn SSH: {e}")
            raise

    def _retry_ssh_in_same_terminal(
        self,
        terminal: Vte.Terminal,
        terminal_id: int,
        session: SessionItem,
        timeout: int = 30,
    ) -> bool:
        """
        Retry SSH connection in the same terminal (single retry mode).

        Unlike auto-reconnect, this does a single retry with extended timeout
        and shows the connection attempt in the same terminal.

        Args:
            terminal: The terminal to retry in.
            terminal_id: Terminal ID.
            session: Session to connect.
            timeout: Connection timeout in seconds.

        Returns:
            True if retry was initiated, False otherwise.
        """
        # Prevent multiple simultaneous retries
        if getattr(terminal, "_retry_in_progress", False):
            self.logger.warning(f"Retry already in progress for terminal {terminal_id}")
            return False

        try:
            # Mark retry in progress - will be cleared by _on_child_exited or _on_connection_success
            terminal._retry_in_progress = True

            # Display retry message
            terminal.feed(
                f"\r\n\x1b[33m[Retry] Attempting reconnection with {timeout}s timeout...\x1b[0m\r\n".encode(
                    "utf-8"
                )
            )

            # Set temporary timeout
            original_timeout = self.settings_manager.get("ssh_connect_timeout", 30)
            self.settings_manager.set(
                "ssh_connect_timeout", timeout, save_immediately=False
            )

            # Re-spawn in the same terminal
            self._respawn_ssh_in_terminal(terminal, terminal_id, session)

            # Restore original timeout after a delay
            def restore_timeout():
                self.settings_manager.set(
                    "ssh_connect_timeout", original_timeout, save_immediately=False
                )
                return False

            GLib.timeout_add(1000, restore_timeout)

            self.logger.info(
                f"Retrying SSH connection to '{session.name}' with {timeout}s timeout in same terminal"
            )
            return True

        except Exception as e:
            terminal._retry_in_progress = False
            self.logger.error(f"Failed to retry SSH in same terminal: {e}")
            terminal.feed(f"\r\n\x1b[31m[Retry] Failed: {e}\x1b[0m\r\n".encode("utf-8"))
            return False

    def cancel_auto_reconnect(self, terminal: Vte.Terminal) -> None:
        """Cancel auto-reconnect for a terminal, including any pending timers."""
        terminal._auto_reconnect_cancelled = True
        terminal._auto_reconnect_active = False

        # Cancel pending timer if exists
        timer_id = getattr(terminal, "_auto_reconnect_timer_id", None)
        if timer_id is not None:
            try:
                GLib.source_remove(timer_id)
            except Exception:
                pass
            terminal._auto_reconnect_timer_id = None

        self.logger.info(
            f"Auto-reconnect cancelled for terminal {getattr(terminal, 'terminal_id', 'N/A')}"
        )

    def is_auto_reconnect_active(self, terminal: Vte.Terminal) -> bool:
        """Check if auto-reconnect is active for a terminal."""
        return getattr(terminal, "_auto_reconnect_active", False)

    def _monitor_connection_status(
        self, terminal: Vte.Terminal, terminal_id: int, pid: int
    ) -> None:
        """
        Monitor SSH connection status after spawn.

        SSH connection is considered successful when:
        1. Process is still running after initial connect phase
        2. Terminal shows shell prompt in recent lines (not error messages)
        """
        import os as os_module

        terminal._monitoring_pid = pid
        terminal._connect_check_count = 0
        terminal._last_line_count = 0

        def check_connection():
            """Periodically check if SSH is truly connected."""
            # Verify we're still monitoring this process
            if getattr(terminal, "_monitoring_pid", None) != pid:
                return False

            terminal._connect_check_count = (
                getattr(terminal, "_connect_check_count", 0) + 1
            )

            # Check if process is alive
            try:
                os_module.kill(pid, 0)
                alive = True
            except OSError:
                alive = False

            if not alive:
                # Process died - child-exited handler will deal with it
                self._cleanup_connection_monitor(terminal)
                return False

            # Check ONLY the last few lines for connection indicators
            # This avoids false negatives from old error messages in the buffer
            try:
                col_count = terminal.get_column_count()
                row_count = terminal.get_row_count()

                # Only check the last 5 lines for recent activity
                start_row = max(0, row_count - 5)
                result = terminal.get_text_range_format(
                    0,
                    start_row,
                    0,
                    row_count - 1,
                    col_count - 1,
                )
                if result and result[0]:
                    recent_text = result[0].lower().strip()

                    # Skip if it's just our auto-reconnect messages
                    if "[auto-reconnect]" in recent_text:
                        if terminal._connect_check_count < 10:
                            return True

                    # Error patterns that indicate connection is still failing
                    error_patterns = [
                        "no route to host",
                        "connection refused",
                        "connection timed out",
                        "permission denied",
                        "authentication failed",
                        "host key verification failed",
                        "broken pipe",
                    ]

                    # Check if recent lines contain fresh errors
                    has_recent_error = any(p in recent_text for p in error_patterns)

                    # Success patterns - shell prompt indicators
                    # These are common prompt terminators that indicate a shell is ready
                    success_patterns = [
                        "$",  # bash/sh prompt
                        "#",  # root prompt
                        "❯",  # starship/modern prompts
                        "➜",  # oh-my-zsh
                        "›",  # fish
                        "last login:",  # SSH MOTD
                        "welcome to",  # MOTD
                    ]

                    has_prompt = any(p in recent_text for p in success_patterns)

                    # If we see a prompt in recent lines and NO recent error, we're connected
                    if has_prompt and not has_recent_error:
                        self.logger.info(f"SSH connected for terminal {terminal_id}")
                        self._on_connection_success(terminal)
                        return False

            except Exception:
                pass

            # Keep checking for up to 10 seconds
            if terminal._connect_check_count < 10:
                return True  # Continue checking

            # After 10 seconds, assume connected if process is still alive
            self.logger.info(
                f"SSH appears connected for terminal {terminal_id} (timeout)"
            )
            self._on_connection_success(terminal)
            return False

        # Check every second
        GLib.timeout_add(1000, check_connection)

    def _cleanup_connection_monitor(self, terminal: Vte.Terminal) -> None:
        """Clean up connection monitoring state."""
        for attr in ["_monitoring_pid", "_connect_check_count", "_last_line_count"]:
            if hasattr(terminal, attr):
                delattr(terminal, attr)

    def _on_connection_success(self, terminal: Vte.Terminal) -> None:
        """Handle successful SSH connection."""
        self._cleanup_connection_monitor(terminal)

        # Hide error banner
        if self.tab_manager:
            self.tab_manager.hide_error_banner_for_terminal(terminal)

        # Stop auto-reconnect
        if getattr(terminal, "_auto_reconnect_active", False):
            terminal._auto_reconnect_active = False
            timer_id = getattr(terminal, "_auto_reconnect_timer_id", None)
            if timer_id:
                try:
                    GLib.source_remove(timer_id)
                except Exception:
                    pass
                terminal._auto_reconnect_timer_id = None

        # Clear retry flag
        terminal._retry_in_progress = False

    def has_active_ssh_sessions(self) -> bool:
        for info in self.registry._terminals.values():
            if info.get("type") == "ssh" and info.get("status") == "running":
                return True
        return False

    def reconnect_all_for_session(self, session_name: str) -> int:
        """
        Reconnect all disconnected terminals for a given session.

        Args:
            session_name: Name of the session to reconnect terminals for.

        Returns:
            Number of terminals where reconnection was initiated.
        """
        terminal_ids = self.registry.get_terminals_for_session(session_name)
        reconnected = 0

        for terminal_id in terminal_ids:
            info = self.registry.get_terminal_info(terminal_id)
            if info and info.get("status") == "disconnected":
                session = info.get("identifier")
                if isinstance(session, SessionItem):
                    terminal = self.registry.get_terminal(terminal_id)
                    if terminal:
                        try:
                            self._respawn_ssh_in_terminal(
                                terminal, terminal_id, session
                            )
                            reconnected += 1
                            self.logger.info(
                                f"Initiated reconnection for terminal {terminal_id} "
                                f"(session: {session_name})"
                            )
                        except Exception as e:
                            self.logger.error(
                                f"Failed to reconnect terminal {terminal_id}: {e}"
                            )

        return reconnected

    def disconnect_all_for_session(self, session_name: str) -> int:
        """
        Gracefully disconnect all terminals for a session.

        This cancels any auto-reconnect and sends exit command to SSH.

        Args:
            session_name: Name of the session to disconnect.

        Returns:
            Number of terminals disconnected.
        """
        terminal_ids = self.registry.get_terminals_for_session(session_name)
        disconnected = 0

        for terminal_id in terminal_ids:
            terminal = self.registry.get_terminal(terminal_id)
            if terminal:
                # Cancel any active auto-reconnect
                self.cancel_auto_reconnect(terminal)

                # Send exit command to terminate SSH session gracefully
                try:
                    terminal.feed_child(b"exit\n")
                    disconnected += 1
                    self.logger.info(
                        f"Sent disconnect to terminal {terminal_id} "
                        f"(session: {session_name})"
                    )
                except Exception as e:
                    self.logger.error(
                        f"Failed to disconnect terminal {terminal_id}: {e}"
                    )

        return disconnected

    def get_session_connection_status(self, session_name: str) -> Dict[str, Any]:
        """
        Get aggregated connection status for all terminals of a session.

        Args:
            session_name: Name of the session.

        Returns:
            Dictionary with connection status summary.
        """
        terminal_ids = self.registry.get_terminals_for_session(session_name)

        status_counts = {
            "connected": 0,
            "disconnected": 0,
            "connecting": 0,
            "reconnecting": 0,
            "other": 0,
        }

        for terminal_id in terminal_ids:
            info = self.registry.get_terminal_info(terminal_id)
            if info:
                status = info.get("status", "unknown")
                if status in status_counts:
                    status_counts[status] += 1
                else:
                    status_counts["other"] += 1

        total = len(terminal_ids)

        # Determine overall status
        if total == 0:
            overall = "no_terminals"
        elif status_counts["connected"] == total:
            overall = "all_connected"
        elif status_counts["disconnected"] == total:
            overall = "all_disconnected"
        elif status_counts["connected"] > 0:
            overall = "partial"
        elif status_counts["connecting"] > 0 or status_counts["reconnecting"] > 0:
            overall = "connecting"
        else:
            overall = "unknown"

        return {
            "total_terminals": total,
            "status_counts": status_counts,
            "overall_status": overall,
        }
