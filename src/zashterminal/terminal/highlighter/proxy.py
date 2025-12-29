# zashterminal/terminal/highlighter/proxy.py
"""
PTY proxy with real-time syntax highlighting.

This module provides HighlightedTerminalProxy, which wraps a PTY
and applies syntax highlighting to output in real-time.
"""

# For now, import from the implementation module
# This will be moved here in a future refactoring step
from .._highlighter_impl import HighlightedTerminalProxy

__all__ = ["HighlightedTerminalProxy"]
