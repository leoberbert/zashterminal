"""Unit tests for ProxyJump, transcript logging, and broadcast-group helpers."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from zashterminal.sessions.models import SessionItem
from zashterminal.utils.proxy_jump import normalize_proxy_jump, validate_proxy_jump
from zashterminal.utils.ssh_config_parser import SSHConfigParser
from zashterminal.utils.transcript import (
    TranscriptWriter,
    build_transcript_path,
    default_transcript_directory,
    resolve_transcript_enabled,
    strip_ansi,
)


class ProxyJumpHelperTests(unittest.TestCase):
    def test_normalize_and_validate(self):
        self.assertEqual(normalize_proxy_jump("  a@b , c@d:22 "), "a@b,c@d:22")
        self.assertEqual(normalize_proxy_jump(""), "")
        self.assertEqual(normalize_proxy_jump(None), "")
        ok, err = validate_proxy_jump("leo@bastion.corp")
        self.assertTrue(ok)
        self.assertEqual(err, "")
        ok, err = validate_proxy_jump("leo@bastion,admin@jump2:2222")
        self.assertTrue(ok)
        bad, msg = validate_proxy_jump("leo@")
        self.assertFalse(bad)
        self.assertIn("Invalid", msg)
        # Bracketed IPv6 with port is accepted; bare multi-colon is not.
        ok, _ = validate_proxy_jump("user@[2001:db8::1]:22")
        self.assertTrue(ok)
        bad, msg = validate_proxy_jump("2001:db8::1")
        self.assertFalse(bad)
        self.assertIn("Invalid", msg)
        ok, _ = validate_proxy_jump(None)
        self.assertTrue(ok)


class SessionProxyJumpModelTests(unittest.TestCase):
    def test_roundtrip_dict(self):
        session = SessionItem(
            name="db",
            host="10.0.0.5",
            user="admin",
            proxy_jump=" leo@bastion ",
            broadcast_group=" db ",
            transcript_logging=True,
        )
        self.assertEqual(session.proxy_jump, "leo@bastion")
        self.assertEqual(session.broadcast_group, "db")
        self.assertTrue(session.transcript_logging)
        restored = SessionItem.from_dict(session.to_dict())
        self.assertEqual(restored.proxy_jump, "leo@bastion")
        self.assertEqual(restored.broadcast_group, "db")
        self.assertTrue(restored.transcript_logging)

        session.proxy_jump = " other@jump "
        session.broadcast_group = " web "
        session.transcript_logging = False
        self.assertEqual(session.proxy_jump, "other@jump")
        self.assertEqual(session.broadcast_group, "web")
        self.assertFalse(session.transcript_logging)

    def test_invalid_proxy_jump_fails_validation(self):
        session = SessionItem(name="bad", host="h", user="u", proxy_jump="user@")
        self.assertFalse(session.validate())


class SSHConfigProxyJumpTests(unittest.TestCase):
    def test_parses_proxyjump(self):
        content = """
Host db-prod
  HostName 10.0.2.20
  User admin
  Port 22
  ProxyJump leo@bastion.corp:22
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config"
            path.write_text(content, encoding="utf-8")
            entries = SSHConfigParser().parse(path)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].proxy_jump, "leo@bastion.corp:22")
        self.assertEqual(entries[0].hostname, "10.0.2.20")


class SpawnerProxyJumpTests(unittest.TestCase):
    def _make_spawner(self, persist: int = 600):
        from zashterminal.terminal.spawner import ProcessSpawner

        settings = MagicMock()
        settings.get.side_effect = lambda key, default=None: {
            "ssh_control_persist_duration": persist,
            "ssh_connect_timeout": 30,
        }.get(key, default)
        spawner = ProcessSpawner()
        spawner.settings_manager = settings
        return spawner

    def test_interactive_ssh_includes_proxyjump_option(self):
        session = SessionItem(
            name="db",
            host="10.0.0.5",
            user="admin",
            port=22,
            auth_type="key",
            auth_value="/tmp/id_test",
            proxy_jump="leo@bastion",
        )
        with (
            patch("zashterminal.terminal.spawner.has_command", return_value=True),
            patch(
                "zashterminal.utils.platform.shutil.which",
                return_value="/usr/bin/ssh",
            ),
            patch("zashterminal.utils.security.validate_ssh_key_file"),
        ):
            spawner = self._make_spawner()
            cmd, _env = spawner._build_remote_command_secure(
                session=session, command_type="ssh"
            )

        joined = " ".join(cmd)
        self.assertIn("ProxyJump=leo@bastion", joined)

    def test_sftp_also_gets_proxyjump(self):
        session = SessionItem(
            name="db",
            host="10.0.0.5",
            user="admin",
            auth_type="key",
            auth_value="/tmp/id_test",
            proxy_jump="jump1",
        )
        with (
            patch("zashterminal.terminal.spawner.has_command", return_value=True),
            patch(
                "zashterminal.utils.platform.shutil.which",
                return_value="/usr/bin/sftp",
            ),
            patch("zashterminal.utils.security.validate_ssh_key_file"),
        ):
            spawner = self._make_spawner(persist=0)
            cmd, _env = spawner._build_remote_command_secure(
                session=session, command_type="sftp"
            )
        self.assertIn("ProxyJump=jump1", " ".join(cmd))


