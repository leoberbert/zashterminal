# zashterminal/terminal/highlighter/constants.py
"""
Constants and pre-compiled patterns for terminal highlighting.

This module contains:
- ANSI escape sequence patterns
- Alt screen detection patterns
- Shell prompt detection patterns
- Utility functions for word boundary detection
"""

import re
from typing import Set

# ANSI reset sequence
ANSI_RESET = "\033[0m"

# Alt screen (alternate screen buffer) patterns
ALT_SCREEN_ENABLE_PATTERNS = [
    b"\x1b[?1049h",
    b"\x1b[?47h",
    b"\x1b[?1047h",
]

ALT_SCREEN_DISABLE_PATTERNS = [
    b"\x1b[?1049l",
    b"\x1b[?47l",
    b"\x1b[?1047l",
]

# Pre-compiled pattern for extracting keywords from alternation patterns
KEYWORD_PATTERN = re.compile(r"^\\b\(([a-zA-Z|?:()]+)\)\\b$")

# Pre-compiled pattern for stripping ANSI escape sequences
ANSI_SEQ_PATTERN = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")

# Pre-compiled pattern to detect ANSI color codes (SGR sequences)
# Matches: standard colors (30-37, 40-47, 90-97, 100-107), 256-color (38;5;N, 48;5;N),
# and RGB colors (38;2;R;G;B, 48;2;R;G;B)
# Also handles leading attributes like "1;" (bold), "0;" (reset) which precede colors.
# Requires the 'm' terminator to ensure we match actual SGR color sequences.
ANSI_COLOR_PATTERN = re.compile(
    r'\x1b\[(?:[0-9;]*;)?'  # Optional leading attributes (0;, 1;, 00;, etc)
    r'(?:'
    r'3[0-7]|4[0-7]|9[0-7]|10[0-7]|'  # Standard and bright colors
    r'38;5;\d+|48;5;\d+|'              # 256-color mode
    r'38;2;\d+;\d+;\d+|48;2;\d+;\d+;\d+'  # True color (RGB)
    r')[;0-9]*m'  # Optional trailing params + SGR terminator
)

# Pre-compiled pattern for ANSI color codes with 'm' terminator
ANSI_COLOR_M_PATTERN = re.compile(r"\x1b\[[0-9;]*m")

# Pre-compiled patterns for stripping CSI control sequences in cat output
CSI_CONTROL_PATTERN = re.compile(r'\x1b\[\??[0-9;]*[ABCDEFGHJKLMPSTXZfhlnsu]|\r')
SGR_RESET_LINE_PATTERN = re.compile(r'^\x1b\(B\x1b\[m\s*$')

# Pre-compiled pattern for stripping OSC (Operating System Command) sequences
OSC_SEQ_PATTERN = re.compile(r"\x1b\][0-9]+;[^\x07\x1b]*[\x07]")

# Pre-compiled pattern for stripping all ANSI/OSC sequences (for input processing)
ALL_ESCAPE_SEQ_PATTERN = re.compile(r"\x1b\[[^a-zA-Z]*[a-zA-Z]|\x1b\].*?\x07")

# Pre-compiled pattern for shell name prompt detection
SHELL_NAME_PROMPT_PATTERN = re.compile(r"^(sh|bash|zsh|fish|dash|ksh|csh|tcsh)(-[\d.]+)?$")

# Pre-compiled pattern for root prompt detection (user@host#)
ROOT_PROMPT_PATTERN = re.compile(r"\w+@\w+.*#$")

# Word character set for boundary detection
WORD_CHAR: Set[str] = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def is_word_boundary(text: str, start: int, end: int) -> bool:
    """
    Check if a match at [start:end] has word boundaries.

    A word boundary exists when:
    - start == 0 or text[start-1] is not a word character
    - end == len(text) or text[end] is not a word character
    
    Args:
        text: The text to check boundaries in.
        start: Start position of the match.
        end: End position of the match.
        
    Returns:
        True if the match has word boundaries on both sides.
    """
    # Check start boundary
    if start > 0 and text[start - 1] in WORD_CHAR:
        return False
    # Check end boundary
    if end < len(text) and text[end] in WORD_CHAR:
        return False
    return True
