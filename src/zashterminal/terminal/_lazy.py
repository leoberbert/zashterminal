"""Shared lazy imports and patterns for terminal manager mixins."""

from __future__ import annotations

import re

# Pre-compiled pattern for ANSI escape sequences used in command detection.
# Quantifiers are bounded to avoid catastrophic backtracking (ReDoS).
ANSI_ESCAPE_PATTERN = re.compile(
    r"(?:"
    r"\x1b\[[0-9;?]{0,64}[A-Za-z]"  # CSI
    r"|\x1b\][^\x07]{0,512}\x07"  # OSC … BEL
    r"|\[{1,8}\??(?:\d{1,4};?){0,16}[ABCDEFGHJKPSTfmnsuhl]"  # malformed CSI
    r")"
)

_spawner = None
_highlight_manager = None
_output_highlighter = None
_terminal_menu_creator = None


def get_spawner():
    global _spawner
    if _spawner is None:
        from .spawner import get_spawner as _factory

        _spawner = _factory
    return _spawner()


def get_highlight_manager():
    global _highlight_manager
    if _highlight_manager is None:
        from ..settings.highlights import get_highlight_manager as _factory

        _highlight_manager = _factory
    return _highlight_manager()


def get_output_highlighter():
    global _output_highlighter
    if _output_highlighter is None:
        from .highlighter import get_output_highlighter as _factory

        _output_highlighter = _factory
    return _output_highlighter()


def create_terminal_menu(*args, **kwargs):
    global _terminal_menu_creator
    if _terminal_menu_creator is None:
        from ..ui.menus import create_terminal_menu as _factory

        _terminal_menu_creator = _factory
    return _terminal_menu_creator(*args, **kwargs)