class TranscriptHelperTests(unittest.TestCase):
    def test_strip_ansi_and_resolve(self):
        self.assertEqual(strip_ansi("\x1b[31mred\x1b[0m"), "red")
        self.assertEqual(strip_ansi(""), "")
        self.assertEqual(strip_ansi(None), "")
        self.assertTrue(
            resolve_transcript_enabled(global_enabled=False, session_override=True)
        )
        self.assertFalse(
            resolve_transcript_enabled(global_enabled=True, session_override=False)
        )
        self.assertTrue(
            resolve_transcript_enabled(global_enabled=True, session_override=None)
        )
        self.assertFalse(
            resolve_transcript_enabled(global_enabled=False, session_override=None)
        )
        default_dir = default_transcript_directory("")
        self.assertTrue(str(default_dir).endswith("zashterminal/transcripts"))
        custom = default_transcript_directory("/tmp/my-transcripts")
        self.assertEqual(custom, Path("/tmp/my-transcripts"))
        with patch.dict("os.environ", {"XDG_DATA_HOME": "/tmp/xdg-data"}, clear=False):
            self.assertEqual(
                default_transcript_directory(""),
                Path("/tmp/xdg-data/zashterminal/transcripts"),
            )

    def test_writer_and_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = build_transcript_path(
                Path(tmp),
                session_name="My DB!",
                host="10.0.0.1",
                when=datetime(2026, 11, 3, 21, 30, 0),
            )
            self.assertTrue(path.name.startswith("2026-11-03_213000_My_DB"))
            no_host = build_transcript_path(
                Path(tmp),
                session_name="local",
                host="",
                when=datetime(2026, 11, 3, 21, 30, 1),
            )
            self.assertTrue(no_host.name.endswith("_local.log"))
            writer = TranscriptWriter(path, strip_escapes=True)
            writer.write("\x1b[32mhello\x1b[0m")
            writer.write("")
            writer.note_row(3)
            self.assertEqual(writer.last_row, 3)
            writer.note_row(1)  # ignored — only advances
            self.assertEqual(writer.last_row, 3)
            writer.close()
            writer.write("after-close")  # no-op once closed
            text = path.read_text(encoding="utf-8")
            self.assertIn("hello", text)
            self.assertNotIn("\x1b[", text)
            self.assertIn("transcript started", text)
            self.assertIn("transcript ended", text)

            raw_path = Path(tmp) / "raw.log"
            raw = TranscriptWriter(raw_path, strip_escapes=False)
            raw.write("\x1b[31mkeep\x1b[0m")
            raw.close()
            self.assertIn("\x1b[31m", raw_path.read_text(encoding="utf-8"))


