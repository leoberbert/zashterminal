"""API key libsecret helpers and settings resolution tests.

Run with: PYTHONPATH=src python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zashterminal.settings.manager import SettingsManager

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


class PerProviderApiKeyTests(unittest.TestCase):
    """Each AI provider keeps its own key; switching never loses one."""

    def setUp(self):
        self.keyring = {}
        patches = [
            patch.object(crypto, "is_encryption_available", return_value=True),
            patch.object(crypto, "store_api_key", side_effect=lambda n, v: self.keyring.__setitem__(n, v) or True),
            patch.object(crypto, "lookup_api_key", side_effect=lambda n: self.keyring.get(n)),
            patch.object(crypto, "clear_api_key", side_effect=lambda n: self.keyring.pop(n, None) is not None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def manager(self, settings=None):
        path = Path(self.tmp) / "settings.json"
        if settings is not None:
            path.write_text(json.dumps(settings))
        return SettingsManager(path)

    def test_switching_provider_keeps_each_key(self):
        sm = self.manager()
        sm.set("ai_assistant_provider", "openai")
        sm.set("ai_assistant_api_key", "sk-openai")
        sm.set("ai_assistant_provider", "anthropic")
        self.assertEqual(sm.get("ai_assistant_api_key"), "")
        sm.set("ai_assistant_api_key", "sk-ant")
        sm.set("ai_assistant_provider", "openai")
        self.assertEqual(sm.get("ai_assistant_api_key"), "sk-openai")
        sm.set("ai_assistant_api_key", "")  # clearing one leaves the other
        sm.set("ai_assistant_provider", "anthropic")
        self.assertEqual(sm.get("ai_assistant_api_key"), "sk-ant")

    def test_old_shared_key_moves_to_the_active_provider(self):
        self.keyring[crypto.AI_ASSISTANT_API_KEY_NAME] = "sk-shared"
        sm = self.manager({"ai_assistant_provider": "groq",
                           "ai_assistant_api_key": crypto.API_KEY_SETTINGS_SENTINEL})
        self.assertEqual(sm.get("ai_assistant_api_key"), "sk-shared")
        self.assertNotIn(crypto.AI_ASSISTANT_API_KEY_NAME, self.keyring)
        sm.set("ai_assistant_provider", "openai")
        self.assertEqual(sm.get("ai_assistant_api_key"), "")

    def test_plaintext_key_migrates_to_the_active_provider(self):
        sm = self.manager({"ai_assistant_provider": "mistral", "ai_assistant_api_key": "plain"})
        self.assertEqual(self.keyring.get(crypto.ai_api_key_name("mistral")), "plain")
        self.assertEqual(sm.get("ai_assistant_api_key"), "plain")


if __name__ == "__main__":
    unittest.main()
