# zashterminal/terminal/highlighter/shell_input.py
"""
Shell input syntax highlighter using Pygments.

This module provides ShellInputHighlighter, which colorizes shell
commands as they are typed in real-time.
"""

import threading
from typing import Dict, List, Optional

from ...utils.logger import get_logger


# Singleton instance
_shell_input_highlighter_instance: Optional["ShellInputHighlighter"] = None
_shell_input_highlighter_lock = threading.Lock()


class ShellInputHighlighter:
    """
    Applies live syntax highlighting to shell commands as they are typed.

    Uses Pygments BashLexer to tokenize and colorize shell input in real-time.
    This works at the terminal level, so it applies to any shell (bash, zsh, etc.)
    even when connecting to remote servers or Docker containers where shell
    configuration cannot be changed.

    The highlighter tracks the current command buffer and applies colors
    when characters are echoed back through the PTY.

    Architecture:
    - Detects when the terminal is at a shell prompt (via OSC7)
    - Tracks typed characters and builds a command buffer
    - When Enter is pressed, the buffer is cleared
    - Each character echo is intercepted and colorized based on its context

    Features:
    - Real-time tokenization using Pygments BashLexer
    - Theme-aware colors using the configured Pygments style
    - Handles backspace, cursor movement, and control characters
    - Properly escapes ANSI sequences in the input
    """

    def __init__(self):
        self.logger = get_logger("zashterminal.terminal.shell_input")
        self._enabled = False
        self._lexer = None
        self._formatter = None
        self._theme = "monokai"

        # Per-proxy state for input tracking
        # Key: proxy_id, Value: current command buffer string
        self._command_buffers: Dict[int, str] = {}

        # Track if we're at a shell prompt (can type commands)
        # Key: proxy_id, Value: True if at prompt
        self._at_prompt: Dict[int, bool] = {}

        # Color palette from terminal color scheme
        self._palette: Optional[List[str]] = None
        self._foreground: str = "#ffffff"

        self._lock = threading.Lock()
        self._refresh_settings()

    def _refresh_settings(self) -> None:
        """Refresh settings from configuration."""
        try:
            from ...settings.manager import get_settings_manager

            settings = get_settings_manager()
            self._enabled = settings.get("shell_input_highlighting_enabled", False)

            # Get theme mode: "auto" or "manual"
            self._theme_mode = settings.get("shell_input_theme_mode", "auto")
            # Legacy theme (used when mode is "manual")
            self._theme = settings.get("shell_input_pygments_theme", "monokai")
            # Themes for auto mode
            self._dark_theme = settings.get("shell_input_dark_theme", "blinds-dark")
            self._light_theme = settings.get("shell_input_light_theme", "blinds-light")

            # Get terminal color scheme for background detection
            gtk_theme = settings.get("gtk_theme", "")
            if gtk_theme == "terminal":
                scheme = settings.get_color_scheme_data()
                self._palette = scheme.get("palette", [])
                self._foreground = scheme.get("foreground", "#ffffff")
                self._background = scheme.get("background", "#000000")
            else:
                self._palette = None
                self._foreground = "#ffffff"
                self._background = "#000000"

            if self._enabled:
                self._init_lexer()
                self.logger.info("Shell input highlighting enabled")
            else:
                self._lexer = None
                self._formatter = None
        except Exception as e:
            self.logger.warning(f"Failed to refresh shell input settings: {e}")
            self._enabled = False

    def _init_lexer(self) -> None:
        """Initialize Pygments lexer and formatter."""
        try:
            from pygments.lexers import BashLexer
            from pygments.formatters import Terminal256Formatter
            from pygments.styles import get_style_by_name
            from pygments.util import ClassNotFound

            self._lexer = BashLexer()

            # Determine which theme to use based on mode
            if self._theme_mode == "auto":
                # Auto mode: select theme based on background luminance
                is_light_bg = self._is_light_color(self._background)
                selected_theme = self._light_theme if is_light_bg else self._dark_theme
                self.logger.debug(
                    f"Auto mode: bg={self._background}, light={is_light_bg}, "
                    f"using theme={selected_theme}"
                )
            else:
                # Manual mode: use the legacy single theme setting
                selected_theme = self._theme

            # Create formatter with selected theme
            try:
                style = get_style_by_name(selected_theme)
            except ClassNotFound:
                # Fallback to monokai if theme not found
                style = get_style_by_name("monokai")
                self.logger.warning(
                    f"Theme '{selected_theme}' not found, falling back to monokai"
                )

            self._formatter = Terminal256Formatter(style=style)

            self.logger.debug(
                f"Shell input highlighter initialized with theme: {selected_theme}"
            )
        except ImportError as e:
            self.logger.warning(
                f"Pygments not available for shell input highlighting: {e}"
            )
            self._enabled = False
            self._lexer = None
            self._formatter = None

    def _is_light_color(self, hex_color: str) -> bool:
        """Determine if a color is light based on its luminance."""
        try:
            hex_val = hex_color.lstrip("#")
            r = int(hex_val[0:2], 16) / 255
            g = int(hex_val[2:4], 16) / 255
            b = int(hex_val[4:6], 16) / 255

            # Calculate relative luminance (simplified)
            luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
            return luminance > 0.5
        except (ValueError, IndexError):
            return False

    def refresh_settings(self) -> None:
        """Public method to refresh settings (called when settings change)."""
        with self._lock:
            self._refresh_settings()

    @property
    def enabled(self) -> bool:
        """Check if shell input highlighting is enabled.
        
        Always reads from settings manager to ensure changes take effect immediately.
        """
        try:
            from ...settings.manager import get_settings_manager
            settings = get_settings_manager()
            is_enabled = settings.get("shell_input_highlighting_enabled", False)
            # Also require lexer to be initialized
            return is_enabled and self._lexer is not None
        except Exception:
            return False

    def register_proxy(self, proxy_id: int) -> None:
        """Register a proxy for input tracking."""
        with self._lock:
            self._command_buffers[proxy_id] = ""
            # Start with True since terminal starts at shell prompt
            self._at_prompt[proxy_id] = True

    def unregister_proxy(self, proxy_id: int) -> None:
        """Unregister a proxy."""
        with self._lock:
            self._command_buffers.pop(proxy_id, None)
            self._at_prompt.pop(proxy_id, None)

    def set_at_prompt(self, proxy_id: int, at_prompt: bool) -> None:
        """
        Set whether the terminal is at a shell prompt.

        When at a prompt, typed characters are part of a command and will
        be highlighted. When not at a prompt (e.g., running a command),
        highlighting is disabled.
        """
        with self._lock:
            old_state = self._at_prompt.get(proxy_id, False)
            self._at_prompt[proxy_id] = at_prompt

            # Clear buffer when transitioning to prompt or away from it
            if old_state != at_prompt:
                self._command_buffers[proxy_id] = ""
                if at_prompt:
                    self.logger.debug(
                        f"Proxy {proxy_id}: At shell prompt, input highlighting active"
                    )

    def is_at_prompt(self, proxy_id: int) -> bool:
        """Check if terminal is at a shell prompt."""
        with self._lock:
            return self._at_prompt.get(proxy_id, False)

    def on_key_pressed(self, proxy_id: int, char: str, keyval: int) -> None:
        """
        Handle a key press event to update the command buffer.

        Called by the terminal when a printable character is typed.
        Special keys (backspace, delete, arrows) are handled separately.
        """
        if not self.enabled:
            return

        with self._lock:
            if not self._at_prompt.get(proxy_id, False):
                return

            buffer = self._command_buffers.get(proxy_id, "")

            # Handle control characters
            if keyval == 65288:  # GDK_KEY_BackSpace
                self._command_buffers[proxy_id] = buffer[:-1] if buffer else ""
            elif keyval in (65293, 65421):  # GDK_KEY_Return, GDK_KEY_KP_Enter
                # Clear buffer on Enter (command submitted)
                self._command_buffers[proxy_id] = ""
                self._at_prompt[proxy_id] = False  # No longer at prompt
            elif keyval == 65507 or keyval == 65508:  # Ctrl keys (Ctrl+C, etc.)
                # Clear buffer on Ctrl+C
                if char == "\x03":  # Ctrl+C
                    self._command_buffers[proxy_id] = ""
            elif len(char) == 1 and char.isprintable():
                # Regular printable character
                self._command_buffers[proxy_id] = buffer + char

    def clear_buffer(self, proxy_id: int) -> None:
        """Clear the command buffer for a proxy."""
        with self._lock:
            self._command_buffers[proxy_id] = ""

    def get_highlighted_char(self, proxy_id: int, char: str) -> str:
        """
        Get the highlighted version of a character being echoed.

        This is called when a character is echoed back from the PTY.
        It returns the character with appropriate ANSI color codes
        based on its context in the current command buffer.

        Args:
            proxy_id: The proxy ID
            char: The character being echoed

        Returns:
            The character with ANSI color codes, or the plain character
            if highlighting is disabled or not applicable.
        """
        if not self.enabled:
            return char

        with self._lock:
            if not self._at_prompt.get(proxy_id, False):
                return char

            buffer = self._command_buffers.get(proxy_id, "")
            if not buffer:
                return char

            # Find position of this char in the buffer
            # This is a simplified approach - we highlight based on the full buffer
            return self._highlight_buffer_char(buffer, char)

    def _highlight_buffer_char(self, buffer: str, char: str) -> str:
        """
        Get the color for a character based on its position in the buffer.

        Tokenizes the full buffer and finds the token containing the last
        character to determine its color.
        """
        if not self._lexer or not self._formatter:
            return char

        try:
            from pygments import highlight

            # Highlight the full buffer
            highlighted = highlight(buffer, self._lexer, self._formatter)

            # For single char, just return it with color from the end of buffer
            # This is a simplified approach that colors the whole buffer
            if len(buffer) == 1:
                return highlighted.rstrip("\n")

            # Return just the character (the actual coloring happens in
            # highlight_input_line for full line redraw)
            return char

        except Exception:
            return char

    def highlight_input_line(self, proxy_id: int, line: str) -> str:
        """
        Highlight a full input line.

        This is used when redrawing the input line (e.g., after cursor
        movement or completion).

        Args:
            proxy_id: The proxy ID
            line: The command line text to highlight

        Returns:
            The line with ANSI color codes applied
        """
        if not self.enabled or not self._lexer or not self._formatter:
            return line

        with self._lock:
            if not self._at_prompt.get(proxy_id, False):
                return line

        try:
            from pygments import highlight

            highlighted = highlight(line, self._lexer, self._formatter)
            return highlighted.rstrip("\n")
        except Exception:
            return line

    def get_current_buffer(self, proxy_id: int) -> str:
        """Get the current command buffer for a proxy."""
        with self._lock:
            return self._command_buffers.get(proxy_id, "")


def get_shell_input_highlighter() -> ShellInputHighlighter:
    """Get the global ShellInputHighlighter singleton instance."""
    global _shell_input_highlighter_instance
    if _shell_input_highlighter_instance is None:
        with _shell_input_highlighter_lock:
            if _shell_input_highlighter_instance is None:
                _shell_input_highlighter_instance = ShellInputHighlighter()
    return _shell_input_highlighter_instance


__all__ = ["ShellInputHighlighter", "get_shell_input_highlighter"]
