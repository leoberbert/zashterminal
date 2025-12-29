# zashterminal/utils/syntax_utils.py
"""
Centralized syntax highlighting utilities for bash commands.

This module provides Pango markup generation for bash command syntax highlighting,
used by both the Command Manager dialogs and the BashTextView widget.
"""

import re
from typing import Dict, List, Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib

# Pre-compiled regex patterns for syntax highlighting (compiled once at module load)
_PATTERN_URL = re.compile(r"(https?://[^\s]+)")
_PATTERN_SINGLE_QUOTE = re.compile(r"('(?:[^'\\]|\\.)*')")
_PATTERN_DOUBLE_QUOTE = re.compile(r'("(?:[^"\\]|\\.)*")')
_PATTERN_VARIABLE = re.compile(r"(\$\{?[A-Za-z_][A-Za-z0-9_]*\}?)")
_PATTERN_SPECIAL_VAR = re.compile(r"(\$[\?\!\$@\*#0-9])")
_PATTERN_FLAG_SPACE = re.compile(r"(\s)(--?[a-zA-Z][a-zA-Z0-9-]*)")
_PATTERN_FLAG_START = re.compile(r"^(--?[a-zA-Z][a-zA-Z0-9-]*)")
_PATTERN_NUMBER_NEG = re.compile(r"(\s)(-\d+)(\s|$)")
_PATTERN_NUMBER = re.compile(r"(\s)(\d+[KMGTkmgt]?)(\s|$)")
_PATTERN_PATH_SPACE = re.compile(r"(\s)(/[^\s'\"]+)")
_PATTERN_PATH_START = re.compile(r"^(/[^\s'\"]+)")
_PATTERN_COMMAND = re.compile(
    r"^(find|grep|ls|cat|echo|cd|rm|mkdir|touch|cp|mv|chmod|chown|tar|curl|wget)\b"
)
_PATTERN_OPERATOR = re.compile(r"(\||&gt;|&lt;|&amp;&amp;|&gt;&gt;|\|\|)")
_PATTERN_BACKTICK = re.compile(r"(`[^`]*`)")
_PATTERN_SUBSHELL = re.compile(r"(\$\([^\)]*\))")


def get_bash_pango_markup(
    command: str,
    palette: Optional[List[str]] = None,
    foreground: str = "#ffffff",
) -> str:
    """
    Convert a bash command to Pango markup with syntax highlighting.
    Uses custom regex-based highlighting for better results with shell commands.

    Args:
        command: The bash command to highlight
        palette: Optional terminal color scheme palette (16 colors)
            [0-7]: Normal colors (black, red, green, yellow, blue, magenta, cyan, white)
            [8-15]: Bright colors (bright versions of above)
        foreground: Foreground color for dimmed text (used when palette unavailable)

    Returns:
        A Pango markup string with syntax highlighting applied
    """
    colors = _build_color_map(palette)
    result = GLib.markup_escape_text(command)

    # Apply pattern replacements using pre-compiled patterns
    # Order matters - most specific patterns first

    # URLs (before paths)
    result = _PATTERN_URL.sub(
        f'<span foreground="{colors["path"]}">' + r"\1</span>", result
    )
    # Single-quoted strings
    result = _PATTERN_SINGLE_QUOTE.sub(
        f'<span foreground="{colors["string"]}">' + r"\1</span>", result
    )
    # Double-quoted strings
    result = _PATTERN_DOUBLE_QUOTE.sub(
        f'<span foreground="{colors["string"]}">' + r"\1</span>", result
    )
    # Variables $VAR or ${VAR}
    result = _PATTERN_VARIABLE.sub(
        f'<span foreground="{colors["variable"]}">' + r"\1</span>", result
    )
    # Special variables $?, $!, $$, $@, $*, $#, $0-9
    result = _PATTERN_SPECIAL_VAR.sub(
        f'<span foreground="{colors["special_var"]}">' + r"\1</span>", result
    )
    # Flags/options after space
    result = _PATTERN_FLAG_SPACE.sub(
        r"\1" + f'<span foreground="{colors["flag"]}">' + r"\2</span>", result
    )
    # Flags/options at start
    result = _PATTERN_FLAG_START.sub(
        f'<span foreground="{colors["flag"]}">' + r"\1</span>", result
    )
    # Negative numbers (like -10 in -mtime -10)
    result = _PATTERN_NUMBER_NEG.sub(
        r"\1" + f'<span foreground="{colors["number"]}">' + r"\2</span>" + r"\3", result
    )
    # Standalone numbers
    result = _PATTERN_NUMBER.sub(
        r"\1" + f'<span foreground="{colors["number"]}">' + r"\2</span>" + r"\3", result
    )
    # Paths after space
    result = _PATTERN_PATH_SPACE.sub(
        r"\1" + f'<span foreground="{colors["path"]}">' + r"\2</span>", result
    )
    # Paths at start
    result = _PATTERN_PATH_START.sub(
        f'<span foreground="{colors["path"]}">' + r"\1</span>", result
    )
    # Common commands at start
    result = _PATTERN_COMMAND.sub(
        f'<span foreground="{colors["command"]}">' + r"\1</span>", result
    )
    # Redirections and pipes
    result = _PATTERN_OPERATOR.sub(
        f'<span foreground="{colors["operator"]}">' + r"\1</span>", result
    )
    # Backticks
    result = _PATTERN_BACKTICK.sub(
        f'<span foreground="{colors["substitution"]}">' + r"\1</span>", result
    )
    # Subshell
    result = _PATTERN_SUBSHELL.sub(
        f'<span foreground="{colors["substitution"]}">' + r"\1</span>", result
    )

    return result


def _build_color_map(palette: Optional[List[str]] = None) -> Dict[str, str]:
    """
    Build a color map for syntax highlighting from a terminal color palette.

    Args:
        palette: Optional terminal color scheme palette (16 colors).
            Terminal color positions:
            0=black, 1=red, 2=green, 3=yellow, 4=blue, 5=magenta, 6=cyan, 7=white
            8-15 are bright variants of the above.

    Returns:
        Dictionary mapping syntax element names to hex color strings.
    """
    if palette and len(palette) >= 8:
        return {
            "command": palette[2] if len(palette) > 2 else "#8ae234",  # Green
            "string": palette[3] if len(palette) > 3 else "#e9b96e",  # Yellow
            "variable": palette[5] if len(palette) > 5 else "#ad7fa8",  # Magenta
            "special_var": palette[13] if len(palette) > 13 else "#ff69b4",  # Bright magenta
            "flag": palette[14] if len(palette) > 14 else "#98d8c8",  # Bright cyan
            "number": palette[11] if len(palette) > 11 else "#f4d03f",  # Bright yellow
            "path": palette[6] if len(palette) > 6 else "#87ceeb",  # Cyan
            "operator": palette[3] if len(palette) > 3 else "#fcaf3e",  # Yellow
            "substitution": palette[11] if len(palette) > 11 else "#b8860b",  # Bright yellow
        }

    # Default colors (no palette provided)
    return {
        "command": "#8ae234",
        "string": "#e9b96e",
        "variable": "#ad7fa8",
        "special_var": "#ff69b4",
        "flag": "#98d8c8",
        "number": "#f4d03f",
        "path": "#87ceeb",
        "operator": "#fcaf3e",
        "substitution": "#b8860b",
    }
