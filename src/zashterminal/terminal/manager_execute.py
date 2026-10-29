# zashterminal/terminal/manager_execute.py
"""Schedule and feed CLI -e / close-after execute payloads into VTE."""

from typing import Optional

import gi
gi.require_version("Vte", "3.91")
from gi.repository import GLib, Vte

from .execute_command import build_feed_child_payloads


class TerminalExecuteMixin:
    def _schedule_execute_command(
        self, terminal: Vte.Terminal, command: str, close_after_execute: bool
    ) -> None:
        if not terminal or not command:
            return
        if getattr(terminal, "_execute_command_ran", False):
            return

        terminal._pending_execute_command = (command, close_after_execute)

        if terminal.get_current_directory_uri():
            GLib.idle_add(self._run_pending_execute_command, terminal)
            return

        if getattr(terminal, "_execute_command_timer_id", None):
            return

        terminal._execute_command_timer_id = GLib.timeout_add(
            500, lambda: self._run_pending_execute_command(terminal)
        )

    def _run_pending_execute_command(self, terminal: Vte.Terminal) -> bool:
        if getattr(terminal, "_execute_command_ran", False):
            return False

        pending = getattr(terminal, "_pending_execute_command", None)
        if not pending:
            return False

        command, close_after_execute = pending
        self._execute_command_in_terminal(
            terminal,
            command,
            close_after_execute,
        )
        terminal._execute_command_ran = True
        try:
            delattr(terminal, "_pending_execute_command")
        except Exception:
            pass

        timer_id = getattr(terminal, "_execute_command_timer_id", None)
        if timer_id:
            try:
                GLib.source_remove(timer_id)
            except Exception:
                pass
            terminal._execute_command_timer_id = None
        return False

    def _execute_command_in_terminal(
        self, terminal: Vte.Terminal, command: str, close_after_execute: bool = False
    ) -> bool:
        try:
            if not terminal or not command:
                return False

            payloads = build_feed_child_payloads(command, close_after_execute)
            if not payloads and not close_after_execute:
                return False
            for payload in payloads:
                terminal.feed_child(payload)
            return True
        except Exception as e:
            self.logger.error(f"Failed to execute command '{command}': {e}")
            return False
