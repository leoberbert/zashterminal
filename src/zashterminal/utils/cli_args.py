"""Pure CLI argument parsing for launch-time behaviour (no GTK)."""

from __future__ import annotations

from typing import Callable, Optional, TypedDict


class LaunchArgs(TypedDict):
    working_directory: Optional[str]
    execute_command: Optional[str]
    ssh_target: Optional[str]
    close_after_execute: bool
    force_new_window: bool


def parse_launch_cli_args(
    arguments: list,
    cwd_uri: Optional[str] = None,
    normalize_cwd: Optional[Callable[[Optional[str]], Optional[str]]] = None,
) -> LaunchArgs:
    """Parse argv-style arguments for window/tab launch options.

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
        elif arg == "--new-window":
            force_new_window = True
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
