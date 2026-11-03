"""Unit tests for PuTTY / mRemoteNG session import parsers."""

from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

from Cryptodome.Cipher import AES
from Cryptodome.Hash import MD5

from zashterminal.utils.mremoteng_import import (
    DEFAULT_MREMOTENG_PASSWORD,
    decrypt_mremote_legacy_rijndael,
    import_mremoteng_sessions_from_file,
)
from zashterminal.utils.putty_import import (
    convert_putty_session,
    import_putty_sessions_from_file,
    putty_tunnels_to_forwardings,
)


class PuttyImportTests(unittest.TestCase):
    def test_convert_ssh_with_key_and_user_at_host(self):
        entry = convert_putty_session(
            "lab/db",
            {
                "Protocol": "ssh",
                "HostName": "admin@db.example",
                "PortNumber": "2222",
                "PublicKeyFile": '"C:\\Users\\a\\.ssh\\id_ed25519"',
                "RemoteCommand": "tmux a",
                "PortForwardings": "L8080=127.0.0.1:80",
            },
            split_folders=True,
        )
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry.name, "db")
        self.assertEqual(entry.folder_path, "/lab")
        self.assertEqual(entry.host, "db.example")
        self.assertEqual(entry.user, "admin")
        self.assertEqual(entry.port, 2222)
        self.assertEqual(entry.auth_type, "key")
        self.assertTrue(entry.auth_value.endswith(".ssh/id_ed25519"))
        self.assertEqual(entry.post_login_command, "tmux a")
        self.assertEqual(len(entry.port_forwardings), 1)
        self.assertEqual(entry.port_forwardings[0]["local_port"], 8080)

    def test_skips_telnet(self):
        entry = convert_putty_session(
            "old",
            {"Protocol": "telnet", "HostName": "router", "PortNumber": "23"},
            split_folders=False,
        )
        self.assertIsNone(entry)

    def test_parse_reg_file(self):
        content = (
            "Windows Registry Editor Version 5.00\n\n"
            "[HKEY_CURRENT_USER\\Software\\SimonTatham\\PuTTY\\Sessions\\prod%20api]\n"
            '"HostName"="api.example"\n'
            '"Protocol"="ssh"\n'
            '"PortNumber"=dword:00000016\n'
            '"UserName"="deploy"\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "putty.reg"
            path.write_text(content, encoding="utf-8")
            sessions, warnings = import_putty_sessions_from_file(
                path, split_folders=False
            )
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].name, "prod api")
        self.assertEqual(sessions[0].host, "api.example")
        self.assertEqual(sessions[0].user, "deploy")
        self.assertEqual(sessions[0].port, 22)
        self.assertEqual(warnings, [])

    def test_tunnel_parser_skips_remote(self):
        tunnels, warnings = putty_tunnels_to_forwardings("R8080=host:80 L9000=h:90")
        self.assertEqual(len(tunnels), 1)
        self.assertEqual(tunnels[0]["local_port"], 9000)
        self.assertTrue(any("Unsupported" in w for w in warnings))


class MRemoteNgImportTests(unittest.TestCase):
    def _legacy_encrypt(self, plain: str, password: str = DEFAULT_MREMOTENG_PASSWORD) -> str:
        key = MD5.new(password.encode("utf-8")).digest()
        iv = b"\x11" * 16
        data = plain.encode("utf-8")
        pad = 16 - (len(data) % 16)
        data = data + bytes([pad]) * pad
        cipher = AES.new(key, AES.MODE_CBC, iv=iv).encrypt(data)
        return base64.b64encode(iv + cipher).decode("ascii")

    def test_legacy_decrypt_roundtrip(self):
        encrypted = self._legacy_encrypt("ThisIsNotProtected")
        self.assertEqual(
            decrypt_mremote_legacy_rijndael(encrypted, DEFAULT_MREMOTENG_PASSWORD),
            "ThisIsNotProtected",
        )

    def test_import_ssh_tree(self):
        protected = self._legacy_encrypt("ThisIsNotProtected")
        password = self._legacy_encrypt("s3cret")
        xml = f"""<?xml version="1.0" encoding="utf-8"?>
<Connections EncryptionEngine="" BlockCipherMode="" KdfIterations="1000"
 FullFileEncryption="False" Protected="{protected}">
  <Node Name="Lab" Type="Container" Username="root" Protocol="SSH2" Port="22"
        InheritUsername="False" InheritProtocol="False" InheritPort="False"
        InheritPassword="False" Password="">
    <Node Name="db1" Type="Connection" Hostname="db1.lab" Username=""
          Protocol="" Port="0" InheritUsername="True" InheritProtocol="True"
          InheritPort="True" InheritPassword="False" Password="{password}"/>
    <Node Name="rdp-box" Type="Connection" Hostname="1.2.3.4" Protocol="RDP"
          Port="3389" InheritUsername="False" InheritProtocol="False"
          InheritPort="False" InheritPassword="False" Password="" Username="u"/>
  </Node>
</Connections>
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "confCons.xml"
            path.write_text(xml, encoding="utf-8")
            sessions, warnings, needs_pw = import_mremoteng_sessions_from_file(path)

        self.assertFalse(needs_pw)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].name, "db1")
        self.assertEqual(sessions[0].folder_path, "/Lab")
        self.assertEqual(sessions[0].host, "db1.lab")
        self.assertEqual(sessions[0].user, "root")
        self.assertEqual(sessions[0].port, 22)
        self.assertEqual(sessions[0].password, "s3cret")
        self.assertTrue(any("rdp" in w.lower() for w in warnings))


if __name__ == "__main__":
    unittest.main()
