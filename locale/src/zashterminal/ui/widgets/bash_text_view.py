# zashterminal/ui/widgets/bash_text_view.py
"""
A multi-line text view widget with bash syntax highlighting using Pygments.
Supports terminal color scheme integration for consistent theming.
"""

import re
from typing import List

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from ..colors import SYNTAX_DARK_COLORS, SYNTAX_LIGHT_COLORS
from .base_syntax_text_view import BaseSyntaxTextView

# Try to import Pygments for bash syntax highlighting
try:
    from pygments.lexers import BashLexer
    PYGMENTS_AVAILABLE = True
except ImportError:
    PYGMENTS_AVAILABLE = False

# Pre-compiled patterns for extra highlighting
_PATH_PATTERN = re.compile(r'(?:^|\s)((?:/[\w.\-]+)+|(?:\.{1,2}/[\w.\-/]+)|(?:~/[\w.\-/]*))')
_FLAG_PATTERN = re.compile(r'(?:^|\s)(--?[\w\-]+=?)')
_SPECIAL_VAR_PATTERN = re.compile(r'(\$[?!@*#$0-9-])')

# Bash-specific token types
BASH_TOKEN_TYPES = (
    "keyword", "builtin", "command", "string", "string_single", "backtick",
    "comment", "variable", "special_var", "operator", "number", "path",
    "function", "redirect", "pipe", "flag", "escape", "substitution", "brace",
)


