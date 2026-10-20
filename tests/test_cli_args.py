"""Tests for launch CLI argument parsing (issue #69)."""

from zashterminal.utils.cli_args import parse_launch_cli_args


def test_execute_short_e_defaults_to_close_after():
    parsed = parse_launch_cli_args(["zashterminal", "-e", "true"])
    assert parsed["execute_command"] == "true"
    assert parsed["close_after_execute"] is True


def test_execute_x_and_long_form_default_to_close_after():
    for flag in ("-x", "--execute"):
        parsed = parse_launch_cli_args(["zashterminal", flag, "ls", "-la"])
        assert parsed["execute_command"] == "ls -la"
        assert parsed["close_after_execute"] is True


def test_execute_equals_form_defaults_to_close_after():
    parsed = parse_launch_cli_args(["zashterminal", "--execute=uptime"])
    assert parsed["execute_command"] == "uptime"
    assert parsed["close_after_execute"] is True


def test_no_close_after_execute_overrides_default():
    parsed = parse_launch_cli_args(
        ["zashterminal", "--no-close-after-execute", "-e", "htop"]
    )
    assert parsed["execute_command"] == "htop"
    assert parsed["close_after_execute"] is False


def test_explicit_close_after_still_works():
    parsed = parse_launch_cli_args(
        ["zashterminal", "--close-after-execute", "-e", "ls"]
    )
    assert parsed["close_after_execute"] is True


def test_no_execute_keeps_close_after_false():
    parsed = parse_launch_cli_args(["zashterminal", "-w", "/tmp"])
    assert parsed["execute_command"] is None
    assert parsed["close_after_execute"] is False
    assert parsed["working_directory"] == "/tmp"


def test_ssh_and_new_window_unaffected():
    parsed = parse_launch_cli_args(
        ["zashterminal", "--new-window", "--ssh", "user@host"]
    )
    assert parsed["ssh_target"] == "user@host"
    assert parsed["force_new_window"] is True
    assert parsed["close_after_execute"] is False
