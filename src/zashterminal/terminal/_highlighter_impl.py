# zashterminal/terminal/_highlighter_impl.py
"""
Terminal output highlighter that applies ANSI color codes based on regex patterns.

Features:
- Multi-group regex: Different capture groups can have different colors
- Theme-aware: Uses logical color names resolved via active theme palette
- Context-aware: Applies command-specific rules based on foreground process
- High-performance: Uses PCRE2 backend with smart pre-filtering
- Cat/file highlighting: Syntax highlighting for file output using Pygments
- Help output highlighting: Colorizes --help and man page output
- Shell input highlighting: Live syntax coloring of typed commands using Pygments

Performance Architecture:
- Per-rule iteration with compiled patterns (faster than megex for <50 rules)
- Fast pre-filtering skips regex when line cannot possibly match
- PCRE2 backend (regex module) for ~50% faster matching
- Early termination on "stop" action rules
"""

import fcntl
import os
import pty
import signal
import struct
import termios
import threading
import weakref
from collections import deque
from typing import TYPE_CHECKING, Dict, Optional, Tuple

import gi

gi.require_version("Vte", "3.91")
gi.require_version("GLib", "2.0")
# Use regex module (PCRE2 backend) for ~50% faster matching
import regex as re_engine
from gi.repository import GLib, Vte

from ..utils.logger import get_logger
from ..utils.shell_echo import is_echo_terminator
from .highlighter.constants import (
    ALL_ESCAPE_SEQ_PATTERN as _ALL_ESCAPE_SEQ_PATTERN,
)
from .highlighter.constants import (
    ANSI_COLOR_PATTERN as _ANSI_COLOR_PATTERN,
)

# Import constants and rules from highlighter package
from .highlighter.constants import (
    ANSI_SEQ_PATTERN as _ANSI_SEQ_PATTERN,
)
from .highlighter.constants import (
    CSI_CONTROL_PATTERN as _CSI_CONTROL_PATTERN,
)
from .highlighter.constants import (
    SGR_RESET_LINE_PATTERN as _SGR_RESET_LINE_PATTERN,
)
from .highlighter.constants import (
    SHELL_NAME_PROMPT_PATTERN as _SHELL_NAME_PROMPT_PATTERN,
)

if TYPE_CHECKING:
    pass

# Import OutputHighlighter from its own module
from .highlighter.output import OutputHighlighter, get_output_highlighter

# Import ShellInputHighlighter from its own module
from .highlighter.shell_input import ShellInputHighlighter, get_shell_input_highlighter

# Sentinel marker for prompt detection in CAT queue
_PROMPT_MARKER = b"__PROMPT_DETECTED__"


