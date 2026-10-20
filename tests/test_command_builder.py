"""SSH/SFTP command construction tests.

Run with: PYTHONPATH=src python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from zashterminal.utils.platform import CommandBuilder


class CommandBuilderTests(unittest.TestCase):
    def setUp(self):
        self.platform = MagicMock()
        self.platform.has_command.return_value = True
        self.builder = CommandBuilder(self.platform)

    @patch("zashterminal.utils.platform.shutil.which", return_value="/usr/bin/ssh")
    def test_ssh_with_user_port_and_key(self, _which):
        cmd = self.builder.build_remote_command(
            "ssh",
            hostname="host.example",
            username="alice",
            key_file="/home/alice/.ssh/id_ed25519",
            port=2222,
            options={"ConnectTimeout": "10"},
        )
        self.assertEqual(cmd[0], "/usr/bin/ssh")
        self.assertIn("-o", cmd)
        self.assertIn("ConnectTimeout=10", cmd)
        self.assertIn("-i", cmd)
        self.assertIn("/home/alice/.ssh/id_ed25519", cmd)
        self.assertIn("-p", cmd)
        self.assertIn("2222", cmd)
        self.assertEqual(cmd[-1], "alice@host.example")

    @patch("zashterminal.utils.platform.shutil.which", return_value="/usr/bin/sftp")
    def test_sftp_uses_capital_p_and_remote_path(self, _which):
        cmd = self.builder.build_remote_command(
            "sftp",
            hostname="host.example",
            username="bob",
            port=2222,
            remote_path="/var/tmp",
        )
        self.assertIn("-P", cmd)
        self.assertIn("2222", cmd)
        self.assertEqual(cmd[-1], "bob@host.example:/var/tmp")

    def test_missing_command_raises(self):
        self.platform.has_command.return_value = False
        with self.assertRaises(Exception):
            self.builder.build_remote_command("ssh", hostname="host")


if __name__ == "__main__":
    unittest.main()
