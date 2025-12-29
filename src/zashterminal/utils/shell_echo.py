"""Utilities for handling shell echo/redraw behavior.

This module is intentionally pure-Python (no GI/VTE dependencies) so it can be
unit-tested in minimal environments.
"""


def is_echo_terminator(line: str) -> bool:
    """Return True if `line` ends the echoed command.

    Readline/redraw during paste can emit standalone carriage returns ("\r")
    and cursor movement sequences without ending the command echo. We only
    consider the echo finished once a newline is observed.

    Args:
        line: A text chunk (may include line endings).

    Returns:
        True if the chunk ends with a newline ("\n").
    """
    return line.endswith("\n")


def ends_with_line_break_bytes(data: bytes) -> bool:
    """Return True if `data` ends with a line break byte.

    We consider both LF (\n) and CR (\r) as line breaks because the terminal
    stream can contain CRLF or CR-only control flows.

    Args:
        data: Raw bytes previously fed to the terminal.

    Returns:
        True if `data` ends with \n or \r.
    """
    return bool(data) and data.endswith((b"\n", b"\r"))


def should_prepend_newline_before_prompt(
    *, last_output_ended_with_line_break: bool, prompt_bytes: bytes
) -> bool:
    """Decide whether to prepend a newline before rendering the prompt.

    This is used to fix the classic case where a command like `cat file` prints
    a file that does *not* end with a trailing newline. In that case, the shell
    prompt is rendered immediately after the last line of the file, breaking
    cursor movement and visual alignment.

    Args:
        last_output_ended_with_line_break: True if the last output chunk ended
            with a CR/LF.
        prompt_bytes: The bytes that will be fed for the prompt/control line.

    Returns:
        True if a CRLF should be inserted before `prompt_bytes`.
    """
    if last_output_ended_with_line_break:
        return False
    if not prompt_bytes:
        return False
    # If the prompt/control chunk already starts on a new line, do not add one.
    if prompt_bytes.startswith((b"\r", b"\n")):
        return False
    return True


def split_incomplete_escape_suffix(data: bytes) -> tuple[bytes, bytes]:
    """Split `data` into (prefix, incomplete_escape_suffix).

    The terminal stream can be fragmented arbitrarily. If an escape sequence is
    split across reads (e.g., a chunk ends with ESC and the next begins with
    "[?25h"), feeding those chunks separately can cause the remainder to be
    rendered literally.

    This helper identifies an *incomplete* ANSI escape sequence starting at the
    last ESC (0x1b) and returning it as `suffix`. If the last escape sequence is
    complete (or there is no ESC), `suffix` is empty.

    Args:
        data: Raw bytes chunk.

    Returns:
        (prefix, suffix) where suffix is either b"" or starts with b"\x1b".
    """
    if not data:
        return b"", b""

    last_esc = data.rfind(b"\x1b")
    if last_esc < 0:
        return data, b""

    # If ESC is the very last byte, it's definitely incomplete.
    if last_esc == len(data) - 1:
        return data[:last_esc], data[last_esc:]

    second = data[last_esc + 1]

    # CSI: ESC [ ... <final byte 0x40..0x7E>
    if second == 0x5B:  # '['
        for i in range(last_esc + 2, len(data)):
            c = data[i]
            if 0x40 <= c <= 0x7E:
                return data, b""  # complete
        return data[:last_esc], data[last_esc:]

    # OSC: ESC ] ... BEL or ST (ESC \)
    if second == 0x5D:  # ']'
        for i in range(last_esc + 2, len(data)):
            c = data[i]
            if c == 0x07:  # BEL
                return data, b""
            if c == 0x1B and i + 1 < len(data) and data[i + 1] == 0x5C:  # ST
                return data, b""
        return data[:last_esc], data[last_esc:]

    # Charset designators: ESC ( X or ESC ) X
    if second in (0x28, 0x29):
        if last_esc + 2 < len(data):
            return data, b""
        return data[:last_esc], data[last_esc:]

    # Other ESC sequences are typically 2 bytes long (ESC <final>).
    return data, b""
