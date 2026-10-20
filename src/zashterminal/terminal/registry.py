# zashterminal/terminal/registry.py
"""Terminal registry, lifecycle, and manual SSH tracking."""

from __future__ import annotations

import threading
import time
import weakref
from enum import Enum
from typing import Any, Dict, List, Optional, Union

import gi

gi.require_version("Vte", "3.91")
from gi.repository import GLib, Vte

from ..sessions.models import SessionItem
from ..utils.logger import get_logger

PSUTIL_AVAILABLE: Optional[bool] = None
psutil = None


def _get_psutil():
    """Lazy import psutil module."""
    global psutil, PSUTIL_AVAILABLE
    if PSUTIL_AVAILABLE is None:
        try:
            import psutil as _psutil

            psutil = _psutil
            PSUTIL_AVAILABLE = True
        except ImportError:
            PSUTIL_AVAILABLE = False
    return psutil


class TerminalState(Enum):
    FOCUSED = "focused"
    UNFOCUSED = "unfocused"
    EXITED = "exited"
    SPAWN_FAILED = "spawn_failed"


class TerminalLifecycleManager:
    def __init__(self, registry, logger):
        self.registry = registry
        self.logger = logger
        self._closing_terminals = set()
        self._lock = threading.RLock()

    def mark_terminal_closing(self, terminal_id: int) -> bool:
        with self._lock:
            if terminal_id in self._closing_terminals:
                return False
            self._closing_terminals.add(terminal_id)
            return True

    def unmark_terminal_closing(self, terminal_id: int) -> None:
        with self._lock:
            self._closing_terminals.discard(terminal_id)

    def transition_state(self, terminal_id: int, new_state: TerminalState) -> bool:
        with self._lock:
            terminal_info = self.registry.get_terminal_info(terminal_id)
            if not terminal_info:
                return False
            current_state = terminal_info.get("status", "")
            if new_state == TerminalState.EXITED and current_state.startswith("exited"):
                return False
            self.registry.update_terminal_status(terminal_id, new_state.value)
            return True


class ManualSSHTracker:
    def __init__(self, registry, on_state_changed_callback):
        self.logger = get_logger("zashterminal.terminal.ssh_tracker")
        self.registry = registry
        self.on_state_changed = on_state_changed_callback
        self._tracked_terminals = {}
        self._lock = threading.Lock()
        self._last_child_count = {}

    def track(self, terminal_id: int, terminal: Vte.Terminal):
        with self._lock:
            if terminal_id not in self._tracked_terminals:
                self._tracked_terminals[terminal_id] = {
                    "terminal_ref": weakref.ref(terminal),
                    "in_ssh": False,
                    "ssh_target": None,
                }

    def untrack(self, terminal_id: int):
        with self._lock:
            self._tracked_terminals.pop(terminal_id, None)
            self._last_child_count.pop(terminal_id, None)

    def get_ssh_target(self, terminal_id: int) -> Optional[str]:
        with self._lock:
            state = self._tracked_terminals.get(terminal_id)
            if state and state.get("in_ssh"):
                return state.get("ssh_target")
            return None

    def check_process_tree(self, terminal_id: int):
        psutil_mod = _get_psutil()
        if not psutil_mod:
            return
        with self._lock:
            if terminal_id not in self._tracked_terminals:
                return
            state = self._tracked_terminals[terminal_id]
            terminal_info = self.registry.get_terminal_info(terminal_id)
            if not terminal_info or terminal_info.get("type") != "local":
                return
            pid = terminal_info.get("process_id")
            if not pid:
                return
            try:
                parent_proc = psutil_mod.Process(pid)
                current_children_count = len(parent_proc.children())

                if self._last_child_count.get(terminal_id) == current_children_count:
                    if not state["in_ssh"]:
                        return

                self._last_child_count[terminal_id] = current_children_count

                children = parent_proc.children(recursive=True)
                ssh_proc = next(
                    (p for p in children if p.name().lower() == "ssh"), None
                )
                currently_in_ssh = ssh_proc is not None
                if currently_in_ssh != state["in_ssh"]:
                    if currently_in_ssh:
                        state["in_ssh"] = True
                        cmdline = ssh_proc.cmdline()
                        state["ssh_target"] = next(
                            (arg for arg in cmdline if "@" in arg), ssh_proc.name()
                        )
                        self.logger.info(
                            f"Detected manual SSH session in terminal {terminal_id}: {state['ssh_target']}"
                        )
                    else:
                        self.logger.info(
                            f"Manual SSH session ended in terminal {terminal_id}"
                        )
                        state["in_ssh"] = False
                        state["ssh_target"] = None
                    terminal = state["terminal_ref"]()
                    if terminal and self.on_state_changed:
                        GLib.idle_add(self.on_state_changed, terminal)
            except psutil_mod.NoSuchProcess:
                if state["in_ssh"]:
                    state["in_ssh"] = False
                    state["ssh_target"] = None
                    terminal = state["terminal_ref"]()
                    if terminal and self.on_state_changed:
                        GLib.idle_add(self.on_state_changed, terminal)
            except Exception as e:
                self.logger.debug(
                    f"Error checking process tree for terminal {terminal_id}: {e}"
                )


