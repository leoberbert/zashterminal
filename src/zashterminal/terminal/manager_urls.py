# zashterminal/terminal/manager_urls.py
"""URL/hyperlink detection, click handling, and command-line analysis."""

import re
from typing import Optional
from urllib.parse import urlparse

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import Gdk, Gtk, Vte

from ..helpers import is_valid_url
from ..settings.config import PROMPT_TERMINATOR_PATTERN
from ._lazy import (
    ANSI_ESCAPE_PATTERN as _ANSI_ESCAPE_PATTERN,
    get_highlight_manager as _get_highlight_manager,
    get_output_highlighter as _get_output_highlighter,
)


class TerminalUrlMixin:
    def _setup_url_patterns(self, terminal: Vte.Terminal) -> None:
        try:
            terminal.set_allow_hyperlink(True)
            if hasattr(terminal, "connect"):
                handler_id = terminal.connect(
                    "hyperlink-hover-uri-changed", self._on_hyperlink_hover_changed
                )
                if not hasattr(terminal, "zashterminal_handler_ids"):
                    terminal.zashterminal_handler_ids = []
                terminal.zashterminal_handler_ids.append(handler_id)

            url_patterns = [
                r"https?://[^\s<>()\"{}|\\^`\[\]]+[^\s<>()\"{}|\\^`\[\].,;:!?]",
                r"ftp://[^\s<>()\"{}|\\^`\[\]]+[^\s<>()\"{}|\\^`\[\].,;:!?]",
                r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
            ]

            patterns_added = 0
            if hasattr(terminal, "match_add_regex") and hasattr(Vte, "Regex"):
                self.logger.debug("Using Vte.Regex for URL pattern matching")
                vte_flags = 1024

                for pattern in url_patterns:
                    try:
                        regex = Vte.Regex.new_for_match(pattern, -1, vte_flags)
                        if regex:
                            tag = terminal.match_add_regex(regex, 0)
                            if hasattr(terminal, "match_set_cursor_name"):
                                terminal.match_set_cursor_name(tag, "pointer")
                            patterns_added += 1
                    except Exception as e:
                        self.logger.warning(
                            f"Vte.Regex pattern '{pattern}' failed: {e}"
                        )

            if patterns_added > 0:
                self.logger.info(
                    f"URL pattern detection configured ({patterns_added} patterns)"
                )
            else:
                self.logger.error(
                    "Failed to configure URL patterns - URL clicking disabled"
                )

        except Exception as e:
            self.logger.error(f"Failed to setup URL patterns: {e}")

    def _on_hyperlink_hover_changed(self, terminal, uri, _bbox):
        try:
            terminal_id = getattr(terminal, "terminal_id", None)
            if terminal_id is not None:
                if uri:
                    terminal._osc8_hovered_uri = uri
                    self.logger.debug(
                        f"OSC8 hyperlink hovered in terminal {terminal_id}: {uri}"
                    )
                else:
                    if hasattr(terminal, "_osc8_hovered_uri"):
                        delattr(terminal, "_osc8_hovered_uri")
                    self.logger.debug(
                        f"OSC8 hyperlink hover cleared in terminal {terminal_id}"
                    )
        except Exception as e:
            self.logger.error(f"OSC8 hyperlink hover handling failed: {e}")

    def _on_terminal_clicked(self, gesture, _n_press, x, y, terminal, terminal_id):
        try:
            modifiers = gesture.get_current_event_state()
            ctrl_pressed = bool(modifiers & Gdk.ModifierType.CONTROL_MASK)

            if ctrl_pressed:
                url_to_open = self._get_url_at_position(terminal, x, y)
                if url_to_open:
                    success = self._open_hyperlink(url_to_open)
                    if success:
                        self.logger.info(f"URL opened from Ctrl+click: {url_to_open}")
                        return Gdk.EVENT_STOP

            terminal.grab_focus()
            self.registry.update_terminal_status(terminal_id, "focused")
            if self.on_terminal_focus_changed:
                self.on_terminal_focus_changed(terminal, False)

            return Gdk.EVENT_PROPAGATE

        except Exception as e:
            self.logger.error(
                f"Terminal click handling failed for terminal {terminal_id}: {e}"
            )
            return Gdk.EVENT_PROPAGATE

    def _on_terminal_right_clicked(
        self, gesture, _n_press, x, y, terminal, terminal_id
    ):
        try:
            self._update_context_menu_with_url(terminal, x, y)
            return Gdk.EVENT_PROPAGATE
        except Exception as e:
            self.logger.error(
                f"Terminal right-click handling failed for terminal {terminal_id}: {e}"
            )
            return Gdk.EVENT_PROPAGATE

    def _on_terminal_key_pressed_for_detection(
        self,
        controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        state: Gdk.ModifierType,
        terminal: Vte.Terminal,
        terminal_id: int,
    ) -> bool:
        """
        Handle key press events to detect command execution via screen scraping.

        This method intercepts the Enter key press (before VTE processes it),
        reads the current line from the terminal using VTE's text extraction,
        and analyzes it to detect the command being executed.

        This approach works reliably for SSH sessions and Docker containers
        where process sniffing is impossible.

        Args:
            controller: The key event controller.
            keyval: The key value (GDK key constant).
            keycode: The hardware keycode.
            state: Modifier state (Shift, Ctrl, etc.).
            terminal: The VTE terminal widget.
            terminal_id: The terminal's registry ID.

        Returns:
            Gdk.EVENT_PROPAGATE to allow VTE to process the key normally.
        """
        try:
            # Only trigger on Enter or KP_Enter, ignore if modifiers are pressed
            if keyval not in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
                return Gdk.EVENT_PROPAGATE

            # Ignore if Shift, Ctrl, or Alt is pressed (might be a different action)
            if state & (
                Gdk.ModifierType.SHIFT_MASK
                | Gdk.ModifierType.CONTROL_MASK
                | Gdk.ModifierType.ALT_MASK
            ):
                return Gdk.EVENT_PROPAGATE

            # Get cursor position to read the current line
            col, row = terminal.get_cursor_position()

            # Extract the text of the current line using get_text_range_format
            # This is the modern VTE API that doesn't use deprecated callbacks
            # Signature: get_text_range_format(format, start_row, start_col, end_row, end_col)
            # Returns: tuple[Optional[str], int] - (text, length)
            try:
                # Get column count for the full line width
                col_count = terminal.get_column_count()

                # Use Vte.Format.TEXT for plain text extraction
                text_result = terminal.get_text_range_format(
                    Vte.Format.TEXT, row, 0, row, col_count
                )

                # get_text_range_format returns (text, length)
                if isinstance(text_result, tuple) and len(text_result) >= 1:
                    line_text = text_result[0] if text_result[0] else ""
                else:
                    line_text = ""

            except Exception as e:
                self.logger.debug(
                    f"get_text_range_format failed for terminal {terminal_id}: {e}"
                )
                return Gdk.EVENT_PROPAGATE

            # Strip whitespace and analyze the command
            line_text = line_text.strip() if line_text else ""
            if line_text:
                self._analyze_command_from_line(line_text, terminal, terminal_id)

        except Exception as e:
            self.logger.error(
                f"Key press detection failed for terminal {terminal_id}: {e}"
            )

        # CRUCIAL: Always propagate the event so VTE processes the newline
        return Gdk.EVENT_PROPAGATE

    def _analyze_command_from_line(
        self, line: str, terminal: Vte.Terminal, terminal_id: int
    ) -> None:
        """
        Analyze a terminal line to extract and set the command context.

        Uses a priority-based detection system:
        1. Ignored commands (tools with native coloring): set context and disable highlighting
        2. Known triggers (from HighlightManager): set context for command-specific rules
        3. Fallback: use first valid non-flag token

        This parses the raw line to separate the shell prompt from the user's
        command using common prompt terminators ($, #, %, >, ➜).

        Args:
            line: The raw line text from the terminal.
            terminal: The VTE terminal widget.
            terminal_id: The terminal's registry ID.
        """
        try:
            if not line:
                return

            # Strip ANSI escape sequences and terminal control codes from the line
            # This handles cases where cursor movement codes get mixed in (e.g., [K, [[[ )
            clean_line = _ANSI_ESCAPE_PATTERN.sub("", line)

            # Find the last occurrence of a prompt separator
            # The pattern matches: $ # % > ➜ followed by a space
            matches = list(PROMPT_TERMINATOR_PATTERN.finditer(clean_line))

            if matches:
                # Get the last match - everything after it is the command
                last_match = matches[-1]
                command_part = clean_line[last_match.end() :].strip()
            else:
                # No prompt found - the whole line might be the command
                # (e.g., if prompt was on a previous line or uses unusual format)
                command_part = clean_line.strip()

            if not command_part:
                return

            # Handle cases where shell keywords might be glued to the start of command_part
            # This can happen when readline merges lines (e.g., "thenecho" from continuation prompt)
            GLUED_KEYWORDS = {
                "then",
                "else",
                "elif",
                "fi",
                "do",
                "done",
                "esac",
                "in",
            }
            command_part_lower = command_part.lower()
            for kw in GLUED_KEYWORDS:
                if command_part_lower.startswith(kw) and len(command_part_lower) > len(
                    kw
                ):
                    # Check if the character after the keyword is alphanumeric (glued)
                    char_after = command_part_lower[len(kw)]
                    if char_after.isalpha():
                        # Remove the keyword prefix to get the actual command
                        command_part = command_part[len(kw) :]
                        break

            # Get settings and highlight manager
            from ..settings.manager import get_settings_manager

            settings_manager = get_settings_manager()
            highlight_manager = _get_highlight_manager()

            ignored_commands = set(
                settings_manager.get("ignored_highlight_commands", [])
            )
            known_triggers = highlight_manager.get_all_triggers()

            # For pipelines, analyze the LAST command in the chain since
            # that's what produces the visible output
            # Split by | and ; and && and || to find pipeline segments
            pipeline_parts = []
            current_part = []
            for char in command_part:
                if char in "|;&":
                    if current_part:
                        pipeline_parts.append("".join(current_part).strip())
                        current_part = []
                else:
                    current_part.append(char)
            if current_part:
                pipeline_parts.append("".join(current_part).strip())

            # Get the last non-empty part of the pipeline
            last_command_part = ""
            for part in reversed(pipeline_parts):
                if part:
                    last_command_part = part
                    break

            if not last_command_part:
                last_command_part = command_part

            # Parse tokens from the last pipeline segment
            tokens = last_command_part.split()
            detected_command = None
            fallback_command = None

            # Prefix commands that should be skipped to find the real command
            # These take another command as their argument
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
                "command",
                "builtin",
                "exec",
            }

            # Shell keywords that should be skipped (not actual commands)
            # These are control flow constructs that appear in multi-line scripts
            SHELL_KEYWORDS = {
                "if", "then", "else", "elif", "fi",
                "for", "do", "done",
                "while", "until",
                "case", "esac",
                "select", "in",
                "function",
                "{", "}",
                "[[", "]]",
                "(", ")",
            }

            for token in tokens:
                # Skip flags (start with -)
                if token.startswith("-"):
                    continue

                # Skip variable assignments (contain = without being a path)
                if "=" in token and "/" not in token:
                    continue

                # Clean the token: remove path prefixes and leading dots
                clean_token = token
                if "/" in clean_token:
                    clean_token = clean_token.split("/")[-1]
                clean_token = clean_token.lstrip(".")

                if not clean_token:
                    continue

                clean_token_lower = clean_token.lower()

                # Skip prefix commands (sudo, time, env, etc.) to find the real command
                if clean_token_lower in PREFIX_COMMANDS:
                    continue

                # Skip shell keywords (if, then, else, for, do, etc.)
                # These are control flow constructs, not actual commands
                if clean_token_lower in SHELL_KEYWORDS:
                    continue

                # Check for shell keywords concatenated at the start of token
                # This handles cases like "thenecho" where readline may have merged text
                for kw in SHELL_KEYWORDS:
                    if clean_token_lower.startswith(kw) and len(
                        clean_token_lower
                    ) > len(kw):
                        # Extract the part after the keyword
                        remainder = clean_token[len(kw) :]
                        if remainder and remainder[0].isalpha():
                            # This looks like a concatenated keyword + command
                            clean_token = remainder
                            clean_token_lower = remainder.lower()
                            break

                # After extracting from concatenated token, re-check if it's still a keyword
                if clean_token_lower in SHELL_KEYWORDS:
                    continue

                # Priority 1: Check if it's an ignored command (native coloring)
                if clean_token_lower in ignored_commands:
                    detected_command = clean_token
                    break

                # Priority 2: Check if it's a known trigger
                if clean_token_lower in known_triggers:
                    detected_command = clean_token
                    break

                # Save first valid token as fallback
                if fallback_command is None:
                    fallback_command = clean_token

            # Use detected command or fallback
            program_name = detected_command or fallback_command

            if not program_name:
                return

            # Check if this is a help command (--help, -h, help builtin, man)
            # If so, set context to "help" for help output highlighting
            command_tokens = command_part.lower().split()
            is_help_command = False

            # Check for --help or -h flags anywhere in the command
            if "--help" in command_tokens or "-h" in command_tokens:
                is_help_command = True
            # Check if first token is "help" (bash builtin) or "man"
            elif command_tokens and command_tokens[0] in ("help", "man"):
                is_help_command = True

            # Update the syntax highlighting context
            # Pass the full command (command_part) so cat can extract the filename
            highlighter = _get_output_highlighter()

            if is_help_command:
                # For help output, use the "help" context for highlighting
                highlighter.set_context("help", terminal_id, full_command=command_part)
                self.logger.debug(
                    f"Terminal {terminal_id}: help command detected, using 'help' context for: {clean_line[:50]}..."
                )
            else:
                highlighter.set_context(
                    program_name, terminal_id, full_command=command_part
                )
                self.logger.debug(
                    f"Terminal {terminal_id}: detected command '{program_name}' from line: {clean_line[:50]}..."
                )

        except Exception as e:
            self.logger.error(
                f"Command analysis failed for terminal {terminal_id}: {e}"
            )

    def _get_url_at_position(
        self, terminal: Vte.Terminal, x: float, y: float
    ) -> Optional[str]:
        try:
            if hasattr(terminal, "_osc8_hovered_uri") and terminal._osc8_hovered_uri:
                return terminal._osc8_hovered_uri

            if hasattr(terminal, "get_hyperlink_hover_uri"):
                try:
                    hover_uri = terminal.get_hyperlink_hover_uri()
                    if hover_uri:
                        return hover_uri
                except Exception as e:
                    self.logger.debug(f"VTE hyperlink detection failed: {e}")

            if hasattr(terminal, "match_check"):
                try:
                    char_width = terminal.get_char_width()
                    char_height = terminal.get_char_height()

                    if char_width > 0 and char_height > 0:
                        col = int(x / char_width)
                        row = int(y / char_height)

                        match_result = terminal.match_check(col, row)

                        if match_result and len(match_result) >= 2:
                            matched_text = match_result[0]
                            if matched_text and is_valid_url(matched_text):
                                return matched_text
                except Exception as e:
                    self.logger.debug(f"Regex match check failed: {e}")

            return None

        except Exception as e:
            self.logger.error(f"URL detection at position failed: {e}")
            return None

    def _open_hyperlink(self, uri: str) -> bool:
        try:
            if not uri or not uri.strip():
                self.logger.warning("Empty or invalid URI provided")
                return False

            uri = uri.strip()

            # Check if it looks like an email without mailto: prefix
            if (
                "@" in uri
                and not uri.startswith(("http://", "https://", "ftp://", "mailto:"))
                and "." in uri.split("@")[-1]
            ):
                uri = f"mailto:{uri}"

            try:
                parsed = urlparse(uri)
                if not parsed.scheme:
                    self.logger.warning(f"URI missing scheme: {uri}")
                    return False
            except Exception as e:
                self.logger.warning(f"Invalid URI format: {uri} - {e}")
                return False

            self.logger.info(f"Opening hyperlink: {uri}")

            subprocess.run(["xdg-open", uri], check=True, timeout=10)
            return True

        except subprocess.TimeoutExpired:
            self.logger.error(f"Timeout opening hyperlink: {uri}")
            return False
        except Exception as e:
            self.logger.error(f"Failed to open hyperlink '{uri}': {e}")
            return False
