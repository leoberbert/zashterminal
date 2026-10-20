"""API key libsecret helpers and settings resolution tests.

Run with: PYTHONPATH=src python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from zashterminal.utils import crypto
from zashterminal.utils.exceptions import ZashterminalError


class ResolveApiKeyTests(unittest.TestCase):
    def test_plaintext_settings_value_returned(self):
        self.assertEqual(
            crypto.resolve_ai_assistant_api_key("plain-key"),
            "plain-key",
        )

    def test_sentinel_looks_up_keyring(self):
        with patch.object(crypto, "lookup_api_key", return_value="from-keyring"):
            self.assertEqual(
                crypto.resolve_ai_assistant_api_key(crypto.API_KEY_SETTINGS_SENTINEL),
                "from-keyring",
            )

    def test_empty_looks_up_keyring(self):
        with patch.object(crypto, "lookup_api_key", return_value=""):
            self.assertEqual(crypto.resolve_ai_assistant_api_key(""), "")


class ApiKeyStoreHelpersTests(unittest.TestCase):
    def test_store_requires_secret_service(self):
        with patch.object(crypto, "is_encryption_available", return_value=False):
            with self.assertRaises(ZashterminalError):
                crypto.store_api_key(crypto.AI_ASSISTANT_API_KEY_NAME, "secret")

    def test_store_and_clear_call_secret_sync(self):
        with (
            patch.object(crypto, "is_encryption_available", return_value=True),
            patch.object(crypto.Secret, "password_store_sync") as store_sync,
            patch.object(crypto.Secret, "password_clear_sync", return_value=True) as clear_sync,
        ):
            self.assertTrue(
                crypto.store_api_key(crypto.AI_ASSISTANT_API_KEY_NAME, "secret")
            )
            store_sync.assert_called_once()
            self.assertTrue(crypto.clear_api_key(crypto.AI_ASSISTANT_API_KEY_NAME))
            clear_sync.assert_called_once()


if __name__ == "__main__":
    unittest.main()