class BroadcastGroupKeyTests(unittest.TestCase):
    def test_group_matching_helper_logic(self):
        # Mirror the grouping rule used by the broadcast mixin.
        def group_of(session: SessionItem | None) -> str:
            return (session.broadcast_group or "").strip() if session else ""

        a = SessionItem(name="w1", host="h1", broadcast_group="web")
        b = SessionItem(name="w2", host="h2", broadcast_group="web")
        c = SessionItem(name="d1", host="h3", broadcast_group="db")
        d = SessionItem(name="local", session_type="local", broadcast_group="")
        focused = "web"
        matched = [s for s in (a, b, c, d) if group_of(s) == focused]
        self.assertEqual([s.name for s in matched], ["w1", "w2"])

    def test_resolve_broadcast_terminals_scopes(self):
        from zashterminal.ui.window_search_broadcast import WindowSearchBroadcastMixin

        class FakeWindow(WindowSearchBroadcastMixin):
            def __init__(self):
                self.tab_manager = MagicMock()
                self.toast_overlay = MagicMock()
                self.broadcast_scope_combo = MagicMock()
                self.settings_manager = MagicMock()

        win = FakeWindow()
        web = SimpleNamespace(
            zashterminal_session_item=SessionItem(
                name="w1", host="h1", broadcast_group="web"
            )
        )
        web2 = SimpleNamespace(
            zashterminal_session_item=SessionItem(
                name="w2", host="h2", broadcast_group="web"
            )
        )
        db = SimpleNamespace(
            zashterminal_session_item=SessionItem(
                name="d1", host="h3", broadcast_group="db"
            )
        )
        bare = SimpleNamespace(zashterminal_session_item=None)
        fleet = [web, web2, db, bare]

        win.tab_manager.get_selected_terminal.return_value = web
        win.broadcast_scope_combo.get_selected.return_value = 0
        self.assertEqual(win._resolve_broadcast_terminals(fleet), [web])

        win.broadcast_scope_combo.get_selected.return_value = 1
        self.assertEqual(win._resolve_broadcast_terminals(fleet), [web, web2])

        win.tab_manager.get_selected_terminal.return_value = bare
        win.tab_manager.get_page_for_terminal.side_effect = RuntimeError("no page")
        self.assertEqual(win._resolve_broadcast_terminals(fleet), [])
        win.toast_overlay.add_toast.assert_called()

        win.tab_manager.get_selected_terminal.return_value = web
        win.broadcast_scope_combo.get_selected.return_value = 2
        self.assertEqual(win._resolve_broadcast_terminals(fleet), fleet)

        win.broadcast_scope_combo.get_selected.return_value = 3
        self.assertIsNone(win._resolve_broadcast_terminals(fleet))

        win.tab_manager.get_selected_terminal.return_value = None
        win.broadcast_scope_combo.get_selected.return_value = 0
        self.assertEqual(win._resolve_broadcast_terminals(fleet), [])

        # Fallback: group from tab.session_item when terminal has no session attr.
        page = SimpleNamespace(
            tab=SimpleNamespace(
                session_item=SessionItem(name="t", host="h", broadcast_group="web")
            )
        )
        orphan = SimpleNamespace(zashterminal_session_item="not-a-session")
        win.tab_manager.get_page_for_terminal.side_effect = None
        win.tab_manager.get_page_for_terminal.return_value = page
        win.tab_manager.get_selected_terminal.return_value = orphan
        win.broadcast_scope_combo.get_selected.return_value = 1
        self.assertEqual(win._terminal_broadcast_group(orphan), "web")