class TerminalRegistry:
    def __init__(self):
        self.logger = get_logger("zashterminal.terminal.registry")
        self._terminals: Dict[int, Dict[str, Any]] = {}
        self._terminal_refs: Dict[int, weakref.ReferenceType] = {}
        self._lock = threading.RLock()
        self._next_id = 1

    def register_terminal(
        self,
        terminal: Vte.Terminal,
        terminal_type: str,
        identifier: Union[str, SessionItem],
    ) -> int:
        with self._lock:
            terminal_id = self._next_id
            self._next_id += 1
            self._terminals[terminal_id] = {
                "type": terminal_type,
                "identifier": identifier,
                "created_at": time.time(),
                "process_id": None,
                "status": "initializing",
            }

            def cleanup_callback(ref):
                self._cleanup_terminal_ref(terminal_id)

            self._terminal_refs[terminal_id] = weakref.ref(terminal, cleanup_callback)
            return terminal_id

    def reregister_terminal(
        self, terminal: Vte.Terminal, terminal_id: int, terminal_info: Dict[str, Any]
    ):
        """Re-registers a terminal that was moved from another window."""
        with self._lock:
            self._terminals[terminal_id] = terminal_info
            self._terminal_refs[terminal_id] = weakref.ref(
                terminal, lambda ref: self._cleanup_terminal_ref(terminal_id)
            )
            self._next_id = max(self._next_id, terminal_id + 1)
            self.logger.info(f"Re-registered terminal {terminal_id} in new window.")

    def deregister_terminal_for_move(
        self, terminal_id: int
    ) -> Optional[Dict[str, Any]]:
        """Removes a terminal from the registry for moving, without cleanup."""
        with self._lock:
            if terminal_id in self._terminals:
                self.logger.info(f"De-registering terminal {terminal_id} for move.")
                self._terminal_refs.pop(terminal_id, None)
                return self._terminals.pop(terminal_id, None)
            return None

    def get_active_terminal_count(self) -> int:
        with self._lock:
            return sum(
                1
                for info in self._terminals.values()
                if info.get("status") not in ["exited", "spawn_failed"]
            )

    def update_terminal_process(self, terminal_id: int, process_id: int) -> None:
        with self._lock:
            if terminal_id in self._terminals:
                self._terminals[terminal_id]["process_id"] = process_id
                self._terminals[terminal_id]["status"] = "running"

    def update_terminal_status(self, terminal_id: int, status: str) -> None:
        with self._lock:
            if terminal_id in self._terminals:
                self._terminals[terminal_id]["status"] = status

    def get_terminal(self, terminal_id: int) -> Optional[Vte.Terminal]:
        with self._lock:
            ref = self._terminal_refs.get(terminal_id)
            return ref() if ref else None

    def get_terminal_info(self, terminal_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._terminals.get(terminal_id, {}).copy()

    def unregister_terminal(self, terminal_id: int) -> bool:
        with self._lock:
            if terminal_id in self._terminals:
                del self._terminals[terminal_id]
                if terminal_id in self._terminal_refs:
                    del self._terminal_refs[terminal_id]
                return True
            return False

    def _cleanup_terminal_ref(self, terminal_id: int) -> None:
        with self._lock:
            if terminal_id in self._terminal_refs:
                del self._terminal_refs[terminal_id]

    def get_all_terminal_ids(self) -> List[int]:
        with self._lock:
            return list(self._terminals.keys())

    def get_terminals_for_session(self, session_name: str) -> List[int]:
        """Get all terminal IDs for a given session name."""
        with self._lock:
            result = []
            for tid, info in self._terminals.items():
                identifier = info.get("identifier")
                if (
                    isinstance(identifier, SessionItem)
                    and identifier.name == session_name
                ):
                    result.append(tid)
            return result

    def get_active_ssh_sessions(self) -> Dict[str, List[int]]:
        """Get all active SSH/SFTP sessions grouped by session name."""
        with self._lock:
            sessions: Dict[str, List[int]] = {}
            for tid, info in self._terminals.items():
                if info.get("type") in ["ssh", "sftp"]:
                    identifier = info.get("identifier")
                    if isinstance(identifier, SessionItem):
                        name = identifier.name
                        if name not in sessions:
                            sessions[name] = []
                        sessions[name].append(tid)
            return sessions

    def get_terminals_by_status(self, status: str) -> List[int]:
        """Get all terminal IDs with a specific status."""
        with self._lock:
            return [
                tid
                for tid, info in self._terminals.items()
                if info.get("status") == status
            ]

    def get_terminals_by_type(self, terminal_type: str) -> List[int]:
        """Get all terminal IDs of a specific type."""
        with self._lock:
            return [
                tid
                for tid, info in self._terminals.items()
                if info.get("type") == terminal_type
            ]

    def get_session_terminal_count(self, session_name: str) -> int:
        """Get the count of terminals for a session."""
        return len(self.get_terminals_for_session(session_name))

    def update_terminal_connection_status(
        self, terminal_id: int, connected: bool, error_message: Optional[str] = None
    ) -> None:
        """Update the connection status of an SSH/SFTP terminal."""
        with self._lock:
            if terminal_id in self._terminals:
                info = self._terminals[terminal_id]
                if connected:
                    info["status"] = "connected"
                    info["connected_at"] = time.time()
                    info.pop("last_error", None)
                    info["reconnect_attempts"] = 0
                else:
                    info["status"] = "disconnected"
                    info["disconnected_at"] = time.time()
                    if error_message:
                        info["last_error"] = error_message

    def increment_reconnect_attempts(self, terminal_id: int) -> int:
        """Increment and return the reconnect attempt count for a terminal."""
        with self._lock:
            if terminal_id in self._terminals:
                info = self._terminals[terminal_id]
                attempts = info.get("reconnect_attempts", 0) + 1
                info["reconnect_attempts"] = attempts
                return attempts
            return 0
