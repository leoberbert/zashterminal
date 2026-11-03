"""Unit tests for PuTTY / mRemoteNG session import parsers."""

from __future__ import annotations

import base64
import os
import tempfile
import unittest
from pathlib import Path

from Cryptodome.Cipher import AES
from Cryptodome.Hash import MD5, SHA1
from Cryptodome.Protocol.KDF import PBKDF2

from zashterminal.utils.mremoteng_import import (
    DEFAULT_MREMOTENG_PASSWORD,
    _resolve_crypto,
    decrypt_mremote_aes_gcm,
    decrypt_mremote_legacy_rijndael,
    import_mremoteng_sessions_from_file,
)
from zashterminal.utils.putty_import import (
    convert_putty_session,
    decode_putty_session_name,
    import_putty_sessions_from_file,
    normalize_putty_path,
    parse_putty_reg_or_ini,
    putty_tunnels_to_forwardings,
    unescape_reg_string,
    _putty_int,
)


class PuttyImportTests(unittest.TestCase):
    def test_helpers(self):
        self.assertEqual(decode_putty_session_name("prod%20api"), "prod api")
        self.assertEqual(decode_putty_session_name(""), "Imported PuTTY Session")
        self.assertEqual(normalize_putty_path('"C:\\Users\\a"'), "C:/Users/a")
        self.assertEqual(unescape_reg_string(r'a\\"b\n'), 'a"b\n')
        self.assertEqual(_putty_int({"PortNumber": 22}, "PortNumber"), 22)
        self.assertEqual(_putty_int({"PortNumber": "0x16"}, "PortNumber"), 0x16)
        self.assertEqual(_putty_int({"PortNumber": "nope"}, "PortNumber", 7), 7)

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

    def test_convert_password_and_ppk_warning(self):
        entry = convert_putty_session(
            "plain",
            {
                "Protocol": "ssh2",
                "HostName": "box",
                "Password": "secret",
                "PublicKeyFile": "C:\\keys\\id.ppk",
            },
            split_folders=False,
        )
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry.auth_type, "key")
        self.assertTrue(any(".ppk" in w for w in entry.warnings))

    def test_skips_telnet_and_missing_host(self):
        self.assertIsNone(
            convert_putty_session(
                "old",
                {"Protocol": "telnet", "HostName": "router", "PortNumber": "23"},
                split_folders=False,
            )
        )
        self.assertIsNone(
            convert_putty_session(
                "empty",
                {"Protocol": "ssh", "HostName": ""},
                split_folders=False,
            )
        )

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

    def test_parse_ini_and_unquoted_values(self):
        content = (
            "[Session\\Sessions\\nested/host]\n"
            "HostName=box.example\n"
            'Protocol="ssh"\n'
            "PortNumber=0x16\n"
            "Note=plain\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "putty.ini"
            path.write_text(content, encoding="utf-8")
            pairs = parse_putty_reg_or_ini(path)
            sessions, warnings = import_putty_sessions_from_file(path)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0][0], "nested/host")
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].port, 0x16)
        self.assertEqual(warnings, [])

    def test_import_warns_for_skipped_sessions(self):
        content = (
            "Windows Registry Editor Version 5.00\n\n"
            "[HKEY_CURRENT_USER\\Software\\SimonTatham\\PuTTY\\Sessions\\telnet]\n"
            '"HostName"="r"\n'
            '"Protocol"="telnet"\n'
            "[HKEY_CURRENT_USER\\Software\\SimonTatham\\PuTTY\\Sessions\\nohost]\n"
            '"Protocol"="ssh"\n'
            '"HostName"=""\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "putty.reg"
            path.write_text(content, encoding="utf-8")
            sessions, warnings = import_putty_sessions_from_file(path)
        self.assertEqual(sessions, [])
        self.assertTrue(any("protocol" in w.lower() for w in warnings))
        self.assertTrue(any("HostName" in w for w in warnings))

    def test_tunnel_parser_edges(self):
        tunnels, warnings = putty_tunnels_to_forwardings(
            "R8080=host:80 L9000=h:90 Lbad L=host:80 L1=host L9000=host:x "
            "Lhost:bad=h:90 Lbadhost=h:90 L65536=h:80 L9001=127.0.0.1:80"
        )
        self.assertEqual(len(tunnels), 2)
        ports = {t["local_port"] for t in tunnels}
        self.assertEqual(ports, {9000, 9001})
        self.assertTrue(any("Unsupported" in w for w in warnings))
        self.assertTrue(any("Malformed" in w for w in warnings))
        self.assertTrue(any("outside supported range" in w for w in warnings))
        with_host, _ = putty_tunnels_to_forwardings("L127.0.0.1:9100=svc:443")
        self.assertEqual(with_host[0]["local_host"], "127.0.0.1")
        self.assertEqual(with_host[0]["local_port"], 9100)


class MRemoteNgImportTests(unittest.TestCase):
    def _legacy_encrypt(self, plain: str, password: str = DEFAULT_MREMOTENG_PASSWORD) -> str:
        key = MD5.new(password.encode("utf-8")).digest()
        iv = b"\x11" * 16
        data = plain.encode("utf-8")
        pad = 16 - (len(data) % 16)
        data = data + bytes([pad]) * pad
        cipher = AES.new(key, AES.MODE_CBC, iv=iv).encrypt(data)
        return base64.b64encode(iv + cipher).decode("ascii")

    def _gcm_encrypt(
        self, plain: str, password: str = DEFAULT_MREMOTENG_PASSWORD, iterations: int = 1000
    ) -> str:
        salt = os.urandom(16)
        nonce = os.urandom(16)
        key = PBKDF2(
            password.encode("utf-8"),
            salt,
            dkLen=32,
            count=max(iterations, 1000),
            hmac_hash_module=SHA1,
        )
        aes = AES.new(key, AES.MODE_GCM, nonce=nonce)
        aes.update(salt)
        cipher, tag = aes.encrypt_and_digest(plain.encode("utf-8"))
        return base64.b64encode(salt + nonce + cipher + tag).decode("ascii")

    def test_legacy_decrypt_roundtrip_and_edges(self):
        encrypted = self._legacy_encrypt("ThisIsNotProtected")
        self.assertEqual(
            decrypt_mremote_legacy_rijndael(encrypted, DEFAULT_MREMOTENG_PASSWORD),
            "ThisIsNotProtected",
        )
        self.assertEqual(decrypt_mremote_legacy_rijndael("", "x"), "")
        with self.assertRaises(ValueError):
            decrypt_mremote_legacy_rijndael(base64.b64encode(b"short").decode(), "x")

    def test_aes_gcm_roundtrip_and_edges(self):
        encrypted = self._gcm_encrypt("ThisIsProtected", iterations=500)
        self.assertEqual(
            decrypt_mremote_aes_gcm(encrypted, DEFAULT_MREMOTENG_PASSWORD, iterations=500),
            "ThisIsProtected",
        )
        self.assertEqual(decrypt_mremote_aes_gcm("  ", "x"), "")
        with self.assertRaises(ValueError):
            decrypt_mremote_aes_gcm(base64.b64encode(b"too-short").decode(), "x")

    def test_resolve_crypto(self):
        self.assertEqual(_resolve_crypto("", ""), ("legacy", None))
        self.assertEqual(_resolve_crypto("AES", "GCM"), ("gcm", None))
        mode, warning = _resolve_crypto("AES", "CBC")
        self.assertEqual(mode, "unsupported")
        self.assertIsNotNone(warning)

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

    def test_import_gcm_with_master_password_and_wrapper(self):
        protected = self._gcm_encrypt("ThisIsProtected", password="master")
        password = self._gcm_encrypt("pw", password="master")
        xml = f"""<?xml version="1.0"?>
<Wrapper>
  <Connections EncryptionEngine="AES" BlockCipherMode="GCM"
   KdfIterations="bad" Protected="{protected}">
    <Extra/>
    <Node Name="ssh1" Type="Connection" Hostname="h1" Username="u"
          Protocol="SSH" Port="22" InheritPassword="False" Password="{password}"/>
    <Node Name="nohost" Type="Connection" Hostname="" Protocol="SSH2" Port="22"
          InheritPassword="False" Password=""/>
    <Node Name="weird" Type="Other" Username="x" Protocol="SSH" Port="abc"
          InheritPort="False" InheritPassword="True" Password=""/>
  </Connections>
</Wrapper>
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "confCons.xml"
            path.write_text(xml, encoding="utf-8")
            empty, _, needs = import_mremoteng_sessions_from_file(path)
            self.assertEqual(empty, [])
            self.assertTrue(needs)
            sessions, warnings, needs_pw = import_mremoteng_sessions_from_file(
                path, master_password="master"
            )
        self.assertFalse(needs_pw)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].password, "pw")
        self.assertTrue(any("missing Hostname" in w for w in warnings))

    def test_import_wrong_master_and_unsupported_crypto(self):
        protected = self._legacy_encrypt("ThisIsNotProtected", password="real")
        xml = f"""<?xml version="1.0"?>
<Connections EncryptionEngine="AES" BlockCipherMode="CBC" Protected="{protected}">
  <Node Name="ssh1" Type="Connection" Hostname="h1" Username="u"
        Protocol="SSH2" Port="22" InheritPassword="False"
        Password="{protected}"/>
</Connections>
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "confCons.xml"
            path.write_text(xml, encoding="utf-8")
            sessions, warnings, needs = import_mremoteng_sessions_from_file(
                path, master_password="wrong"
            )
        self.assertFalse(needs)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].password, "")
        self.assertTrue(any("not supported" in w.lower() for w in warnings))

    def test_full_file_encryption_rejected(self):
        xml = """<?xml version="1.0"?>
<Connections FullFileEncryption="true" Protected="x"/>
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "confCons.xml"
            path.write_text(xml, encoding="utf-8")
            with self.assertRaises(ValueError):
                import_mremoteng_sessions_from_file(path)

    def test_invalid_root_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "confCons.xml"
            path.write_text("<Root><Child/></Root>", encoding="utf-8")
            with self.assertRaises(ValueError):
                import_mremoteng_sessions_from_file(path)

    def test_inherit_password_and_decrypt_failure(self):
        protected = self._legacy_encrypt("ThisIsNotProtected")
        xml = f"""<?xml version="1.0"?>
