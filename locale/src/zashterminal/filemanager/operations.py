# zashterminal/filemanager/operations.py
import ctypes
import os
import re
import signal
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import List, Optional, Tuple

from gi.repository import GLib

from ..sessions.models import SessionItem
from ..utils.logger import get_logger
from ..utils.translation_utils import _

# Pre-compiled pattern for rsync progress parsing
_PROGRESS_PERCENT_PATTERN = re.compile(r"(\d+)%")

# --- NEW: Kernel-level process lifecycle management ---
# Use ctypes to access the prctl system call for robust cleanup.
# PR_SET_PDEATHSIG: Asks the kernel to send a signal to this process
# when its parent dies. This is the most reliable way to ensure
# child processes (like rsync/ssh) do not get orphaned.
try:
    libc = ctypes.CDLL("libc.so.6")
    PR_SET_PDEATHSIG = 1
except (OSError, AttributeError):
    libc = None
    PR_SET_PDEATHSIG = None
    # This will fallback to os.killpg if prctl is not available.


def set_pdeathsig_kill():
    """
    Function to be run in the child process before exec.
    It tells the kernel to send SIGKILL to this process when the parent exits.
    """
    if libc and PR_SET_PDEATHSIG is not None:
        libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL)


def _drain_stderr_to_list(stderr_stream, output_list: list):
    """Helper function to drain stderr in a separate thread.

    This prevents deadlocks when reading stdout and stderr from a subprocess.
    If only stdout is read in a loop while stderr is ignored, the stderr
    buffer can fill up and cause the process to block indefinitely.

    Args:
        stderr_stream: The stderr pipe from the subprocess
        output_list: A list to append stderr lines to (thread-safe with append)
    """
    try:
        for line in iter(stderr_stream.readline, ""):
            output_list.append(line)
    except Exception:
        pass  # Ignore errors during stderr reading
    finally:
        try:
            stderr_stream.close()
        except Exception:
            pass


# --- End of process management setup ---


class OperationCancelledError(Exception):
    """Custom exception to indicate that an operation was cancelled by the user."""



