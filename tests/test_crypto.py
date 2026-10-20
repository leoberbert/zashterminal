"""SecureCRT V2-compatible password crypto tests.

Run with: PYTHONPATH=src python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import unittest

from zashterminal.utils.crypto import (
    decrypt_session_password,
    encrypt_session_password,
    is_securecrt_v2_password,
    is_session_password_encryption_available,
)
from zashterminal.utils.exceptions import ZashterminalError


@unittest.skipUnless(
    is_session_password_encryption_available(),
    "pycryptodomex (Cryptodome) is required for SecureCRT V2 crypto",
)
class SecureCRTV2CryptoTests(unittest.TestCase):
    def test_roundtrip_with_passphrase(self):
        password = "s3cret-value"
        passphrase = "config-pass"
        encrypted = encrypt_session_password(password, passphrase)
        self.assertTrue(is_securecrt_v2_password(encrypted))
        self.assertEqual(decrypt_session_password(encrypted, passphrase), password)

    def test_roundtrip_with_empty_passphrase(self):
        password = "another-secret"
        encrypted = encrypt_session_password(password, "")
        self.assertEqual(decrypt_session_password(encrypted, ""), password)

    def test_wrong_passphrase_fails_integrity(self):
        encrypted = encrypt_session_password("pw", "correct")
        with self.assertRaises(ZashterminalError):
            decrypt_session_password(encrypted, "wrong")

    def test_invalid_hex_rejected(self):
        with self.assertRaises(ZashterminalError):
            decrypt_session_password("02:not-hex", "")

    def test_prefix_detection(self):
        self.assertTrue(is_securecrt_v2_password("02:abcd"))
        self.assertFalse(is_securecrt_v2_password(""))
        self.assertFalse(is_securecrt_v2_password("01:abcd"))


if __name__ == "__main__":
    unittest.main()