<Connections Protected="{protected}">
  <Node Name="Lab" Type="Container" Username="root" Protocol="SSH2" Port="22"
        InheritUsername="False" InheritProtocol="False" InheritPort="False"
        InheritPassword="False" Password="{self._legacy_encrypt("parent-secret")}">
    <Node Name="child" Type="Connection" Hostname="c.lab" Username=""
          Protocol="" Port="0" InheritUsername="True" InheritProtocol="True"
          InheritPort="True" InheritPassword="True" Password=""/>
    <Node Name="broken" Type="Connection" Hostname="b.lab" Username="u"
          Protocol="SSH2" Port="22" InheritPassword="False"
          Password="%%%not-base64%%%"/>
  </Node>
</Connections>
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "confCons.xml"
            path.write_text(xml, encoding="utf-8")
            sessions, warnings, needs = import_mremoteng_sessions_from_file(path)
        self.assertFalse(needs)
        by_name = {s.name: s for s in sessions}
        self.assertEqual(by_name["child"].password, "parent-secret")
        self.assertEqual(by_name["broken"].password, "")
        self.assertTrue(any("could not be decrypted" in w.lower() for w in warnings))

    def test_master_password_empty_continues_without_passwords(self):
        protected = self._legacy_encrypt("ThisIsNotProtected", password="custom")
        password = self._legacy_encrypt("secret", password="custom")
        xml = f"""<?xml version="1.0"?>
<Connections Protected="{protected}">
  <Node Name="ssh1" Type="Connection" Hostname="h1" Username="u"
        Protocol="SSH2" Port="22" InheritPassword="False" Password="{password}"/>
</Connections>
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "confCons.xml"
            path.write_text(xml, encoding="utf-8")
            sessions, warnings, needs = import_mremoteng_sessions_from_file(
                path, master_password=""
            )
        self.assertFalse(needs)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].password, "")
        self.assertTrue(any("master password" in w.lower() for w in warnings))

    def test_kdf_zero_and_wrong_master_exception_path(self):
        protected = self._gcm_encrypt("ThisIsProtected", password="real")
        xml = f"""<?xml version="1.0"?>
