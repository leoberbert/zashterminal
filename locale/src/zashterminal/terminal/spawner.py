# zashterminal/terminal/spawner.py

import fcntl
import os
import shlex
import shutil
import signal
import subprocess
import tempfile
import termios
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

import gi

gi.require_version("Vte", "3.91")
from gi.repository import GLib, Vte

if TYPE_CHECKING:
    from ..sessions.models import SessionItem
    from .highlighter import HighlightedTerminalProxy

from ..settings.manager import get_settings_manager
from ..utils.exceptions import SSHConnectionError, SSHKeyError, TerminalCreationError
from ..utils.logger import get_logger, log_error_with_context, log_terminal_event
from ..utils.platform import (
    get_command_builder,
    get_environment_manager,
    get_platform_info,
    has_command,
)
from ..utils.security import (
    validate_ssh_hostname,
    validate_ssh_key_file,
)
from ..utils.translation_utils import _
from ..utils.osc7 import OSC7_HOST_DETECTION_SNIPPET


class ProcessTracker:
    """Track spawned processes for proper cleanup."""

    def __init__(self):
        self.logger = get_logger("zashterminal.spawner.tracker")
        self._processes: Dict[int, Dict[str, Any]] = {}
        self._lock = threading.RLock()

    def register_process(self, pid: int, process_info: Dict[str, Any]) -> None:
        """Register a spawned process."""
        with self._lock:
            self._processes[pid] = {**process_info, "registered_at": time.time()}

    def unregister_process(self, pid: int) -> bool:
        """Unregister a process."""
        with self._lock:
            if pid in self._processes:
                process_info = self._processes.pop(pid)
                temp_dir_path = process_info.get("temp_dir_path")
                if temp_dir_path:
                    try:
                        shutil.rmtree(temp_dir_path)
                        self.logger.debug(
                            f"Cleaned up temp zshrc directory: {temp_dir_path}"
                        )
                    except Exception as e:
                        self.logger.error(
                            f"Failed to clean up temp zshrc directory {temp_dir_path}: {e}"
                        )
                return True
            return False

    def terminate_process(self, pid: int) -> None:
        """
        Terminate a specific process ID safely.
        Used by window managers to clean up only their own children.
        """
        with self._lock:
            if pid in self._processes:
                self.logger.info(f"Terminating specific process {pid}")
                try:
                    # Try graceful termination first
                    os.kill(pid, signal.SIGTERM)
                except (OSError, ProcessLookupError):
                    pass
                finally:
                    # Ensure cleanup happens immediately
                    self.unregister_process(pid)

    def terminate_all(self) -> None:
        """Terminate all tracked processes robustly on Linux."""
        with self._lock:
            pids_to_terminate = list(self._processes.keys())
            if not pids_to_terminate:
                return

            self.logger.info(f"Terminating {len(pids_to_terminate)} tracked processes.")

            # On Unix-like systems, try SIGTERM first, then SIGKILL.
            for pid in pids_to_terminate:
                try:
                    os.kill(pid, signal.SIGTERM)
                except (OSError, ProcessLookupError):
                    self.unregister_process(pid)

            time.sleep(0.2)

            remaining_pids = list(self._processes.keys())
            for pid in remaining_pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                    self.logger.warning(
                        f"Process {pid} did not respond to SIGTERM, sent SIGKILL."
                    )
                except (OSError, ProcessLookupError):
                    pass
                finally:
                    self.unregister_process(pid)


