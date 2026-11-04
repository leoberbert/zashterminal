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
        ok, err = validate_proxy_jump("leo@bastion.corp")
        self.assertTrue(ok)
        self.assertEqual(err, "")
        ok, err = validate_proxy_jump("leo@bastion,admin@jump2:2222")
        self.assertTrue(ok)
        bad, msg = validate_proxy_jump("leo@")
        self.assertFalse(bad)
        self.assertIn("Invalid", msg)


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
        self.assertTrue(
            resolve_transcript_enabled(global_enabled=False, session_override=True)
        )
        self.assertFalse(
            resolve_transcript_enabled(global_enabled=True, session_override=False)
        )
        self.assertTrue(
            resolve_transcript_enabled(global_enabled=True, session_override=None)
        )
        default_dir = default_transcript_directory("")
        self.assertTrue(str(default_dir).endswith("zashterminal/transcripts"))
        custom = default_transcript_directory("/tmp/my-transcripts")
        self.assertEqual(custom, Path("/tmp/my-transcripts"))

    def test_writer_and_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = build_transcript_path(
                Path(tmp),
                session_name="My DB!",
                host="10.0.0.1",
                when=datetime(2026, 11, 3, 21, 30, 0),
            )
            self.assertTrue(path.name.startswith("2026-11-03_213000_My_DB"))
            writer = TranscriptWriter(path, strip_escapes=True)
            writer.write("\x1b[32mhello\x1b[0m")
            writer.close()
            text = path.read_text(encoding="utf-8")
            self.assertIn("hello", text)
            self.assertNotIn("\x1b[", text)
            self.assertIn("transcript started", text)
            self.assertIn("transcript ended", text)


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


class TranscriptPreferenceApplyTests(unittest.TestCase):
    def test_disabling_stops_open_writers_unless_always_on(self):
        from zashterminal.terminal.manager import TerminalManager
        from zashterminal.utils.transcript import TranscriptWriter

        mgr = object.__new__(TerminalManager)
        mgr.logger = MagicMock()
        mgr.parent_window = MagicMock()
        mgr.parent_window.toast_overlay = MagicMock()
        mgr.registry = MagicMock()

        auto_session = SessionItem(name="auto", host="h", transcript_logging=None)
        always_session = SessionItem(name="force", host="h", transcript_logging=True)
        auto_term = SimpleNamespace(zashterminal_session_item=auto_session)
        always_on_term = SimpleNamespace(zashterminal_session_item=always_session)

        with tempfile.TemporaryDirectory() as tmp:
            auto_writer = TranscriptWriter(Path(tmp) / "a.log")
            always_writer = TranscriptWriter(Path(tmp) / "b.log")
            auto_term._zashterminal_transcript = auto_writer
            always_on_term._zashterminal_transcript = always_writer

            mgr.registry.get_all_terminal_ids.return_value = [1, 2]
            mgr.registry.get_terminal.side_effect = lambda tid: {
                1: auto_term,
                2: always_on_term,
            }[tid]

            stopped = mgr.apply_transcript_preference_change(False)
            self.assertEqual(stopped, 1)
            self.assertIsNone(getattr(auto_term, "_zashterminal_transcript", None))
            # Always-on session keeps recording
            self.assertIs(always_on_term._zashterminal_transcript, always_writer)
            always_writer.close()


if __name__ == "__main__":
    unittest.main()
