"""Integration-style tests for -e / close-after beyond CLI parsing.

Covers feed_child(+exit), spawn scheduling, and last-tab quit without a
live GTK window. Run with: PYTHONPATH=src python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import threading
import unittest
from unittest.mock import MagicMock, patch

from zashterminal.terminal.manager import TerminalManager
from zashterminal.terminal.manager_execute import TerminalExecuteMixin
from zashterminal.terminal.tabs import TabManager
from zashterminal.window import ZashTerminalWindow


class ExecuteFeedChildTests(unittest.TestCase):
    def setUp(self):
        self.mgr = MagicMock()
        self.mgr.logger = MagicMock()
        self.terminal = MagicMock()
        self.feeds: list[bytes] = []
        self.terminal.feed_child.side_effect = lambda data: self.feeds.append(data)

    def test_close_after_feeds_command_then_exit(self):
        ok = TerminalExecuteMixin._execute_command_in_terminal(
            self.mgr, self.terminal, "echo a\necho b", True
        )
        self.assertTrue(ok)
        self.assertEqual(self.feeds, [b"echo a\n", b"echo b\n", b"exit\n"])

    def test_no_close_after_skips_exit(self):
        ok = TerminalExecuteMixin._execute_command_in_terminal(
            self.mgr, self.terminal, "htop", False
        )
        self.assertTrue(ok)
        self.assertEqual(self.feeds, [b"htop\n"])
        self.assertNotIn(b"exit\n", self.feeds)

    def test_falsy_command_is_noop(self):
        self.assertFalse(
            TerminalExecuteMixin._execute_command_in_terminal(
                self.mgr, self.terminal, "", True
            )
        )
        self.terminal.feed_child.assert_not_called()

    def test_whitespace_only_with_close_still_sends_exit(self):
        # Matches current manager behavior: blank lines are filtered, then exit.
        ok = TerminalExecuteMixin._execute_command_in_terminal(
            self.mgr, self.terminal, "  \n  ", True
        )
        self.assertTrue(ok)
        self.assertEqual(self.feeds, [b"exit\n"])

    def test_blank_lines_are_skipped(self):
        TerminalExecuteMixin._execute_command_in_terminal(
            self.mgr, self.terminal, "one\n\n\ntwo\n", False
        )
        self.assertEqual(self.feeds, [b"one\n", b"two\n"])


class ScheduleExecuteTests(unittest.TestCase):
    def setUp(self):
        self.mgr = MagicMock()
        self.mgr.logger = MagicMock()
        self.terminal = MagicMock()
        self.terminal._execute_command_ran = False
        self.terminal._execute_command_timer_id = None

    def test_idle_when_cwd_uri_ready(self):
        self.terminal.get_current_directory_uri.return_value = "file:///tmp"
        with patch("zashterminal.terminal.manager_execute.GLib") as glib:
            TerminalExecuteMixin._schedule_execute_command(
                self.mgr, self.terminal, "true", True
            )
            glib.idle_add.assert_called_once_with(
                self.mgr._run_pending_execute_command_idle, self.terminal
            )
            glib.timeout_add.assert_not_called()
        self.assertEqual(
            self.terminal._pending_execute_command, ("true", True)
        )

    def test_timeout_when_cwd_uri_missing(self):
        self.terminal.get_current_directory_uri.return_value = None
        with patch("zashterminal.terminal.manager_execute.GLib") as glib:
            TerminalExecuteMixin._schedule_execute_command(
                self.mgr, self.terminal, "uptime", False
            )
            glib.timeout_add.assert_called_once()
            args, _kwargs = glib.timeout_add.call_args
            self.assertEqual(args[0], 500)
            self.assertEqual(args[1], self.mgr._run_pending_execute_command_idle)
            self.assertIs(args[2], self.terminal)
        self.assertEqual(
            self.terminal._pending_execute_command, ("uptime", False)
        )

    def test_run_pending_marks_ran_and_clears(self):
        self.terminal._pending_execute_command = ("seq 1 3", True)
        self.terminal._execute_command_timer_id = 99
        with patch("zashterminal.terminal.manager_execute.GLib") as glib:
            TerminalExecuteMixin._run_pending_execute_command(
                self.mgr, self.terminal
            )
        self.mgr._execute_command_in_terminal.assert_called_once_with(
            self.terminal, "seq 1 3", True
        )
        self.assertTrue(self.terminal._execute_command_ran)
        self.assertFalse(hasattr(self.terminal, "_pending_execute_command"))
        self.assertIsNone(self.terminal._execute_command_timer_id)
        glib.source_remove.assert_called_once_with(99)

    def test_run_pending_skips_if_already_ran(self):
        self.terminal._execute_command_ran = True
        self.terminal._pending_execute_command = ("true", True)
        TerminalExecuteMixin._run_pending_execute_command(self.mgr, self.terminal)
        self.mgr._execute_command_in_terminal.assert_not_called()

    def test_idle_wrapper_stops_glib_source(self):
        self.mgr._run_pending_execute_command = MagicMock()
        with patch("zashterminal.terminal.manager_execute.GLib") as glib:
            glib.SOURCE_REMOVE = False
            result = TerminalExecuteMixin._run_pending_execute_command_idle(
                self.mgr, self.terminal
            )
        self.mgr._run_pending_execute_command.assert_called_once_with(self.terminal)
        self.assertIs(result, glib.SOURCE_REMOVE)


class SpawnCallbackExecuteTests(unittest.TestCase):
    def setUp(self):
        self.mgr = MagicMock()
        self.mgr.logger = MagicMock()
        self.mgr.tab_manager = None
        self.mgr.registry = MagicMock()
        self.terminal = MagicMock()
        self.terminal._auto_reconnect_active = False
        self.terminal._retry_in_progress = False

    def _user_data(self, execute_command, close_after):
        return {
            "original_user_data": (
                7,
                {
                    "execute_command": execute_command,
                    "close_after_execute": close_after,
                },
            )
        }

    def test_spawn_success_schedules_execute_with_close_flag(self):
        TerminalManager._on_spawn_callback(
            self.mgr,
            self.terminal,
            4242,
            None,
            self._user_data("true", True),
        )
        self.mgr.registry.update_terminal_process.assert_called_once_with(7, 4242)
        self.mgr._schedule_execute_command.assert_called_once_with(
            self.terminal, "true", True
        )

    def test_spawn_success_without_execute_does_not_schedule(self):
        TerminalManager._on_spawn_callback(
            self.mgr,
            self.terminal,
            1,
            None,
            self._user_data(None, False),
        )
        self.mgr._schedule_execute_command.assert_not_called()

    def test_spawn_error_skips_execute(self):
        error = MagicMock()
        error.message = "boom"
        TerminalManager._on_spawn_callback(
            self.mgr,
            self.terminal,
            -1,
            error,
            self._user_data("true", True),
        )
        self.mgr.registry.update_terminal_status.assert_called_once_with(
            7, "spawn_failed"
        )
        self.mgr._schedule_execute_command.assert_not_called()


class CreateExecuteTabWiringTests(unittest.TestCase):
    def test_window_create_execute_tab_forwards_close_after(self):
        window = MagicMock()
        window.tab_manager = MagicMock()
        ZashTerminalWindow.create_execute_tab(window, "ls -la", "/tmp", True)
        window.tab_manager.create_local_tab.assert_called_once_with(
            working_directory="/tmp",
            execute_command="ls -la",
            close_after_execute=True,
        )

    def test_window_create_execute_tab_keeps_shell_open(self):
        window = MagicMock()
        window.tab_manager = MagicMock()
        ZashTerminalWindow.create_execute_tab(window, "htop", None, False)
        window.tab_manager.create_local_tab.assert_called_once_with(
            working_directory=None,
            execute_command="htop",
            close_after_execute=False,
        )


class LastTabQuitTests(unittest.TestCase):
    def _tab_manager(self):
        tm = MagicMock()
        tm._cleanup_lock = threading.Lock()
        tm.logger = MagicMock()
        tm.terminal_manager = MagicMock()
        tm.terminal_manager.is_auto_reconnect_active.return_value = False
        tm._find_pane_and_parent.return_value = (None, None)
        return tm

    def test_last_active_terminal_requests_quit(self):
        tm = self._tab_manager()
        page = MagicMock()
        page.get_title.return_value = "Local"
        tm.get_page_for_terminal.return_value = page
        tm.get_all_active_terminals_in_page.return_value = []
        tm.terminal_manager.registry.get_active_terminal_count.return_value = 0
        tm._close_tab_by_page = MagicMock()
        tm._quit_application = MagicMock()

        terminal = MagicMock()
        terminal.terminal_id = 3

        with patch("zashterminal.terminal.tabs.GLib") as glib:
            TabManager._on_terminal_process_exited(tm, terminal, 0, "Local")

        tm._close_tab_by_page.assert_called_once_with(page)
        glib.idle_add.assert_called_once_with(tm._quit_application)

    def test_remaining_terminals_do_not_quit(self):
        tm = self._tab_manager()
        page = MagicMock()
        page.get_title.return_value = "Local"
        tm.get_page_for_terminal.return_value = page
        other = MagicMock()
        tm.get_all_active_terminals_in_page.return_value = [other]
        tm.terminal_manager.registry.get_active_terminal_count.return_value = 1
        tm._close_tab_by_page = MagicMock()
        tm._quit_application = MagicMock()

        with patch("zashterminal.terminal.tabs.GLib") as glib:
            TabManager._on_terminal_process_exited(
                tm, MagicMock(terminal_id=1), 0, "Local"
            )

        tm._close_tab_by_page.assert_not_called()
        glib.idle_add.assert_not_called()

    def test_auto_reconnect_skips_cleanup_and_quit(self):
        tm = self._tab_manager()
        tm.terminal_manager.is_auto_reconnect_active.return_value = True
        tm._close_tab_by_page = MagicMock()

        with patch("zashterminal.terminal.tabs.GLib") as glib:
            TabManager._on_terminal_process_exited(
                tm, MagicMock(terminal_id=9), 1, "ssh"
            )

        tm.terminal_manager._cleanup_terminal.assert_not_called()
        glib.idle_add.assert_not_called()


if __name__ == "__main__":
    unittest.main()