class ProcessSpawner:
    """Enhanced process spawner with comprehensive security and error handling."""

    def __init__(self):
        self.logger = get_logger("zashterminal.spawner")
        self.platform_info = get_platform_info()
        self.command_builder = get_command_builder()
        self.environment_manager = get_environment_manager()
        self.process_tracker = ProcessTracker()
        self.settings_manager = get_settings_manager()
        self._spawn_lock = threading.Lock()
        self.logger.info("Process spawner initialized on Linux")

    def _get_expected_terminal_size(
        self, terminal: Vte.Terminal
    ) -> Tuple[int, int]:
        """
        Get the expected terminal size (rows, cols) based on saved window dimensions.
        
        This helps avoid initial resize SIGWINCH by starting the PTY with
        the correct size that matches the window we will restore to.
        Falls back to terminal's current size or defaults if calculation fails.
        """
        try:
            # Get saved window dimensions
            window_width = self.settings_manager.get("window_width", 1200)
            window_height = self.settings_manager.get("window_height", 700)

            # Account for UI elements (headerbar ~47px, tabbar ~36px, padding ~20px)
            # These are approximate but help avoid the initial resize
            ui_overhead_height = 103  # headerbar + tabbar + margins
            ui_overhead_width = 20  # sidebar margin if visible

            available_width = max(400, window_width - ui_overhead_width)
            available_height = max(200, window_height - ui_overhead_height)

            # Get character dimensions from terminal's font
            char_width = terminal.get_char_width()
            char_height = terminal.get_char_height()

            if char_width > 0 and char_height > 0:
                cols = max(40, available_width // char_width)
                rows = max(10, available_height // char_height)
                self.logger.debug(
                    f"Calculated expected terminal size: {rows}x{cols} "
                    f"(window: {window_width}x{window_height}, "
                    f"char: {char_width}x{char_height})"
                )
                return (rows, cols)
        except Exception as e:
            self.logger.debug(f"Could not calculate expected terminal size: {e}")

        # Fallback to terminal's current size or defaults
        rows = terminal.get_row_count() or 24
        cols = terminal.get_column_count() or 80
        return (rows, cols)

    def _prepare_shell_environment(
        self, working_directory: Optional[str] = None
    ) -> Tuple[List[str], Dict[str, str], Optional[str]]:
        """
        Prepare the shell environment for local terminal spawning.

        This method handles:
        - User shell detection
        - VTE version environment variable
        - OSC7 integration for directory tracking (zsh via ZDOTDIR, bash via PROMPT_COMMAND)
        - Login shell configuration

        Args:
            working_directory: Optional directory to start the shell in.

        Returns:
            A tuple of (command_list, environment_dict, temp_dir_path).
            temp_dir_path is the path to the temporary ZDOTDIR for zsh, or None.
        """
        shell = Vte.get_user_shell()
        shell_basename = os.path.basename(shell)
        temp_dir_path: Optional[str] = None

        env = self.environment_manager.get_terminal_environment()
        # OSC7 integration for CWD tracking.
        # Wrapped in a function to avoid escape sequence interpretation issues
        # with bash extensions like ble.sh that can cause
        # `$'\E]7': command not found` errors.
        osc7_command = (
            f"__zashterminal_osc7() {{ {OSC7_HOST_DETECTION_SNIPPET} "
            'printf "\\033]7;file://%s%s\\007" "$ZASHTERMINAL_OSC7_HOST" "$PWD"; }; __zashterminal_osc7'
        )

        if shell_basename == "zsh":
            try:
                # Create a temporary directory that we will manage for cleanup
                temp_dir_path = tempfile.mkdtemp(prefix="zashterminal_zsh_")
                zshrc_path = os.path.join(temp_dir_path, ".zshrc")

                # This zshrc adds our hook, then sources the user's real .zshrc
                zshrc_content = (
                    f"_zashterminal_update_cwd() {{ {osc7_command}; }}\n"
                    'if [[ -z "$precmd_functions" ]]; then\n'
                    "  typeset -a precmd_functions\n"
                    "fi\n"
                    "precmd_functions+=(_zashterminal_update_cwd)\n"
                    'if [ -f "$HOME/.zshrc" ]; then . "$HOME/.zshrc"; fi\n'
                )

                with open(zshrc_path, "w", encoding="utf-8") as f:
                    f.write(zshrc_content)

                env["ZDOTDIR"] = temp_dir_path
                self.logger.info(
                    f"Using temporary ZDOTDIR for zsh OSC7 integration: {temp_dir_path}"
                )

            except Exception as e:
                self.logger.error(f"Failed to set up zsh OSC7 integration: {e}")
                if temp_dir_path:
                    shutil.rmtree(temp_dir_path, ignore_errors=True)
                temp_dir_path = None
        else:  # Bash and other compatible shells
            # Don't inject PROMPT_COMMAND for bash to avoid conflicts with
            # bash extensions like ble.sh. The VTE terminal already detects
            # OSC7 sequences natively via the current-directory-uri signal.
            # Users with ble.sh or similar already have OSC7 configured.
            self.logger.info(
                "Bash detected - using native shell behavior for OSC7."
            )

        # Build command based on login shell preference
        if self.settings_manager.get("use_login_shell", False):
            cmd = [shell, "-l"]
            self.logger.info(f"Spawning '{shell} -l' as a login shell.")
        else:
            cmd = [shell]


        return cmd, env, temp_dir_path

    def _get_ssh_control_path(self, session: "SessionItem") -> str:
        user = session.user or os.getlogin()
        port = session.port or 22
        self.platform_info.cache_dir.mkdir(parents=True, exist_ok=True)
        return str(
            self.platform_info.cache_dir / f"ssh_control_{session.host}_{port}_{user}"
        )

    def spawn_local_terminal(
        self,
        terminal: Vte.Terminal,
        callback: Optional[Callable] = None,
        user_data: Any = None,
        working_directory: Optional[str] = None,
        precreated_env: Optional[tuple] = None,
    ) -> None:
        """Spawn a local terminal session. Raises TerminalCreationError on setup failure.

        Args:
            terminal: The VTE terminal widget
            callback: Spawn callback function
            user_data: User data to pass to callback
            working_directory: Optional working directory
            precreated_env: Optional pre-prepared (cmd, env, temp_dir_path) tuple for faster spawn
        """
        with self._spawn_lock:
            working_dir = self._resolve_and_validate_working_directory(
                working_directory
            )
            if working_directory and not working_dir:
                self.logger.warning(
                    f"Invalid working directory '{working_directory}', using home directory."
                )

            # Use pre-prepared environment if available, otherwise prepare now
            if precreated_env and not working_directory:
                cmd, env, temp_dir_path = precreated_env
                self.logger.debug("Using pre-prepared shell environment")
            else:
                cmd, env, temp_dir_path = self._prepare_shell_environment(
                    working_directory
                )
            env_list = [f"{k}={v}" for k, v in env.items()]

            # Wrap user_data to include the temp dir path for zsh cleanup
            final_user_data = {
                "original_user_data": user_data,
                "temp_dir_path": temp_dir_path,
            }

            terminal.spawn_async(
                Vte.PtyFlags.DEFAULT,
                working_dir,
                cmd,
                env_list,
                GLib.SpawnFlags.DEFAULT,
                None,
                None,
                -1,
                None,
                callback if callback else self._default_spawn_callback,
                (final_user_data,),
            )
            self.logger.info("Local terminal spawn initiated successfully")
            log_terminal_event(
                "spawn_initiated", str(user_data), f"shell command: {' '.join(cmd)}"
            )

    def _spawn_remote_session(
        self,
        terminal: Vte.Terminal,
        session: "SessionItem",
        command_type: str,
        callback: Optional[Callable] = None,
        user_data: Any = None,
        initial_command: Optional[str] = None,
        sftp_local_dir: Optional[str] = None,
        sftp_remote_path: Optional[str] = None,
    ) -> None:
        """Generic method to spawn a remote (SSH/SFTP) session."""
        with self._spawn_lock:
            if not session.is_ssh():
                raise TerminalCreationError(
                    f"Session is not configured for {command_type.upper()}",
                    command_type,
                )
            try:
                self._validate_ssh_session(session)
                result = self._build_remote_command_secure(
                    command_type,
                    session,
                    initial_command,
                    sftp_remote_path,
                )
                if not result:
                    raise TerminalCreationError(
                        f"Failed to build {command_type.upper()} command", command_type
                    )

                remote_cmd, sshpass_env = result

                working_dir = str(self.platform_info.home_dir)
                if command_type == "sftp" and sftp_local_dir:
                    try:
                        local_path = Path(sftp_local_dir).expanduser()
                        if local_path.exists() and local_path.is_dir():
                            working_dir = str(local_path)
                        else:
                            self.logger.warning(
                                f"SFTP local directory '{sftp_local_dir}' is invalid; falling back to home directory."
                            )
                    except Exception as e:
                        self.logger.warning(
                            f"Failed to use SFTP local directory '{sftp_local_dir}': {e}"
                        )
                env = self.environment_manager.get_terminal_environment()

                # Add SSHPASS to environment if using password authentication
                # This prevents the password from being visible in process list
                if sshpass_env:
                    env.update(sshpass_env)

                env_list = [f"{k}={v}" for k, v in env.items()]

                final_user_data = {
                    "original_user_data": user_data,
                    "temp_dir_path": None,
                }

                terminal.spawn_async(
                    Vte.PtyFlags.DEFAULT,
                    working_dir,
                    remote_cmd,
                    env_list,
                    GLib.SpawnFlags.DEFAULT,
                    None,
                    None,
                    -1,
                    None,
                    callback if callback else self._ssh_spawn_callback,
                    (final_user_data,),
                )
                self.logger.info(
                    f"{command_type.upper()} session spawn initiated for: {session.name}"
                )
                log_terminal_event(
                    "spawn_initiated",
                    session.name,
                    f"{command_type.upper()} to {session.get_connection_string()}",
                )
            except Exception as e:
                self.logger.error(
                    f"{command_type.upper()} session spawn failed for {session.name}: {e}"
                )
                log_error_with_context(
                    e,
                    f"{command_type.upper()} spawn for {session.name}",
                    "zashterminal.spawner",
                )
                raise TerminalCreationError(str(e), command_type) from e

    def spawn_ssh_session(
        self,
        terminal: Vte.Terminal,
        session: "SessionItem",
        callback: Optional[Callable] = None,
        user_data: Any = None,
        initial_command: Optional[str] = None,
    ) -> None:
        """Spawns an SSH session in the given terminal."""
        self._spawn_remote_session(
            terminal,
            session,
            "ssh",
            callback,
            user_data,
            initial_command=initial_command,
        )

    def spawn_sftp_session(
        self,
        terminal: Vte.Terminal,
        session: "SessionItem",
        callback: Optional[Callable] = None,
        user_data: Any = None,
        local_directory: Optional[str] = None,
        remote_path: Optional[str] = None,
    ) -> None:
        """Spawns an SFTP session in the given terminal."""
        self._spawn_remote_session(
            terminal,
            session,
            "sftp",
            callback,
            user_data,
            sftp_local_dir=local_directory,
            sftp_remote_path=remote_path,
        )

    def spawn_highlighted_local_terminal(
        self,
        terminal: Vte.Terminal,
        session: Optional["SessionItem"] = None,
        callback: Optional[Callable] = None,
        user_data: Any = None,
        working_directory: Optional[str] = None,
        terminal_id: Optional[int] = None,
    ) -> Optional["HighlightedTerminalProxy"]:
        """
        Spawn a local terminal with output highlighting support.

        Args:
            terminal: The VTE terminal widget.
            callback: Callback function for spawn completion.
            user_data: User data to pass to callback.
            working_directory: Directory to start the shell in.
            terminal_id: The terminal ID from registry. This ID is used for context
                        detection and must match what the TerminalManager uses.
        """
        from .highlighter import HighlightedTerminalProxy

        with self._spawn_lock:
            working_dir = self._resolve_and_validate_working_directory(
                working_directory
            )
            if working_directory and not working_dir:
                self.logger.warning(
                    f"Invalid working directory '{working_directory}', using home directory."
                )
            if not working_dir:
                working_dir = str(self.platform_info.home_dir)

            # Use centralized shell environment preparation
            cmd, env, temp_dir_path = self._prepare_shell_environment(working_directory)

            proxy = HighlightedTerminalProxy(
                terminal,
                "local",
                proxy_id=terminal_id,
            )

            master_fd = None
            slave_fd = None
            slave_fd_closed = False
            try:
                master_fd, slave_fd = proxy.create_pty()
                # Use expected size based on saved window dimensions to avoid initial resize
                rows, cols = self._get_expected_terminal_size(terminal)
                proxy.set_window_size(rows, cols)

                def preexec_fn():
                    """Setup PTY in child process before exec."""
                    os.setsid()
                    fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
                    os.dup2(slave_fd, 0)
                    os.dup2(slave_fd, 1)
                    os.dup2(slave_fd, 2)
                    if slave_fd > 2:
                        os.close(slave_fd)
                    os.close(master_fd)

                proc = subprocess.Popen(
                    cmd,
                    cwd=working_dir,
                    env=env,
                    preexec_fn=preexec_fn,
                    close_fds=False,  # Keep slave_fd open for preexec_fn
                )
                pid = proc.pid

                # Close slave_fd in parent - child has its own copy
                os.close(slave_fd)
                slave_fd_closed = True

                if not proxy.start(pid):
                    self.logger.error("Failed to start highlight proxy")
                    os.close(master_fd)
                    master_fd = None  # Mark as closed to prevent double-close
                    return None

                # Register process
                process_info = {
                    "name": str(user_data) if user_data else "Terminal",
                    "type": "local",
                    "terminal": terminal,
                    "temp_dir_path": temp_dir_path,
                    "highlight_proxy": proxy,
                }
                self.process_tracker.register_process(pid, process_info)

                if callback:
                    final_user_data = {
                        "original_user_data": user_data,
                        "temp_dir_path": temp_dir_path,
                    }
                    GLib.idle_add(callback, terminal, pid, None, (final_user_data,))

                self.logger.info(
                    f"Highlighted local terminal spawned with PID {pid}"
                )
                log_terminal_event(
                    "spawn_initiated",
                    str(user_data),
                    f"highlighted shell: {' '.join(cmd)}",
                )

                return proxy

            except Exception as e:
                self.logger.error(f"Highlighted spawn failed: {e}")
                # Clean up file descriptors on error
                if slave_fd is not None and not slave_fd_closed:
                    try:
                        os.close(slave_fd)
                    except OSError:
                        pass
                if master_fd is not None:
                    try:
                        os.close(master_fd)
                    except OSError:
                        pass
                proxy.stop()
                if callback:
                    error = GLib.Error.new_literal(
                        GLib.quark_from_string("spawn-error"),
                        str(e),
                        0,
                    )
                    final_user_data = {
                        "original_user_data": user_data,
                        "temp_dir_path": temp_dir_path,
                    }
                    GLib.idle_add(callback, terminal, -1, error, (final_user_data,))
                return None

    def spawn_highlighted_ssh_session(
        self,
        terminal: Vte.Terminal,
        session: "SessionItem",
        callback: Optional[Callable] = None,
        user_data: Any = None,
        initial_command: Optional[str] = None,
        terminal_id: Optional[int] = None,
    ) -> Optional["HighlightedTerminalProxy"]:
        """
        Spawn an SSH session with output highlighting support.

        Args:
            terminal: The VTE terminal widget.
            session: SSH session configuration.
            callback: Callback function for spawn completion.
            user_data: User data to pass to callback.
            initial_command: Command to run after SSH connection.
            terminal_id: The terminal ID from registry. This ID is used for context
                        detection and must match what the TerminalManager uses.
        """
        from .highlighter import HighlightedTerminalProxy

        with self._spawn_lock:
            if not session.is_ssh():
                raise TerminalCreationError("Session is not configured for SSH", "ssh")

            try:
                self._validate_ssh_session(session)
                result = self._build_remote_command_secure(
                    "ssh",
                    session,
                    initial_command,
                    None,
                )
                if not result:
                    raise TerminalCreationError("Failed to build SSH command", "ssh")

                remote_cmd, sshpass_env = result

                working_dir = str(self.platform_info.home_dir)
                env = self.environment_manager.get_terminal_environment()

                # Add SSHPASS to environment if using password authentication
                # This prevents the password from being visible in process list
                if sshpass_env:
                    env.update(sshpass_env)

                proxy = HighlightedTerminalProxy(
                    terminal,
                    "ssh",
                    proxy_id=terminal_id,
                )

                master_fd = None
                slave_fd = None
                slave_fd_closed = False

                try:
                    master_fd, slave_fd = proxy.create_pty()

                    # Use expected size based on saved window dimensions to avoid initial resize
                    rows, cols = self._get_expected_terminal_size(terminal)
                    proxy.set_window_size(rows, cols)

                    # Use subprocess.Popen instead of os.fork() to avoid
                    # DeprecationWarning about fork() in multi-threaded process.
                    # Note: We must NOT use start_new_session=True because we need
                    # to call setsid() BEFORE ioctl(TIOCSCTTY) in the preexec_fn.
                    def preexec_fn():
                        """Setup PTY in child process before exec."""
                        os.setsid()
                        fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
                        os.dup2(slave_fd, 0)
                        os.dup2(slave_fd, 1)
                        os.dup2(slave_fd, 2)
                        if slave_fd > 2:
                            os.close(slave_fd)
                        os.close(master_fd)

                    proc = subprocess.Popen(
                        remote_cmd,
                        cwd=working_dir,
                        env=env,
                        preexec_fn=preexec_fn,
                        close_fds=False,  # Keep slave_fd open for preexec_fn
                    )
                    pid = proc.pid

                    # Close slave_fd in parent - child has its own copy
                    os.close(slave_fd)
                    slave_fd_closed = True

                    if not proxy.start(pid):
                        self.logger.error("Failed to start highlight proxy for SSH")
                        os.close(master_fd)
                        master_fd = None  # Mark as closed to prevent double-close
                        return None

                    process_info = {
                        "name": session.name,
                        "type": "ssh",
                        "terminal": terminal,
                        "session": session,
                        "highlight_proxy": proxy,
                    }
                    self.process_tracker.register_process(pid, process_info)

                    if callback:
                        final_user_data = {
                            "original_user_data": user_data,
                            "temp_dir_path": None,
                        }
                        GLib.idle_add(callback, terminal, pid, None, (final_user_data,))

                    self.logger.info(
                        f"Highlighted SSH session spawned with PID {pid} for {session.name}"
                    )
                    log_terminal_event(
                        "spawn_initiated",
                        session.name,
                        f"highlighted SSH to {session.get_connection_string()}",
                    )

                    return proxy

                except Exception as inner_e:
                    # Clean up file descriptors on error
                    if slave_fd is not None and not slave_fd_closed:
                        try:
                            os.close(slave_fd)
                        except OSError:
                            pass
                    if master_fd is not None:
                        try:
                            os.close(master_fd)
                        except OSError:
                            pass
                    raise inner_e

            except Exception as e:
                self.logger.error(f"Highlighted SSH spawn failed: {e}")
                if "proxy" in locals():
                    proxy.stop()
                if callback:
                    error = GLib.Error.new_literal(
                        GLib.quark_from_string("spawn-error"),
                        str(e),
                        0,
                    )
                    final_user_data = {
                        "original_user_data": user_data,
                        "temp_dir_path": None,
                    }
                    GLib.idle_add(callback, terminal, -1, error, (final_user_data,))
                return None

    def execute_remote_command_sync(
        self, session: "SessionItem", command: List[str], timeout: int = 10
    ) -> Tuple[bool, str]:
        """
        Executes a non-interactive command on a remote session synchronously.

        Uses aggressive timeout settings to prevent UI freezing when connection
        is lost. The SSH connection uses ServerAliveInterval and ServerAliveCountMax
        to detect dead connections quickly.

        Args:
            session: The SSH session to execute the command on.
            command: The command to execute as a list of strings.
            timeout: Maximum time to wait in seconds (default 10).

        Returns:
            Tuple of (success: bool, output: str)
        """
        if not session.is_ssh():
            return False, _("Not an SSH session.")

        try:
            self._validate_ssh_session(session)
            # Use shorter connect timeout based on overall timeout
            connect_timeout = min(timeout - 2, 8) if timeout > 4 else timeout
            result = self._build_non_interactive_ssh_command(
                session, command, connect_timeout=connect_timeout
            )
            if not result:
                raise TerminalCreationError(
                    "Failed to build non-interactive SSH command", "ssh"
                )

            full_cmd, sshpass_env = result

            self.logger.debug(
                f"Executing remote command (timeout={timeout}s): {' '.join(full_cmd)}"
            )

            # Merge sshpass_env with current environment if needed
            run_env = None
            if sshpass_env:
                run_env = os.environ.copy()
                run_env.update(sshpass_env)

            proc_result = subprocess.run(
                full_cmd, capture_output=True, text=True, timeout=timeout, env=run_env
            )

            if proc_result.returncode == 0:
                return True, proc_result.stdout
            else:
                error_output = (
                    proc_result.stdout.strip() + "\n" + proc_result.stderr.strip()
                ).strip()
                # Check for connection-related errors
                if any(
                    err in error_output.lower()
                    for err in [
                        "connection",
                        "timed out",
                        "unreachable",
                        "refused",
                        "reset",
                    ]
                ):
                    self.logger.warning(
                        f"Connection issue for {session.name}: {error_output}"
                    )
                    return False, _("Connection lost or unreachable.")

                self.logger.warning(
                    f"Remote command failed for {session.name} with code {proc_result.returncode}: {error_output}"
                )
                return False, error_output
        except subprocess.TimeoutExpired:
            self.logger.error(
                f"Remote command timed out after {timeout}s for session {session.name}"
            )
            return False, _("Command timed out. Connection may be lost.")
        except Exception as e:
            self.logger.error(
                f"Failed to execute remote command for {session.name}: {e}"
            )
            log_error_with_context(
                e, f"Remote command execution for {session.name}", "zashterminal.spawner"
            )
            return False, str(e)

    def test_ssh_connection(self, session: "SessionItem") -> Tuple[bool, str]:
        """
        Tests an SSH connection without spawning a full terminal.
        Returns a tuple of (success, message).
        """
        if not session.is_ssh():
            return False, "Not an SSH session."

        try:
            self._validate_ssh_session(session)

            ssh_options = {
                "BatchMode": "yes",
                "ConnectTimeout": "10",
                "StrictHostKeyChecking": "no",
                "PasswordAuthentication": "no" if session.uses_key_auth() else "yes",
            }
            if getattr(session, "x11_forwarding", False):
                ssh_options["ForwardX11"] = "yes"
                ssh_options["ForwardX11Trusted"] = "yes"

            cmd = self.command_builder.build_remote_command(
                "ssh",
                hostname=session.host,
                port=session.port if session.port != 22 else None,
                username=session.user if session.user else None,
                key_file=session.auth_value if session.uses_key_auth() else None,
                options=ssh_options,
            )
            if getattr(session, "x11_forwarding", False) and "-Y" not in cmd:
                cmd.insert(1, "-Y")
            cmd.append("exit")

            # Store password for environment variable (won't appear in process list)
            sshpass_env = None
            if session.uses_password_auth() and session.auth_value:
                if has_command("sshpass"):
                    # Use -e flag to read password from SSHPASS environment variable
                    # This prevents the password from appearing in the process list
                    cmd = ["sshpass", "-e"] + cmd
                    sshpass_env = {"SSHPASS": session.auth_value}
                else:
                    return (
                        False,
                        "sshpass is not installed, cannot test password authentication.",
                    )

            self.logger.info(f"Testing SSH connection with command: {' '.join(cmd)}")

            # Merge sshpass_env with current environment if needed
            run_env = None
            if sshpass_env:
                run_env = os.environ.copy()
                run_env.update(sshpass_env)

            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=15, env=run_env
            )

            if result.returncode == 0:
                self.logger.info(f"SSH connection test successful for {session.name}")
                return True, "Connection successful."
            else:
                error_message = result.stderr.strip()
                self.logger.warning(
                    f"SSH connection test failed for {session.name}: {error_message}"
                )
                return False, error_message

        except Exception as e:
            self.logger.error(
                f"Exception during SSH connection test for {session.name}: {e}"
            )
            return False, str(e)

    def _validate_ssh_session(self, session: "SessionItem") -> None:
        try:
            validate_ssh_hostname(session.host)
        except Exception as e:
            raise SSHConnectionError(session.host, f"Invalid hostname: {e}") from e
        if session.uses_key_auth():
            # CORRECTED LOGIC: Only validate the key file if a path is provided.
            if session.auth_value:
                try:
                    validate_ssh_key_file(session.auth_value)
                except Exception as e:
                    raise SSHKeyError(session.auth_value, str(e)) from e

    def _build_remote_command_secure(
        self,
        command_type: str,
        session: "SessionItem",
        initial_command: Optional[str] = None,
        sftp_remote_path: Optional[str] = None,
    ) -> Optional[Tuple[List[str], Optional[Dict[str, str]]]]:
        """Builds an SSH/SFTP command for an INTERACTIVE session.

        Returns:
            A tuple of (command_list, sshpass_env) where sshpass_env is None
            if not using password auth, or {"SSHPASS": password} if using
            password auth with sshpass -e flag (password not visible in process list).
        """
        if not has_command(command_type):
            raise SSHConnectionError(
                session.host, f"{command_type.upper()} command not found on system"
            )

        persist_duration = self.settings_manager.get(
            "ssh_control_persist_duration", 600
        )
        # Get connect timeout from settings (can be temporarily increased for retries)
        connect_timeout = self.settings_manager.get("ssh_connect_timeout", 30)
        ssh_options = {
            "ConnectTimeout": str(connect_timeout),
            "ServerAliveInterval": "30",
            "ServerAliveCountMax": "3",
            "StrictHostKeyChecking": "accept-new",
            "UpdateHostKeys": "yes",
            "ControlMaster": "auto",
            "ControlPath": self._get_ssh_control_path(session),
        }
        if persist_duration > 0:
            ssh_options["ControlPersist"] = str(persist_duration)
        if command_type == "ssh" and getattr(session, "x11_forwarding", False):
            ssh_options.pop("ControlPersist", None)
            ssh_options.pop("ControlMaster", None)
            ssh_options.pop("ControlPath", None)
        if command_type == "ssh" and getattr(session, "port_forwardings", None):
            # Port forwarding sessions should tear down immediately when the terminal exits.
            ssh_options.pop("ControlPersist", None)
            ssh_options.pop("ControlMaster", None)
            ssh_options.pop("ControlPath", None)
            ssh_options["ExitOnForwardFailure"] = "yes"
        if command_type == "ssh" and getattr(session, "x11_forwarding", False):
            ssh_options["ForwardX11"] = "yes"
            ssh_options["ForwardX11Trusted"] = "yes"

        cmd = self.command_builder.build_remote_command(
            command_type,
            hostname=session.host,
            port=session.port if session.port != 22 else None,
            username=session.user if session.user else None,
            key_file=session.auth_value if session.uses_key_auth() else None,
            options=ssh_options,
            remote_path=sftp_remote_path if command_type == "sftp" else None,
        )

        if command_type == "ssh" and getattr(session, "x11_forwarding", False):
            if "-Y" not in cmd:
                insertion_index = 1 if len(cmd) > 1 else len(cmd)
                cmd.insert(insertion_index, "-Y")

        if command_type == "ssh" and getattr(session, "port_forwardings", None):
            for tunnel in session.port_forwardings:
                try:
                    local_host = tunnel.get("local_host", "localhost") or "localhost"
                    local_port = int(tunnel.get("local_port", 0))
                    remote_host = tunnel.get("remote_host") or session.host
                    remote_port = int(tunnel.get("remote_port", 0))
                except (TypeError, ValueError):
                    continue

                if (
                    not remote_host
                    or not (1 <= local_port <= 65535)
                    or not (1 <= remote_port <= 65535)
                ):
                    continue

                forward_spec = f"{local_host}:{local_port}:{remote_host}:{remote_port}"
                insertion_index = max(len(cmd) - 1, 1)
                cmd[insertion_index:insertion_index] = ["-L", forward_spec]

        if command_type == "ssh":
            osc7_setup = (
                f"{OSC7_HOST_DETECTION_SNIPPET} "
                'export PROMPT_COMMAND=\'printf "\\033]7;file://%s%s\\007" "$ZASHTERMINAL_OSC7_HOST" "$PWD"\''
            )
            shell_exec = 'exec "$SHELL" -l'

            remote_parts = []
            if initial_command:
                remote_parts.append(initial_command)
            remote_parts.append(osc7_setup)
            remote_parts.append(shell_exec)

            full_remote_command = "; ".join(remote_parts)

            if "-t" not in cmd:
                cmd.insert(1, "-t")
            cmd.append(full_remote_command)

        # Handle password authentication securely
        # Use SSHPASS environment variable instead of -p flag to avoid
        # password being visible in process list (ps aux)
        sshpass_env: Optional[Dict[str, str]] = None
        if session.uses_password_auth() and session.auth_value:
            if has_command("sshpass"):
                cmd = ["sshpass", "-e"] + cmd
                sshpass_env = {"SSHPASS": session.auth_value}
            else:
                self.logger.warning("sshpass not available for password authentication")
        return (cmd, sshpass_env)

    def _build_non_interactive_ssh_command(
        self, session: "SessionItem", command: List[str], connect_timeout: int = 10
    ) -> Optional[Tuple[List[str], Optional[Dict[str, str]]]]:
        """Builds an SSH command for a NON-INTERACTIVE session.

        Args:
            session: The SSH session to connect to.
            command: The command to execute remotely.
            connect_timeout: SSH connection timeout in seconds (default 10).

        Returns:
            A tuple of (command_list, sshpass_env) where sshpass_env is None
            if not using password auth, or {"SSHPASS": password} if using
            password auth with sshpass -e flag (password not visible in process list).
        """
        if not has_command("ssh"):
            raise SSHConnectionError(session.host, "SSH command not found on system")

        persist_duration = self.settings_manager.get(
            "ssh_control_persist_duration", 600
        )
        ssh_options = {
            "ConnectTimeout": str(connect_timeout),
            "ControlMaster": "auto",
            "ControlPath": self._get_ssh_control_path(session),
            "BatchMode": "yes",
            "ServerAliveInterval": "5",
            "ServerAliveCountMax": "2",
        }
        if persist_duration > 0:
            ssh_options["ControlPersist"] = str(persist_duration)
        if getattr(session, "x11_forwarding", False):
            ssh_options["ForwardX11"] = "yes"
            ssh_options["ForwardX11Trusted"] = "yes"
            ssh_options.pop("ControlPersist", None)
            ssh_options.pop("ControlMaster", None)
            ssh_options.pop("ControlPath", None)

        cmd = self.command_builder.build_remote_command(
            "ssh",
            hostname=session.host,
            port=session.port if session.port != 22 else None,
            username=session.user if session.user else None,
            key_file=session.auth_value if session.uses_key_auth() else None,
            options=ssh_options,
        )

        if getattr(session, "x11_forwarding", False) and "-Y" not in cmd:
            insertion_index = 1 if len(cmd) > 1 else len(cmd)
            cmd.insert(insertion_index, "-Y")

        remote_command_str = " ".join(shlex.quote(part) for part in command)
        cmd.append(remote_command_str)

        # Handle password authentication securely
        # Use SSHPASS environment variable instead of -p flag to avoid
        # password being visible in process list (ps aux)
        sshpass_env: Optional[Dict[str, str]] = None
        if session.uses_password_auth() and session.auth_value:
            if has_command("sshpass"):
                cmd = ["sshpass", "-e"] + cmd
                sshpass_env = {"SSHPASS": session.auth_value}
            else:
                self.logger.warning("sshpass not available for password authentication")
        return (cmd, sshpass_env)

    def _generic_spawn_callback(
        self,
        terminal: Vte.Terminal,
        pid: int,
        error: Optional[GLib.Error],
        user_data: Any = None,
        spawn_type: str = "local",
    ) -> None:
        """
        Generic spawn callback for both local and SSH terminals.

        Args:
            terminal: The VTE terminal widget
            pid: Process ID of spawned process
            error: GLib.Error if spawn failed, None otherwise
            user_data: User data containing original_user_data and temp_dir_path
            spawn_type: Type of spawn - "local" or "ssh"
        """
        try:
            final_user_data = (
                user_data[0] if isinstance(user_data, tuple) else user_data
            )
            original_user_data = final_user_data.get("original_user_data")
            temp_dir_path = final_user_data.get("temp_dir_path")

            # Extract name based on spawn type
            if spawn_type == "ssh":
                actual_data = (
                    original_user_data[0]
                    if isinstance(original_user_data, tuple) and original_user_data
                    else original_user_data
                )
                name = getattr(actual_data, "name", "SSH Session")
            else:
                actual_data = None
                name = (
                    str(original_user_data[0])
                    if isinstance(original_user_data, tuple) and original_user_data
                    else "Terminal"
                )

            if error:
                event_type = (
                    f"{spawn_type}_spawn_failed"
                    if spawn_type == "ssh"
                    else "spawn_failed"
                )
                self.logger.error(f"Process spawn failed for {name}: {error.message}")
                log_terminal_event(event_type, name, f"error: {error.message}")

                # Build error message based on spawn type
                if spawn_type == "ssh" and actual_data:
                    error_guidance = self._get_ssh_error_guidance(error.message)
                    connection_str = getattr(
                        actual_data, "get_connection_string", lambda: "unknown"
                    )()
                    error_msg = f"\nSSH Connection Failed:\nSession: {name}\nHost: {connection_str}\nError: {error.message}\n"
                    if error_guidance:
                        error_msg += f"Suggestion: {error_guidance}\n"
                    error_msg += "\n"
                else:
                    error_msg = f"\nFailed to start {name}:\nError: {error.message}\nPlease check your system configuration.\n\n"

                if terminal.get_realized():
                    terminal.feed(error_msg.encode("utf-8"))
            else:
                self.logger.info(
                    f"Process spawned successfully for {name} with PID {pid}"
                )
                log_terminal_event("spawned", name, f"PID {pid}")

                if pid > 0:
                    process_info = {
                        "name": name,
                        "type": spawn_type,
                        "terminal": terminal,
                    }
                    if temp_dir_path:
                        process_info["temp_dir_path"] = temp_dir_path
                    if spawn_type == "ssh" and actual_data:
                        process_info["session"] = actual_data
                    self.process_tracker.register_process(pid, process_info)

        except Exception as e:
            self.logger.error(f"Spawn callback handling failed: {e}")

    def _default_spawn_callback(
        self,
        terminal: Vte.Terminal,
        pid: int,
        error: Optional[GLib.Error],
        user_data: Any = None,
    ) -> None:
        """Spawn callback for local terminals."""
        self._generic_spawn_callback(
            terminal, pid, error, user_data, spawn_type="local"
        )

    def _ssh_spawn_callback(
        self,
        terminal: Vte.Terminal,
        pid: int,
        error: Optional[GLib.Error],
        user_data: Any = None,
    ) -> None:
        """Spawn callback for SSH terminals."""
        self._generic_spawn_callback(terminal, pid, error, user_data, spawn_type="ssh")

    def _get_ssh_error_guidance(self, error_message: str) -> str:
        error_lower = error_message.lower()
        if "connection refused" in error_lower:
            return "Check if SSH service is running on the target host and the port is correct"
        elif "permission denied" in error_lower:
            return "Check your username, password, or SSH key configuration"
        elif "host key verification failed" in error_lower:
            return "The host key has changed. Remove the old key from known_hosts if this is expected"
        elif "network is unreachable" in error_lower:
            return "Check your network connection and the hostname/IP address"
        elif "no route to host" in error_lower:
            return "The host is not reachable. Check network connectivity and firewall settings"
        elif "connection timed out" in error_lower:
            return "Connection timeout. The host may be down or firewalled"
        else:
            return "Check your SSH configuration and network connectivity"

    def _resolve_and_validate_working_directory(
        self, working_directory: Optional[str]
    ) -> str:
        if not working_directory:
            return str(self.platform_info.home_dir)
        try:
            expanded_path = os.path.expanduser(os.path.expandvars(working_directory))
            resolved_path = os.path.abspath(expanded_path)
            path_obj = Path(resolved_path)
            if not path_obj.exists():
                self.logger.error(
                    f"Working directory does not exist: {working_directory}"
                )
                return str(self.platform_info.home_dir)
            if not path_obj.is_dir():
                self.logger.error(
                    f"Working directory is not a directory: {working_directory}"
                )
                return str(self.platform_info.home_dir)
            if not os.access(resolved_path, os.R_OK | os.X_OK):
                self.logger.error(
                    f"Working directory is not accessible: {working_directory}"
                )
                return str(self.platform_info.home_dir)
            return resolved_path
        except Exception as e:
            self.logger.error(
                f"Error validating working directory '{working_directory}': {e}"
            )
            return str(self.platform_info.home_dir)


class SSHConnectionChecker:
    """
    Utility to check and manage existing SSH ControlMaster connections.

    This class provides methods to:
    - Check if a ControlMaster connection is active for a session
    - Get connection information
    - Terminate existing connections gracefully

    Uses the existing ControlPath sockets created by ProcessSpawner.
    """

    def __init__(self, spawner: Optional[ProcessSpawner] = None):
        """
        Initialize the SSH connection checker.

        Args:
            spawner: ProcessSpawner instance to use for control path generation.
                    If None, will use the global spawner instance.
        """
        self.logger = get_logger("zashterminal.ssh_checker")
        self._spawner = spawner

    @property
    def spawner(self) -> ProcessSpawner:
        """Get the spawner instance, using global if none provided."""
        if self._spawner is None:
            self._spawner = get_spawner()
        return self._spawner

    def is_master_active(self, session: "SessionItem") -> bool:
        """
        Check if a ControlMaster connection is already active for this session.

        Args:
            session: The SSH session to check.

        Returns:
            True if an active ControlMaster connection exists, False otherwise.
        """
        control_path = self.spawner._get_ssh_control_path(session)

        # First check if socket file exists
        if not Path(control_path).exists():
            return False

        # Use ssh -O check to verify the connection is actually active
        user = session.user or os.getlogin()
        cmd = ["ssh", "-O", "check", "-S", control_path, f"{user}@{session.host}"]

        try:
            result = subprocess.run(cmd, capture_output=True, timeout=5, text=True)
            is_active = result.returncode == 0
            self.logger.debug(
                f"ControlMaster check for {session.name}: "
                f"{'active' if is_active else 'inactive'}"
            )
            return is_active
        except subprocess.TimeoutExpired:
            self.logger.warning(f"Timeout checking ControlMaster for {session.name}")
            return False
        except Exception as e:
            self.logger.debug(f"Error checking ControlMaster for {session.name}: {e}")
            return False

    def get_master_info(self, session: "SessionItem") -> Optional[Dict[str, Any]]:
        """
        Get information about an active ControlMaster connection.

        Args:
            session: The SSH session to get info for.

        Returns:
            Dictionary with connection info if active, None otherwise.
        """
        if not self.is_master_active(session):
            return None

        control_path = self.spawner._get_ssh_control_path(session)
        socket_stat = Path(control_path).stat()

        return {
            "host": session.host,
            "user": session.user or os.getlogin(),
            "port": session.port or 22,
            "control_path": control_path,
            "active": True,
            "socket_created": socket_stat.st_ctime,
            "can_quick_reconnect": True,
        }

    def terminate_master(self, session: "SessionItem") -> bool:
        """
        Gracefully terminate a ControlMaster connection.

        Args:
            session: The SSH session whose master connection to terminate.

        Returns:
            True if successfully terminated (or wasn't active), False on error.
        """
        control_path = self.spawner._get_ssh_control_path(session)

        if not Path(control_path).exists():
            return True  # No socket means nothing to terminate

        user = session.user or os.getlogin()
        cmd = ["ssh", "-O", "exit", "-S", control_path, f"{user}@{session.host}"]

        try:
            result = subprocess.run(cmd, capture_output=True, timeout=10, text=True)

            if result.returncode == 0:
                self.logger.info(
                    f"Successfully terminated ControlMaster for {session.name}"
                )
                return True
            else:
                self.logger.warning(
                    f"Failed to terminate ControlMaster for {session.name}: "
                    f"{result.stderr}"
                )
                return False

        except subprocess.TimeoutExpired:
            self.logger.warning(f"Timeout terminating ControlMaster for {session.name}")
            return False
        except Exception as e:
            self.logger.error(
                f"Error terminating ControlMaster for {session.name}: {e}"
            )
            return False

    def terminate_all_masters(self, sessions: List["SessionItem"]) -> int:
        """
        Terminate all ControlMaster connections for the given sessions.

        Args:
            sessions: List of sessions to terminate connections for.

        Returns:
            Number of connections successfully terminated.
        """
        terminated = 0
        for session in sessions:
            if session.is_ssh() and self.is_master_active(session):
                if self.terminate_master(session):
                    terminated += 1
        return terminated

    def cleanup_stale_sockets(self) -> int:
        """
        Remove stale control socket files that don't have active connections.

        Returns:
            Number of stale sockets cleaned up.
        """
        cache_dir = self.spawner.platform_info.cache_dir
        cleaned = 0

        if not cache_dir.exists():
            return 0

        for socket_file in cache_dir.glob("ssh_control_*"):
            if socket_file.is_socket():
                # Try to check if it's still active by connecting
                try:
                    # If we can't connect, it's stale
                    # Use a very short timeout
                    result = subprocess.run(
                        ["ssh", "-O", "check", "-S", str(socket_file), "dummy"],
                        capture_output=True,
                        timeout=2,
                    )
                    if result.returncode != 0:
                        socket_file.unlink(missing_ok=True)
                        cleaned += 1
                        self.logger.debug(f"Cleaned stale socket: {socket_file}")
                except subprocess.TimeoutExpired:
                    # Timeout means it's probably stale
                    socket_file.unlink(missing_ok=True)
                    cleaned += 1
                except Exception:
                    pass

        if cleaned > 0:
            self.logger.info(f"Cleaned up {cleaned} stale SSH control sockets")

        return cleaned


# Module-level singleton instances
_spawner_instance: Optional[ProcessSpawner] = None
_spawner_lock = threading.Lock()
_checker_instance: Optional[SSHConnectionChecker] = None
_checker_lock = threading.Lock()


def get_spawner() -> ProcessSpawner:
    global _spawner_instance
    if _spawner_instance is None:
        with _spawner_lock:
            if _spawner_instance is None:
                _spawner_instance = ProcessSpawner()
    return _spawner_instance


def get_ssh_connection_checker() -> SSHConnectionChecker:
    """Get the singleton SSH connection checker instance."""
    global _checker_instance
    if _checker_instance is None:
        with _checker_lock:
            if _checker_instance is None:
                _checker_instance = SSHConnectionChecker()
    return _checker_instance


def cleanup_spawner() -> None:
    """Clean up spawner resources and terminate tracked processes."""
    global _spawner_instance, _checker_instance

    # First, try to clean up stale sockets
    if _checker_instance is not None:
        try:
            _checker_instance.cleanup_stale_sockets()
        except Exception:
            pass

    if _spawner_instance is not None:
        with _spawner_lock:
            if _spawner_instance is not None:
                _spawner_instance.process_tracker.terminate_all()
                _spawner_instance = None

    # Reset checker instance
    with _checker_lock:
        _checker_instance = None
