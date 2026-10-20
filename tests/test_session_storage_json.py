"""Session JSON load/save helpers without requiring a live keyring.

Run with: PYTHONPATH=src python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class SessionJsonShapeTests(unittest.TestCase):
    """Validates the on-disk session file contract used by SessionStorageManager."""

    def test_empty_and_missing_files_are_safe(self):
        from zashterminal.sessions.storage import SessionStorageManager

        with tempfile.TemporaryDirectory() as directory:
            sessions_file = Path(directory) / "sessions.json"
            with (
                patch(
                    "zashterminal.sessions.storage.get_config_directory",
                    return_value=Path(directory),
                ),
                patch(
                    "zashterminal.sessions.storage.SESSIONS_FILE",
                    str(sessions_file),
                ),
                patch(
                    "zashterminal.sessions.storage.ensure_secure_directory_permissions"
                ),
                patch("zashterminal.sessions.storage.ensure_secure_file_permissions"),
                patch(
                    "zashterminal.sessions.storage.create_security_auditor",
                    return_value=None,
                ),
                patch("zashterminal.sessions.storage.validate_file_path"),
            ):
                manager = SessionStorageManager()
                sessions, folders = manager.load_sessions_and_folders_safe()
                self.assertEqual(sessions, [])
                self.assertEqual(folders, [])

                sessions_file.write_text("", encoding="utf-8")
                sessions, folders = manager.load_sessions_and_folders_safe()
                self.assertEqual(sessions, [])
                self.assertEqual(folders, [])

    def test_valid_payload_roundtrips_lists(self):
        from zashterminal.sessions.storage import SessionStorageManager

        payload = {
            "sessions": [
                {
                    "name": "lab",
                    "session_type": "ssh",
                    "host": "10.0.0.5",
                    "user": "root",
                    "auth_type": "key",
                    "auth_value": "/tmp/id_test",
                    "port": 22,
                }
            ],
            "folders": [{"name": "Prod", "path": "Prod"}],
        }

        with tempfile.TemporaryDirectory() as directory:
            sessions_file = Path(directory) / "sessions.json"
            sessions_file.write_text(json.dumps(payload), encoding="utf-8")
            with (
                patch(
                    "zashterminal.sessions.storage.get_config_directory",
                    return_value=Path(directory),
                ),
                patch(
                    "zashterminal.sessions.storage.SESSIONS_FILE",
                    str(sessions_file),
                ),
                patch(
                    "zashterminal.sessions.storage.ensure_secure_directory_permissions"
                ),
                patch("zashterminal.sessions.storage.ensure_secure_file_permissions"),
                patch(
                    "zashterminal.sessions.storage.create_security_auditor",
                    return_value=None,
                ),
                patch("zashterminal.sessions.storage.validate_file_path"),
            ):
                manager = SessionStorageManager()
                sessions, folders = manager.load_sessions_and_folders_safe()
                self.assertEqual(len(sessions), 1)
                self.assertEqual(sessions[0]["name"], "lab")
                self.assertEqual(len(folders), 1)
                self.assertEqual(folders[0]["name"], "Prod")

    def test_corrupt_json_raises_storage_error(self):
        from zashterminal.sessions.storage import SessionStorageManager
        from zashterminal.utils.exceptions import StorageCorruptedError

        with tempfile.TemporaryDirectory() as directory:
            sessions_file = Path(directory) / "sessions.json"
            sessions_file.write_text("{not-json", encoding="utf-8")
            with (
                patch(
                    "zashterminal.sessions.storage.get_config_directory",
                    return_value=Path(directory),
                ),
                patch(
                    "zashterminal.sessions.storage.SESSIONS_FILE",
                    str(sessions_file),
                ),
                patch(
                    "zashterminal.sessions.storage.ensure_secure_directory_permissions"
                ),
                patch("zashterminal.sessions.storage.ensure_secure_file_permissions"),
                patch(
                    "zashterminal.sessions.storage.create_security_auditor",
                    return_value=None,
                ),
                patch("zashterminal.sessions.storage.validate_file_path"),
                # Expected failure path logs ERROR; keep unittest output clean.
                patch("zashterminal.sessions.storage.get_logger") as get_logger,
            ):
                get_logger.return_value.error = lambda *args, **kwargs: None
                get_logger.return_value.warning = lambda *args, **kwargs: None
                get_logger.return_value.info = lambda *args, **kwargs: None
                manager = SessionStorageManager()
                with self.assertRaises(StorageCorruptedError):
                    manager.load_sessions_and_folders_safe()


if __name__ == "__main__":
    unittest.main()
