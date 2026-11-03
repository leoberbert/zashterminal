# zashterminal/ui/window_search_broadcast.py
"""Search bar and broadcast-to-terminals helpers for the main window."""

from __future__ import annotations

import re
from typing import List, Optional

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

        self.search_all_tabs_switch.connect(
            "notify::active", self._on_search_all_tabs_changed
        )
        self.search_all_tabs_switch.set_active(
            self.settings_manager.get("search_all_tabs", False)
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
        if self.terminal_search_entry.get_text():
            self._on_search_text_changed(self.terminal_search_entry)

    def _on_regex_changed(self, switch, param):
        """Handle regex switch changes."""
        use_regex = switch.get_active()
        self.settings_manager.set("search_use_regex", use_regex)
        if self.terminal_search_entry.get_text():
            self._on_search_text_changed(self.terminal_search_entry)

    def _on_search_all_tabs_changed(self, switch, param):
        self.settings_manager.set("search_all_tabs", switch.get_active())
        if self.terminal_search_entry.get_text():
            self._on_search_text_changed(self.terminal_search_entry)

    def _search_all_tabs_enabled(self) -> bool:
        return bool(self.settings_manager.get("search_all_tabs", False))

    def _build_search_regex(self, text: str) -> Optional[Vte.Regex]:
        pcre2_flags = 0x00000400  # PCRE2_MULTILINE
        if not self.settings_manager.get("search_case_sensitive", False):
            pcre2_flags |= 0x00000008  # PCRE2_CASELESS
        pattern = text if self.settings_manager.get("search_use_regex", False) else re.escape(text)
        try:
            return Vte.Regex.new_for_search(pattern, -1, pcre2_flags)
        except GLib.Error as exc:
            self.logger.error(
                f"Invalid regex for search pattern '{text}': {exc.message}",
                exc_info=True,
            )
            self.toast_overlay.add_toast(Adw.Toast(title=_("Invalid search pattern.")))
            return None

    def _clear_search_on_terminals(self, terminals: List[Vte.Terminal]) -> None:
        for terminal in terminals:
            try:
                terminal.search_set_regex(None, 0)
            except Exception:
                pass

    def _terminal_has_match(self, terminal: Vte.Terminal, regex: Vte.Regex) -> bool:
        """Apply regex and return True if at least one match exists."""
        try:
            terminal.search_set_regex(None, 0)
            terminal.search_set_regex(regex, 0)
            v_adjustment = terminal.get_vadjustment()
            if v_adjustment:
                v_adjustment.set_value(0.0)
            return bool(terminal.search_find_next())
        except Exception as exc:
            self.logger.debug(f"Error probing terminal for search match: {exc}")
            return False

    def _update_search_occurrence_display(self):
        """Update the search occurrence / scope counter display."""
        if not self.search_active:
            self.search_occurrence_label.set_text("")
            return

        if self._search_all_tabs_enabled() and self._global_search_terminals:
            terminal = None
            if 0 <= self._global_search_index < len(self._global_search_terminals):
                terminal = self._global_search_terminals[self._global_search_index]
            name = self._get_terminal_display_name(terminal) if terminal else ""
            total = len(self._global_search_terminals)
            index = self._global_search_index + 1
            if name:
                text = _("{name} · {index}/{total}").format(
                    name=name, index=index, total=total
                )
            else:
                text = f"{index}/{total}"
            self.search_occurrence_label.set_text(text)
            return

        if self.search_current_occurrence > 0:
            self.search_occurrence_label.set_text(str(self.search_current_occurrence))
        else:
            self.search_occurrence_label.set_text("")

    def _hide_search_if_terminal_changed(self):
        """Hide single-tab search when the focused terminal changes."""
        if self._search_all_tabs_enabled():
            return
        current_terminal = self.tab_manager.get_selected_terminal()
        if self.search_active and current_terminal != self.current_search_terminal:
            self.search_bar.set_search_mode(False)
            self.search_active = False
            self.current_search_terminal = None
            self._update_search_occurrence_display()

    def _on_search_text_changed(self, search_entry):
        """Handle search text changes immediately."""
        text = search_entry.get_text()

        if not text:
            self._clear_search_on_terminals(
                self.tab_manager.get_all_terminals_across_tabs()
            )
            self.search_active = False
            self.current_search_terminal = None
            self.search_current_occurrence = 0
            self._global_search_terminals = []
            self._global_search_index = 0
            self._update_search_occurrence_display()
            return

        self._perform_search(text)

    def _search_from_beginning(self, terminal, regex):
        """Search from the beginning of the terminal content."""
        try:
            found = terminal.search_find_next()
            if found:
                v_adjustment = terminal.get_vadjustment()
                if v_adjustment:
                    _match_col, match_row = terminal.get_cursor_position()
                    v_adjustment.set_value(max(0, match_row - 5))
            return found
        except Exception as e:
            self.logger.debug(f"Error searching from beginning: {e}")
            return False

    def _perform_search(self, text):
        """Perform search for the given text (current tab or all tabs)."""
        regex = self._build_search_regex(text)
        if not regex:
            return

        if self._search_all_tabs_enabled():
            self._perform_global_search(regex)
            return

        terminal = self.tab_manager.get_selected_terminal()
        if not terminal:
            self.logger.warning("Search triggered but no active terminal found.")
            self.toast_overlay.add_toast(
                Adw.Toast(title=_("No active terminal to search in."))
            )
            return

        self.current_search_terminal = terminal
        self.search_active = True
        self._global_search_terminals = []
        self._global_search_index = 0

        try:
            terminal.search_set_regex(None, 0)
            terminal.search_set_regex(regex, 0)

            found = terminal.search_find_next()
            if not found:
                try:
                    v_adjustment = terminal.get_vadjustment()
                    if v_adjustment:
                        v_adjustment.set_value(0.0)
                        found = self._search_from_beginning(terminal, regex)
                except Exception as e:
                    self.logger.debug(f"Error during scroll-based search: {e}")
                    found = terminal.search_find_next()

            self.search_current_occurrence = 1 if found else 0
            self._update_search_occurrence_display()
        except GLib.Error as e:
            self.logger.error(
                f"Invalid regex for search pattern '{text}': {e.message}", exc_info=True
            )
            self.toast_overlay.add_toast(Adw.Toast(title=_("Invalid search pattern.")))
            terminal.search_set_regex(None, 0)

    def _perform_global_search(self, regex: Vte.Regex) -> None:
        """Scan every open terminal; Next/Previous walks matching panes."""
        all_terminals = self.tab_manager.get_all_terminals_across_tabs()
        if not all_terminals:
            self.toast_overlay.add_toast(Adw.Toast(title=_("No open terminals found.")))
            return

        matching: List[Vte.Terminal] = []
        for terminal in all_terminals:
            if self._terminal_has_match(terminal, regex):
                matching.append(terminal)

        self._global_search_terminals = matching
        self._global_search_index = 0
        self.search_active = True
        self.search_current_occurrence = 1 if matching else 0

        if not matching:
            self.current_search_terminal = None
            self._update_search_occurrence_display()
            self.toast_overlay.add_toast(Adw.Toast(title=_("No matches found.")))
            return

        # Keep highlights on all matching terminals; jump to the first hit.
        first = matching[0]
        self.current_search_terminal = first
        self.tab_manager.focus_terminal(first)
        self._update_search_occurrence_display()

    def _on_search_next(self, button=None):
        if not self.search_active:
            return

        if self._search_all_tabs_enabled():
            self._global_search_step(forward=True)
            return

        terminal = self.tab_manager.get_selected_terminal()
        if not terminal:
            return

        found = terminal.search_find_next()
        if not found:
            try:
                v_adjustment = terminal.get_vadjustment()
                if v_adjustment:
                    v_adjustment.set_value(0.0)
                    found = terminal.search_find_next()
                    if found:
                        self.search_current_occurrence = 1
            except Exception as e:
                self.logger.debug(f"Error during next search: {e}")
        elif found:
            self.search_current_occurrence += 1

        if not found:
            self.toast_overlay.add_toast(Adw.Toast(title=_("No more matches found.")))
        else:
            self._update_search_occurrence_display()

    def _on_search_previous(self, button=None):
        if not self.search_active:
            return

        if self._search_all_tabs_enabled():
            self._global_search_step(forward=False)
            return

        terminal = self.tab_manager.get_selected_terminal()
        if not terminal:
            return

        found = terminal.search_find_previous()
        if not found:
            try:
                v_adjustment = terminal.get_vadjustment()
                if v_adjustment:
                    v_adjustment.set_value(
                        v_adjustment.get_upper() - v_adjustment.get_page_size()
                    )
                    found = terminal.search_find_previous()
                    if found:
                        self.search_current_occurrence = 1
            except Exception as e:
                self.logger.debug(f"Error during previous search: {e}")
        elif found:
            if self.search_current_occurrence > 1:
                self.search_current_occurrence -= 1

        if not found:
            self.toast_overlay.add_toast(Adw.Toast(title=_("No more matches found.")))
        else:
            self._update_search_occurrence_display()

    def _global_search_step(self, *, forward: bool) -> None:
        terminals = self._global_search_terminals
        if not terminals:
            self.toast_overlay.add_toast(Adw.Toast(title=_("No matches found.")))
            return

        current = self.tab_manager.get_selected_terminal()
        if current in terminals:
            self._global_search_index = terminals.index(current)

        terminal = terminals[self._global_search_index]
        self.tab_manager.focus_terminal(terminal)

        found = (
            terminal.search_find_next() if forward else terminal.search_find_previous()
        )
        if found:
            self.current_search_terminal = terminal
            self._update_search_occurrence_display()
            return

        # Exhausted this pane — move to next/previous matching terminal (wrap).
        if forward:
            self._global_search_index = (self._global_search_index + 1) % len(terminals)
        else:
            self._global_search_index = (self._global_search_index - 1) % len(terminals)

        terminal = terminals[self._global_search_index]
        self.tab_manager.focus_terminal(terminal)
        self.current_search_terminal = terminal
        try:
            v_adjustment = terminal.get_vadjustment()
            if v_adjustment:
                if forward:
                    v_adjustment.set_value(0.0)
                    terminal.search_find_next()
                else:
                    v_adjustment.set_value(
                        v_adjustment.get_upper() - v_adjustment.get_page_size()
                    )
                    terminal.search_find_previous()
        except Exception as exc:
            self.logger.debug(f"Error wrapping global search: {exc}")
        self._update_search_occurrence_display()

    def _on_search_stop(self, search_entry):
        self._clear_search_on_terminals(
            self.tab_manager.get_all_terminals_across_tabs()
        )
        terminal = self.tab_manager.get_selected_terminal()
        if terminal:
            terminal.grab_focus()

        self.search_active = False
        self.current_search_terminal = None
        self.search_current_occurrence = 0
        self._global_search_terminals = []
        self._global_search_index = 0
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
