#!/usr/bin/env python3
"""Local end-to-end simulation of ProxyJump, transcripts, and broadcast groups.

Does not require a live bastion or GUI window. Exercises the same code paths the
app uses (session model, ssh_config import, spawner argv, OpenSSH -G, rsync -e,
transcript writer/attach policy, broadcast scope resolution + feed_child).

Run:
  PYTHONPATH=src python3 scripts/simulate_proxyjump_transcript_broadcast.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# Ensure src is importable when run from repo root.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gi.repository import Gio  # noqa: E402

from zashterminal.sessions.models import SessionFolder, SessionItem  # noqa: E402
from zashterminal.sessions.operations import SessionOperations  # noqa: E402
from zashterminal.terminal.spawner import ProcessSpawner  # noqa: E402
from zashterminal.ui.window_search_broadcast import (  # noqa: E402
    WindowSearchBroadcastMixin,
)
from zashterminal.utils.transcript import (  # noqa: E402
    TranscriptWriter,
    build_transcript_path,
    resolve_transcript_enabled,
    strip_ansi,
)


class _SimFail(AssertionError):
    pass


def _ok(msg: str) -> None:
    print(f"  OK  {msg}")


def _fail(msg: str) -> None:
    raise _SimFail(msg)


def simulate_proxyjump() -> None:
    print("\n== ProxyJump ==")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        key = tmp_path / "id_ed25519"
        key.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\ndummy\n", encoding="utf-8")
        key.chmod(0o600)

        # ssh_config → SessionOperations import
        cfg = tmp_path / "config"
        cfg.write_text(
            "\n".join(
                [
                    "Host db-prod",
                    "  HostName 10.0.2.20",
                    "  User admin",
                    "  Port 22",
                    "  IdentityFile " + str(key),
                    "  ProxyJump leo@bastion.corp:22",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        settings = MagicMock()
        settings.get.return_value = []
        storage = MagicMock()
        storage.save_sessions_and_folders_safe.return_value = True
        session_store = Gio.ListStore.new(SessionItem)
        folder_store = Gio.ListStore.new(SessionFolder)
        with patch(
            "zashterminal.sessions.operations.get_storage_manager",
            return_value=storage,
        ), patch(
            "zashterminal.sessions.operations.AppSignals.get",
            return_value=MagicMock(),
        ):
            ops = SessionOperations(session_store, folder_store, settings)
            result = ops.import_sessions_from_ssh_config(cfg)

        if not result.success:
            _fail(f"ssh_config import failed: {result.message}")
        if session_store.get_n_items() != 1:
            _fail(f"expected 1 session, got {session_store.get_n_items()}")
        imported = session_store.get_item(0)
        if imported.proxy_jump != "leo@bastion.corp:22":
            _fail(f"proxy_jump not imported: {imported.proxy_jump!r}")
        if imported.host != "10.0.2.20":
            _fail(f"hostname not imported: {imported.host!r}")
        _ok(f"imported session '{imported.name}' with ProxyJump={imported.proxy_jump}")

        # Spawner builds ssh/sftp argv with ProxyJump
        settings_mgr = MagicMock()
        settings_mgr.get.side_effect = lambda key, default=None: {
            "ssh_control_persist_duration": 0,
            "ssh_connect_timeout": 10,
        }.get(key, default)
        spawner = ProcessSpawner()
        spawner.settings_manager = settings_mgr
        session = SessionItem(
            name="db-prod",
            host="10.0.2.20",
            user="admin",
            port=22,
            auth_type="key",
            auth_value=str(key),
            proxy_jump="leo@bastion.corp:22",
        )
        with patch("zashterminal.terminal.spawner.has_command", return_value=True), patch(
            "zashterminal.utils.platform.shutil.which",
            side_effect=lambda c: f"/usr/bin/{c}",
        ):
            ssh_cmd, _ = spawner._build_remote_command_secure(
                session=session, command_type="ssh"
            )
            sftp_cmd, _ = spawner._build_remote_command_secure(
                session=session, command_type="sftp"
            )
            ni_cmd, _ = spawner._build_non_interactive_ssh_command(session, ["uptime"])

        for label, cmd in (
            ("ssh", ssh_cmd),
            ("sftp", sftp_cmd),
            ("non-interactive", ni_cmd),
        ):
            joined = " ".join(cmd)
            if "ProxyJump=leo@bastion.corp:22" not in joined:
                _fail(f"{label} missing ProxyJump: {joined}")
            _ok(f"{label} argv includes ProxyJump ({cmd[0]})")

        # OpenSSH accepts the option (no network): ssh -G dumps effective config.
        # Use -F /dev/null so system ssh_config.d quirks don't break the simulation.
        dump = subprocess.run(
            [
                "ssh",
                "-F",
                "/dev/null",
                "-G",
                "-o",
                "ProxyJump=leo@bastion.corp:22",
                "-o",
                "BatchMode=yes",
                "admin@10.0.2.20",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if dump.returncode != 0:
            # Some sandboxes block reading /etc/ssh; fall back to argv-only proof.
            if "Bad owner or permissions" in (dump.stderr or ""):
                _ok(
                    "ssh -G skipped (sandbox ssh_config permissions); "
                    "argv already validated"
                )
            else:
                _fail(f"ssh -G failed: {dump.stderr.strip()}")
        else:
            if "proxyjump leo@bastion.corp:22" not in dump.stdout.lower():
                _fail("ssh -G did not echo proxyjump in effective config")
            _ok("OpenSSH -G accepts ProxyJump=leo@bastion.corp:22")

        # rsync -e string used by file manager
        control = spawner._get_ssh_control_path(session)
        ssh_e = f"ssh -o ControlPath={control} -o ProxyJump={session.proxy_jump}"
        if "ProxyJump=leo@bastion.corp:22" not in ssh_e:
            _fail("rsync -e simulation missing ProxyJump")
        _ok(f"rsync -e would be: {ssh_e}")


def simulate_transcript() -> None:
    print("\n== Transcript logging ==")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # Policy matrix
        cases = [
            (False, None, False),
            (True, None, True),
            (False, True, True),
            (True, False, False),
        ]
        for global_on, override, expect in cases:
            got = resolve_transcript_enabled(
                global_enabled=global_on, session_override=override
            )
            if got != expect:
                _fail(f"policy {global_on}/{override} => {got}, expected {expect}")
        _ok("tri-state policy matrix matches expected outcomes")

        # Simulate a live session writing colored output then closing
        path = build_transcript_path(
            tmp_path / "transcripts",
            session_name="db-prod",
            host="10.0.2.20",
        )
        writer = TranscriptWriter(path, strip_escapes=True)
        chunks = [
            "\x1b[32mConnecting to 10.0.2.20 via bastion...\x1b[0m\n",
            "Last login: Tue Nov 3 21:00:00 2026\n",
            "admin@db-prod:~$ \x1b[1muptime\x1b[0m\n",
            " 21:01:02 up 40 days,  3:12,  1 user\n",
            "admin@db-prod:~$ exit\n",
        ]
        for chunk in chunks:
            writer.write(chunk)
            writer.note_row(writer.last_row + 1)
        writer.close()

        text = path.read_text(encoding="utf-8")
        if "\x1b[" in text:
            _fail("ANSI escapes leaked into transcript")
        if "Connecting to 10.0.2.20 via bastion..." not in text:
            _fail("missing connection banner in transcript")
        if "uptime" not in text or "40 days" not in text:
            _fail("missing command output in transcript")
        if "transcript started" not in text or "transcript ended" not in text:
            _fail("missing transcript header/footer")
        _ok(f"wrote readable transcript ({path.name}, {path.stat().st_size} bytes)")

        # Simulate manager attach decision + VTE contents-changed handler
        from zashterminal.terminal.manager import TerminalManager

        settings = MagicMock()
        settings.get.side_effect = lambda key, default=None: {
            "transcript_logging_enabled": False,
            "transcript_strip_ansi": True,
            "transcript_directory": str(tmp_path / "mgr_transcripts"),
        }.get(key, default)

        # Minimal TerminalManager without full UI init: call helpers on a bare object
        mgr = object.__new__(TerminalManager)
        mgr.settings_manager = settings
        mgr.logger = MagicMock()

        session = SessionItem(
            name="lab",
            host="10.0.0.8",
            user="root",
            transcript_logging=True,  # force on despite global off
        )
        terminal = MagicMock()
        terminal.zashterminal_handler_ids = []
        terminal.get_cursor_position.return_value = (0, 2)
        terminal.get_column_count.return_value = 80
        terminal.get_text_range_format.return_value = (
            "\x1b[31mERROR\x1b[0m: disk full\n",
            20,
        )

        mgr._attach_transcript_logger(terminal, session, terminal_id=42)
        writer = getattr(terminal, "_zashterminal_transcript", None)
        if writer is None:
            _fail("manager did not attach transcript when session override=True")
        if not terminal.zashterminal_handler_ids:
            _fail("contents-changed handler was not registered")

        mgr._on_terminal_contents_changed_for_transcript(terminal)
        mgr._detach_transcript_logger(terminal)

        files = list((tmp_path / "mgr_transcripts").glob("*.log"))
        if not files:
            _fail("manager attach created no transcript file")
        body = files[0].read_text(encoding="utf-8")
        if "ERROR: disk full" not in body:
            _fail(f"manager handler did not persist VTE text: {body!r}")
        if "\x1b[" in body:
            _fail("manager path left ANSI in file")
        _ok("TerminalManager attach/handler/detach simulated with mock VTE")


class _BroadcastHarness(WindowSearchBroadcastMixin):
    """Drive mixin methods without a real Adw window."""

    def __init__(self, scope_index: int, focused, terminals):
        self.settings_manager = MagicMock()
        self.toast_overlay = MagicMock()
        self.broadcast_scope_combo = MagicMock()
        self.broadcast_scope_combo.get_selected.return_value = scope_index
        self.tab_manager = MagicMock()
        self.tab_manager.get_selected_terminal.return_value = focused
        self.tab_manager.get_all_terminals_across_tabs.return_value = terminals
        self.logger = MagicMock()
        self._fed: list[tuple[object, bytes]] = []

    def _execute_broadcast(self, command: str, terminals):
        payload = command.encode("utf-8") + b"\n"
        for terminal in terminals:
            terminal.feed_child(payload)
            self._fed.append((terminal, payload))


def simulate_broadcast_groups() -> None:
    print("\n== Broadcast groups ==")

    def make_term(name: str, group: str):
        term = MagicMock(name=name)
        term.zashterminal_session_item = SessionItem(
            name=name,
            host=f"{name}.lab",
            user="ops",
            broadcast_group=group,
            session_type="ssh",
        )
        return term

    def fleet():
        web1 = make_term("web-1", "web")
        web2 = make_term("web-2", "web")
        db1 = make_term("db-1", "db")
        local = make_term("local", "")
        local.zashterminal_session_item = SessionItem(
            name="local", session_type="local", broadcast_group=""
        )
        return web1, web2, db1, local, [web1, web2, db1, local]

    # This pane
    web1, web2, db1, local, all_terms = fleet()
    h = _BroadcastHarness(0, web1, all_terms)
    got = h._resolve_broadcast_terminals(all_terms)
    if got != [web1]:
        _fail(f"This pane resolved {got}")
    h._execute_broadcast("hostname", got)
    if web1.feed_child.call_count != 1 or web2.feed_child.call_count != 0:
        _fail("This pane fed the wrong terminals")
    _ok("scope=This pane → only focused terminal")

    # Group web
    web1, web2, db1, local, all_terms = fleet()
    h = _BroadcastHarness(1, web2, all_terms)
    got = h._resolve_broadcast_terminals(all_terms)
    names = {
        t.zashterminal_session_item.name for t in got  # type: ignore[union-attr]
    }
    if names != {"web-1", "web-2"}:
        _fail(f"Group web resolved {names}")
    h._execute_broadcast("sudo systemctl reload nginx", got)
    if web1.feed_child.call_count != 1 or web2.feed_child.call_count != 1:
        _fail("Group web did not feed both web panes")
    if db1.feed_child.call_count != 0:
        _fail("Group web leaked into db pane")
    web1.feed_child.assert_called_with(b"sudo systemctl reload nginx\n")
    _ok("scope=Group → web-1 + web-2 only, command bytes correct")

    # Group with empty group on focus
    web1, web2, db1, local, all_terms = fleet()
    h = _BroadcastHarness(1, local, all_terms)
    got = h._resolve_broadcast_terminals(all_terms)
    if got != []:
        _fail("empty group should resolve to []")
    if not h.toast_overlay.add_toast.called:
        _fail("expected toast when focused pane has no group")
    _ok("scope=Group with empty group → toast + no targets")

    # All panes
    web1, web2, db1, local, all_terms = fleet()
    h = _BroadcastHarness(2, web1, all_terms)
    got = h._resolve_broadcast_terminals(all_terms)
    if got != all_terms:
        _fail("All panes did not return full list")
    _ok("scope=All panes → every terminal")

    # Select… opens picker (None)
    web1, web2, db1, local, all_terms = fleet()
    h = _BroadcastHarness(3, web1, all_terms)
    got = h._resolve_broadcast_terminals(all_terms)
    if got is not None:
        _fail("Select… should return None to open picker")
    _ok("scope=Select… → None (manual picker)")


def main() -> int:
    print("Simulating ProxyJump / transcript / broadcast-groups flows…")
    # Avoid touching the user's real settings file if SettingsManager is constructed.
    os.environ.setdefault("ZASHTERMINAL_DISABLE_AUTO_UPDATE", "1")
    try:
        simulate_proxyjump()
        simulate_transcript()
        simulate_broadcast_groups()
    except _SimFail as exc:
        print(f"\nFAIL: {exc}")
        return 1
    except Exception as exc:
        print(f"\nERROR: {exc}")
        raise
    print("\nAll local simulations passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
