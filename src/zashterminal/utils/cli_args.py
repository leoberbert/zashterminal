"""Single source of truth for zashterminal CLI options (no GTK).

Launch behaviour for Gio ``HANDLES_COMMAND_LINE`` is implemented by
``parse_launch_cli_args``. The argparse builder is only for ``--help`` /
``--version`` and early pre-GTK flags (``--debug``, ``--log-level``), so the
option list cannot drift from the Gio parser.
"""

from __future__ import annotations

import argparse
import sys
from typing import Callable, List, Optional, Sequence, TypedDict
from urllib.parse import unquote, urlparse

Gettext = Callable[[str], str]


class EarlyCliArgs(TypedDict):
    debug: bool
    log_level: Optional[str]
    want_help: bool
    want_version: bool


class LaunchArgs(TypedDict):
    working_directory: Optional[str]
    execute_command: Optional[str]
    ssh_target: Optional[str]
    close_after_execute: bool
    force_new_window: bool


def _identity(message: str) -> str:
    return message


def convert_remote_uri_to_ssh_target(uri: str) -> Optional[str]:
    """Convert sftp/ssh/fish/scp URIs (KIO/GVFS) to Zashterminal SSH target form.

    Output shape matches ``create_ssh_tab``: ``[user@]host[:port][/path]``.
    """
    raw = (uri or "").strip()
    if not raw:
        return None

    if "://" not in raw:
        # Already a bare SSH target (or local path — caller decides).
        return raw if ("@" in raw or raw.count(":") >= 1) else None

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"sftp", "ssh", "fish", "scp"}:
        return None

    host = parsed.hostname
    if not host:
        return None

    user = unquote(parsed.username) if parsed.username else ""
    port = parsed.port
    path = unquote(parsed.path or "")

    target = f"{user}@{host}" if user else host
    if port and port != 22:
        target = f"{target}:{port}"
    if path and path not in {"", "/"}:
        if not path.startswith("/"):
            path = f"/{path}"
        target = f"{target}{path}"
    return target


def parse_early_cli_args(argv: Optional[Sequence[str]] = None) -> EarlyCliArgs:
    """Parse pre-GTK flags from argv (does not raise on unknown options)."""
    args = list(argv if argv is not None else sys.argv[1:])
    debug = False
    log_level: Optional[str] = None
    want_help = False
    want_version = False

    i = 0
    while i < len(args):
        arg = args[i]
        if arg in {"-h", "--help"}:
            want_help = True
        elif arg in {"-v", "--version"}:
            want_version = True
        elif arg in {"-d", "--debug"}:
            debug = True
        elif arg == "--log-level" and i + 1 < len(args):
            log_level = args[i + 1]
            i += 1
        elif arg.startswith("--log-level="):
            log_level = arg.split("=", 1)[1]
        i += 1

    return {
        "debug": debug,
        "log_level": log_level,
        "want_help": want_help,
        "want_version": want_version,
    }


def build_argument_parser(gettext: Optional[Gettext] = None) -> argparse.ArgumentParser:
    """Build the public CLI help/version parser from the shared option set."""
    _ = gettext or _identity
    parser = argparse.ArgumentParser(
        prog="zashterminal",
        description=_(
            "Zashterminal - A modern terminal emulator with session management"
        ),
        epilog=_(
            "For more information, visit: https://github.com/leoberbert/zashterminal/"
        ),
    )

    class LazyVersionAction(argparse.Action):
        def __call__(self, parser_, _namespace, _values, _option_string=None):
            from zashterminal.settings.config import APP_VERSION

            print(f"zashterminal {APP_VERSION}")
            parser_.exit()

    parser.add_argument(
        "--version",
        "-v",
        nargs=0,
        action=LazyVersionAction,
        help=_("Show version and exit"),
    )
    parser.add_argument(
        "--debug", "-d", action="store_true", help=_("Enable debug mode")
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help=_("Set logging level"),
    )
    parser.add_argument(
        "--working-directory",
        "-w",
        metavar="DIR",
        help=_("Set the working directory for the initial terminal"),
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default=None,
        help=_("Working directory (positional argument)"),
    )
    parser.add_argument(
        "--execute",
        "-e",
        "-x",
        metavar="COMMAND",
        nargs=argparse.REMAINDER,
        help=_(
            "Execute command in the terminal (takes all remaining arguments); "
            "closes the terminal when the command finishes by default"
        ),
    )
    parser.add_argument(
        "--close-after-execute",
        action="store_true",
        help=_("Close terminal after executing command (default with --execute)"),
    )
    parser.add_argument(
        "--no-close-after-execute",
        action="store_true",
        help=_("Keep the shell open after --execute finishes"),
    )
    parser.add_argument(
        "--ssh",
        metavar="[USER@]HOST[:PORT][/PATH]",
        help=_("Connect to SSH host with optional user, port, and remote path"),
    )
    parser.add_argument(
        "--convert-to-ssh",
        metavar="URI",
        help=_("Convert KIO/GVFS URI (sftp/ssh/fish) to an SSH session target"),
    )
    parser.add_argument(
        "--new-window", action="store_true", help=_("Force opening a new window")
    )
    return parser


