# zashterminal/terminal/highlighter/__init__.py
"""
Syntax highlighting module for terminal output and shell input.

This package provides:
- OutputHighlighter: Rule-based highlighting for command output
- ShellInputHighlighter: Pygments-based highlighting for shell input
- HighlightedTerminalProxy: PTY proxy that applies highlighting in real-time

Usage:
    from zashterminal.terminal.highlighter import (
        OutputHighlighter,
        ShellInputHighlighter,
        HighlightedTerminalProxy,
        get_output_highlighter,
        get_shell_input_highlighter,
    )

Note: This module uses lazy loading to improve startup performance.
Heavy modules are only imported when their symbols are first accessed.
"""

from typing import TYPE_CHECKING

# Lightweight constants are imported eagerly (no heavy dependencies)
from .constants import (
    ALT_SCREEN_DISABLE_PATTERNS,
    ALT_SCREEN_ENABLE_PATTERNS,
    ANSI_RESET,
)

# Rules are also lightweight
from .rules import CompiledRule, LiteralKeywordRule

# Type checking imports don't affect runtime
if TYPE_CHECKING:
    from .output import OutputHighlighter
    from .shell_input import ShellInputHighlighter
    from .._highlighter_impl import HighlightedTerminalProxy

__all__ = [
    # Constants
    "ANSI_RESET",
    "ALT_SCREEN_ENABLE_PATTERNS",
    "ALT_SCREEN_DISABLE_PATTERNS",
    # Classes
    "CompiledRule",
    "LiteralKeywordRule",
    "OutputHighlighter",
    "ShellInputHighlighter",
    "HighlightedTerminalProxy",
    # Functions
    "get_output_highlighter",
    "get_shell_input_highlighter",
]


# Lazy loading for heavy modules
_output_highlighter_module = None
_shell_input_module = None
_highlighter_impl_module = None


def get_output_highlighter():
    """Get the singleton OutputHighlighter instance (lazy import)."""
    global _output_highlighter_module
    if _output_highlighter_module is None:
        from .output import get_output_highlighter as _get_output_highlighter

        _output_highlighter_module = _get_output_highlighter
    return _output_highlighter_module()


def get_shell_input_highlighter():
    """Get the singleton ShellInputHighlighter instance (lazy import)."""
    global _shell_input_module
    if _shell_input_module is None:
        from .shell_input import (
            get_shell_input_highlighter as _get_shell_input_highlighter,
        )

        _shell_input_module = _get_shell_input_highlighter
    return _shell_input_module()


def __getattr__(name: str):
    """Lazy loading for heavy classes."""
    global _output_highlighter_module, _shell_input_module, _highlighter_impl_module

    if name == "OutputHighlighter":
        from .output import OutputHighlighter

        return OutputHighlighter
    elif name == "ShellInputHighlighter":
        from .shell_input import ShellInputHighlighter

        return ShellInputHighlighter
    elif name == "HighlightedTerminalProxy":
        from .._highlighter_impl import HighlightedTerminalProxy

        return HighlightedTerminalProxy

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
