"""Coverage for PuTTY / mRemoteNG SessionOperations commit paths."""

from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from Cryptodome.Cipher import AES
from Cryptodome.Hash import MD5
from gi.repository import Gio

from zashterminal.sessions.models import SessionFolder, SessionItem
from zashterminal.sessions.operations import SessionOperations
from zashterminal.utils.mremoteng_import import DEFAULT_MREMOTENG_PASSWORD
from zashterminal.utils.putty_import import PuttyImportedSession


def _legacy_encrypt(plain: str, password: str = DEFAULT_MREMOTENG_PASSWORD) -> str:
    key = MD5.new(password.encode("utf-8")).digest()
    iv = b"\x22" * 16
    data = plain.encode("utf-8")
    pad = 16 - (len(data) % 16)
    data = data + bytes([pad]) * pad
    cipher = AES.new(key, AES.MODE_CBC, iv=iv).encrypt(data)
    return base64.b64encode(iv + cipher).decode("ascii")


class SessionImportOperationsTests(unittest.TestCase):
    def setUp(self):
        self.settings = MagicMock()
        self.settings.get.return_value = []
        self.storage = MagicMock()
        self.storage.save_sessions_and_folders_safe.return_value = True
        self.session_store = Gio.ListStore.new(SessionItem)
        self.folder_store = Gio.ListStore.new(SessionFolder)

        with patch(
            "zashterminal.sessions.operations.get_storage_manager",
            return_value=self.storage,
        ):
            self.ops = SessionOperations(
                self.session_store, self.folder_store, self.settings
            )

    def _signals(self):
        signals = MagicMock()
        return (
            patch(
                "zashterminal.sessions.operations.AppSignals.get",
                return_value=signals,
            ),
            signals,
        )

    def test_putty_missing_file(self):
        result = self.ops.import_sessions_from_putty_file("/no/such/putty.reg")
        self.assertFalse(result.success)
        self.assertIn("not found", result.message.lower())

    def test_putty_parse_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.reg"
            path.write_text("x", encoding="utf-8")
            with patch(
                "zashterminal.sessions.operations.import_putty_sessions_from_file",
                side_effect=RuntimeError("boom"),
            ):
                result = self.ops.import_sessions_from_putty_file(path)
        self.assertFalse(result.success)
        self.assertIn("Failed to parse", result.message)

    def test_putty_no_ssh_sessions(self):
        content = (
            "Windows Registry Editor Version 5.00\n\n"
            "[HKEY_CURRENT_USER\\Software\\SimonTatham\\PuTTY\\Sessions\\old]\n"
            '"HostName"="router"\n'
            '"Protocol"="telnet"\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "putty.reg"
            path.write_text(content, encoding="utf-8")
            result = self.ops.import_sessions_from_putty_file(path)
        self.assertFalse(result.success)
        self.assertIn("No PuTTY", result.message)

    def test_putty_import_success_creates_folders_and_key_auth(self):
        content = (
            "Windows Registry Editor Version 5.00\n\n"
            "[HKEY_CURRENT_USER\\Software\\SimonTatham\\PuTTY\\Sessions\\lab%2Fdb]\n"
            '"HostName"="db.example"\n'
            '"Protocol"="ssh"\n'
            '"PortNumber"=dword:00000016\n'
            '"UserName"="admin"\n'
            '"PublicKeyFile"="/tmp/id_ed25519"\n'
            '"RemoteCommand"="uptime"\n'
            '"PortForwardings"="L8080=127.0.0.1:80"\n'
        )
        signals_patch, signals = self._signals()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "putty.reg"
            path.write_text(content, encoding="utf-8")
            with signals_patch:
                result = self.ops.import_sessions_from_putty_file(
                    path, split_folders=True
                )
        self.assertTrue(result.success, result.message)
        self.assertEqual(self.session_store.get_n_items(), 1)
        session = self.session_store.get_item(0)
        self.assertEqual(session.name, "db")
        self.assertEqual(session.folder_path, "/lab")
        self.assertEqual(session.host, "db.example")
        self.assertEqual(session.auth_type, "key")
        self.assertTrue(session.post_login_command_enabled)
        self.assertGreaterEqual(self.folder_store.get_n_items(), 1)
        signals.emit.assert_called_with("request-tree-refresh")
        self.storage.save_sessions_and_folders_safe.assert_called()

    def test_putty_save_failure(self):
        self.storage.save_sessions_and_folders_safe.return_value = False
        entry = PuttyImportedSession(
            name="box",
            folder_path="",
            host="h.example",
            user="u",
            port=22,
            auth_type="key",
            auth_value="/tmp/key",
        )
        signals_patch, _ = self._signals()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "putty.reg"
            path.write_text("x", encoding="utf-8")
            with (
                patch(
                    "zashterminal.sessions.operations.import_putty_sessions_from_file",
                    return_value=([entry], []),
                ),
                signals_patch,
            ):
                result = self.ops.import_sessions_from_putty_file(path)
        self.assertFalse(result.success)
        self.assertIn("Failed to save", result.message)

    def test_multiputty_missing_file(self):
        result = self.ops.import_sessions_from_multiputty_file("/no/such/mputty.dat")
        self.assertFalse(result.success)
        self.assertIn("/no/such/mputty.dat", result.message)

    def test_multiputty_parse_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.xml"
            path.write_text("<not><closed>", encoding="utf-8")
            result = self.ops.import_sessions_from_multiputty_file(path)
        self.assertFalse(result.success)
        self.assertIn("Invalid Multi PuTTY XML", result.message)

    def test_multiputty_no_ssh_sessions(self):
        content = """<?xml version="1.0"?>
<config>
  <connection name="old">
    <protocol>telnet</protocol>
    <host>router</host>
  </connection>
</config>
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mputty.dat"
            path.write_text(content, encoding="utf-8")
            result = self.ops.import_sessions_from_multiputty_file(path)
        self.assertFalse(result.success)
        self.assertEqual(self.session_store.get_n_items(), 0)
        self.assertTrue(result.warnings)

    def test_multiputty_import_success_creates_folders(self):
        content = """<?xml version="1.0"?>
<config>
  <container name="Prod">
    <connection name="api">
      <protocol>ssh</protocol>
      <host>api.example</host>
      <port>2222</port>
      <login>deploy</login>
      <password>secret</password>
    </connection>
  </container>
</config>
"""
        signals_patch, signals = self._signals()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mputty.dat"
            path.write_text(content, encoding="utf-8")
            with signals_patch:
                result = self.ops.import_sessions_from_multiputty_file(path)
        self.assertTrue(result.success, result.message)
        self.assertEqual(self.session_store.get_n_items(), 1)
        session = self.session_store.get_item(0)
        self.assertEqual(session.name, "api")
        self.assertEqual(session.folder_path, "/Prod")
        self.assertEqual(session.host, "api.example")
        self.assertEqual(session.port, 2222)
        self.assertEqual(session.user, "deploy")
        self.assertEqual(session.auth_type, "password")
        self.assertGreaterEqual(self.folder_store.get_n_items(), 1)
        signals.emit.assert_called_with("request-tree-refresh")
        self.assertIn("Multi PuTTY", result.message)

    def test_mremoteng_missing_file(self):
        result = self.ops.import_sessions_from_mremoteng_file("/missing/confCons.xml")
        self.assertFalse(result.success)
        self.assertIn("not found", result.message.lower())

    def test_mremoteng_parse_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.xml"
            path.write_text("<not-connections/>", encoding="utf-8")
            result = self.ops.import_sessions_from_mremoteng_file(path)
        self.assertFalse(result.success)
        self.assertIn("Failed to parse", result.message)

    def test_mremoteng_needs_master_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "confCons.xml"
            path.write_text(
                '<?xml version="1.0"?><Connections Protected="AAAA"/>',
                encoding="utf-8",
            )
            with patch(
                "zashterminal.sessions.operations.import_mremoteng_sessions_from_file",
                return_value=([], ["hint"], True),
            ):
                result = self.ops.import_sessions_from_mremoteng_file(path)
        self.assertFalse(result.success)
        self.assertEqual(result.message, "master-password-required")

    def test_mremoteng_no_sessions(self):
        protected = _legacy_encrypt("ThisIsNotProtected")
        xml = f"""<?xml version="1.0"?>
<Connections Protected="{protected}">
  <Node Name="only-rdp" Type="Connection" Hostname="1.2.3.4"
        Protocol="RDP" Port="3389" Username="u" Password=""/>
</Connections>
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "confCons.xml"
            path.write_text(xml, encoding="utf-8")
            result = self.ops.import_sessions_from_mremoteng_file(path)
        self.assertFalse(result.success)
        self.assertIn("No mRemoteNG", result.message)

    def test_mremoteng_import_success_with_password(self):
        protected = _legacy_encrypt("ThisIsNotProtected")
        password = _legacy_encrypt("hunter2")
        xml = f"""<?xml version="1.0"?>
<Connections Protected="{protected}">
  <Node Name="Prod" Type="Container" Username="root" Protocol="SSH2" Port="22"
        InheritUsername="False" InheritProtocol="False" InheritPort="False"
        InheritPassword="False" Password="">
    <Node Name="web" Type="Connection" Hostname="web.lab" Username=""
          Protocol="" Port="0" InheritUsername="True" InheritProtocol="True"
          InheritPort="True" InheritPassword="False" Password="{password}"/>
  </Node>
</Connections>
"""
        signals_patch, signals = self._signals()
        crypto = MagicMock()
        crypto.encrypt_session_password.side_effect = lambda value: f"enc:{value}"
        crypto.clear_password.return_value = None
        crypto.is_encryption_available.return_value = True
        with (
            signals_patch,
            patch("zashterminal.sessions.models._get_crypto", return_value=crypto),
        ):
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "confCons.xml"
                path.write_text(xml, encoding="utf-8")
                result = self.ops.import_sessions_from_mremoteng_file(path)
        self.assertTrue(result.success, result.message)
        self.assertEqual(self.session_store.get_n_items(), 1)
        session = self.session_store.get_item(0)
        self.assertEqual(session.name, "web")
        self.assertEqual(session.folder_path, "/Prod")
        self.assertEqual(session.auth_type, "password")
        self.assertEqual(session._auth_value, "enc:hunter2")
        signals.emit.assert_called_with("request-tree-refresh")

    def test_commit_password_store_failure_and_invalid_and_folder_normalize(self):
        signals_patch, _ = self._signals()
        crypto = MagicMock()
        crypto.encrypt_session_password.side_effect = RuntimeError("no crypto")
        crypto.is_encryption_available.return_value = False

        entries = [
            {
                "name": "bad",
                "folder_path": "/",
                "host": "",
                "user": "u",
                "port": 22,
                "auth_type": "",
                "auth_value": "",
                "post_login_command": "",
                "port_forwardings": [],
                "source": "putty",
            },
            {
                "name": "pw",
                "folder_path": "nested/box",
                "host": "ok.example",
                "user": "u",
                "port": 22,
                "auth_type": "password",
                "auth_value": "secret",
                "post_login_command": "  ",
                "port_forwardings": [],
                "source": "putty",
            },
        ]

        with (
            signals_patch,
            patch("zashterminal.sessions.models._get_crypto", return_value=crypto),
        ):
            result = self.ops._commit_imported_ssh_entries(
                entries=entries,
                warnings=[],
                success_template="Imported {count} from {path}.",
                path="/tmp/x",
            )

        self.assertTrue(result.success, result.message)
        self.assertEqual(self.session_store.get_n_items(), 1)
        session = self.session_store.get_item(0)
        self.assertEqual(session.name, "pw")
        self.assertEqual(session.folder_path, "/nested/box")
        self.assertEqual(session.auth_type, "")
        self.assertTrue(
            any("pw" in w and ("password" in w.lower() or "senha" in w.lower()) for w in result.warnings)
        )
        self.assertTrue(any("bad" in w for w in result.warnings))
        self.assertTrue(
            "folder" in result.message.lower() or "pasta" in result.message.lower()
        )

    def test_commit_all_invalid_returns_false(self):
        result = self.ops._commit_imported_ssh_entries(
            entries=[
                {
                    "name": "x",
                    "folder_path": ".",
                    "host": "",
                    "user": "",
                    "port": 22,
                    "auth_type": "",
                    "auth_value": "",
                    "post_login_command": "",
                    "port_forwardings": [],
                    "source": "putty",
                }
            ],
            warnings=[],
            success_template="Imported {count} from {path}.",
            path="/tmp/x",
        )
        self.assertFalse(result.success)
        self.assertTrue(
            "No sessions were imported" in result.message
            or "Nenhuma sessão" in result.message
        )

    def test_commit_reuses_existing_folder_and_unique_names(self):
        existing = SessionFolder(name="lab", path="/lab", parent_path="")
        self.folder_store.append(existing)
        self.session_store.append(
            SessionItem(
                name="db",
                session_type="ssh",
                host="old",
                user="u",
                port=22,
                folder_path="/lab",
                auth_type="key",
                auth_value="/tmp/k",
            )
        )
        signals_patch, _ = self._signals()
        with signals_patch:
            result = self.ops._commit_imported_ssh_entries(
                entries=[
                    {
                        "name": "db",
                        "folder_path": "/lab",
                        "host": "new.example",
                        "user": "u",
                        "port": 22,
                        "auth_type": "key",
                        "auth_value": "/tmp/k2",
                        "post_login_command": "",
                        "port_forwardings": [],
                        "source": "putty",
                    }
                ],
                warnings=[],
                success_template="Imported {count} from {path}.",
                path="/tmp/x",
            )
        self.assertTrue(result.success)
        names = {
            self.session_store.get_item(i).name
            for i in range(self.session_store.get_n_items())
        }
        self.assertIn("db", names)
        self.assertTrue(any(n.startswith("db") and n != "db" for n in names))
        self.assertEqual(self.folder_store.get_n_items(), 1)


if __name__ == "__main__":
    unittest.main()
