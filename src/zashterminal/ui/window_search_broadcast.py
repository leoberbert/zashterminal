# zashterminal/ui/window_search_broadcast.py
"""Search bar and broadcast-to-terminals helpers for the main window."""

from __future__ import annotations

from typing import List

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import Adw, Gdk, GLib, GObject, Gtk, Vte

from ..sessions.models import SessionItem
from ..utils.syntax_utils import get_bash_pango_markup
from ..utils.translation_utils import _


class WindowSearchBroadcastMixin:
    def _setup_search(self) -> None:
        """Connects signals for the terminal search UI."""
        self.search_button.bind_property(
            "active",
            self.search_bar,
            "search-mode-enabled",
            GObject.BindingFlags.BIDIRECTIONAL,
        )
        # Connect the correct search entry to the correct handler
        self.terminal_search_entry.connect(
            "search-changed", self._on_search_text_changed
        )
        self.terminal_search_entry.connect("stop-search", self._on_search_stop)
        self.terminal_search_entry.connect("activate", self._on_search_next)
        self.search_prev_button.connect("clicked", self._on_search_previous)
        self.search_next_button.connect("clicked", self._on_search_next)
        self.search_bar.connect(
            "notify::search-mode-enabled", self._on_search_mode_changed
        )
        # Connect case sensitive switch
        self.case_sensitive_switch.connect(
            "notify::active", self._on_case_sensitive_changed
        )
        # Initialize switch state from settings
        self.case_sensitive_switch.set_active(
            self.settings_manager.get("search_case_sensitive", False)
        )

        # Connect regex switch
        self.regex_switch.connect("notify::active", self._on_regex_changed)
        # Initialize switch state from settings
        self.regex_switch.set_active(
            self.settings_manager.get("search_use_regex", False)
        )

    def _setup_broadcast(self) -> None:
        """Connects signals for the command broadcast UI."""
        self.broadcast_button.bind_property(
            "active",
            self.broadcast_bar,
            "search-mode-enabled",
            GObject.BindingFlags.BIDIRECTIONAL,
        )
        self.broadcast_entry.connect("activate", self._on_broadcast_activate)
        self.broadcast_bar.connect(
            "notify::search-mode-enabled", self._on_broadcast_mode_changed
        )
        key_controller = Gtk.EventControllerKey.new()
        key_controller.connect("key-pressed", self._on_broadcast_key_pressed)
        self.broadcast_entry.add_controller(key_controller)

    def _on_search_mode_changed(self, search_bar, param):
        if search_bar.get_search_mode():
            self.terminal_search_entry.grab_focus()
            # Set current terminal when search mode is enabled
            current_terminal = self.tab_manager.get_selected_terminal()
            if current_terminal:
                self.current_search_terminal = current_terminal
                self.search_active = True
        else:
            self._on_search_stop(self.terminal_search_entry)

    def _on_broadcast_mode_changed(self, broadcast_bar, param):
        if broadcast_bar.get_search_mode():
            self.broadcast_entry.grab_focus()
        else:
            if terminal := self.tab_manager.get_selected_terminal():
                terminal.grab_focus()

    def _on_broadcast_key_pressed(self, _controller, keyval, _keycode, _state):
        if keyval == Gdk.KEY_Escape:
            self.broadcast_bar.set_search_mode(False)
            return True
        return False

    def _get_terminal_display_name(self, terminal: Vte.Terminal) -> str:
        page = self.tab_manager.get_page_for_terminal(terminal)
        if page and page.get_title():
            return page.get_title()

        terminal_id = getattr(terminal, "terminal_id", None)
        if terminal_id is not None:
            terminal_info = self.terminal_manager.registry.get_terminal_info(
                terminal_id
            )
            if terminal_info:
                identifier = terminal_info.get("identifier")
                if isinstance(identifier, SessionItem):
                    return identifier.name
                if isinstance(identifier, str):
                    return identifier

        return _("Terminal {id}").format(
            id=terminal_id if terminal_id is not None else "?"
        )

    def _on_broadcast_activate(self, entry: Gtk.Entry):
        """
        Shows a confirmation dialog before broadcasting the command to all terminals.
        """
        command = entry.get_text().strip()
        if not command:
            return

        all_terminals = self.tab_manager.get_all_terminals_across_tabs()
        if not all_terminals:
            self.toast_overlay.add_toast(Adw.Toast(title=_("No open terminals found.")))
            return

        remember_choice = self._broadcast_remember_choice
        last_selection_keys = self._broadcast_last_selection

        if remember_choice and last_selection_keys:
            selected_terminals = []
            for terminal in all_terminals:
                key = self._make_broadcast_terminal_key(terminal)
                if key in last_selection_keys:
                    selected_terminals.append(terminal)

            if not selected_terminals:
                selected_terminals = all_terminals

            self._execute_broadcast(command, selected_terminals)
            entry.set_text("")
            self.broadcast_bar.set_search_mode(False)
            return

        count = len(all_terminals)
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Confirm sending of command"),
            body=_(
                "Select which of the <b>{count}</b> open terminals should receive the command below."
            ).format(count=count),
            body_use_markup=True,
            close_response="cancel",
        )

        # Display the command for the user to review with syntax highlighting
        highlighted_cmd = get_bash_pango_markup(command)
        command_label = Gtk.Label(
            label=f"<tt>{highlighted_cmd}</tt>",
            use_markup=True,
            css_classes=["card"],
            halign=Gtk.Align.CENTER,
            margin_start=8,
            margin_end=8,
            margin_top=6,
            margin_bottom=6,
        )

        instructions_label = Gtk.Label(
            label=_("Choose the tabs that should run this command:"),
            halign=Gtk.Align.START,
            margin_top=6,
        )
        instructions_label.set_wrap(True)

        flow_box = Gtk.FlowBox()
        flow_box.set_selection_mode(Gtk.SelectionMode.NONE)
        flow_box.set_row_spacing(6)
        flow_box.set_column_spacing(12)
        max_columns = 3
        columns = max(1, min(max_columns, len(all_terminals)))
        flow_box.set_min_children_per_line(columns)
        flow_box.set_max_children_per_line(max_columns)

        selection_controls = []
        for terminal in all_terminals:
            display_title = self._get_terminal_display_name(terminal)
            check_button = Gtk.CheckButton(label=display_title)
            check_button.set_active(True)
            check_button.set_halign(Gtk.Align.START)
            flow_box.insert(check_button, -1)
            selection_controls.append((terminal, check_button))

        if len(selection_controls) > 6:
            scrolled = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
            scrolled.set_min_content_height(200)
            scrolled.set_child(flow_box)
            selection_container = scrolled
        else:
            selection_container = flow_box

        remember_check = Gtk.CheckButton(label=_("Remember my choice"))
        remember_check.set_active(remember_choice)
        remember_check.set_halign(Gtk.Align.START)

        content_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )
        content_box.append(command_label)
        content_box.append(instructions_label)
        content_box.append(selection_container)
        content_box.append(remember_check)
        dialog.set_extra_child(content_box)

        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("send", _("Send Command"))
        dialog.set_default_response("send")
        dialog.set_response_appearance("send", Adw.ResponseAppearance.SUGGESTED)

        dialog.connect(
            "response",
            self._on_broadcast_confirm,
            command,
            selection_controls,
            remember_check,
        )
        dialog.present()

    def _on_broadcast_confirm(
        self,
        dialog,
        response_id: str,
        command: str,
        selection_controls: list,
        remember_check: Gtk.CheckButton,
    ):
        """
        Callback that executes the broadcast if the user confirms.
        """
        if response_id == "send":
            selected_terminals = [
                terminal
                for terminal, control in selection_controls
                if control.get_active()
            ]

            remember = remember_check.get_active()
            self._broadcast_remember_choice = remember
            if remember:
                self._broadcast_last_selection = [
                    self._make_broadcast_terminal_key(t) for t in selected_terminals
                ]
            else:
                self._broadcast_last_selection = []

            if not selected_terminals:
                self.toast_overlay.add_toast(
                    Adw.Toast(title=_("No tabs were selected to receive the command."))
                )
                dialog.close()
                self.broadcast_entry.set_text("")
                self.broadcast_bar.set_search_mode(False)
                return

            self._execute_broadcast(command, selected_terminals)

        dialog.close()

        # Clear and hide the bar regardless of the response
        self.broadcast_entry.set_text("")
        self.broadcast_bar.set_search_mode(False)

    def _execute_broadcast(self, command: str, terminals: List[Vte.Terminal]) -> None:
        command_bytes = command.encode("utf-8") + b"\n"
        for terminal in terminals:
            terminal.feed_child(command_bytes)

        self.logger.info(
            f"Broadcasted command to {len(terminals)} terminals."
        )

    def _make_broadcast_terminal_key(self, terminal: Vte.Terminal) -> str:
        page = self.tab_manager.get_page_for_terminal(terminal)
        page_title = page.get_title() if page else ""
        terminal_id = getattr(terminal, "terminal_id", "")

        session = getattr(terminal, "zashterminal_session_item", None)
        if isinstance(session, SessionItem):
            base = f"{session.session_type}:{session.name}:{session.host}:{session.user}:{session.port}"
        else:
            base = page_title or str(terminal_id)
        return base

    def _on_case_sensitive_changed(self, switch, param):
        """Handle case sensitive switch changes."""
        case_sensitive = switch.get_active()
        self.settings_manager.set("search_case_sensitive", case_sensitive)
        # Re-trigger search if there's text in the search entry
        if self.terminal_search_entry.get_text():
            self._on_search_text_changed(self.terminal_search_entry)

    def _on_regex_changed(self, switch, param):
        """Handle regex switch changes."""
        use_regex = switch.get_active()
        self.settings_manager.set("search_use_regex", use_regex)
        # Re-trigger search if there's text in the search entry
        if self.terminal_search_entry.get_text():
            self._on_search_text_changed(self.terminal_search_entry)

    def _update_search_occurrence_display(self):
        """Update the search occurrence counter display."""
        if self.search_active and self.search_current_occurrence > 0:
            text = f"{self.search_current_occurrence}"
        else:
            text = ""
        self.search_occurrence_label.set_text(text)

    def _hide_search_if_terminal_changed(self):
        """Hide search if the current terminal is different from when search was started."""
        current_terminal = self.tab_manager.get_selected_terminal()
        if self.search_active and current_terminal != self.current_search_terminal:
            self.search_bar.set_search_mode(False)
            self.search_active = False
            self.current_search_terminal = None
            self._update_search_occurrence_display()

    def _on_search_text_changed(self, search_entry):
        """Handle search text changes immediately."""
        text = search_entry.get_text()

        # If text is empty, clear search immediately
        if not text:
            terminal = self.tab_manager.get_selected_terminal()
            if terminal:
                terminal.search_set_regex(None, 0)
            self.search_active = False
            self.current_search_terminal = None
            self.search_current_occurrence = 0
            self._update_search_occurrence_display()
            return

        # Perform the search immediately
        self._perform_search(text)

    def _search_from_beginning(self, terminal, regex):
        """Search from the beginning of the terminal content."""
        try:
            # Try to find the first match from the beginning
            found = terminal.search_find_next()
            if found:
                # Scroll to show the first match
                v_adjustment = terminal.get_vadjustment()
                if v_adjustment:
                    # Get the position of the match and scroll to it
                    match_col, match_row = terminal.get_cursor_position()
                    # Scroll to show the match
                    v_adjustment.set_value(max(0, match_row - 5))  # Show some context
            return found
        except Exception as e:
            self.logger.debug(f"Error searching from beginning: {e}")
            return False

    def _perform_search(self, text):
        """Perform search immediately for the given text."""
        terminal = self.tab_manager.get_selected_terminal()
        if not terminal:
            self.logger.warning("Search triggered but no active terminal found.")
            self.toast_overlay.add_toast(
                Adw.Toast(title=_("No active terminal to search in."))
            )
            return

        # Update search state
        self.current_search_terminal = terminal
        self.search_active = True

        # First, clear any existing search to reset the state completely
        terminal.search_set_regex(None, 0)

        try:
            # Use Vte.Regex for search operations
            # PCRE2_MULTILINE (0x00000400) and optionally PCRE2_CASELESS (0x00000008)
            pcre2_flags = 0x00000400  # PCRE2_MULTILINE always enabled
            if not self.settings_manager.get("search_case_sensitive", False):
                pcre2_flags |= 0x00000008  # PCRE2_CASELESS for case insensitive

            use_regex = self.settings_manager.get("search_use_regex", False)
            search_text = text

            if not use_regex:
                # For literal search, escape regex special characters
                search_text = re.escape(text)

            # Always use new_for_search, but with escaped pattern for literal mode
            regex = Vte.Regex.new_for_search(search_text, -1, pcre2_flags)

            if not regex:
                self.logger.warning(
                    f"Failed to compile search regex for pattern: {text}"
                )
                self.toast_overlay.add_toast(
                    Adw.Toast(title=_("Invalid search pattern."))
                )
                return

            # Completely clear search state before setting new regex
            terminal.search_set_regex(None, 0)

            # Set the regex for search FIRST - establish working search
            terminal.search_set_regex(regex, 0)

            # More robust search: try multiple approaches to ensure consistency
            found = False

            # Method 1: Try from current position first
            current_col, current_row = terminal.get_cursor_position()
            found = terminal.search_find_next()

            # Method 2: If not found from current position, search from beginning of scrollback
            if not found:
                try:
                    # Save current scroll position
                    v_adjustment = terminal.get_vadjustment()
                    if v_adjustment:
                        # Scroll to the very beginning (including scrollback)
                        v_adjustment.set_value(0.0)

                        # Try searching from beginning immediately
                        found = self._search_from_beginning(terminal, regex)

                except Exception as e:
                    self.logger.debug(f"Error during scroll-based search: {e}")
                    # Fallback to just trying again from current position
                    found = terminal.search_find_next()

            # Count occurrences using the EXACT same regex object
            if found:
                # Just set current occurrence to 1 since we found at least one match
                self.search_current_occurrence = 1
                self._update_search_occurrence_display()
            else:
                self.search_current_occurrence = 0
                self._update_search_occurrence_display()

        except GLib.Error as e:
            self.logger.error(
                f"Invalid regex for search pattern '{text}': {e.message}", exc_info=True
            )
            self.toast_overlay.add_toast(Adw.Toast(title=_("Invalid search pattern.")))
            terminal.search_set_regex(None, 0)

    def _on_search_next(self, button=None):
        terminal = self.tab_manager.get_selected_terminal()
        if terminal and self.search_active:
            # Try to find next match
            found = terminal.search_find_next()

            # If no match found from current position, try wrapping to beginning
            if not found:
                try:
                    v_adjustment = terminal.get_vadjustment()
                    if v_adjustment:
                        # Scroll to the beginning and wrap around
                        v_adjustment.set_value(0.0)
                        found = terminal.search_find_next()

                        if found:
                            self.search_current_occurrence = 1  # Wrapped to first match
                except Exception as e:
                    self.logger.debug(f"Error during next search: {e}")
            elif found:
                # Increment current occurrence
                self.search_current_occurrence += 1

            if not found:
                self.toast_overlay.add_toast(
                    Adw.Toast(title=_("No more matches found."))
                )
            else:
                self._update_search_occurrence_display()

    def _on_search_previous(self, button=None):
        terminal = self.tab_manager.get_selected_terminal()
        if terminal and self.search_active:
            # Try to find previous match
            found = terminal.search_find_previous()

            # If no match found, try wrapping to end
            if not found:
                try:
                    v_adjustment = terminal.get_vadjustment()
                    if v_adjustment:
                        # Scroll to the end and wrap around
                        v_adjustment.set_value(
                            v_adjustment.get_upper() - v_adjustment.get_page_size()
                        )
                        found = terminal.search_find_previous()

                        if found:
                            self.search_current_occurrence = 1  # Wrapped to match
                except Exception as e:
                    self.logger.debug(f"Error during previous search: {e}")
            elif found:
                # Decrement current occurrence
                if self.search_current_occurrence > 1:
                    self.search_current_occurrence -= 1

            if not found:
                self.toast_overlay.add_toast(
                    Adw.Toast(title=_("No more matches found."))
                )
            else:
                self._update_search_occurrence_display()

    def _on_search_stop(self, search_entry):
        terminal = self.tab_manager.get_selected_terminal()
        if terminal:
            terminal.search_set_regex(None, 0)
            terminal.grab_focus()

        # Reset search state
        self.search_active = False
        self.current_search_terminal = None
        self.search_current_occurrence = 0
        self._update_search_occurrence_display()

    # --- Window Lifecycle and State ---

    def _broadcast_command_to_all(self, command_text: str):
        """Send a command to all open terminals."""
        all_terminals = self.tab_manager.get_all_terminals_across_tabs()
        if not all_terminals:
            self.toast_overlay.add_toast(Adw.Toast(title=_("No open terminals found.")))
            return

        command_bytes = command_text.encode("utf-8")
        # If command doesn't end with newline and doesn't contain one, just insert it
        if not command_text.endswith("\n"):
            # Use bracketed paste for insertion without execution
            for terminal in all_terminals:
                paste_data = b"\x1b[200~" + command_bytes + b"\x1b[201~"
                terminal.feed_child(paste_data)
        else:
            # Execute on all terminals
            for terminal in all_terminals:
                terminal.feed_child(command_bytes)

        self.logger.info(f"Broadcasted command to {len(all_terminals)} terminals.")