<Connections EncryptionEngine="AES" BlockCipherMode="GCM"
 KdfIterations="0" Protected="{protected}">
  <Node Name="ssh1" Type="Connection" Hostname="h1" Username="u"
        Protocol="SSH2" Port="22" InheritPassword="False" Password=""/>
</Connections>
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "confCons.xml"
            path.write_text(xml, encoding="utf-8")
            sessions, warnings, needs = import_mremoteng_sessions_from_file(
                path, master_password="wrong"
            )
        self.assertFalse(needs)
        self.assertEqual(len(sessions), 1)
        self.assertTrue(any("master password" in w.lower() for w in warnings))

    def test_unsupported_decrypt_value_branch(self):
        from zashterminal.utils import mremoteng_import as mod

        self.assertEqual(mod._resolve_crypto("X", "Y")[0], "unsupported")
        # Exercise decrypt_value raise via a tiny XML with unsupported crypto
        # and a non-empty Protected that skips password_available without master.
        xml = """<?xml version="1.0"?>
<Connections EncryptionEngine="AES" BlockCipherMode="CBC" Protected="QQ==">
  <Node Name="ssh1" Type="Connection" Hostname="h1" Username="u"
        Protocol="SSH2" Port="22" InheritPassword="False" Password="QQ=="/>
</Connections>
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "confCons.xml"
            path.write_text(xml, encoding="utf-8")
            sessions, warnings, needs = import_mremoteng_sessions_from_file(
                path, master_password="x"
            )
        self.assertFalse(needs)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].password, "")


if __name__ == "__main__":
    unittest.main()