class HighlightedTerminalProxy:
    """
    A proxy that intercepts terminal output and applies syntax highlighting.
    Robust against Local Terminal race conditions.

    Supports context-aware highlighting via the highlighter property.
    Also supports Pygments integration for cat/file and help output highlighting.
    """

    # Class-level counter for unique proxy IDs (fallback if not provided)
    _next_proxy_id = 1
    _id_lock = threading.Lock()

    def __init__(
        self,
        terminal: Vte.Terminal,
        terminal_type: str = "local",
        proxy_id: Optional[int] = None,
    ):
        """
        Initialize a highlighted terminal proxy.
        """
        self.logger = get_logger("zashterminal.terminal.proxy")

        if proxy_id is not None:
            self._proxy_id = proxy_id
        else:
            with HighlightedTerminalProxy._id_lock:
                self._proxy_id = HighlightedTerminalProxy._next_proxy_id
                HighlightedTerminalProxy._next_proxy_id += 1
            self.logger.warning(
                f"HighlightedTerminalProxy created without explicit proxy_id, "
                f"auto-generated ID {self._proxy_id}. This may cause context detection issues."
            )

        self._terminal_ref = weakref.ref(terminal)
        self._terminal_type = terminal_type
        self._highlighter = get_output_highlighter()
        self._shell_input_highlighter = get_shell_input_highlighter()

        self._highlighter.register_proxy(self._proxy_id)
        self._shell_input_highlighter.register_proxy(self._proxy_id)

        self._master_fd: Optional[int] = None
        self._slave_fd: Optional[int] = None
        self._io_watch_id: Optional[int] = None

        self._destroy_handler_id: Optional[int] = None

        self._running = False
        self._widget_destroyed = False

        self._lock = threading.Lock()
        self._is_alt_screen = False
        self._child_pid: Optional[int] = None

        self._sequence_counter = 0
        self._pending_outputs: Dict[int, bytes] = {}
        self._next_sequence_to_feed = 0
        self._output_lock = threading.Lock()

        self._line_queue: deque = deque()
        self._queue_processing = False

        # Buffer for partial lines
        self._partial_line_buffer: bytes = b""

        # Burst detection counter
        # Tracks consecutive large chunks to detect file dumps vs commands
        self._burst_counter = 0

        # Bracketed Paste State
        self._in_bracketed_paste = False

        # Pygments state for cat command highlighting
        self._cat_filename: Optional[str] = None
        self._cat_bytes_processed: int = 0
        self._cat_limit_reached: bool = False
        self._cat_waiting_for_newline: bool = False

        self._input_highlight_buffer = ""
        # Start as False; will be set True when shell prompt is detected via termprop
        self._at_shell_prompt = False
        self._need_color_reset = False
        # When True, do not apply per-character shell input highlighting.
        # This is used to avoid interfering with readline redisplay/cursor movement
        # after paste or navigation keys, which can cause visible artifacts.
        self._suppress_shell_input_highlighting = False
        # Track previous token type for retroactive recoloring
        self._prev_shell_input_token_type = None
        self._prev_shell_input_token_len = 0

        if terminal:
            self._destroy_handler_id = terminal.connect(
                "destroy", self._on_widget_destroy
            )
            # Use VTE's native shell integration for prompt detection
            self._termprop_handler_id = terminal.connect(
                "termprop-changed", self._on_termprop_changed
            )

    def _on_termprop_changed(self, terminal: Vte.Terminal, prop: str) -> None:
        """Handle VTE termprop changes for shell integration."""
        if prop == Vte.TERMPROP_SHELL_PRECMD:
            # Shell is about to display prompt - command finished
            if not self._at_shell_prompt:
                self._at_shell_prompt = True
                self._shell_input_highlighter.set_at_prompt(self._proxy_id, True)
            self._reset_input_buffer()
            self._need_color_reset = True
            self._suppress_shell_input_highlighting = False
            # Don't clear cat context immediately - wait for content to finish
            # The context will be cleared when prompt is detected in _process_cat_output
            context = self._highlighter._proxy_contexts.get(self._proxy_id, "")
            if context.lower() != "cat":
                self._highlighter.clear_context(self._proxy_id)
                self._reset_cat_state()
        elif prop == Vte.TERMPROP_SHELL_PREEXEC:
            # Shell is about to execute command
            if self._at_shell_prompt:
                self._at_shell_prompt = False
                self._shell_input_highlighter.set_at_prompt(self._proxy_id, False)
            self._reset_input_buffer()
        elif prop == Vte.TERMPROP_SHELL_POSTEXEC:
            # Command finished executing - reset highlighting state
            # Don't clear cat context immediately - content may still be arriving
            context = self._highlighter._proxy_contexts.get(self._proxy_id, "")
            if context.lower() != "cat":
                self._highlighter.clear_context(self._proxy_id)
                self._reset_cat_state()
            self._reset_input_buffer()
        elif prop == Vte.TERMPROP_CURRENT_DIRECTORY_URI:
            # OSC7 - directory change signals shell ready (fallback for shells without precmd)
            if not self._at_shell_prompt:
                self._at_shell_prompt = True
                self._shell_input_highlighter.set_at_prompt(self._proxy_id, True)
            self._reset_input_buffer()

    def _has_incomplete_escape(self, data: bytes) -> bool:
        """Check if data ends with an incomplete escape sequence."""
        last_esc = data.rfind(b"\x1b")
        if last_esc == -1:
            return False

        data_len = len(data)
        pos = last_esc + 1
        if pos >= data_len:
            return True  # ESC at end with nothing after

        second = data[pos]
        if second == 0x5B:  # '[' - CSI
            for i in range(pos + 1, data_len):
                if 0x40 <= data[i] <= 0x7E:
                    return False  # Found terminator
            return True  # No terminator found
        elif second == 0x5D:  # ']' - OSC
            for i in range(pos + 1, data_len):
                if data[i] == 0x07 or (
                    data[i] == 0x1B and i + 1 < data_len and data[i + 1] == 0x5C
                ):
                    return False  # Found BEL or ST
            return True
        elif second in (0x28, 0x29):  # G0/G1 charset
            return pos + 1 >= data_len
        return False  # Simple escape, complete

    def _is_in_unclosed_multiline_block(self, buffer: str) -> bool:
        """
        Check if the buffer contains an unclosed multi-line block.

        Returns True if we detect:
        - if/then without fi
        - for/do without done
        - while/do without done
        - unclosed braces
        - line ending with continuation indicators (|, &&, ||, \\) - NOT 'then'/'do' if closed
        """
        if not buffer:
            return False

        buffer_stripped = buffer.strip()
        if not buffer_stripped:
            return False

        # Check for unclosed if/then block
        has_then = (
            " then" in buffer_stripped
            or buffer_stripped.startswith("then")
            or "\nthen" in buffer_stripped
        )
        has_fi = (
            " fi" in buffer_stripped
            or buffer_stripped.endswith("fi")
            or "\nfi" in buffer_stripped
        )

        # Check for unclosed for/while do block
        has_do = " do" in buffer_stripped or buffer_stripped.startswith("do") or "\ndo" in buffer_stripped
        has_done = " done" in buffer_stripped or buffer_stripped.endswith("done") or "\ndone" in buffer_stripped

        # Check structural completeness of blocks
        then_block_open = has_then and not has_fi
        do_block_open = has_do and not has_done

        if then_block_open or do_block_open:
            return True

        # Check for unclosed braces
        if buffer_stripped.count("{") > buffer_stripped.count("}"):
            return True

        # Check if LAST line ends with a continuation indicator
        # Only check operators that always indicate continuation, not 'then'/'do'
        # because those could be part of a closed block checked above
        lines = buffer_stripped.split("\n")
        if lines:
            last_line = lines[-1].strip()
            # These operators always indicate continuation to the next line
            operator_continuations = ("|", "&&", "||", "\\", "{")
            if last_line and last_line.endswith(operator_continuations):
                return True
            # Check 'then'/'do'/'else' only if the corresponding block is not closed
            if last_line.endswith("then") and then_block_open:
                return True
            if last_line.endswith("do") and do_block_open:
                return True
            if last_line.endswith("else"):
                # 'else' always needs more content
                return True

        return False

    def _detect_interactive_marker(self, data: bytes) -> tuple[bool, bool, bool]:
        """
        Detect interactive marker in data (NUL prefix from PTY).

        Returns: (has_marker, is_user_input, is_newline)
        """
        data_len = len(data)
        if data_len < 2 or data[0] != 0x00:
            return (False, False, False)

        next_byte = data[1]
        # Check for newline - can be longer due to escape sequences like bracketed paste mode
        # The data often contains sequences like \x00\r\n\x1b[?2004l\r\x1b[?2004h>
        if b"\r\n" in data or (b"\r" in data and data_len <= 3):
            return (True, False, True)  # Newline marker
        elif next_byte in (0x08, 0x7F):
            return (True, True, False)  # Backspace
        elif data_len <= 3 and 0x20 <= next_byte <= 0x7E:
            return (True, True, False)  # Printable char
        return (False, False, False)

    def _handle_prompt_split(self, content: str, prompt: str, add_newline: bool = True) -> None:
        """
        Handle splitting of content that contains an embedded prompt.
        Highlights content and queues prompt marker.
        """
        clean = _CSI_CONTROL_PATTERN.sub('', content)
        clean = _SGR_RESET_LINE_PATTERN.sub('', clean).strip()
        if clean:
            highlighted = self._highlight_line_with_pygments(clean, self._cat_filename)
            self._cat_queue.append(highlighted.encode("utf-8", errors="replace"))
            if add_newline:
                self._cat_queue.append(b"\r\n")
        self._cat_queue.append(_PROMPT_MARKER)
        self._cat_queue.append(prompt.encode("utf-8", errors="replace"))

    def _handle_backspace_in_buffer(self, data: bytes) -> int:
        """
        Handle backspace characters in input data by updating the highlight buffer.
        
        Counts backspace characters (\x08 and \x7f) and removes that many characters
        from the input highlight buffer. Also handles shell-style \x08 \x08 patterns.
        
        Args:
            data: The byte data that may contain backspace characters.
            
        Returns:
            The number of characters actually removed from the buffer.
        """
        if not self._input_highlight_buffer:
            return 0

        # Count backspaces - handle \x08 \x08 patterns (sh/dash style)
        temp_data = data
        backspace_count = 0

        # Count \x08 \x08 patterns first (count as 1 each)
        while b"\x08 \x08" in temp_data:
            backspace_count += 1
            temp_data = temp_data.replace(b"\x08 \x08", b"", 1)

        # Count remaining individual backspaces
        backspace_count += temp_data.count(b"\x7f") + temp_data.count(b"\x08")

        if backspace_count > 0:
            chars_to_remove = min(backspace_count, len(self._input_highlight_buffer))
            if chars_to_remove > 0:
                self._input_highlight_buffer = self._input_highlight_buffer[:-chars_to_remove]
            # Reset token tracking after backspace
            self._prev_shell_input_token_type = None
            self._prev_shell_input_token_len = 0
            return chars_to_remove

        return 0

    @property
    def proxy_id(self) -> int:
        """Get the unique proxy ID for this instance."""
        return self._proxy_id

    @property
    def highlighter(self) -> OutputHighlighter:
        """Get the highlighter instance for context management."""
        return self._highlighter

    @property
    def shell_input_highlighter(self) -> ShellInputHighlighter:
        """Get the shell input highlighter instance."""
        return self._shell_input_highlighter

    @property
    def child_pid(self) -> Optional[int]:
        """Get the child process ID (shell PID)."""
        return self._child_pid

    @property
    def slave_fd(self) -> Optional[int]:
        """Get the slave file descriptor (for process detection)."""
        return self._slave_fd

    @property
    def _terminal(self) -> Optional[Vte.Terminal]:
        if self._widget_destroyed:
            return None
        return self._terminal_ref()

    def _on_widget_destroy(self, widget):
        """Called immediately when the GTK widget is being destroyed."""
        # Mark as destroyed IMMEDIATELY so no other thread tries to access it
        self._widget_destroyed = True
        self._running = False
        # We do NOT call stop() logic that touches the widget here.
        # We only clean up our Python-side IO watches.
        self._cleanup_io_watch()

    def create_pty(self) -> Tuple[int, int]:
        master_fd, slave_fd = pty.openpty()
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        self._master_fd = master_fd
        self._slave_fd = slave_fd
        self._setup_pty_attrs(slave_fd)

        return master_fd, slave_fd

    def _setup_pty_attrs(self, slave_fd: int) -> None:
        try:
            attrs = termios.tcgetattr(slave_fd)
            attrs[0] |= termios.ICRNL
            if hasattr(termios, "IUTF8"):
                attrs[0] |= termios.IUTF8
            attrs[1] |= termios.OPOST | termios.ONLCR
            attrs[3] |= (
                termios.ISIG
                | termios.ICANON
                | termios.ECHO
                | termios.ECHOE
                | termios.ECHOK
                | termios.IEXTEN
            )
            termios.tcsetattr(slave_fd, termios.TCSANOW, attrs)
        except Exception:
            pass

    def set_window_size(self, rows: int, cols: int) -> None:
        # If destroyed, do nothing.
        if rows <= 0 or cols <= 0 or self._master_fd is None or self._widget_destroyed:
            return

        try:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, winsize)
            if self._child_pid:
                os.kill(self._child_pid, signal.SIGWINCH)
        except (OSError, ProcessLookupError, Exception):
            pass

    def start(self, child_pid: int) -> bool:
        if self._running or self._widget_destroyed:
            return False

        if self._master_fd is None:
            return False

        term = self._terminal
        if term is None:
            return False

        self._child_pid = child_pid

        if self._slave_fd is not None:
            try:
                os.close(self._slave_fd)
            except OSError:
                pass
            self._slave_fd = None

        try:
            # VTE takes ownership of FD here.
            vte_pty = Vte.Pty.new_foreign_sync(self._master_fd)

            if vte_pty:
                term.set_pty(vte_pty)
                # CRITICAL: Call watch_child() to make VTE track the child process.
                # This ensures the 'child-exited' signal is emitted when the process
                # terminates (e.g., SSH connection failure, timeout, or normal exit).
                # Without this, VTE won't know about the process and won't emit signals.
                term.watch_child(child_pid)
            else:
                return False

            # Reset sequence counters
            self._sequence_counter = 0
            self._pending_outputs = {}
            self._next_sequence_to_feed = 0

            self._io_watch_id = GLib.io_add_watch(
                self._master_fd,
                GLib.PRIORITY_DEFAULT,
                GLib.IOCondition.IN | GLib.IOCondition.HUP | GLib.IOCondition.ERR,
                self._on_pty_readable,
            )

            # Note: We deliberately do NOT connect to notify::columns/rows
            # or manually send SIGWINCH. The VTE automatically propagates
            # terminal size changes to the PTY when using new_foreign_sync.
            # Adding our own resize handling would cause duplicate SIGWINCH
            # signals, leading to prompt duplication with ble.sh.

            self._running = True
            return True

        except Exception as e:
            self.logger.error(f"Failed to start highlight proxy: {e}")
            # Clean up FDs that weren't transferred to VTE on failure
            if self._master_fd is not None:
                try:
                    os.close(self._master_fd)
                except OSError:
                    pass
                self._master_fd = None
            if self._slave_fd is not None:
                try:
                    os.close(self._slave_fd)
                except OSError:
                    pass
                self._slave_fd = None
            self.stop()
            return False

    def _cleanup_io_watch(self):
        """Helper to safely remove the GLib IO watch."""
        with self._lock:
            if self._io_watch_id is not None:
                try:
                    GLib.source_remove(self._io_watch_id)
                except Exception:
                    pass
                self._io_watch_id = None

    def stop(self, from_destroy: bool = False) -> None:
        """
        Stops the proxy.
        """
        self._running = False

        self._cleanup_io_watch()

        with self._output_lock:
            self._pending_outputs.clear()
        self._line_queue.clear()
        self._queue_processing = False
        self._partial_line_buffer = b""
        self._burst_counter = 0
        self._in_bracketed_paste = False

        self._cat_filename = None
        self._input_highlight_buffer = ""
        self._at_shell_prompt = False
        self._suppress_shell_input_highlighting = False
        self._prev_shell_input_token_type = None
        self._prev_shell_input_token_len = 0

        self._highlighter.unregister_proxy(self._proxy_id)
        self._shell_input_highlighter.unregister_proxy(self._proxy_id)

        if from_destroy or self._widget_destroyed:
            self._terminal_ref = None
            return

        with self._lock:
            # Reference terminal to ensure it's not garbage collected during cleanup
            _ = self._terminal_ref()

            self._master_fd = None
            if self._slave_fd is not None:
                try:
                    os.close(self._slave_fd)
                except OSError:
                    pass
                self._slave_fd = None

            self._terminal_ref = None

    def _update_alt_screen_state(self, data: bytes) -> bool:
        """
        Check for Alternate Screen buffer switches.
        Returns True if state changed.
        """
        # Common sequences for entering/exiting alt screen (vim, fzf, htop, etc)
        # \x1b[?1049h : Enable Alt Screen
        # \x1b[?1049l : Disable Alt Screen
        # \x1b[?47h   : Enable Alt Screen (Legacy)
        # \x1b[?47l   : Disable Alt Screen (Legacy)

        changed = False

        # Check for enable patterns
        if b"\x1b[?1049h" in data or b"\x1b[?47h" in data or b"\x1b[?1047h" in data:
            if not self._is_alt_screen:
                self._is_alt_screen = True
                changed = True

        # Check for disable patterns
        # Note: We check disable AFTER enable in case both are in the same chunk (rare but possible)
        if b"\x1b[?1049l" in data or b"\x1b[?47l" in data or b"\x1b[?1047l" in data:
            if self._is_alt_screen:
                self._is_alt_screen = False
                changed = True

        return changed

    def _on_pty_readable(self, fd: int, condition: GLib.IOCondition) -> bool:
        # 1. Fail fast if stopped or destroyed
        if not self._running or self._widget_destroyed:
            self._io_watch_id = None
            return False

        # 2. Check errors BEFORE trying to read
        if condition & (GLib.IOCondition.HUP | GLib.IOCondition.ERR):
            self._io_watch_id = None
            return False

        try:
            # 3. Try read - use 4KB buffer
            data = os.read(fd, 4096)
            if not data:
                return True  # Empty read, keep waiting

            # 4. Verify widget is alive before feeding
            term = self._terminal
            if term is None:
                self._io_watch_id = None
                return False

            # Safety check: Verify widget is still valid and realized
            # This is especially important on XFCE/X11 where widget destruction
            # timing can differ from Wayland compositors
            try:
                # Double-check the widget is not being destroyed
                if self._widget_destroyed:
                    self._io_watch_id = None
                    return False
                # Check if widget is realized (mapped to screen)
                if not term.get_realized():
                    return False
                # Also verify the widget has a parent (not orphaned)
                if term.get_parent() is None:
                    self._widget_destroyed = True
                    self._io_watch_id = None
                    return False
            except Exception:
                self._widget_destroyed = True
                self._io_watch_id = None
                return False

            # --- GLOBAL SPLIT ESCAPE FIX ---
            # Combine with any partial data from previous read
            if self._partial_line_buffer:
                data = self._partial_line_buffer + data
                self._partial_line_buffer = b""

            data_len = len(data)

            # Check for incomplete escape sequence and buffer if needed
            if self._has_incomplete_escape(data):
                self._partial_line_buffer = data
                return True  # Wait for next chunk

            # Processing logic - skip alt screen check on small packets
            if data_len > 10:
                self._update_alt_screen_state(data)

            try:
                if self._is_alt_screen:
                    term.feed(data)
                else:
                    # Check if any highlighting feature is enabled
                    from ..settings.manager import get_settings_manager

                    settings = get_settings_manager()

                    # First check if output highlighting is enabled at all
                    # (Local Terminals or SSH Sessions must be enabled)
                    output_highlighting_enabled = self._highlighter.is_enabled_for_type(
                        self._terminal_type
                    )

                    # Cat colorization and shell input highlighting only work
                    # when output highlighting is enabled (as shown in UI)
                    cat_colorization_enabled = (
                        output_highlighting_enabled
                        and settings.get("cat_colorization_enabled", True)
                    )
                    shell_input_enabled = (
                        output_highlighting_enabled
                        and self._shell_input_highlighter.enabled
                    )

                    any_highlighting_enabled = (
                        output_highlighting_enabled
                        or cat_colorization_enabled
                        or shell_input_enabled
                    )

                    if not any_highlighting_enabled:
                        # No highlighting features enabled - feed raw data
                        term.feed(data)
                    else:
                        # Get context with lock
                        with self._highlighter._lock:
                            context = self._highlighter._proxy_contexts.get(
                                self._proxy_id, ""
                            )
                            is_ignored = (
                                context
                                and context.lower()
                                in self._highlighter._ignored_commands
                            )

                        # Check for cat syntax highlighting
                        is_cat_context = context and context.lower() == "cat"

                        if is_cat_context and cat_colorization_enabled:
                            # Skip cat processing for small single-character interactive input
                            # This happens when cat is waiting for stdin and user types
                            is_interactive_input = (
                                data_len <= 3
                                and b"\n" not in data
                                and b"\r" not in data
                                and not data.startswith(b"\x1b")
                            )
                            if is_interactive_input:
                                term.feed(data)
                            else:
                                self._process_cat_output(data, term)
                        elif is_ignored:
                            # PERFORMANCE FIX FOR IGNORED COMMANDS
                            # Only apply shell input highlighting if enabled
                            if shell_input_enabled and data_len < 1024:
                                text = data.decode("utf-8", errors="replace")
                                self._check_and_update_prompt_state(text)

                                if self._at_shell_prompt:
                                    highlighted = self._apply_shell_input_highlighting(
                                        text, term
                                    )
                                    if highlighted is not None:
                                        return True

                            term.feed(data)
                        elif (
                            not context
                            and self._at_shell_prompt
                            and shell_input_enabled
                        ):
                            # NO CONTEXT (at shell prompt before first command)
                            # Only when shell input highlighting is enabled
                            if data_len < 1024:
                                text = data.decode("utf-8", errors="replace")
                                self._check_and_update_prompt_state(text)

                                if self._at_shell_prompt:
                                    highlighted = self._apply_shell_input_highlighting(
                                        text, term
                                    )
                                    if highlighted is not None:
                                        return True

                            term.feed(data)
                        elif output_highlighting_enabled:
                            # Output highlighting is enabled - stream data with highlighting
                            self._process_data_streaming(data, term)
                        else:
                            # No applicable highlighting feature is enabled
                            # Feed raw data directly
                            term.feed(data)
            except Exception:
                self._widget_destroyed = True
                self._io_watch_id = None
                return False

            return True

        except OSError:
            self._io_watch_id = None
            return False
        except Exception as e:
            self.logger.error(f"PTY read error: {e}")
            return True

    def _process_cat_output(self, data: bytes, term: Vte.Terminal) -> None:
        """
        Process cat output through Pygments for syntax highlighting.
        Includes safety limit, Strict Queue Ordering, Partial Buffer Flushing,
        and Robust Echo Skipping.
        """
        # Early check: if cat colorization is disabled, bypass processing
        # Cat colorization also depends on output highlighting being enabled
        from ..settings.manager import get_settings_manager

        settings = get_settings_manager()
        output_highlighting_enabled = self._highlighter.is_enabled_for_type(
            self._terminal_type
        )
        cat_colorization_enabled = output_highlighting_enabled and settings.get(
            "cat_colorization_enabled", True
        )
        if not cat_colorization_enabled:
            term.feed(data)
            return

        # 1. Flush standard line queue to ensure command echo order
        self._flush_queue(term)

        # 2. Clear any leftover remainder from streaming mode.
        # At prompt boundaries this buffer may contain stale readline fragments;
        # feeding it here can duplicate characters on the next echo.
        self._partial_line_buffer = b""

        # Constante de limite: 1MB
        CAT_HIGHLIGHT_LIMIT = 1048576

        try:
            data_len = len(data)

            # --- SAFETY LIMIT CHECK ---
            if self._cat_limit_reached or (
                self._cat_bytes_processed + data_len > CAT_HIGHLIGHT_LIMIT
            ):
                if not self._cat_limit_reached:
                    self._cat_limit_reached = True

                term.feed(data)

                # Check if shell prompt (via termprops or OSC7 fallback)
                if self._at_shell_prompt or b"\x1b]7;" in data or b"\033]7;" in data:
                    self._highlighter.clear_context(self._proxy_id)
                    self._reset_cat_state()
                    self._reset_input_buffer()
                return

            self._cat_bytes_processed += data_len

            # --- NORMAL PROCESSING ---
            text = data.decode("utf-8", errors="replace")
            # FIX: Remove NULL bytes which can cause display issues
            text = text.replace("\x00", "")

            if not text:
                term.feed(data)
                return

            # Check for shell input control sequences (backspace/edits)
            if text in ("\x08\x1b[K", "\x08 \x08") or (
                text.startswith("\x08") and len(text) <= 5
            ):
                self._highlighter.clear_context(self._proxy_id)
                self._reset_cat_state()
                self._reset_input_buffer()
                term.feed(data)
                return

            # Get filename from full command or try TERMPROP_CURRENT_FILE_URI
            full_command = self._highlighter.get_full_command(self._proxy_id)
            new_filename = self._extract_filename_from_cat_command(full_command) or ""

            # Debug log for cat filename detection
            if not new_filename and full_command:
                self.logger.debug(
                    f"Cat: full_command='{full_command}' but no filename extracted"
                )
            elif new_filename:
                self.logger.debug(
                    f"Cat: filename='{new_filename}' from command='{full_command}'"
                )

            if new_filename != self._cat_filename:
                self._cat_filename = new_filename
                self._pygments_lexer = None
                self._content_buffer = []
                self._cat_lines_processed = 0
                self._pending_lines = []
                self._php_in_multiline_comment = False

                import os.path

                _, ext = os.path.splitext(new_filename)
                self._pygments_needs_content_detection = not ext and new_filename

            if not hasattr(self, "_cat_queue"):
                from collections import deque

                self._cat_queue = deque()
                self._cat_queue_processing = False

            # Check if we should start skipping the echo
            skip_check = self._highlighter.should_skip_first_output(self._proxy_id)
            if skip_check:
                self._cat_waiting_for_newline = True

            lines = text.splitlines(keepends=True)

            for i, line in enumerate(lines):
                # --- ECHO SKIPPING LOGIC ---
                # If we are waiting for the command echo to finish (newline),
                # pass everything through raw. This handles split escape sequences
                # in the echo (like \x1b[C) correctly.
                if self._cat_waiting_for_newline:
                    self._cat_queue.append(line.encode("utf-8", errors="replace"))
                    # IMPORTANT: During paste, readline often redraws the line using a standalone
                    # '\r' followed by CSI cursor moves (e.g. ESC[C). A bare '\r' is NOT the end
                    # of the echoed command; only stop skipping once we see a newline.
                    if is_echo_terminator(line):
                        self._cat_waiting_for_newline = False
                    continue

                # Check for embedded prompt (OSC7/OSC0)
                prompt_split_idx = -1
                if "\x1b]7;" in line:
                    prompt_split_idx = line.find("\x1b]7;")
                elif "\x1b]0;" in line:
                    prompt_split_idx = line.find("\x1b]0;")

                if prompt_split_idx >= 0:
                    if prompt_split_idx > 0:
                        self._handle_prompt_split(
                            line[:prompt_split_idx], line[prompt_split_idx:]
                        )
                    else:
                        self._cat_queue.append(_PROMPT_MARKER)
                        self._cat_queue.append(line.encode("utf-8", errors="replace"))
                    continue

                # Check for bracketed paste mode sequence (indicates prompt is coming)
                bpm_idx = line.find("\x1b[?2004h")
                if bpm_idx >= 0:
                    if bpm_idx > 0:
                        self._handle_prompt_split(line[:bpm_idx], line[bpm_idx:])
                    else:
                        self._cat_queue.append(_PROMPT_MARKER)
                        self._cat_queue.append(line.encode("utf-8", errors="replace"))
                    continue

                content, ending = self._split_line_ending(line)

                # Check for embedded prompt pattern when file doesn't end with newline
                prompt_patterns = [
                    r"([a-zA-Z_][a-zA-Z0-9_-]*@[^\s:]+:[^\$]+\$\s)",  # user@host:path$
                    r"(sh-\d+\.\d+\$\s)",  # sh-x.x$
                    r"(bash-\d+\.\d+\$\s)",  # bash-x.x$
                ]
                embedded_found = False
                for pattern in prompt_patterns:
                    match = re_engine.search(pattern, content)
                    if match and match.start() > 0:
                        self._handle_prompt_split(
                            content[: match.start()], content[match.start() :] + ending
                        )
                        embedded_found = True
                        break

                if embedded_found:
                    continue

                # Check for shell prompt (whole line is prompt)
                # NOTE: Do NOT use _at_shell_prompt alone here because TERMPROP_SHELL_PRECMD
                # fires before all cat content arrives. We must use actual prompt detection.
                # The cat context should have already been cleared by prompt detection in the queue.

                # Fallback: check for shell prompt patterns (for shells without termprops)
                lines_done = getattr(self, "_cat_lines_processed", 0)
                is_potential_prompt = lines_done > 0 or (
                    len(content) < 30 and "$" in content
                )

                if is_potential_prompt and self._is_shell_prompt(content):
                    self._cat_queue.append(_PROMPT_MARKER)
                    self._cat_queue.append(line.encode("utf-8", errors="replace"))
                    continue

                # Skip pure ANSI control sequences
                clean_content = _CSI_CONTROL_PATTERN.sub("", content).strip()
                if not clean_content and (not content or content.startswith("\x1b")):
                    self._cat_queue.append(line.encode("utf-8", errors="replace"))
                    continue

                # Highlight content
                has_ansi_colors = bool(_ANSI_COLOR_PATTERN.search(content))
                is_content = bool(content.strip())

                if is_content and not has_ansi_colors:
                    highlighted = self._highlight_line_with_pygments(
                        content, self._cat_filename
                    )

                    current_lexer = getattr(self, "_pygments_lexer", None)
                    if current_lexer is not None:
                        # Flush pending
                        pending = getattr(self, "_pending_lines", [])
                        if pending:
                            for pending_content, pending_ending in pending:
                                pending_highlighted = (
                                    self._highlight_line_with_pygments(
                                        pending_content, self._cat_filename
                                    )
                                )
                                self._cat_queue.append(
                                    (pending_highlighted + pending_ending).encode(
                                        "utf-8", errors="replace"
                                    )
                                )
                            self._pending_lines = []

                        self._cat_queue.append(
                            (highlighted + ending).encode("utf-8", errors="replace")
                        )
                    else:
                        # Buffer
                        pending = getattr(self, "_pending_lines", [])
                        pending.append((content, ending))
                        self._pending_lines = pending

                    self._cat_lines_processed = lines_done + 1
                else:
                    self._cat_queue.append(line.encode("utf-8", errors="replace"))
                    if is_content:
                        self._cat_lines_processed = lines_done + 1

            # Process batch
            if self._cat_queue and not self._cat_queue_processing:
                self._process_cat_queue_batch(term, immediate=True)
                if self._cat_queue:
                    self._cat_queue_processing = True
                    GLib.idle_add(self._process_cat_queue, term)

        except Exception as e:
            self.logger.error(f"Cat highlighting error: {e}")
            term.feed(data)

    def _is_shell_prompt(self, line: str) -> bool:
        """
        Fallback prompt detection for shells without VTE shell integration.
        Primary detection uses TERMPROP_SHELL_PRECMD via termprop-changed signal.
        """
        if len(line) < 3:
            return False

        # OSC7 detection (file:// URI indicates shell ready)
        if ("\x1b]7;" in line or "\033]7;" in line) and "file://" in line:
            pos = line.find("\x1b]7;") if "\x1b]7;" in line else line.find("\033]7;")
            prefix = _ANSI_SEQ_PATTERN.sub("", line[:pos]).replace("\x00", "").strip()
            if not prefix:
                return True

        # OSC0 (title setting, often sent with prompt)
        if "\x1b]0;" in line or "\033]0;" in line:
            pos = line.find("\x1b]0;") if "\x1b]0;" in line else line.find("\033]0;")
            prefix = _ANSI_SEQ_PATTERN.sub("", line[:pos]).replace("\x00", "").strip()
            if not prefix:
                return True

        # Traditional prompt patterns
        clean = _ANSI_SEQ_PATTERN.sub("", line).replace("\x00", "").strip()

        # user@host:path$ pattern with space
        if clean.endswith(("$ ", "# ", "% ")) and "@" in clean:
            return True

        # Shell name prompts: sh-5.3$, bash$
        if _SHELL_NAME_PROMPT_PATTERN.match(clean.rstrip("$#% ")):
            return True

        # Powerline prompts
        if clean and clean[-1] in ("➜", "❯", "»"):
            return True

        return False

    def _split_line_ending(self, line: str) -> tuple:
        """Split line into content and ending, normalizing to CRLF for terminal."""
        if line.endswith("\r\n"):
            return line[:-2], "\r\n"
        elif line.endswith("\n"):
            return line[:-1], "\r\n"  # Normalize to CRLF
        elif line.endswith("\r"):
            return line[:-1], "\r"
        return line, ""

    def _is_light_background(self) -> bool:
        """Check if the terminal background is light using luminance calculation."""
        try:
            terminal = self._terminal
            if terminal is None:
                return False

            # Get background color
            bg_rgba = terminal.get_color_background_for_draw()
            if bg_rgba is None:
                return False

            # Calculate luminance using standard formula
            r = bg_rgba.red
            g = bg_rgba.green
            b = bg_rgba.blue
            luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
            return luminance > 0.5
        except Exception:
            return False

    def _get_pygments_theme(self) -> str:
        """Get the configured Pygments theme from settings, with auto mode support."""
        try:
            from ..settings.manager import get_settings_manager

            settings = get_settings_manager()
            mode = settings.get("cat_theme_mode", "auto")

            if mode == "auto":
                # Auto mode: select theme based on background luminance
                if self._is_light_background():
                    return settings.get("cat_light_theme", "blinds-light").lower()
                else:
                    return settings.get("cat_dark_theme", "blinds-dark").lower()
            else:
                # Manual mode: use the single selected theme
                return settings.get("pygments_theme", "monokai").lower()
        except Exception:
            return "blinds-dark"

    def _detect_lexer_from_shebang(self, content: str):
        """
        Detect the lexer from content using Pygments' guess_lexer.

        Uses Pygments' guess_lexer to analyze the content, which internally
        handles shebang detection via lexer analyse_text() methods. This is
        more reliable than manual interpreter mapping.

        Args:
            content: Content to analyze (can be single line or multiple lines)

        Returns:
            A Pygments lexer if detected, None otherwise
        """
        if not content:
            return None

        try:
            from pygments.lexers import TextLexer, guess_lexer
            from pygments.util import ClassNotFound

            try:
                lexer = guess_lexer(content)
                # Only accept non-TextLexer results
                if not isinstance(lexer, TextLexer):
                    return lexer
            except ClassNotFound:
                pass

            return None
        except ImportError:
            return None

    def _highlight_line_with_pygments(self, line: str, filename: str) -> str:
        """
        Highlight a single line using Pygments.

        For PHP files, we track multi-line comment state manually to ensure
        lines inside /* ... */ blocks are highlighted as comments.

        Args:
            line: Single line of text (without line ending)
            filename: Filename for lexer detection

        Returns:
            Highlighted line with ANSI escape codes
        """
        try:
            from pygments import highlight
            from pygments.formatters import Terminal256Formatter
            from pygments.lexers import (
                TextLexer,
                get_lexer_by_name,
                get_lexer_for_filename,
                guess_lexer,
            )
            from pygments.styles import get_style_by_name
            from pygments.util import ClassNotFound

            # Get or create lexer
            current_lexer = getattr(self, "_pygments_lexer", None)
            needs_content_detection = getattr(
                self, "_pygments_needs_content_detection", False
            )
            is_php = filename and filename.lower().endswith(".php")

            # Debug log for lexer state
            if current_lexer is None:
                self.logger.debug(f"Pygments: no lexer yet for filename='{filename}', needs_content_detection={needs_content_detection}")

            # Detect lexer if we don't have one
            if current_lexer is None:
                lexer_found = False

                # Try filename-based detection first
                if filename and not needs_content_detection:
                    try:
                        self._pygments_lexer = get_lexer_for_filename(filename)
                        lexer_found = True
                        self.logger.debug(f"Pygments: found lexer {type(self._pygments_lexer).__name__} for filename '{filename}'")

                        # For PHP, use startinline=True so code is recognized without <?php
                        if is_php:
                            from pygments.lexers import PhpLexer

                            self._pygments_lexer = PhpLexer(startinline=True)
                            # Initialize multi-line comment tracking for PHP
                            self._php_in_multiline_comment = False

                    except ClassNotFound:
                        # Unknown extension - enable content detection as fallback
                        self._pygments_needs_content_detection = True
                        needs_content_detection = True

                # Content-based detection (for files without extension OR unknown extension)
                if needs_content_detection and not lexer_found:
                    # Initialize buffer if needed
                    if not hasattr(self, "_content_buffer"):
                        self._content_buffer = []

                    # Add line to buffer (skip ANSI sequences)
                    # Strip NULL chars and control chars from terminal
                    clean = line.strip().lstrip("\x00\x01\x02\x03\x04\x05\x06\x07\x08")
                    if clean and not clean.startswith("\x1b"):
                        self._content_buffer.append(clean)

                        # Try shebang detection on first line
                        if len(self._content_buffer) == 1 and clean.startswith("#!"):
                            shebang = clean.lower()
                            # Check for shell interpreters
                            is_shell = any(
                                s in shebang
                                for s in [
                                    "bash",
                                    "/sh",
                                    " sh",
                                    "zsh",
                                    "ksh",
                                    "dash",
                                    "fish",
                                ]
                            )
                            if is_shell:
                                self._pygments_lexer = get_lexer_by_name("bash")
                                self._pygments_needs_content_detection = False
                                lexer_found = True
                            elif "python" in shebang:
                                self._pygments_lexer = get_lexer_by_name("python")
                                self._pygments_needs_content_detection = False
                                lexer_found = True
                            elif "perl" in shebang:
                                self._pygments_lexer = get_lexer_by_name("perl")
                                self._pygments_needs_content_detection = False
                                lexer_found = True
                            elif "ruby" in shebang:
                                self._pygments_lexer = get_lexer_by_name("ruby")
                                self._pygments_needs_content_detection = False
                                lexer_found = True
                            elif "node" in shebang:
                                self._pygments_lexer = get_lexer_by_name("javascript")
                                self._pygments_needs_content_detection = False
                                lexer_found = True

                    # Try guess_lexer after 3+ lines
                    if not lexer_found and len(self._content_buffer) >= 3:
                        try:
                            content = "\n".join(self._content_buffer)
                            guessed = guess_lexer(content)
                            if not isinstance(guessed, TextLexer):
                                self._pygments_lexer = guessed
                                self._pygments_needs_content_detection = False
                                lexer_found = True
                        except Exception:
                            pass

                    # Give up after 10 lines - use TextLexer (no color)
                    if not lexer_found and len(self._content_buffer) >= 10:
                        self._pygments_lexer = TextLexer()
                        self._pygments_needs_content_detection = False
                        self._content_buffer = []

                # Update current_lexer reference
                current_lexer = getattr(self, "_pygments_lexer", None)

            # Still no lexer? Return plain text
            if current_lexer is None:
                return line

            # Get or create formatter - also recreate if theme changed
            formatter = getattr(self, "_pygments_formatter", None)
            current_theme = self._get_pygments_theme()
            cached_theme = getattr(self, "_pygments_cached_theme", None)

            if formatter is None or cached_theme != current_theme:
                try:
                    style = get_style_by_name(current_theme)
                except ClassNotFound:
                    style = get_style_by_name("monokai")
                self._pygments_formatter = Terminal256Formatter(style=style)
                self._pygments_cached_theme = current_theme
                formatter = self._pygments_formatter

            # For PHP: Track multi-line comments manually
            # Pygments with startinline=True doesn't track state between lines
            is_php = filename and filename.lower().endswith(".php")
            if is_php:
                in_comment = getattr(self, "_php_in_multiline_comment", False)

                # Check if line opens or closes a multi-line comment
                # Strip the line for checking, but preserve original for highlighting
                if in_comment:
                    # We're inside a comment - check if it closes
                    if "*/" in line:
                        # Comment closes on this line
                        self._php_in_multiline_comment = False
                        # Let Pygments try to highlight - it may do partial job
                    else:
                        # Still inside comment - apply comment color directly
                        # Get comment color from the style (usually gray/green)
                        comment_color = "\x1b[38;5;245m"  # Gray (monokai comment color)
                        reset = "\x1b[39m"
                        return f"{comment_color}{line}{reset}"
                else:
                    # Not in comment - check if one starts
                    if "/*" in line:
                        # Check if it also closes on this line
                        start_pos = line.find("/*")
                        end_pos = line.find("*/", start_pos + 2)
                        if end_pos == -1:
                            # Comment starts but doesn't close - track it
                            self._php_in_multiline_comment = True
                    # Also check for /** docblock
                    elif "/**" in line:
                        start_pos = line.find("/**")
                        end_pos = line.find("*/", start_pos + 3)
                        if end_pos == -1:
                            self._php_in_multiline_comment = True

            # Highlight using Pygments
            return highlight(line, current_lexer, formatter).rstrip("\n")

        except Exception as e:
            self.logger.error(f"Highlighting error: {e}")
            return line

    def _process_cat_queue_batch(
        self, term: Vte.Terminal, immediate: bool = False
    ) -> bool:
        """
        Process a batch of lines from the cat queue.

        Args:
            term: VTE terminal to feed output to
            immediate: If True, process smaller batch for immediate display

        Returns:
            True if prompt was detected (signals end of output)
        """
        queue = getattr(self, "_cat_queue", None)
        if not queue:
            return False

        # Smaller batch for immediate display, larger for background
        batch_size = 10 if immediate else 30

        lines_to_feed = []
        prompt_detected = False
        remaining_after_prompt = []

        for _ in range(batch_size):
            if not queue:
                break
            try:
                line_data = queue.popleft()

                # Check for prompt marker
                if line_data == _PROMPT_MARKER:
                    prompt_detected = True
                    # Don't break - continue to collect any remaining lines (prompt lines)
                    # that came in the same chunk after the marker was added
                    continue

                if prompt_detected:
                    # Lines after marker are prompt/control lines - collect them
                    remaining_after_prompt.append(line_data)
                else:
                    lines_to_feed.append(line_data)
            except IndexError:
                break

        # Feed batch to terminal
        if lines_to_feed:
            term.feed(b"".join(lines_to_feed))

        # Handle prompt detection - clear context
        if prompt_detected:
            # Flush remaining pending lines (content that was buffered)
            pending = getattr(self, "_pending_lines", [])
            for pending_content, pending_ending in pending:
                term.feed(
                    (pending_content + pending_ending).encode("utf-8", errors="replace")
                )
            self._pending_lines = []

            # Feed any lines that came after the prompt marker
            # These are prompt lines (OSC7, PS1, etc.) that need to be displayed
            if remaining_after_prompt:
                term.feed(b"".join(remaining_after_prompt))

            # Drain any remaining lines in the queue (could be prompt data from next chunk)
            drain_lines = []
            while queue:
                try:
                    line_data = queue.popleft()
                    if line_data != _PROMPT_MARKER:
                        drain_lines.append(line_data)
                except IndexError:
                    break
            if drain_lines:
                term.feed(b"".join(drain_lines))

            self._highlighter.clear_context(self._proxy_id)
            self._reset_cat_state()
            self._reset_input_buffer()

        return prompt_detected

    def _process_cat_queue(self, term: Vte.Terminal) -> bool:
        """
        Process lines from cat queue in batches via GTK idle callback.

        Processes lines in small batches for responsive streaming.
        Uses GLib.idle_add to yield to GTK main loop between batches.

        Args:
            term: VTE terminal to feed output to

        Returns:
            False to remove from idle queue when done
        """
        if not self._running or self._widget_destroyed:
            self._cat_queue_processing = False
            return False

        try:
            queue = getattr(self, "_cat_queue", None)
            if not queue:
                self._cat_queue_processing = False
                return False

            # Process batch
            prompt_detected = self._process_cat_queue_batch(term, immediate=False)

            if prompt_detected:
                self._cat_queue_processing = False
                return False

            # Schedule next batch if queue has more
            if queue:
                return True  # Keep callback scheduled
            else:
                self._cat_queue_processing = False
                return False

        except Exception as e:
            self.logger.error(f"Cat queue processing error: {e}")
            self._cat_queue_processing = False
            return False

    def _reset_cat_state(self) -> None:
        """Reset cat/pygments state."""
        self._cat_filename = None
        self._cat_bytes_processed = 0  # Resetar contador
        self._cat_limit_reached = False  # Resetar flag
        self._cat_waiting_for_newline = False
        self._pygments_lexer = None
        self._pygments_needs_content_detection = False
        self._content_buffer = []
        self._pending_lines = []
        self._cat_lines_processed = 0
        if hasattr(self, "_cat_queue"):
            self._cat_queue.clear()
        self._cat_queue_processing = False
        if hasattr(self, "_pygments_formatter"):
            delattr(self, "_pygments_formatter")

    def _extract_filename_from_cat_command(self, command: str) -> Optional[str]:
        """
        Extract the filename from a cat command for language detection.

        Args:
            command: The full cat command (e.g., "cat file.py", "cat -n file.sh")

        Returns:
            The first filename found, or None
        """
        if not command:
            return None

        # Parse the command to extract filenames
        parts = command.split()
        if not parts or parts[0].lower() not in ("cat", "/bin/cat", "/usr/bin/cat"):
            return None

        # Skip the command name and flags, find the first filename
        for part in parts[1:]:
            if part.startswith("-"):
                continue
            # This is likely a filename
            return part.strip("'\"")

        return None

    def _flush_queue(self, term: Vte.Terminal) -> None:
        """
        Force flush any pending lines in the highlighting queue to the terminal.
        This ensures strict ordering before switching to raw feed.
        """
        if self._line_queue:
            # Drain the entire queue immediately
            while self._line_queue:
                try:
                    chunk = self._line_queue.popleft()
                    term.feed(chunk)
                except IndexError:
                    break
            self._queue_processing = False

    def _process_data_streaming(self, data: bytes, term: Vte.Terminal) -> None:
        """
        Apply highlighting with Adaptive Burst Detection, Alt-Screen Bypass,
        Bracketed Paste Bypass, Strict Ordering, and Robust Split-Escape Safety.
        """
        try:
            # --- EARLY EXIT: Output highlighting disabled ---
            # Check if output highlighting is disabled for this terminal type.
            # This ensures that when the user disables output highlighting,
            # all subsequent data is fed raw to the terminal immediately.
            if not self._highlighter.is_enabled_for_type(self._terminal_type):
                # Clear any stale highlighted data from the queue
                self._line_queue.clear()
                # Feed any partial buffer and the current data raw
                if self._partial_line_buffer:
                    term.feed(self._partial_line_buffer)
                    self._partial_line_buffer = b""
                term.feed(data)
                return

            # --- 0. BRACKETED PASTE DETECTION ---
            if b"\x1b[200~" in data:
                self._in_bracketed_paste = True
                self._flush_queue(term)
                if self._partial_line_buffer:
                    term.feed(self._partial_line_buffer)
                    self._partial_line_buffer = b""

            if self._in_bracketed_paste:
                term.feed(data)
                if b"\x1b[201~" in data:
                    self._in_bracketed_paste = False
                    self._reset_input_buffer()
                    self._suppress_shell_input_highlighting = True
                return

            # --- 0b. PROMPT REDRAW / CURSOR MOVEMENT BYPASS ---
            # Readline commonly redraws the prompt/line using a carriage return
            # plus CSI cursor movement/erase sequences (especially after paste
            # and when using left/right arrows). Our partial buffering and
            # split-escape handling are optimized for command output streams
            # and can introduce visual-only artifacts here (e.g., duplicated
            # last character under the cursor).
            #
            # When we're at an interactive prompt, keep these sequences raw
            # and never buffer them.
            #
            # NOTE: The size limit is set very high (1MB) to handle very long command
            # lines from history (CTRL+R, arrow up) which would otherwise not be
            # displayed correctly until the user interacts with them.
            if self._at_shell_prompt and len(data) < 1048576:
                # Check if this is an Enter key press (newline from user)
                # The newline may be bundled with escape sequences like bracketed paste mode
                is_enter_marker = (
                    data[0] == 0x00 if len(data) >= 1 else False
                ) and (b"\r\n" in data or b"\n" in data)

                # Check for readline redraw sequences:
                # - \r (carriage return) - line redraw start
                # - \x1b[D / \x1b[C - cursor left/right
                # - \x1b[1D / \x1b[1C - cursor left/right by 1
                # - \x1b[K / \x1b[0K - clear to end of line
                # - \x1b[J / \x1b[0J - clear to end of screen
                # - \x1b[A / \x1b[B - cursor up/down (history navigation)
                # - \x1b[H - cursor home
                # - \x1b[?25l / \x1b[?25h - hide/show cursor
                # - \x1b[ followed by digits and G - cursor column move
                # - \x1b[<n>P - delete characters
                # - \x1b[<n>@ - insert characters
                # - (reverse-i-search) etc. - readline search prompts
                search_prompt_patterns = (
                    b"(reverse-i-search)",
                    b"(i-search)",
                    b"(bck-i-search)",
                    b"(fwd-i-search)",
                    b"(failed ",  # "(failed reverse-i-search)" etc.
                )
                is_readline_redraw = (
                    b"\r" in data
                    or b"\x1b[D" in data
                    or b"\x1b[C" in data
                    or b"\x1b[K" in data
                    or b"\x1b[0K" in data
                    or b"\x1b[1D" in data
                    or b"\x1b[1C" in data
                    or b"\x1b[A" in data
                    or b"\x1b[B" in data
                    or b"\x1b[J" in data
                    or b"\x1b[0J" in data
                    or b"\x1b[H" in data
                    or b"\x1b[?25l" in data
                    or b"\x1b[?25h" in data
                    # Additional patterns for CTRL+R and history search
                    or b"\x1b[P" in data  # Delete character
                    or b"\x1b[@" in data  # Insert character
                    or any(pattern in data for pattern in search_prompt_patterns)
                )

                # Also check for large line data without escape sequences
                # When readline selects a long history entry, it may send just the
                # text with minimal escape sequences
                is_large_line_at_prompt = (
                    len(data) > 200 and not is_enter_marker and b"\n" not in data
                )

                if (not is_enter_marker) and (
                    is_readline_redraw or is_large_line_at_prompt
                ):
                    self._flush_queue(term)
                    # Any buffered remainder at the prompt is stale and can
                    # cause visible duplication when readline redraws.
                    self._partial_line_buffer = b""

                    # Handle backspace - update the input buffer before returning
                    if b"\x08" in data or b"\x7f" in data:
                        self._handle_backspace_in_buffer(data)

                    term.feed(data)
                    return

            # --- 1. ALT SCREEN DETECTION ---
            self._update_alt_screen_state(data)

            if self._is_alt_screen:
                self._flush_queue(term)
                if self._partial_line_buffer:
                    term.feed(self._partial_line_buffer)
                    self._partial_line_buffer = b""
                term.feed(data)
                return

            # Combine with partial data
            if self._partial_line_buffer:
                data = self._partial_line_buffer + data
                self._partial_line_buffer = b""

            data_len = len(data)

            # --- 2. HARD LIMIT (Safety Valve) ---
            # Use 1MB limit to handle extremely long command lines while
            # still providing protection against streaming binary data
            if data_len > 1048576:
                self._burst_counter = 100
                self._flush_queue(term)
                term.feed(data)
                return

            # --- 3. ADAPTIVE BURST DETECTION ---
            if data_len > 1024:
                self._burst_counter += 1
            else:
                self._burst_counter = 0

            if self._burst_counter > 15:
                # Use termprop state first, then OSC7 fallback
                if self._at_shell_prompt or b"\x1b]7;" in data or b"\033]7;" in data:
                    self._reset_input_buffer()

                self._flush_queue(term)
                term.feed(data)
                return

            # Standard partial line handling (for newlines)
            last_newline_pos = data.rfind(b"\n")

            if last_newline_pos != -1 and last_newline_pos < data_len - 1:
                remainder = data[last_newline_pos + 1 :]

                is_interactive = False
                if len(remainder) < 200:
                    rem_str = remainder.decode("utf-8", errors="ignore")
                    # Heuristics for interactive remainders:
                    # - prompt only (ends with $, #, %, >, :) after stripping whitespace
                    # - prompt + current input (common after readline redisplay on tab completion)
                    # - escape sequences present (prompt styling, OSC7)
                    stripped = rem_str.strip()
                    if stripped.endswith(("$", "#", "%", ">", ":")):
                        is_interactive = True
                    elif any(t in rem_str for t in ("$ ", "# ", "% ", "> ")):
                        is_interactive = True
                    elif (
                        "\x1b[" in rem_str
                        or "\x1b]7;" in rem_str
                        or "\033]7;" in rem_str
                    ):
                        is_interactive = True

                # Do not buffer remainders while at a shell prompt. Readline may
                # be mid-redisplay, and buffering can replay stale bytes later
                # (visible as duplicated trailing characters).
                if not is_interactive and not self._at_shell_prompt:
                    self._partial_line_buffer = remainder
                    data = data[: last_newline_pos + 1]

            elif last_newline_pos == -1 and data_len < 4096:
                pass

            # --- 5. NORMAL PROCESSING ---
            text = data.decode("utf-8", errors="replace")
            if not text:
                return

            # EARLY PRIMARY PROMPT DETECTION:
            # When a command finishes executing, the shell sends output followed by
            # a new primary prompt. We must detect this and reset the buffer.
            # This is especially important for shells without termprop support (sh, dash).
            if self._input_highlight_buffer and self._at_shell_prompt:
                stripped_for_prompt = (
                    _ALL_ESCAPE_SEQ_PATTERN.sub("", text).replace("\x00", "").strip()
                )
                # Check if text ENDS with a primary prompt (not continuation)
                if stripped_for_prompt.endswith("$") or stripped_for_prompt.endswith(
                    "#"
                ):
                    # Extract the part before $ or #
                    prompt_candidate = stripped_for_prompt[:-1].rstrip()
                    # Get just the last line (the actual prompt)
                    if "\n" in prompt_candidate:
                        prompt_candidate = prompt_candidate.split("\n")[-1].strip()
                    # Check if it looks like a shell prompt
                    if prompt_candidate and (
                        _SHELL_NAME_PROMPT_PATTERN.match(prompt_candidate)
                        or "@" in prompt_candidate
                        or ":" in prompt_candidate
                        or prompt_candidate.endswith("~")
                        or prompt_candidate.endswith("/")
                    ):
                        self._reset_input_buffer()
                        self._need_color_reset = True

            # Track whether THIS chunk is likely echoed user input.
            chunk_is_likely_user_input = False

            # Interactive marker check using helper function
            if data_len < 1024:
                has_marker, is_user_input, is_newline = self._detect_interactive_marker(
                    data
                )

                # Handle backspace early return
                if has_marker and is_user_input and (data[1] in (0x08, 0x7F)):
                    if self._at_shell_prompt:
                        self._handle_backspace_in_buffer(data)
                        clean_data = data.replace(b"\x00", b"")
                        if clean_data:
                            term.feed(clean_data)
                        return

                if has_marker:
                    if is_newline:
                        if self._at_shell_prompt:
                            # Check if we're in a multi-line command before resetting
                            is_in_unclosed_block = self._is_in_unclosed_multiline_block(self._input_highlight_buffer)

                            # Also check continuation prompt in the data
                            stripped_text = _ALL_ESCAPE_SEQ_PATTERN.sub("", text)
                            has_continuation_prompt = ">" in stripped_text.strip()

                            if is_in_unclosed_block or has_continuation_prompt:
                                # Multi-line command - add newline to buffer instead of resetting
                                if not self._input_highlight_buffer.endswith("\n"):
                                    self._input_highlight_buffer += "\n"
                            else:
                                # Simple command submitted - reset
                                self._at_shell_prompt = False
                                self._shell_input_highlighter.set_at_prompt(self._proxy_id, False)
                        self._suppress_shell_input_highlighting = False
                    elif is_user_input:
                        chunk_is_likely_user_input = True
                        if not self._at_shell_prompt:
                            self._at_shell_prompt = True
                            self._shell_input_highlighter.set_at_prompt(self._proxy_id, True)
                            self._reset_input_buffer()
                            self._need_color_reset = True
                            self._suppress_shell_input_highlighting = False

            # Suppress highlighting on cursor-movement sequences at prompt
            if self._at_shell_prompt and (b"\x1b" in data or b"\r" in data):
                # Check if this is a backspace sequence
                is_backspace = b"\x08" in data or b"\x7f" in data

                if is_backspace and self._input_highlight_buffer:
                    self._handle_backspace_in_buffer(data)
                elif self._input_highlight_buffer:
                    # Only suppress/reset if this is a cursor movement, not a newline from user input
                    # and NOT when we're in a multi-line command
                    is_possible_newline = b"\r\n" in data or (
                        b"\r" in data and b"\n" in data
                    )

                    # Check if data contains a primary prompt (sh-5.3$, bash$, etc.)
                    # If so, we should reset even if we're in a multiline block (command was aborted/errored)
                    stripped_for_prompt = _ALL_ESCAPE_SEQ_PATTERN.sub("", text).strip()
                    has_primary_prompt = False
                    if stripped_for_prompt.endswith("$") or stripped_for_prompt.endswith("#"):
                        prompt_part = stripped_for_prompt[:-1].strip()
                        if _SHELL_NAME_PROMPT_PATTERN.match(prompt_part) or "@" in prompt_part or ":" in prompt_part:
                            has_primary_prompt = True

                    # Check if we're in a multi-line command (unclosed block)
                    is_in_multiline = self._is_in_unclosed_multiline_block(
                        self._input_highlight_buffer
                    )

                    if has_primary_prompt:
                        # Primary prompt detected - command finished, reset buffer
                        self._reset_input_buffer()
                        self._need_color_reset = True
                    elif not is_possible_newline and not is_in_multiline:
                        self._suppress_shell_input_highlighting = True
                        self._reset_input_buffer()
                        self._need_color_reset = True

            # Never render NUL marker bytes to the terminal.
            # They are used internally for interactive detection and can cause
            # subtle cursor/render artifacts during rapid input (e.g., paste).
            if b"\x00" in data:
                data = data.replace(b"\x00", b"")
                text = text.replace("\x00", "")

            # Handle backspace that may come without escape sequences (just \x08 or \x7f)
            # This is separate from the escape sequence handling above
            if self._at_shell_prompt:
                has_backspace_char = b"\x08" in data or b"\x7f" in data
                if has_backspace_char:
                    chars_removed = self._handle_backspace_in_buffer(data)
                    if chars_removed > 0:
                        # Feed the backspace to terminal and return - don't add to buffer
                        term.feed(data)
                        return

            # Get rules
            rules = None
            with self._highlighter._lock:
                context = self._highlighter._proxy_contexts.get(self._proxy_id, "")
                rules = self._highlighter._get_active_rules(context)

            # Check for shell prompt detection
            self._check_and_update_prompt_state(text)

            # Shell input highlighting
            if (
                self._at_shell_prompt
                and self._shell_input_highlighter.enabled
                and chunk_is_likely_user_input
                and not self._suppress_shell_input_highlighting
            ):
                # Keep strict ordering with any queued output. This matters when
                # readline redraws the prompt/line (tab completion) and output
                # arrives slightly earlier/later than echoed keystrokes.
                self._flush_queue(term)
                highlighted_data = self._apply_shell_input_highlighting(text, term)
                if highlighted_data is not None:
                    return

            # When the shell is at an interactive prompt, its output can include
            # readline redisplay and cursor-movement sequences. Modifying that
            # stream (e.g., by applying output highlighting) can cause visible
            # artifacts such as duplicated characters when moving the cursor
            # after pasting. Keep prompt interactions raw.
            #
            # HOWEVER: If we detect a newline from command submission (not continuation),
            # we should NOT skip output highlighting for command output that follows.
            if self._at_shell_prompt:
                # Even without interactive markers, we need to track newlines
                # for the input buffer to handle multi-line commands correctly.
                if "\n" in text:
                    # Check if we have content in the buffer (command was typed)
                    if self._input_highlight_buffer.strip():
                        # Check for unclosed blocks (if/then without fi, for/do without done, etc.)
                        is_in_unclosed_block = self._is_in_unclosed_multiline_block(self._input_highlight_buffer)

                        stripped_text = _ALL_ESCAPE_SEQ_PATTERN.sub("", text)
                        has_continuation_prompt = (
                            stripped_text.strip() == ">"
                            or stripped_text.strip().endswith(">")
                        )

                        if is_in_unclosed_block or has_continuation_prompt:
                            if not self._input_highlight_buffer.endswith("\n"):
                                self._input_highlight_buffer += "\n"
                            # Still at prompt for multiline - skip output highlighting
                            self._flush_queue(term)
                            term.feed(data)
                            return
                        else:
                            # Command submitted without continuation - CONTINUE to output highlighting
                            self._at_shell_prompt = False
                            self._reset_input_buffer()
                            # DON'T return here - let output be highlighted
                    else:
                        # Empty buffer with newline (e.g., command from history via ↑)
                        # Check if we have a context (manager.py detects command from terminal line)
                        # Re-obtain context and rules as they may have been set since line 1753
                        # due to async GTK event processing
                        with self._highlighter._lock:
                            context = self._highlighter._proxy_contexts.get(self._proxy_id, "")
                            if context:
                                rules = self._highlighter._get_active_rules(context)

                        if context and rules:
                            # We have a context and rules - command likely from history
                            # Proceed to output highlighting
                            self._at_shell_prompt = False
                            self._reset_input_buffer()
                            # DON'T return here - let output be highlighted
                        else:
                            # No context - just pass through raw
                            self._flush_queue(term)
                            term.feed(data)
                            return
                else:
                    # No newline - regular prompt interaction, skip highlighting
                    self._flush_queue(term)
                    term.feed(data)
                    return

            # If no rules OR output highlighting is disabled, feed raw
            output_enabled = self._highlighter.is_enabled_for_type(self._terminal_type)
            if not rules or not output_enabled:
                self._flush_queue(term)
                term.feed(data)
                return

            # Highlighting Logic
            lines = text.splitlines(keepends=True)
            highlight_line = self._highlighter._apply_highlighting_to_line
            # Use simple skip-first logic like the original implementation
            skip_first = self._highlighter.should_skip_first_output(self._proxy_id)

            for i, line in enumerate(lines):
                if skip_first and i == 0:
                    self._line_queue.append(line.encode("utf-8", errors="replace"))
                    continue

                if not line or line in ("\n", "\r", "\r\n"):
                    self._line_queue.append(line.encode("utf-8"))
                    continue

                if "\x1b]7;" in line or "\033]7;" in line:
                    self._line_queue.append(line.encode("utf-8", errors="replace"))
                    # Termprop handler already manages prompt state for OSC7
                    continue

                if line[-1] == "\n":
                    if len(line) > 1 and line[-2] == "\r":
                        content, ending = line[:-2], "\r\n"
                    else:
                        content, ending = line[:-1], "\n"
                elif line[-1] == "\r":
                    content, ending = line[:-1], "\r"
                else:
                    content, ending = line, ""

                if content:
                    highlighted = highlight_line(content, rules) + ending
                else:
                    highlighted = ending

                self._line_queue.append(highlighted.encode("utf-8", errors="replace"))

            if not self._queue_processing:
                self._queue_processing = True
                self._process_line_queue(term)

        except Exception:
            self._flush_queue(term)
            term.feed(data)

    def _reset_input_buffer(self) -> None:
        """Reset shell input highlighting buffer state."""
        if self._input_highlight_buffer:
            self._input_highlight_buffer = ""
        self._prev_shell_input_token_type = None
        self._prev_shell_input_token_len = 0

    def _check_and_update_prompt_state(self, text: str) -> bool:
        """
        Check if text contains a shell prompt (primary or continuation).
        Primary prompt detection is handled by termprop-changed signal,
        but we also detect prompts directly for shells without termprop support.

        Returns True if a prompt was detected.
        """
        # Early exit: if no potential prompt characters, skip expensive processing
        if not any(c in text for c in "$#%>❯"):
            return False

        stripped_text = _ALL_ESCAPE_SEQ_PATTERN.sub("", text).replace("\x00", "")

        # Check for continuation prompt ("> ")
        stripped_clean = stripped_text.strip()
        if stripped_clean == ">":
            self._at_shell_prompt = True
            self._shell_input_highlighter.set_at_prompt(self._proxy_id, True)
            if (
                self._input_highlight_buffer
                and not self._input_highlight_buffer.endswith("\n")
            ):
                self._input_highlight_buffer += "\n"
            self._prev_shell_input_token_type = None
            self._prev_shell_input_token_len = 0
            return True

        # Get the last line only - prompts are single lines
        last_line = stripped_clean.rsplit('\n', 1)[-1].strip()
        last_line = last_line.rsplit('\r', 1)[-1].strip()

        # Sanity check: prompts are short (typically < 100 chars)
        if len(last_line) > 100:
            return False

        # Check for modern prompts (Starship, Oh-My-Zsh, Powerlevel10k, etc.)
        # These typically end with ❯, ➜, λ, or other symbols
        prompt_end_chars = ("#", "%", "❯", "➜", "λ", "›")

        for char in prompt_end_chars:
            if last_line.endswith(char):
                # For fancy prompts like ❯, ➜, λ - just check it's at the end
                # These are distinctive enough that we can trust them as prompt indicators
                if char in ("❯", "➜", "λ", "›"):
                    self._at_shell_prompt = True
                    self._shell_input_highlighter.set_at_prompt(self._proxy_id, True)
                    self._reset_input_buffer()
                    self._need_color_reset = True
                    # Clear highlighting context when returning to prompt
                    self._highlighter.clear_context(self._proxy_id)
                    self._reset_cat_state()
                    return True

                # For traditional prompts ($, #, %), verify it looks like a shell prompt
                prompt_part = last_line[:-1].strip()  # Remove prompt char at the end

                # Match shell name patterns: sh-5.3, bash, etc.
                if _SHELL_NAME_PROMPT_PATTERN.match(prompt_part):
                    self._at_shell_prompt = True
                    self._shell_input_highlighter.set_at_prompt(self._proxy_id, True)
                    self._reset_input_buffer()
                    self._need_color_reset = True
                    # Clear highlighting context when returning to prompt
                    self._highlighter.clear_context(self._proxy_id)
                    self._reset_cat_state()
                    return True

                # Match paths like ~ or /home/user or user@host:~
                if (
                    prompt_part.endswith("~")
                    or prompt_part.endswith("/")
                    or "@" in prompt_part
                    or ":" in prompt_part
                ):
                    self._at_shell_prompt = True
                    self._shell_input_highlighter.set_at_prompt(self._proxy_id, True)
                    self._reset_input_buffer()
                    self._need_color_reset = True
                    # Clear highlighting context when returning to prompt
                    self._highlighter.clear_context(self._proxy_id)
                    self._reset_cat_state()
                    return True

        return False

    def _apply_shell_input_highlighting(
        self, text: str, term: Vte.Terminal
    ) -> Optional[bytes]:
        """
        Apply syntax highlighting to shell input being echoed.

        This method handles the case where we're at a shell prompt and
        characters are being echoed back as the user types.

        The approach:
        1. Track characters as they're typed (building a buffer)
        2. For each character echoed, append to buffer
        3. Re-tokenize the full buffer and apply colors
        4. Output only the newly typed character with appropriate color

        Args:
            text: The echoed text from PTY
            term: The VTE terminal

        Returns:
            bytes if handled, None if shell input highlighting didn't apply
        """
        if not self._shell_input_highlighter.enabled:
            return None

        # Strip NULL bytes that may be prepended by terminal
        text = text.lstrip("\x00")
        if not text:
            return None

        # Don't highlight control sequences or chunks containing them
        # This prevents interference with prompt colors and escape sequences
        if text.startswith("\x1b"):
            return None

        # Handle backspace: reuse unified helper function
        # Patterns: \x08 \x08 (sh/dash), \x08\x1b[K (bash), single \x08 or \x7f
        if "\x08" in text or "\x7f" in text:
            data = text.encode("utf-8", errors="replace")
            if self._handle_backspace_in_buffer(data) > 0:
                return None  # Let terminal handle the backspace display

        # Don't process chunks that contain escape sequences (like OSC7, colors, etc.)
        # These are command output or prompt rendering, not user input
        # HOWEVER, we need to handle newlines first even if there are escape sequences
        # because the Enter key often comes bundled with escape sequences like bracketed paste
        if "\n" in text:
            # If buffer has content, the user submitted a command or is continuing multi-line
            if self._input_highlight_buffer.strip():
                # Check if we're inside an unclosed block
                is_in_unclosed_block = self._is_in_unclosed_multiline_block(self._input_highlight_buffer)

                # Also check if this chunk contains a continuation prompt ("> ")
                stripped_text = _ALL_ESCAPE_SEQ_PATTERN.sub("", text)
                has_continuation_prompt = (
                    stripped_text.strip() == ">" or stripped_text.strip().endswith(">")
                )

                if is_in_unclosed_block or has_continuation_prompt:
                    self._input_highlight_buffer += "\n"
                else:
                    # Command submitted - no longer at prompt
                    self._at_shell_prompt = False
                    self._reset_input_buffer()
            return None

        # Now check for escape sequences AFTER handling newlines
        if "\x1b" in text or "\033" in text:
            return None

        # Don't process large chunks - user input comes one character at a time
        # Large chunks are likely command output
        # Exception: autocomplete may send small completions (e.g., "e.php " to complete "test" to "teste.php ")
        if len(text) > 10:
            return None

        # Check if we need to send a color reset before starting input highlighting
        # This happens when prompt was detected (OSC7/traditional) after command output
        # and ensures prompt colors don't leak into input highlighting
        if self._need_color_reset and text and text[0].isprintable():
            # Send SGR reset to clear any active terminal attributes
            term.feed(b"\x1b[0m")
            self._need_color_reset = False

        if "\r" in text:
            # Readline redraw (tab completion, bracketed paste, etc.)
            self._reset_input_buffer()
            self._need_color_reset = True
            return None

        # Only process printable characters
        if not text or not all(c.isprintable() or c == " " for c in text):
            return None

        # Filter out literal escape sequences shown by simple shells (sh/dash)
        # When ^[[D appears as text (arrow key not handled by shell), skip it
        # These patterns indicate the shell doesn't support this key
        # ^[ is the visual representation of ESC that some shells show
        # [A, [B, [C, [D are arrow keys shown literally when shell doesn't handle them
        if "^[" in text:
            return None
        # Check for arrow key sequences shown as literal text: [A, [B, [C, [D
        if text in ("[A", "[B", "[C", "[D", "[H", "[F"):
            return None

        # Don't add continuation prompt to buffer - it's not part of the command
        text_stripped = text.strip()
        if text_stripped == ">" or text_stripped == "> ":
            return None

        # Append to buffer (strip leading newlines if buffer was empty)
        if not self._input_highlight_buffer:
            self._input_highlight_buffer = text
        else:
            self._input_highlight_buffer += text

        # Get highlighted version of the current buffer
        try:
            from pygments import lex
            from pygments.lexers import BashLexer

            # Always use lexer/formatter from the global singleton
            # This ensures theme changes are applied immediately
            highlighter = self._shell_input_highlighter
            lexer = highlighter._lexer
            formatter = highlighter._formatter

            # Fallback if singleton not initialized
            if lexer is None:
                lexer = BashLexer()

            # Tokenize to find the color of the last character
            # We use lex() to get token types and then map to colors
            tokens = list(lex(self._input_highlight_buffer, lexer))

            if not tokens:
                # No tokens, just output the raw text
                term.feed(text.encode("utf-8"))
                return b""

            # Pygments always adds a trailing newline token (Token.Text.Whitespace '\n')
            # So we need to find the actual token containing our typed character
            # Skip trailing whitespace-only tokens to find the real last token
            actual_token_type = None
            actual_token_value = None
            for token_type, token_value in reversed(tokens):
                # Skip pure whitespace/newline tokens at the end
                if token_value.strip():
                    actual_token_type = token_type
                    actual_token_value = token_value.rstrip(
                        "\n"
                    )  # Strip trailing newline from value
                    break
                # If the token is just a space (not newline) and we typed a space, use it
                elif token_value == " " and text == " ":
                    actual_token_type = token_type
                    actual_token_value = token_value
                    break

            if actual_token_type is None:
                # If we still didn't find anything useful, use the second-to-last token
                # (first is our content, last is the trailing newline)
                if len(tokens) >= 2:
                    actual_token_type, actual_token_value = tokens[-2]
                    actual_token_value = actual_token_value.rstrip("\n")
                else:
                    actual_token_type, actual_token_value = tokens[-1]
                    actual_token_value = actual_token_value.rstrip("\n")

            # Enhanced token detection: improve coloring for commands and options
            # Pygments BashLexer doesn't recognize external commands or options well
            from pygments.token import Token

            enhanced_token_type = actual_token_type

            # Get the current line being typed (last line of buffer)
            current_line = self._input_highlight_buffer.split("\n")[-1].strip()

            # Define prefix commands that should be treated specially
            # These commands take other commands as arguments
            PREFIX_COMMANDS = {
                "sudo",
                "time",
                "env",
                "nice",
                "nohup",
                "strace",
                "ltrace",
                "doas",
                "pkexec",
            }
            # Commands that should be highlighted with Token.Name.Exception
            WARNING_COMMANDS = {"sudo", "doas", "pkexec", "rm", "dd"}

            # Check if this is an option (starts with - or --)
            if actual_token_value and (
                actual_token_value.startswith("--")
                or (actual_token_value.startswith("-") and len(actual_token_value) > 1)
            ):
                # Options: use Token.Name.Attribute which most themes style nicely
                # Don't set custom_ansi_color - let the formatter provide the color
                enhanced_token_type = Token.Name.Attribute

            # Check if this is the first word on the line (command position)
            # A command is the first word, or word after pipe |, semicolon ;, &&, ||, or prefix command
            elif actual_token_type in (Token.Text, Token.Name):
                if actual_token_value:
                    # Find position of current token in the line
                    words_before = current_line.rsplit(actual_token_value, 1)[
                        0
                    ].rstrip()
                    # Check if nothing before, or ends with control character, or follows a prefix command
                    is_command_position = not words_before or words_before.endswith((
                        "|",
                        ";",
                        "&&",
                        "||",
                        "(",
                        "`",
                        "$(",
                    ))

                    # Also check if last word was a prefix command (sudo, time, env, etc.)
                    if not is_command_position and words_before:
                        last_word = (
                            words_before.split()[-1] if words_before.split() else ""
                        )
                        if last_word in PREFIX_COMMANDS:
                            is_command_position = True

                    if is_command_position:
                        # Check if this is a warning command (sudo, rm, dd, etc.)
                        if actual_token_value in WARNING_COMMANDS:
                            # Warning commands: use Token.Name.Exception for visual distinction
                            # Most themes style this prominently (often in red/orange tones)
                            enhanced_token_type = Token.Name.Exception
                        else:
                            enhanced_token_type = (
                                Token.Name.Function
                            )  # Commands as functions (green in most themes)

            # Store state for potential future heuristics.
            # Perform retroactive recoloring ONLY when token type changes for the
            # same word being typed. This fixes the issue where typing "if" starts
            # as Token.Name ('i') and becomes Token.Keyword ('if'), but the 'i'
            # was already rendered with Name color.
            prev_token_type = self._prev_shell_input_token_type
            prev_token_len = self._prev_shell_input_token_len
            current_token_len = len(actual_token_value) if actual_token_value else 0

            # Detect if token type changed for the same word (not a new word)
            # Conditions for retroactive recolor:
            # 1. We have a previous token type recorded
            # 2. Token type changed
            # 3. Current token is longer than 1 char (we added to existing word)
            # 4. Current token length = prev + 1 (we're extending the same word)
            # 5. Not suppressed (to avoid conflicts with readline)
            should_retroactive_recolor = (
                prev_token_type is not None
                and enhanced_token_type != prev_token_type
                and current_token_len > 1
                and current_token_len == prev_token_len + 1
                and not self._suppress_shell_input_highlighting
            )

            self._prev_shell_input_token_type = enhanced_token_type
            self._prev_shell_input_token_len = current_token_len

            # Get the ANSI code for this token type from style_string
            # style_string is a dict mapping "Token.Type.Name" -> (start_ansi, end_ansi)
            if hasattr(formatter, "style_string"):
                # Use enhanced token type for better command/option coloring
                token_str = str(enhanced_token_type)
                style_codes = formatter.style_string.get(token_str)

                # Fallback to original Pygments token type if enhanced type has no style
                if not style_codes:
                    token_str = str(actual_token_type)
                    style_codes = formatter.style_string.get(token_str)

                if style_codes:
                    ansi_start, ansi_end = style_codes

                    if ansi_start:
                        # Check if we need retroactive recoloring
                        # This happens when the token type changed (e.g., 'i' was Name,
                        # now 'if' is Keyword) and we need to recolor the whole word
                        if should_retroactive_recolor and actual_token_value:
                            # DEBUG: Log retroactive recolor decision
                            self.logger.debug(f"[RETROACTIVE] Recoloring: token={repr(actual_token_value)}, len={current_token_len}, prev_len={prev_token_len}, buffer={repr(self._input_highlight_buffer)}")
                            # Move cursor back by (token_len - 1) positions to recolor
                            # the previously typed characters of this word
                            chars_to_recolor = current_token_len - 1
                            if chars_to_recolor > 0:
                                # CSI sequence to move cursor back: ESC[<n>D
                                cursor_back = f"\x1b[{chars_to_recolor}D"
                                # Recolor the entire token (including the new char)
                                highlighted_text = f"{cursor_back}{ansi_start}{actual_token_value}{ansi_end}"
                                term.feed(highlighted_text.encode("utf-8"))
                                return b""

                        # Normal case: just color the newly typed character
                        highlighted_text = f"{ansi_start}{text}{ansi_end}"
                        term.feed(highlighted_text.encode("utf-8"))
                        return b""

            # Fallback: output raw text
            term.feed(text.encode("utf-8"))
            return b""

        except Exception as e:
            self.logger.debug(f"Shell input highlighting failed: {e}")
            return None

    def _process_line_queue(self, term: Vte.Terminal) -> bool:
        """
        Process multiple lines from queue per callback for efficiency.

        This is the SINGLE consumer for the line queue. It processes
        a batch of lines per callback, balancing responsiveness with efficiency.

        Uses deque.popleft() for O(1) performance.

        Returns False to remove from idle queue.
        """
        if not self._running or self._widget_destroyed:
            self._queue_processing = False
            return False

        try:
            if self._line_queue:
                # Process up to 10 lines per callback for efficiency
                # This reduces GTK overhead while maintaining responsiveness
                lines_to_feed = []
                for _ in range(10):
                    if self._line_queue:
                        chunk = self._line_queue.popleft()
                        lines_to_feed.append(chunk)
                    else:
                        break

                # Feed all lines in one batch
                if lines_to_feed:
                    term.feed(b"".join(lines_to_feed))

                # Schedule next batch if queue not empty
                if self._line_queue:
                    GLib.idle_add(self._process_line_queue, term)
                else:
                    self._queue_processing = False
            else:
                self._queue_processing = False

        except Exception:
            self._queue_processing = False

        return False  # Remove this callback