class FileOperations:
    def __init__(self, session_item: SessionItem):
        self.session_item = session_item
        self.logger = get_logger("zashterminal.filemanager.operations")
        self._command_cache = {}
        self._active_processes = {}
        self._lock = threading.Lock()

    def shutdown(self):
        """Terminate all active subprocess groups managed by this instance."""
        self.logger.info(
            f"Shutting down operations. Terminating {len(self._active_processes)} active process groups."
        )
        with self._lock:
            for transfer_id, process in list(self._active_processes.items()):
                try:
                    # Best effort to terminate gracefully first.
                    pgid = os.getpgid(process.pid)
                    os.killpg(pgid, signal.SIGTERM)
                    process.wait(timeout=2)
                except ProcessLookupError:
                    self.logger.warning(
                        f"Process for transfer {transfer_id} (PID: {process.pid}) not found. Already terminated?"
                    )
                except subprocess.TimeoutExpired:
                    self.logger.warning(
                        f"Process group for transfer {transfer_id} did not terminate in time, killing."
                    )
                    # Force kill if graceful shutdown fails
                    pgid = os.getpgid(process.pid)
                    os.killpg(pgid, signal.SIGKILL)
                except Exception as e:
                    self.logger.error(
                        f"Error terminating process group for transfer {transfer_id}: {e}"
                    )
            self._active_processes.clear()

    def _get_session_key(self, session: SessionItem) -> str:
        return f"{session.user or ''}@{session.host}:{session.port or 22}"

    def _is_command_available(
        self, session: SessionItem, command: str, use_cache: bool = True
    ) -> bool:
        session_key = self._get_session_key(session)
        if use_cache:
            if (
                session_key in self._command_cache
                and command in self._command_cache[session_key]
            ):
                return self._command_cache[session_key][command]

        check_command = ["command", "-v", command]
        success, _ = self.execute_command_on_session(
            check_command, session_override=session
        )

        if session_key not in self._command_cache:
            self._command_cache[session_key] = {}
        self._command_cache[session_key][command] = success
        return success

    def check_command_available(
        self,
        command: str,
        use_cache: bool = True,
        session_override: Optional[SessionItem] = None,
    ) -> bool:
        """
        Public helper to check the availability of a command in the current or
        overridden session. Optionally bypasses the local cache when a fresh
        verification is required.
        """
        session = session_override if session_override else self.session_item
        if not session:
            return False
        return self._is_command_available(session, command, use_cache=use_cache)

    def execute_command_on_session(
        self,
        command: List[str],
        session_override: Optional[SessionItem] = None,
        timeout: int = 10,
    ) -> Tuple[bool, str]:
        """
        Executes a command either locally or remotely via the centralized spawner.

        Args:
            command: The command to execute as a list of strings.
            session_override: Optional session to use instead of the default.
            timeout: Maximum time to wait for command completion (default 10s for file manager ops).

        Returns:
            Tuple of (success: bool, output: str)
        """
        session_to_use = session_override if session_override else self.session_item
        if not session_to_use:
            return False, _("No session context for file operation.")

        try:
            if session_to_use.is_local():
                result = subprocess.run(
                    command,
                    shell=False,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                return (
                    (True, result.stdout)
                    if result.returncode == 0
                    else (False, result.stderr)
                )
            elif session_to_use.is_ssh():
                from ..terminal.spawner import get_spawner

                spawner = get_spawner()
                # Use shorter timeout for file manager operations to avoid UI freeze
                return spawner.execute_remote_command_sync(
                    session_to_use, command, timeout=timeout
                )
        except subprocess.TimeoutExpired:
            self.logger.error(
                f"Command timed out after {timeout}s: {' '.join(command)}"
            )
            return False, _("Command timed out. Connection may be lost.")
        except Exception as e:
            self.logger.error(f"Command execution failed: {e}")
            return False, str(e)

        # This case should not be reached if session is always local or ssh
        return False, _("Unsupported session type for command execution.")

    def get_remote_file_timestamp(self, remote_path: str) -> Optional[int]:
        """Gets the modification timestamp of a remote file."""
        # The command 'stat -c %Y' is standard on GNU systems for getting the epoch timestamp.
        command = ["stat", "-c", "%Y", remote_path]
        success, output = self.execute_command_on_session(command)
        if success and output.strip().isdigit():
            return int(output.strip())
        self.logger.warning(
            f"Failed to get timestamp for {remote_path}. Output: {output}"
        )
        return None

    def get_directory_size(
        self,
        path: str,
        is_remote: bool = False,
        session_override: Optional[SessionItem] = None,
    ) -> int:
        """
        Get the total size of a file or directory in bytes.

        Args:
            path: Path to the file or directory
            is_remote: Whether this is a remote path
            session_override: Optional session for remote operations

        Returns:
            Size in bytes, or 0 if unable to determine
        """
        try:
            # Use 'du -sb' for total size in bytes (summary, bytes)
            command = ["du", "-sb", path]

            if is_remote:
                success, output = self.execute_command_on_session(
                    command, session_override, timeout=30
                )
            else:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                success = result.returncode == 0
                output = result.stdout if success else result.stderr

            if success and output.strip():
                # Output format: "SIZE\tPATH"
                parts = output.strip().split()
                if parts and parts[0].isdigit():
                    return int(parts[0])
        except Exception as e:
            self.logger.warning(f"Failed to get directory size for {path}: {e}")

        return 0

    def get_free_space(
        self,
        path: str,
        is_remote: bool = False,
        session_override: Optional[SessionItem] = None,
    ) -> int:
        """
        Get available free space at the given path in bytes.

        Args:
            path: Path to check for free space
            is_remote: Whether this is a remote path
            session_override: Optional session for remote operations

        Returns:
            Available space in bytes, or -1 if unable to determine
        """
        try:
            # Use 'df -B1' for size in bytes, get available space
            command = ["df", "-B1", "--output=avail", path]

            if is_remote:
                success, output = self.execute_command_on_session(
                    command, session_override, timeout=10
                )
            else:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                success = result.returncode == 0
                output = result.stdout if success else result.stderr

            if success and output.strip():
                # Output format: "Avail\n12345678" (header + value)
                lines = output.strip().split("\n")
                if len(lines) >= 2:
                    avail_str = lines[1].strip()
                    if avail_str.isdigit():
                        return int(avail_str)
        except Exception as e:
            self.logger.warning(f"Failed to get free space for {path}: {e}")

        return -1

    def _start_process(self, transfer_id, command):
        """Helper to start a subprocess with robust lifecycle management."""
        # MODIFIED: Use preexec_fn for robust cleanup
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, # Capture stderr separately
            text=True,
            bufsize=1,
            universal_newlines=True,
            start_new_session=True,  # Create a new process group
            preexec_fn=set_pdeathsig_kill,  # Ensure kernel cleans it up if we die
        )
        with self._lock:
            self._active_processes[transfer_id] = process
        return process

    def _parse_transfer_error(self, output: str) -> str:
        """Parses command output to find specific, user-friendly error messages."""
        output_lower = output.lower()
        permission_errors = {
            "permission denied",
            "permissão negada",
            "operation not permitted",
        }
        if any(err in output_lower for err in permission_errors):
            return _("Permission Denied: Check write permissions on the destination.")

        # Fallback to a generic message if output is empty but an error occurred
        if not output.strip():
            return _("An unknown transfer error occurred.")

        return output.strip()

    def start_download_with_progress(
        self,
        transfer_id: str,
        session: SessionItem,
        remote_path: str,
        local_path: Path,
        is_directory: bool,
        progress_callback=None,
        completion_callback=None,
        cancellation_event: Optional[threading.Event] = None,
    ):
        def download_thread():
            process = None
            stderr_thread = None
            stderr_lines: list = []
            try:
                from ..terminal.spawner import get_spawner

                spawner = get_spawner()

                if self._is_command_available(session, "rsync"):
                    ssh_cmd = (
                        f"ssh -o ControlPath={spawner._get_ssh_control_path(session)}"
                    )

                    # FIX: Add trailing slash to source for rsync directory copy
                    source_path_rsync = remote_path
                    if is_directory:
                        source_path_rsync = remote_path.rstrip("/") + "/"

                    transfer_cmd = [
                        "rsync",
                        "-avz",
                        "--progress",
                        "-e",
                        ssh_cmd,
                        f"{session.user}@{session.host}:{source_path_rsync}",
                        str(local_path),
                    ]
                    process = self._start_process(transfer_id, transfer_cmd)

                    # Start stderr draining thread to prevent deadlock
                    # Deadlock can occur if stderr buffer fills while we read stdout
                    stderr_thread = threading.Thread(
                        target=_drain_stderr_to_list,
                        args=(process.stderr, stderr_lines),
                        daemon=True,
                    )
                    stderr_thread.start()

                    full_output = ""
                    for line in iter(process.stdout.readline, ""):
                        if cancellation_event and cancellation_event.is_set():
                            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                            raise OperationCancelledError("Download cancelled by user.")

                        full_output += line
                        match = _PROGRESS_PERCENT_PATTERN.search(line)
                        if match and progress_callback:
                            progress = float(match.group(1))
                            GLib.idle_add(progress_callback, transfer_id, progress)

                    # Wait for stderr thread to complete
                    if stderr_thread and stderr_thread.is_alive():
                        stderr_thread.join(timeout=2.0)

                    stderr_output = "".join(stderr_lines)
                    process.wait()
                    exit_code = process.returncode

                    if exit_code == 0:
                        GLib.idle_add(
                            completion_callback,
                            transfer_id,
                            True,
                            "Download completed successfully.",
                        )
                    else:
                        error_message = self._parse_transfer_error(full_output + stderr_output)
                        GLib.idle_add(
                            completion_callback,
                            transfer_id,
                            False,
                            error_message,
                        )
                else:  # Fallback for SFTP
                    # ... (SFTP logic remains the same, as it doesn't provide detailed stderr during run)
                    sftp_cmd_base = spawner.command_builder.build_remote_command(
                        "sftp", session
                    )

                    # FIX: For SFTP directory copy, destination must be the parent directory
                    dest_path_sftp = str(local_path)
                    if is_directory:
                        dest_path_sftp = str(local_path.parent)

                    with tempfile.NamedTemporaryFile(
                        mode="w", delete=False, suffix=".sftp"
                    ) as batch_file:
                        batch_file.write(
                            f'get -r "{remote_path}" "{dest_path_sftp}"\nquit\n'
                        )
                        batch_file_path = batch_file.name
                    transfer_cmd = sftp_cmd_base + ["-b", batch_file_path]

                    process = self._start_process(transfer_id, transfer_cmd)

                    stdout, stderr = process.communicate()
                    exit_code = process.returncode
                    if "batch_file_path" in locals() and Path(batch_file_path).exists():
                        Path(batch_file_path).unlink()

                    if exit_code == 0:
                        GLib.idle_add(
                            completion_callback,
                            transfer_id,
                            True,
                            "Download completed successfully.",
                        )
                    else:
                        error_msg = self._parse_transfer_error(stdout + stderr)
                        GLib.idle_add(
                            completion_callback, transfer_id, False, error_msg
                        )

            except OperationCancelledError:
                self.logger.warning(f"Download cancelled for {remote_path}")
                if completion_callback:
                    GLib.idle_add(completion_callback, transfer_id, False, "Cancelled")
            except Exception as e:
                self.logger.error(f"Exception during download: {e}")
                if completion_callback:
                    GLib.idle_add(completion_callback, transfer_id, False, str(e))
            finally:
                with self._lock:
                    if transfer_id in self._active_processes:
                        del self._active_processes[transfer_id]

        threading.Thread(target=download_thread, daemon=True).start()

    def start_upload_with_progress(
        self,
        transfer_id: str,
        session: SessionItem,
        local_path: Path,
        remote_path: str,
        is_directory: bool,
        progress_callback=None,
        completion_callback=None,
        cancellation_event: Optional[threading.Event] = None,
    ):
        def upload_thread():
            process = None
            stderr_thread = None
            stderr_lines: list = []
            try:
                from ..terminal.spawner import get_spawner

                spawner = get_spawner()

                if self._is_command_available(session, "rsync"):
                    ssh_cmd = (
                        f"ssh -o ControlPath={spawner._get_ssh_control_path(session)}"
                    )

                    # FIX: Add trailing slash to source for rsync directory copy
                    source_path_rsync = str(local_path)
                    if is_directory:
                        source_path_rsync = str(local_path).rstrip("/") + "/"

                    transfer_cmd = [
                        "rsync",
                        "-avz",
                        "--progress",
                        "-e",
                        ssh_cmd,
                        source_path_rsync,
                        f"{session.user}@{session.host}:{remote_path}",
                    ]
                    process = self._start_process(transfer_id, transfer_cmd)

                    # Start stderr draining thread to prevent deadlock
                    # Deadlock can occur if stderr buffer fills while we read stdout
                    stderr_thread = threading.Thread(
                        target=_drain_stderr_to_list,
                        args=(process.stderr, stderr_lines),
                        daemon=True,
                    )
                    stderr_thread.start()

                    full_output = ""
                    for line in iter(process.stdout.readline, ""):
                        if cancellation_event and cancellation_event.is_set():
                            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                            raise OperationCancelledError("Upload cancelled by user.")

                        full_output += line
                        match = _PROGRESS_PERCENT_PATTERN.search(line)
                        if match and progress_callback:
                            progress = float(match.group(1))
                            GLib.idle_add(progress_callback, transfer_id, progress)

                    # Wait for stderr thread to complete
                    if stderr_thread and stderr_thread.is_alive():
                        stderr_thread.join(timeout=2.0)

                    stderr_output = "".join(stderr_lines)
                    process.wait()
                    exit_code = process.returncode

                    if exit_code == 0:
                        GLib.idle_add(
                            completion_callback,
                            transfer_id,
                            True,
                            "Upload completed successfully.",
                        )
                    else:
                        error_message = self._parse_transfer_error(full_output + stderr_output)
                        GLib.idle_add(
                            completion_callback,
                            transfer_id,
                            False,
                            error_message,
                        )
                else:  # SFTP fallback
                    # ... (SFTP logic remains the same)
                    sftp_cmd_base = spawner.command_builder.build_remote_command(
                        "sftp", session
                    )

                    # FIX: For SFTP directory copy, destination must be the parent directory
                    dest_path_sftp = remote_path
                    if is_directory:
                        dest_path_sftp = str(Path(remote_path).parent)

                    with tempfile.NamedTemporaryFile(
                        mode="w", delete=False, suffix=".sftp"
                    ) as batch_file:
                        batch_file.write(
                            f'put -r "{str(local_path)}" "{dest_path_sftp}"\nquit\n'
                        )
                        batch_file_path = batch_file.name
                    transfer_cmd = sftp_cmd_base + ["-b", batch_file_path]

                    process = self._start_process(transfer_id, transfer_cmd)

                    stdout, stderr = process.communicate()
                    exit_code = process.returncode
                    if "batch_file_path" in locals() and Path(batch_file_path).exists():
                        Path(batch_file_path).unlink()

                    if exit_code == 0:
                        GLib.idle_add(
                            completion_callback,
                            transfer_id,
                            True,
                            "Upload completed successfully.",
                        )
                    else:
                        error_msg = self._parse_transfer_error(stdout + stderr)
                        GLib.idle_add(
                            completion_callback, transfer_id, False, error_msg
                        )

            except OperationCancelledError:
                self.logger.warning(f"Upload cancelled for {local_path}")
                if completion_callback:
                    GLib.idle_add(completion_callback, transfer_id, False, "Cancelled")
            except Exception as e:
                self.logger.error(f"Exception during upload: {e}")
                if completion_callback:
                    GLib.idle_add(completion_callback, transfer_id, False, str(e))
            finally:
                with self._lock:
                    if transfer_id in self._active_processes:
                        del self._active_processes[transfer_id]

        threading.Thread(target=upload_thread, daemon=True).start()
