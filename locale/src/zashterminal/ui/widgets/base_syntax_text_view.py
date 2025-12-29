# zashterminal/ui/widgets/base_syntax_text_view.py
"""
Base class for syntax highlighting text views.

Provides common functionality for BashTextView and RegexTextView,
including monospace font, margins, and color scheme integration.
"""

from typing import Dict, List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk


class BaseSyntaxTextView(Gtk.TextView):
    """
    Base class for text views with syntax highlighting.
    
    Provides common initialization for:
    - Monospace font
    - Margins and padding
    - Dark/light mode detection
    - Color scheme integration
    - get_text() and set_text() methods
    """

    # Subclasses should override this with their specific colors
    _DEFAULT_DARK_COLORS: Dict[str, str] = {}
    _DEFAULT_LIGHT_COLORS: Dict[str, str] = {}

    def __init__(
        self,
        css_class: str = "syntax-textview",
        wrap_mode: Gtk.WrapMode = Gtk.WrapMode.WORD_CHAR,
        top_margin: int = 4,
        bottom_margin: int = 4,
        left_margin: int = 4,
        right_margin: int = 4,
        accepts_tab: bool = False,
    ):
        """
        Initialize the base syntax text view.
        
        Args:
            css_class: CSS class to add to the widget
            wrap_mode: Text wrapping mode
            top_margin: Top margin in pixels
            bottom_margin: Bottom margin in pixels  
            left_margin: Left margin in pixels
            right_margin: Right margin in pixels
            accepts_tab: Whether Tab key inserts tabs
        """
        super().__init__()

        # Common configuration
        self.set_wrap_mode(wrap_mode)
        self.set_accepts_tab(accepts_tab)
        self.set_monospace(True)

        if css_class:
            self.add_css_class(css_class)

        # Margins
        self.set_top_margin(top_margin)
        self.set_bottom_margin(bottom_margin)
        self.set_left_margin(left_margin)
        self.set_right_margin(right_margin)

        # Buffer reference
        self.buffer = self.get_buffer()

        # Highlighting timeout ID for debouncing
        self._highlight_timeout_id: Optional[int] = None

        # Current color scheme
        self._syntax_colors: Dict[str, str] = {}

    def _is_dark_mode(self) -> bool:
        """Detect if the system is in dark mode."""
        try:
            style_manager = Adw.StyleManager.get_default()
            return style_manager.get_dark()
        except Exception:
            return True  # Default to dark mode if detection fails

    def _get_default_colors(self) -> Dict[str, str]:
        """
        Get default syntax colors based on dark/light mode.
        
        Subclasses should override _DEFAULT_DARK_COLORS and
        _DEFAULT_LIGHT_COLORS class attributes.
        """
        if self._is_dark_mode():
            return self._DEFAULT_DARK_COLORS.copy()
        return self._DEFAULT_LIGHT_COLORS.copy()

    def _setup_tags(self):
        """
        Setup text tags for syntax highlighting.
        
        Should be called by subclass __init__ after setting up colors.
        """
        tag_table = self.buffer.get_tag_table()

        for name, color in self._syntax_colors.items():
            tag = Gtk.TextTag.new(name)
            if isinstance(color, dict):
                # Color can be a dict with 'fg' and optionally 'weight'
                if "fg" in color:
                    tag.set_property("foreground", color["fg"])
                if "weight" in color:
                    tag.set_property("weight", color["weight"])
            else:
                # Color is a simple string
                tag.set_property("foreground", color)
            tag_table.add(tag)

    def _update_tag_colors(self):
        """Update the colors of existing text tags."""
        tag_table = self.buffer.get_tag_table()

        for name, color in self._syntax_colors.items():
            tag = tag_table.lookup(name)
            if tag:
                if isinstance(color, dict):
                    if "fg" in color:
                        tag.set_property("foreground", color["fg"])
                else:
                    tag.set_property("foreground", color)

    def _schedule_highlighting(self, delay_ms: int = 150):
        """
        Schedule syntax highlighting with debounce.
        
        Args:
            delay_ms: Delay in milliseconds before applying highlighting
        """
        if self._highlight_timeout_id:
            GLib.source_remove(self._highlight_timeout_id)
        self._highlight_timeout_id = GLib.timeout_add(
            delay_ms, self._apply_highlighting
        )

    def _apply_highlighting(self) -> bool:
        """
        Apply syntax highlighting to the buffer.
        
        Subclasses must override this method to implement
        their specific highlighting logic.
        
        Returns:
            False to stop the timeout (required by GLib.timeout_add)
        """
        self._highlight_timeout_id = None
        return False

    def _clear_highlighting(self):
        """Remove all highlighting tags from the buffer."""
        start = self.buffer.get_start_iter()
        end = self.buffer.get_end_iter()
        self.buffer.remove_all_tags(start, end)

    def get_text(self) -> str:
        """Get the text content of the buffer."""
        start = self.buffer.get_start_iter()
        end = self.buffer.get_end_iter()
        return self.buffer.get_text(start, end, True)

    def set_text(self, text: str):
        """Set the text content of the buffer."""
        self.buffer.set_text(text)

    def connect_changed(self, callback):
        """Connect a callback to the buffer's 'changed' signal.
        
        Convenience method for connecting to text changes.
        
        Args:
            callback: Function to call when text changes.
                     Signature: callback(widget) where widget is this text view.
        
        Returns:
            Signal handler ID that can be used to disconnect.
        """
        return self.buffer.connect("changed", lambda _: callback(self))

    def update_colors_from_scheme(self, palette: List[str], foreground: str = "#ffffff"):
        """
        Update syntax highlighting colors from a terminal color scheme palette.
        
        The palette is typically 16 colors:
        [0-7]: Normal colors (black, red, green, yellow, blue, magenta, cyan, white)
        [8-15]: Bright colors (bright versions of above)
        
        Subclasses should override this to map palette colors
        to their specific syntax elements.
        
        Args:
            palette: List of hex color strings from the terminal color scheme
            foreground: Foreground color for normal text/comments
        """
        if len(palette) < 8:
            return  # Not enough colors to work with

        # Subclasses should implement the actual color mapping
        # This base implementation just stores the palette for reference
        self._palette = palette
        self._foreground = foreground