class TranscriptPreferenceApplyTests(unittest.TestCase):
    def _make_manager(self):
        from zashterminal.terminal.manager import TerminalManager

        mgr = object.__new__(TerminalManager)
        mgr.logger = MagicMock()
        mgr.parent_window = MagicMock()
        mgr.parent_window.toast_overlay = MagicMock()
        mgr.registry = MagicMock()
        mgr.settings_manager = MagicMock()
        return mgr

    def test_disabling_stops_open_writers_unless_always_on(self):
        mgr = self._make_manager()

        auto_session = SessionItem(name="auto", host="h", transcript_logging=None)
        always_session = SessionItem(name="force", host="h", transcript_logging=True)
        auto_term = SimpleNamespace(zashterminal_session_item=auto_session)
        always_on_term = SimpleNamespace(zashterminal_session_item=always_session)

        with tempfile.TemporaryDirectory() as tmp:
            auto_writer = TranscriptWriter(Path(tmp) / "a.log")
            always_writer = TranscriptWriter(Path(tmp) / "b.log")
            auto_term._zashterminal_transcript = auto_writer
            always_on_term._zashterminal_transcript = always_writer

            mgr.registry.get_all_terminal_ids.return_value = [1, 2, 3]
            mgr.registry.get_terminal.side_effect = lambda tid: {
                1: auto_term,
                2: always_on_term,
                3: None,
            }[tid]

            stopped = mgr.apply_transcript_preference_change(False)
            self.assertEqual(stopped, 1)
            self.assertIsNone(getattr(auto_term, "_zashterminal_transcript", None))
            # Always-on session keeps recording
            self.assertIs(always_on_term._zashterminal_transcript, always_writer)
            always_writer.close()

    def test_attach_flush_and_contents_changed(self):
        from gi.repository import Vte

        mgr = self._make_manager()
        mgr.settings_manager.get.side_effect = lambda key, default=None: {
            "transcript_logging_enabled": True,
            "transcript_directory": "",
            "transcript_strip_ansi": True,
        }.get(key, default)

        session = SessionItem(
            name="db", host="10.0.0.5", user="u", transcript_logging=True
        )
        terminal = MagicMock()
        terminal.zashterminal_handler_ids = []
        terminal.get_cursor_position.return_value = (0, 2)
        terminal.get_column_count.return_value = 80
        terminal.get_text_range_format.return_value = ("line one\nline two", 20)

        with tempfile.TemporaryDirectory() as tmp:
            mgr.settings_manager.get.side_effect = lambda key, default=None: {
                "transcript_logging_enabled": True,
                "transcript_directory": tmp,
                "transcript_strip_ansi": True,
            }.get(key, default)

            mgr._attach_transcript_logger(terminal, session, terminal_id=7)
            writer = terminal._zashterminal_transcript
            self.assertIsInstance(writer, TranscriptWriter)
            self.assertTrue(terminal.zashterminal_handler_ids)
            mgr.parent_window.toast_overlay.add_toast.assert_called()

            # Cursor advanced → flush completed lines (not force).
            writer.note_row(0)
            mgr._flush_transcript_line(terminal, writer, force=False)
            self.assertGreaterEqual(writer.last_row, 2)

            # Same row without force → no-op.
            terminal.get_cursor_position.return_value = (0, writer.last_row)
            before = writer.last_row
            mgr._flush_transcript_line(terminal, writer, force=False)
            self.assertEqual(writer.last_row, before)

            # Force flush captures current partial line.
            terminal.get_cursor_position.return_value = (0, before)
            terminal.get_text_range_format.return_value = ("partial", 7)
            mgr._flush_transcript_line(terminal, writer, force=True)

            # Scrollback reset path.
            terminal.get_cursor_position.return_value = (0, 0)
            writer.note_row(5)
            mgr._flush_transcript_line(terminal, writer, force=False)
            self.assertEqual(writer.last_row, 5)  # note_row only advances

            # contents-changed handler
            mgr._on_terminal_contents_changed_for_transcript(terminal)
            bare = SimpleNamespace()
            mgr._on_terminal_contents_changed_for_transcript(bare)

            # Disabled attach is a no-op.
            quiet = SimpleNamespace(zashterminal_handler_ids=[])
            mgr.settings_manager.get.side_effect = lambda key, default=None: {
                "transcript_logging_enabled": False,
                "transcript_directory": tmp,
                "transcript_strip_ansi": True,
            }.get(key, default)
            off = SessionItem(name="off", host="h", transcript_logging=None)
            mgr._attach_transcript_logger(quiet, off, terminal_id=9)
            self.assertIsNone(getattr(quiet, "_zashterminal_transcript", None))

            # String identifier path
            mgr.settings_manager.get.side_effect = lambda key, default=None: {
                "transcript_logging_enabled": True,
                "transcript_directory": tmp,
                "transcript_strip_ansi": False,
            }.get(key, default)
            local = SimpleNamespace(zashterminal_handler_ids=[])
            # connect() must return a handler id
            local.connect = MagicMock(return_value=42)
            mgr._attach_transcript_logger(local, "Local Shell", terminal_id=11)
            self.assertIsInstance(local._zashterminal_transcript, TranscriptWriter)
            mgr._detach_transcript_logger(local)
            self.assertIsNone(getattr(local, "_zashterminal_transcript", None))

            mgr._detach_transcript_logger(terminal)

            # Toast helpers tolerate missing overlay.
            mgr.parent_window = None
            mgr._notify_transcript_started(Path(tmp) / "x.log")
            mgr._toast("hello")

        # Vte.Format referenced so import stays meaningful for static checkers.
        self.assertTrue(hasattr(Vte, "Format"))


if __name__ == "__main__":
    unittest.main()