def handle_help_or_version(
    argv: Optional[Sequence[str]] = None,
    gettext: Optional[Gettext] = None,
) -> Optional[int]:
    """If argv requests --help/--version, print and return an exit code; else None."""
    full_argv = list(argv if argv is not None else sys.argv)
    early = parse_early_cli_args(full_argv)
    if not early["want_help"] and not early["want_version"]:
        return None

    if early["want_version"]:
        from zashterminal.settings.config import APP_VERSION

        print(f"zashterminal {APP_VERSION}")
        return 0

    build_argument_parser(gettext).print_help()
    return 0


def parse_launch_cli_args(
    arguments: list,
    cwd_uri: Optional[str] = None,
    normalize_cwd: Optional[Callable[[Optional[str]], Optional[str]]] = None,
) -> LaunchArgs:
    """Parse argv-style arguments for window/tab launch options (Gio path).

    When ``-e`` / ``-x`` / ``--execute`` is used, ``close_after_execute`` defaults
    to True (xterm/konsole/gnome-terminal behaviour). Explicit
    ``--close-after-execute`` / ``--no-close-after-execute`` override the default.
    Flags after ``-e``/``-x``/``--execute`` are part of the command.
    """
    normalize = normalize_cwd or (lambda path: path)

    working_directory: Optional[str] = None
    execute_command: Optional[str] = None
    ssh_target: Optional[str] = None
    close_after_execute: Optional[bool] = None
    force_new_window = False

    i = 1
    execute_index = None
    while i < len(arguments):
        arg = arguments[i]
        if arg in ["-w", "--working-directory"] and i + 1 < len(arguments):
            working_directory = normalize(arguments[i + 1])
            i += 2
            continue
        if arg.startswith("--working-directory="):
            working_directory = normalize(arg.split("=", 1)[1])
        elif arg in ["-e", "-x", "--execute"]:
            execute_index = i + 1
            break
        elif arg.startswith("--execute="):
            execute_command = arg.split("=", 1)[1]
        elif arg == "--close-after-execute":
            close_after_execute = True
        elif arg == "--no-close-after-execute":
            close_after_execute = False
        elif arg == "--ssh" and i + 1 < len(arguments):
            ssh_target = arguments[i + 1]
            i += 1
        elif arg.startswith("--ssh="):
            ssh_target = arg.split("=", 1)[1]
        elif arg == "--convert-to-ssh" and i + 1 < len(arguments):
            converted = convert_remote_uri_to_ssh_target(arguments[i + 1])
            if converted:
                ssh_target = converted
            i += 1
        elif arg.startswith("--convert-to-ssh="):
            converted = convert_remote_uri_to_ssh_target(arg.split("=", 1)[1])
            if converted:
                ssh_target = converted
        elif arg == "--new-window":
            force_new_window = True
        elif arg in {"-d", "--debug", "-v", "--version", "-h", "--help"}:
            # Handled before GTK / by argparse help path — ignore for launch.
            pass
        elif arg == "--log-level":
            i += 1  # skip value
        elif arg.startswith("--log-level="):
            pass
        elif not arg.startswith("-") and working_directory is None:
            working_directory = normalize(arg)
        i += 1

    if not working_directory:
        working_directory = normalize(cwd_uri)

    if execute_index is not None and execute_index < len(arguments):
        remaining = arguments[execute_index:]
        if remaining:
            execute_command = " ".join(remaining)

    if execute_command and close_after_execute is None:
        close_after_execute = True
    elif close_after_execute is None:
        close_after_execute = False

    return {
        "working_directory": working_directory,
        "execute_command": execute_command,
        "ssh_target": ssh_target,
        "close_after_execute": bool(close_after_execute),
        "force_new_window": force_new_window,
    }


def known_launch_option_names() -> List[str]:
    """Names documented by the shared argparse builder (for tests / drift checks)."""
    parser = build_argument_parser()
    names: List[str] = []
    for action in parser._actions:
        for opt in action.option_strings:
            names.append(opt)
    return names
