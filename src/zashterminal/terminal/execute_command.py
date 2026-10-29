"""Pure helpers for CLI ``-e`` / close-after command feeding.

Kept free of GTK/VTE so unit tests can cover feed payloads without a display.
"""

from __future__ import annotations

from typing import List, Optional, Tuple


def split_execute_lines(command: str) -> List[str]:
    """Split a multi-line execute payload, dropping blank lines."""
    if not command:
        return []
    return [line for line in command.strip().split("\n") if line.strip()]


def build_feed_child_payloads(
    command: str, close_after_execute: bool = False
) -> List[bytes]:
    """Return the exact byte sequences that should be fed to the VTE child.

    When ``close_after_execute`` is True, appends ``exit\\n`` after the command
    lines so the shell (and typically the last tab) terminates.
    """
    lines = split_execute_lines(command)
    payloads = [f"{line}\n".encode("utf-8") for line in lines]
    if close_after_execute:
        payloads.append(b"exit\n")
    return payloads


def pack_execute_user_data(
    terminal_id: int,
    execute_command: Optional[str],
    close_after_execute: bool = False,
) -> Tuple[int, dict]:
    """Build the ``(terminal_id, dict)`` tuple passed through spawn callbacks."""
    return (
        terminal_id,
        {
            "execute_command": execute_command,
            "close_after_execute": bool(close_after_execute),
        },
    )


def unpack_execute_user_data(user_data) -> Tuple[Optional[str], bool]:
    """Extract execute command + close flag from spawn ``original_user_data``."""
    if isinstance(user_data, tuple) and len(user_data) >= 2:
        payload = user_data[1]
    else:
        payload = user_data
    if not isinstance(payload, dict):
        return None, False
    command = payload.get("execute_command")
    if command is not None:
        command = str(command) or None
    return command, bool(payload.get("close_after_execute", False))
