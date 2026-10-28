"""Tests for the shared CLI module (single source of truth).

Run with: PYTHONPATH=src python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import unittest

from zashterminal.utils.cli_args import (
    build_argument_parser,
    convert_remote_uri_to_ssh_target,
    handle_help_or_version,
    known_launch_option_names,
    parse_early_cli_args,
    parse_launch_cli_args,
)


class LaunchParseTests(unittest.TestCase):
    def test_execute_short_e_defaults_to_close_after(self):
        parsed = parse_launch_cli_args(["zashterminal", "-e", "true"])
        self.assertEqual(parsed["execute_command"], "true")
        self.assertTrue(parsed["close_after_execute"])

    def test_execute_x_and_long_form_default_to_close_after(self):
        for flag in ("-x", "--execute"):
            with self.subTest(flag=flag):
                parsed = parse_launch_cli_args(["zashterminal", flag, "ls", "-la"])
                self.assertEqual(parsed["execute_command"], "ls -la")
                self.assertTrue(parsed["close_after_execute"])

    def test_execute_equals_form_defaults_to_close_after(self):
        parsed = parse_launch_cli_args(["zashterminal", "--execute=uptime"])
        self.assertEqual(parsed["execute_command"], "uptime")
        self.assertTrue(parsed["close_after_execute"])

    def test_no_close_after_execute_overrides_default(self):
        parsed = parse_launch_cli_args(
            ["zashterminal", "--no-close-after-execute", "-e", "htop"]
        )
        self.assertEqual(parsed["execute_command"], "htop")
        self.assertFalse(parsed["close_after_execute"])

    def test_explicit_close_after_still_works(self):
        parsed = parse_launch_cli_args(
            ["zashterminal", "--close-after-execute", "-e", "ls"]
        )
        self.assertTrue(parsed["close_after_execute"])

    def test_no_execute_keeps_close_after_false(self):
        parsed = parse_launch_cli_args(["zashterminal", "-w", "/tmp"])
        self.assertIsNone(parsed["execute_command"])
        self.assertFalse(parsed["close_after_execute"])
        self.assertEqual(parsed["working_directory"], "/tmp")

    def test_ssh_and_new_window(self):
        parsed = parse_launch_cli_args(
            ["zashterminal", "--new-window", "--ssh", "user@host"]
        )
        self.assertEqual(parsed["ssh_target"], "user@host")
        self.assertTrue(parsed["force_new_window"])
        self.assertFalse(parsed["close_after_execute"])

    def test_convert_to_ssh_sets_target(self):
        parsed = parse_launch_cli_args(
            [
                "zashterminal",
                "--convert-to-ssh",
                "sftp://alice@example.com:2222/home/alice/proj",
            ]
        )
        self.assertEqual(
            parsed["ssh_target"], "alice@example.com:2222/home/alice/proj"
        )


class UriConvertTests(unittest.TestCase):
    def test_sftp_uri(self):
        self.assertEqual(
            convert_remote_uri_to_ssh_target("sftp://bob@host/tmp"),
            "bob@host/tmp",
        )

    def test_fish_uri_with_port(self):
        self.assertEqual(
            convert_remote_uri_to_ssh_target("fish://user@box:2222/var/log"),
            "user@box:2222/var/log",
        )

    def test_rejects_http(self):
        self.assertIsNone(convert_remote_uri_to_ssh_target("https://example.com"))


class EarlyAndHelpTests(unittest.TestCase):
    def test_parse_early_debug_and_log_level(self):
        early = parse_early_cli_args(
            ["zashterminal", "--debug", "--log-level", "INFO", "-e", "true"]
        )
        self.assertTrue(early["debug"])
        self.assertEqual(early["log_level"], "INFO")
        self.assertFalse(early["want_help"])

    def test_handle_version(self):
        code = handle_help_or_version(["zashterminal", "--version"])
        self.assertEqual(code, 0)

    def test_handle_help(self):
        code = handle_help_or_version(["zashterminal", "--help"])
        self.assertEqual(code, 0)

    def test_no_early_exit_for_normal_argv(self):
        self.assertIsNone(handle_help_or_version(["zashterminal", "-e", "true"]))


class ParserDriftTests(unittest.TestCase):
    def test_help_parser_documents_launch_flags(self):
        names = set(known_launch_option_names())
        for required in (
            "-e",
            "--execute",
            "-x",
            "--ssh",
            "--convert-to-ssh",
            "--new-window",
            "--no-close-after-execute",
            "--close-after-execute",
            "-w",
            "--working-directory",
            "-d",
            "--debug",
        ):
            self.assertIn(required, names)

    def test_build_parser_prog(self):
        parser = build_argument_parser()
        self.assertEqual(parser.prog, "zashterminal")


if __name__ == "__main__":
    unittest.main()
