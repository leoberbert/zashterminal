"""Pure execute-command helper tests (no GTK).

Run with: PYTHONPATH=src python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import unittest

from zashterminal.terminal.execute_command import (
    build_feed_child_payloads,
    pack_execute_user_data,
    split_execute_lines,
    unpack_execute_user_data,
)


class SplitAndFeedTests(unittest.TestCase):
    def test_split_drops_blank_lines(self):
        # Outer strip removes trailing whitespace before split.
        self.assertEqual(split_execute_lines("a\n\n  b\n\n"), ["a", "  b"])

    def test_feed_without_close(self):
        self.assertEqual(
            build_feed_child_payloads("echo a\necho b", False),
            [b"echo a\n", b"echo b\n"],
        )

    def test_feed_with_close_appends_exit(self):
        self.assertEqual(
            build_feed_child_payloads("true", True),
            [b"true\n", b"exit\n"],
        )

    def test_whitespace_only_with_close_still_exits(self):
        self.assertEqual(
            build_feed_child_payloads("  \n  ", True),
            [b"exit\n"],
        )

    def test_empty_without_close_is_empty(self):
        self.assertEqual(build_feed_child_payloads("", False), [])


class PackUnpackTests(unittest.TestCase):
    def test_roundtrip(self):
        packed = pack_execute_user_data(42, "uptime", True)
        self.assertEqual(packed[0], 42)
        command, close_after = unpack_execute_user_data(packed)
        self.assertEqual(command, "uptime")
        self.assertTrue(close_after)

    def test_unpack_none_command(self):
        original = pack_execute_user_data(7, None, False)
        command, close_after = unpack_execute_user_data(original)
        self.assertIsNone(command)
        self.assertFalse(close_after)

    def test_unpack_rejects_non_dict_payload(self):
        command, close_after = unpack_execute_user_data((1, "not-a-dict"))
        self.assertIsNone(command)
        self.assertFalse(close_after)

    def test_unpack_empty_string_command_becomes_none(self):
        command, close_after = unpack_execute_user_data(
            {"execute_command": "", "close_after_execute": True}
        )
        self.assertIsNone(command)
        self.assertTrue(close_after)


if __name__ == "__main__":
    unittest.main()