class BashTextView(BaseSyntaxTextView):
    """
    A multi-line text view with bash syntax highlighting using Pygments.
    Falls back to monospace font if Pygments is unavailable.
    Auto-resizes based on content up to a maximum height.
    """

    # Use centralized color definitions filtered for bash tokens
    _DEFAULT_DARK_COLORS = {k: v for k, v in SYNTAX_DARK_COLORS.items() if k in BASH_TOKEN_TYPES}
    _DEFAULT_LIGHT_COLORS = {k: v for k, v in SYNTAX_LIGHT_COLORS.items() if k in BASH_TOKEN_TYPES}

    def __init__(self, auto_resize: bool = True, min_lines: int = 2, max_lines: int = 8):
        """
        Initialize the BashTextView.
        
        Args:
            auto_resize: Whether to automatically resize based on content
            min_lines: Minimum number of visible lines
            max_lines: Maximum number of visible lines
        """
        super().__init__(
            css_class="bash-textview",
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            top_margin=6,
            bottom_margin=6,
            left_margin=4,
            right_margin=4,
            accepts_tab=False,
        )

        # Auto-resize configuration
        self._auto_resize = auto_resize
        self._line_height = 22  # Pixels per line (compact spacing)
        self._min_lines = min_lines
        self._max_lines = max_lines

        # Set line spacing - minimal for compact display
        self.set_pixels_above_lines(1)
        self.set_pixels_below_lines(1)
        self.set_pixels_inside_wrap(1)

        # Initialize colors and tags
        self._syntax_colors = self._get_default_colors()
        self._setup_tags()

        # Connect to text changes for highlighting and auto-resize
        self.buffer.connect("changed", self._on_buffer_changed)

        # Apply highlighting when widget becomes visible
        self.connect("map", self._on_map)

    def update_colors_from_scheme(self, palette: List[str], foreground: str = "#ffffff"):
        """
        Update syntax highlighting colors from a terminal color scheme palette.
        
        The palette is typically 16 colors:
        [0-7]: Normal colors (black, red, green, yellow, blue, magenta, cyan, white)
        [8-15]: Bright colors (bright versions of above)
        
        Args:
            palette: List of hex color strings from the terminal color scheme
            foreground: Foreground color for normal text/comments
        """
        if len(palette) < 8:
            return  # Not enough colors

        # Map palette colors to syntax elements
        # Using standard terminal color positions:
        # 0=black, 1=red, 2=green, 3=yellow, 4=blue, 5=magenta, 6=cyan, 7=white
        # 8-15 are bright variants

        self._syntax_colors = {
            "keyword": palette[4] if len(palette) > 4 else "#729fcf",      # Blue
            "builtin": palette[2] if len(palette) > 2 else "#8ae234",      # Green
            "command": palette[2] if len(palette) > 2 else "#8ae234",      # Green
            "string": palette[3] if len(palette) > 3 else "#e9b96e",       # Yellow
            "string_single": palette[3] if len(palette) > 3 else "#e9b96e",# Yellow
            "backtick": palette[11] if len(palette) > 11 else "#b8860b",   # Bright yellow
            "comment": foreground + "80" if len(foreground) == 7 else "#888a85",  # Dimmed foreground
            "variable": palette[5] if len(palette) > 5 else "#ad7fa8",     # Magenta
            "special_var": palette[13] if len(palette) > 13 else "#ff69b4",# Bright magenta
            "operator": palette[3] if len(palette) > 3 else "#fcaf3e",     # Yellow
            "number": palette[11] if len(palette) > 11 else "#f4d03f",     # Bright yellow
            "path": palette[6] if len(palette) > 6 else "#87ceeb",         # Cyan
            "function": palette[13] if len(palette) > 13 else "#dda0dd",   # Bright magenta
            "redirect": palette[1] if len(palette) > 1 else "#fcaf3e",     # Red
            "pipe": palette[6] if len(palette) > 6 else "#fcaf3e",         # Cyan
            "flag": palette[14] if len(palette) > 14 else "#98d8c8",       # Bright cyan
            "escape": palette[3] if len(palette) > 3 else "#deb887",       # Yellow
            "substitution": palette[12] if len(palette) > 12 else "#b8860b",# Bright blue
            "brace": palette[6] if len(palette) > 6 else "#20b2aa",        # Cyan
        }

        # Update existing tags
        self._update_tag_colors()

        # Re-apply highlighting
        if PYGMENTS_AVAILABLE:
            self._apply_highlighting()

    def _on_map(self, widget):
        """Apply highlighting when widget becomes visible."""
        if PYGMENTS_AVAILABLE and self.get_text():
            self._apply_highlighting()

    def _on_buffer_changed(self, buffer):
        """Handle buffer changes for highlighting and auto-resize."""
        # Auto-resize
        if self._auto_resize:
            self._update_size()

        # Syntax highlighting (debounced)
        if PYGMENTS_AVAILABLE:
            self._schedule_highlighting(delay_ms=150)

    def _update_size(self):
        """Update the text view height based on content."""
        text = self.get_text()
        line_count = max(self._min_lines, text.count('\n') + 1)
        line_count = min(line_count, self._max_lines)
        height = line_count * self._line_height + 16  # Add padding
        self.set_size_request(-1, height)

    def _apply_highlighting(self) -> bool:
        """Apply enhanced bash syntax highlighting using Pygments."""
        self._highlight_timeout_id = None

        if not PYGMENTS_AVAILABLE:
            return False

        text = self.get_text()

        if not text:
            return False

        # Remove existing tags
        self._clear_highlighting()

        # Apply highlighting using Pygments tokens
        try:
            lexer = BashLexer()
            for index, token_type, token_value in lexer.get_tokens_unprocessed(text):
                if not token_value:
                    continue

                # Map Pygments token types to our tags
                tag_name = self._map_token_to_tag(token_type, token_value)

                if tag_name:
                    start_iter = self.buffer.get_iter_at_offset(index)
                    end_iter = self.buffer.get_iter_at_offset(index + len(token_value))
                    self.buffer.apply_tag_by_name(tag_name, start_iter, end_iter)

            # Additional pass for paths and flags (not well detected by Pygments)
            self._apply_extra_highlighting(text)
        except Exception:
            pass  # Silently fail highlighting on errors

        return False

    def _map_token_to_tag(self, token_type, token_value: str) -> str | None:
        """Map Pygments token type to our tag name."""
        token_str = str(token_type)

        # More comprehensive token mapping
        if "Keyword" in token_str or "Reserved" in token_str:
            return "keyword"
        elif "Name.Builtin" in token_str:
            return "builtin"
        elif "Name.Function" in token_str:
            return "function"
        elif "Name.Variable" in token_str or "Name.Attribute" in token_str:
            # Check for special variables
            if token_value in ("$?", "$!", "$$", "$@", "$*", "$#", "$0", "$-"):
                return "special_var"
            return "variable"
        elif "String.Escape" in token_str:
            return "escape"
        elif "String.Backtick" in token_str:
            return "backtick"
        elif "String.Single" in token_str:
            return "string_single"
        elif "String.Interpol" in token_str:
            return "substitution"
        elif "String" in token_str:
            return "string"
        elif "Comment" in token_str:
            return "comment"
        elif "Operator" in token_str:
            # Check for specific operators
            if token_value == "|":
                return "pipe"
            elif token_value in (">", ">>", "<", "<<", ">&", "2>", "2>>", "<&", ">&", "&>"):
                return "redirect"
            return "operator"
        elif "Number" in token_str:
            return "number"
        elif "Punctuation" in token_str:
            if token_value == "|":
                return "pipe"
            elif token_value in (";", "&", "&&", "||"):
                return "operator"
            elif token_value in ("{", "}"):
                return "brace"

        return None

    def _apply_extra_highlighting(self, text: str):
        """Apply additional highlighting for paths, flags, and special constructs."""
        # Highlight file paths (absolute and relative)
        for match in _PATH_PATTERN.finditer(text):
            start_offset = match.start(1)
            end_offset = match.end(1)
            start_iter = self.buffer.get_iter_at_offset(start_offset)
            end_iter = self.buffer.get_iter_at_offset(end_offset)
            self.buffer.apply_tag_by_name("path", start_iter, end_iter)

        # Highlight command flags (-x, --option, including with =value)
        for match in _FLAG_PATTERN.finditer(text):
            start_offset = match.start(1)
            end_offset = match.end(1)
            start_iter = self.buffer.get_iter_at_offset(start_offset)
            end_iter = self.buffer.get_iter_at_offset(end_offset)
            self.buffer.apply_tag_by_name("flag", start_iter, end_iter)

        # Highlight special variables ($?, $!, etc.)
        for match in _SPECIAL_VAR_PATTERN.finditer(text):
            start_offset = match.start(1)
            end_offset = match.end(1)
            start_iter = self.buffer.get_iter_at_offset(start_offset)
            end_iter = self.buffer.get_iter_at_offset(end_offset)
            self.buffer.apply_tag_by_name("special_var", start_iter, end_iter)
