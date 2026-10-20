"""Session password fail-closed storage tests.

Run with: PYTHONPATH=src python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from zashterminal.sessions.models import SessionItem
from zashterminal.utils.exceptions import SessionValidationError, ZashterminalError


class PasswordFailClosedTests(unittest.TestCase):
    def test_encrypt_success_stores_v2_blob(self):
        session = SessionItem(name="lab", session_type="ssh", host="h", auth_type="password")
        with (
            patch("zashterminal.sessions.models._get_crypto") as get_crypto,
        ):
            crypto = MagicMock()
            crypto.encrypt_session_password.return_value = "02:abcd"
            crypto.is_securecrt_v2_password.return_value = True
            get_crypto.return_value = crypto

            session.auth_value = "secret"
            self.assertEqual(session._auth_value, "02:abcd")
            crypto.clear_password.assert_called_once_with("lab")

    def test_encrypt_fail_keyring_success(self):
        session = SessionItem(name="lab", session_type="ssh", host="h", auth_type="password")
        with patch("zashterminal.sessions.models._get_crypto") as get_crypto:
            crypto = MagicMock()
            crypto.encrypt_session_password.side_effect = ZashterminalError("no cryptodome")
            crypto.is_encryption_available.return_value = True
            get_crypto.return_value = crypto

            session.auth_value = "secret"
            self.assertEqual(session._auth_value, "")
            crypto.store_password.assert_called_once_with("lab", "secret")

    def test_encrypt_and_keyring_fail_refuses_plaintext(self):
        session = SessionItem(name="lab", session_type="ssh", host="h", auth_type="password")
        with patch("zashterminal.sessions.models._get_crypto") as get_crypto:
            crypto = MagicMock()
            crypto.encrypt_session_password.side_effect = ZashterminalError("no cryptodome")
            crypto.is_encryption_available.return_value = False
            get_crypto.return_value = crypto

            with self.assertRaises(SessionValidationError):
                session.auth_value = "secret"
            self.assertNotEqual(session._auth_value, "secret")


if __name__ == "__main__":
    unittest.main()
