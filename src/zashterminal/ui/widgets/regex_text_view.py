# zashterminal/ui/widgets/regex_text_view.py
"""
A single-line text entry widget with regex syntax highlighting.
Supports terminal color scheme integration for consistent theming.
"""

import re
from typing import List

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk

from ..colors import SYNTAX_DARK_COLORS, SYNTAX_LIGHT_COLORS
from .base_syntax_text_view import BaseSyntaxTextView


# Regex patterns for syntax elements
_REGEX_PATTERNS = {
    "bracket": r"[\[\]]",           # Character class brackets
    "group": r"[()]",               # Grouping parentheses
    "quantifier": r"[*+?]|\{\d+(?:,\d*)?\}",  # Quantifiers: *, +, ?, {n}, {n,}, {n,m}
    "anchor": r"\\[bBAZzGsSwWdD]|[\^$]",  # Anchors and special escapes
    "escape": r"\\[nrtfvae0-9]|\\x[0-9a-fA-F]{2}|\\u[0-9a-fA-F]{4}",  # Escape sequences
    "special": r"\\.|\\[\\^?|:alpha:|:digit:|:alnum:|:space:|:word:",  # Special chars & POSIX classes
    "operator": r"[|&]",            # Alternation, conjunction
    "range": r"-(?=[^\[\]])",       # Range operator inside character class (simplified)
}

# Regex-specific token types
REGEX_TOKEN_TYPES = ("bracket", "group", "quantifier", "anchor", "escape", "special", "operator", "range")


class RegexTextView(BaseSyntaxTextView):
    """
    A single-line text entry with regex syntax highlighting.
    Designed for entering regular expressions with visual feedback.
    Blocks Enter/Return to prevent multi-line input.
    """

    # Use centralized color definitions filtered for regex tokens
    _DEFAULT_DARK_COLORS = {k: v for k, v in SYNTAX_DARK_COLORS.items() if k in REGEX_TOKEN_TYPES}
    _DEFAULT_LIGHT_COLORS = {k: v for k, v in SYNTAX_LIGHT_COLORS.items() if k in REGEX_TOKEN_TYPES}

    def __init__(self, single_line: bool = True):
        """
        Initialize the RegexTextView.
        
        Args:
            single_line: Whether to block Enter/Return for single-line mode
        """
        super().__init__(
            css_class="regex-textview",
            wrap_mode=Gtk.WrapMode.NONE,
            top_margin=4,
            bottom_margin=4,
            left_margin=8,
            right_margin=8,
            accepts_tab=False,
        )

        self._single_line = single_line

        # Initialize colors and tags
        self._syntax_colors = self._get_default_colors()
        self._setup_tags()

        # Connect to text changes for highlighting
        self.buffer.connect("changed", self._on_buffer_changed)

        # Block newlines in single-line mode
        if single_line:
            key_controller = Gtk.EventControllerKey()
            key_controller.connect("key-pressed", self._on_key_pressed)
            self.add_controller(key_controller)

    def _on_key_pressed(self, controller, keyval, _keycode, state) -> bool:
        """Block Enter/Return key in single-line mode."""
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            return True  # Block the event
        return False

    def update_colors_from_scheme(self, palette: List[str], foreground: str = "#ffffff"):
        """
        Update syntax highlighting colors from a terminal color scheme palette.
        
        Args:
            palette: List of hex color strings from the terminal color scheme
            foreground: Foreground color (not used for regex, kept for API consistency)
        """
        if len(palette) < 8:
            return

        # Map palette colors to syntax elements
        self._syntax_colors = {
            "bracket": palette[4] if len(palette) > 4 else "#729fcf",      # Blue
            "group": palette[5] if len(palette) > 5 else "#ad7fa8",        # Magenta
            "quantifier": palette[2] if len(palette) > 2 else "#8ae234",   # Green
            "anchor": palette[3] if len(palette) > 3 else "#fcaf3e",       # Yellow
            "escape": palette[11] if len(palette) > 11 else "#e9b96e",     # Bright yellow
            "special": palette[13] if len(palette) > 13 else "#ff69b4",    # Bright magenta
            "operator": palette[3] if len(palette) > 3 else "#f4d03f",     # Yellow
            "range": palette[6] if len(palette) > 6 else "#87ceeb",        # Cyan
        }

        self._update_tag_colors()
        self._apply_highlighting()

    def _on_buffer_changed(self, buffer):
        """Handle buffer changes for syntax highlighting."""
        self._schedule_highlighting(delay_ms=100)

    def _apply_highlighting(self) -> bool:
        """Apply regex syntax highlighting."""
        self._highlight_timeout_id = None

        text = self.get_text()

        if not text:
            return False

        # Remove existing tags
        self._clear_highlighting()

        # Apply highlighting based on regex patterns
        # Order matters: more specific patterns should come first
        highlight_order = ["escape", "anchor", "quantifier", "bracket", "group", "special", "operator", "range"]

        for tag_name in highlight_order:
            pattern_str = _REGEX_PATTERNS.get(tag_name)
            if not pattern_str:
                continue

            try:
                pattern = re.compile(pattern_str)
                for match in pattern.finditer(text):
                    start_offset = match.start()
                    end_offset = match.end()
                    start_iter = self.buffer.get_iter_at_offset(start_offset)
                    end_iter = self.buffer.get_iter_at_offset(end_offset)
                    self.buffer.apply_tag_by_name(tag_name, start_iter, end_iter)
            except re.error:
                pass  # Skip invalid patterns

        return False
