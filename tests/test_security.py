"""Input sanitization and path/hostname validation tests.

Run with: PYTHONPATH=src python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from zashterminal.utils.security import (
    HostnameValidator,
    InputSanitizer,
    PathValidator,
    SSHKeyValidator,
)


class InputSanitizerTests(unittest.TestCase):
    def test_sanitize_filename_strips_forbidden_chars(self):
        self.assertEqual(InputSanitizer.sanitize_filename('a<b>c:"|?*'), "a_b_c_____")

    def test_sanitize_filename_empty_becomes_unnamed(self):
        result = InputSanitizer.sanitize_filename("")
        self.assertTrue(result)

    def test_sanitize_hostname(self):
        self.assertEqual(InputSanitizer.sanitize_hostname("  ExAmPle.COM "), "example.com")
        self.assertEqual(InputSanitizer.sanitize_hostname("bad host!"), "badhost")


class HostnameValidatorTests(unittest.TestCase):
    def test_valid_hostnames(self):
        for host in ("localhost", "example.com", "a-b.example.org", "192.168.1.1"):
            self.assertTrue(HostnameValidator.is_valid_hostname(host), host)

    def test_invalid_hostnames(self):
        for host in ("", "-bad.com", "bad-.com", "has space", "a" * 300):
            self.assertFalse(HostnameValidator.is_valid_hostname(host), host)

    def test_private_ip_detection(self):
        self.assertTrue(HostnameValidator.is_private_ip("10.0.0.1"))
        self.assertFalse(HostnameValidator.is_private_ip("8.8.8.8"))
        self.assertFalse(HostnameValidator.is_private_ip("not-an-ip"))


class PathValidatorTests(unittest.TestCase):
    def test_rejects_traversal(self):
        self.assertFalse(PathValidator.is_safe_path("../etc/passwd"))
        self.assertFalse(PathValidator.is_safe_path("foo/../../etc/passwd"))

    def test_accepts_absolute_safe_path(self):
        self.assertTrue(PathValidator.is_safe_path("/home/user/file.txt"))

    def test_base_path_enforcement(self):
        self.assertTrue(PathValidator.is_safe_path("/home/user/a", "/home/user"))
        self.assertFalse(PathValidator.is_safe_path("/etc/passwd", "/home/user"))


class SSHKeyValidatorTests(unittest.TestCase):
    def test_missing_key_path(self):
        ok, err = SSHKeyValidator.validate_ssh_key_path("")
        self.assertFalse(ok)
        self.assertIsNotNone(err)

    def test_nonexistent_key(self):
        ok, err = SSHKeyValidator.validate_ssh_key_path("/no/such/key")
        self.assertFalse(ok)
        self.assertIsNotNone(err)

    def test_valid_secure_key_file(self):
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "id_test"
            key.write_text("ssh-test-key\n", encoding="utf-8")
            key.chmod(0o600)
            ok, err = SSHKeyValidator.validate_ssh_key_path(str(key))
            self.assertTrue(ok)
            self.assertIsNone(err)


if __name__ == "__main__":
    unittest.main()
