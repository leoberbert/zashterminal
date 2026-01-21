# zashterminal/ui/sidebar_manager.py

from typing import TYPE_CHECKING, Any, List, Tuple

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk

from ..sessions.models import SessionFolder, SessionItem
from ..utils.icons import set_button_icon
from ..utils.logger import get_logger
from ..utils.tooltip_helper import get_tooltip_helper
from ..utils.translation_utils import _

if TYPE_CHECKING:
    from ..sessions.tree import SessionTreeView
    from ..settings.manager import SettingsManager
    from ..window import CommTerminalWindow
    from .window_ui import WindowUIBuilder


class SidebarManager:
    """
    Manages the state and interactions of the main window's sidebar,
    including the flap, popover for auto-hide mode, search, and sizing.
    """

    def __init__(self, window: "CommTerminalWindow", ui: "WindowUIBuilder"):
        self.window = window
        self.settings_manager: "SettingsManager" = window.settings_manager
        self.session_tree: "SessionTreeView" = window.session_tree
        self.logger = get_logger("zashterminal.ui.sidebar")

        # Get widgets from the UI builder
        self.flap = ui.flap
        self.sidebar_box = ui.sidebar_box
        self.sidebar_popover = ui.sidebar_popover
        self.toggle_sidebar_button = ui.toggle_sidebar_button
        self.search_entry = ui.sidebar_search_entry
        self.add_session_button = ui.add_session_button
        self.add_folder_button = ui.add_folder_button
        self.edit_button = ui.edit_button
        self.save_layout_button = ui.save_layout_button
        self.remove_button = ui.remove_button

        self._connect_signals()
        self.initialize_state()

    def _connect_signals(self):
        """Connects signals for all widgets managed by this class."""
        self.toggle_sidebar_button.connect("toggled", self._on_toggle_sidebar)
        self.sidebar_popover.connect("closed", self._on_sidebar_popover_closed)
        self.sidebar_popover.connect("show", self._on_sidebar_popover_show)

        popover_key_controller = Gtk.EventControllerKey.new()
        popover_key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        popover_key_controller.connect(
            "key-pressed", self._on_sidebar_popover_key_pressed
        )
        self.sidebar_popover.add_controller(popover_key_controller)

        self.flap.connect("notify::folded", self._on_sidebar_folded_changed)

        # MODIFIED: Connect the buttons to intermediate methods in this class
        self.add_session_button.connect("clicked", self._on_add_session_clicked)
        self.add_folder_button.connect("clicked", self._on_add_folder_clicked)
        self.edit_button.connect("clicked", self._on_edit_selected_clicked)
        self.save_layout_button.connect("clicked", self._on_save_layout_clicked)
        self.remove_button.connect("clicked", self._on_delete_selected_clicked)

        # Search
        self.search_entry.connect("search-changed", self._on_search_changed)
        search_key_controller = Gtk.EventControllerKey.new()
        search_key_controller.connect("key-pressed", self._on_search_key_pressed)
        self.search_entry.add_controller(search_key_controller)

        # Window resizing for popover
        self.window.connect("notify::default-width", self._on_window_size_changed)
        self.window.connect("notify::default-height", self._on_window_size_changed)

    # NEW: Callback methods that close the popover before calling the action
    def _close_popover_if_active(self):
        """Closes the popover if it's visible."""
        if self.sidebar_popover and self.sidebar_popover.get_visible():
            self.sidebar_popover.popdown()

    def _on_add_session_clicked(self, button):
        self._close_popover_if_active()
        self.window.action_handler.add_session_root(None, None)

    def _on_add_folder_clicked(self, button):
        self._close_popover_if_active()
        self.window.action_handler.add_folder_root(None, None)

    def _on_save_layout_clicked(self, button):
        self._close_popover_if_active()
        self.window.action_handler.save_layout(None, None)

    def _on_delete_selected_clicked(self, button):
        self._close_popover_if_active()
        self.window.action_handler.delete_selected_items(None, None)

    def _on_edit_selected_clicked(self, _button):
        """
        Determines the type of the selected item and calls the appropriate
        action handler method, closing the popover first.
        """
        self._close_popover_if_active()
        item = self.session_tree.get_selected_item()
        if isinstance(item, SessionItem):
            self.window.action_handler.edit_session()
        elif isinstance(item, SessionFolder):
            self.window.action_handler.edit_folder()

    def initialize_state(self):
        """Sets the initial state of the sidebar based on settings."""
        auto_hide = self.settings_manager.get("auto_hide_sidebar", False)
        self.handle_auto_hide_change(auto_hide, is_initial_setup=True)
        self.update_sidebar_sizes()

    def handle_auto_hide_change(
        self, auto_hide_enabled: bool, is_initial_setup: bool = False
    ):
        """Switches the sidebar between normal (flap) and auto-hide (popover) modes."""
        current_parent = self.sidebar_box.get_parent()

        # Safely unparent the sidebar_box from its current container
        if current_parent:
            if isinstance(current_parent, Adw.Flap):
                if current_parent.get_flap() == self.sidebar_box:
                    current_parent.set_flap(None)
            elif hasattr(current_parent, "set_child"):  # For Popover, Bin, etc.
                current_parent.set_child(None)
            elif hasattr(current_parent, "remove"):  # For Box, etc.
                current_parent.remove(self.sidebar_box)

        if auto_hide_enabled:
            # Now that it's unparented, set it as the child of the popover
            self.sidebar_popover.set_child(self.sidebar_box)
            self.flap.set_reveal_flap(False)
            if not is_initial_setup:
                self.sidebar_popover.popdown()
            self.toggle_sidebar_button.set_active(False)
        else:  # Normal mode
            # Now that it's unparented, set it as the flap widget
            self.flap.set_flap(self.sidebar_box)
            initial_visible = self.settings_manager.get_sidebar_visible()
            self.flap.set_reveal_flap(initial_visible)
            self.toggle_sidebar_button.set_active(initial_visible)
            if initial_visible:
                self._update_flap_size()

        self._update_sidebar_button_icon()

    def _on_toggle_sidebar(self, button: Gtk.ToggleButton) -> None:
        """Handles sidebar toggle button clicks for both modes."""
        auto_hide = self.settings_manager.get("auto_hide_sidebar", False)
        is_active = button.get_active()

        if auto_hide:
            if is_active:
                self._update_popover_size()
                if self.sidebar_popover.get_parent() is not None:
                    self.sidebar_popover.unparent()
                self.sidebar_popover.set_parent(button)
                self.sidebar_popover.popup()
                GLib.idle_add(self._focus_first_item)
            else:
                self.sidebar_popover.popdown()
        else:
            self.flap.set_reveal_flap(is_active)
            self.settings_manager.set_sidebar_visible(is_active)
            if is_active:
                self._update_flap_size()

        self._update_sidebar_button_icon()

    def _on_sidebar_popover_show(self, popover):
        self._update_popover_size()
        popover.set_can_focus(True)
        popover.grab_focus()

    def _on_sidebar_popover_closed(self, popover):
        self.toggle_sidebar_button.set_active(False)
        self._update_sidebar_button_icon()
        self.search_entry.set_text("")
        self.session_tree.clear_search()

        # Always transfer focus back to the terminal when popup closes
        self._transfer_focus_to_terminal()

    def _transfer_focus_to_terminal(self):
        """Transfer focus to the active terminal after popup closes."""
        try:
            # Get the active terminal from the tab manager
            if hasattr(self.window, "tab_manager") and self.window.tab_manager:
                active_terminal = self.window.tab_manager.get_selected_terminal()
                if active_terminal and active_terminal.get_realized():
                    # Use idle_add to ensure the UI is ready
                    def focus_terminal():
                        if active_terminal.get_can_focus():
                            active_terminal.grab_focus()
                            self.logger.debug(
                                "Focus transferred to terminal after popup close"
                            )
                        return False

                    GLib.idle_add(focus_terminal)
        except Exception as e:
            self.logger.error(f"Failed to transfer focus to terminal: {e}")

    def _on_sidebar_popover_key_pressed(self, _, keyval, _keycode, state) -> bool:
        # Always allow closing with the Escape key
        if keyval == Gdk.KEY_Escape:
            self.sidebar_popover.popdown()
            return Gdk.EVENT_STOP

        # Get the configured shortcut for "toggle-sidebar"
        shortcut_string = self.settings_manager.get_shortcut("toggle-sidebar")
        if not shortcut_string:
            return Gdk.EVENT_PROPAGATE

        # Parse the shortcut string to get the expected keyval and modifiers
        success, parsed_keyval, parsed_mods = Gtk.accelerator_parse(shortcut_string)

        if success:
            # Get the relevant modifiers from the current key event
            event_mods = state & Gtk.accelerator_get_default_mod_mask()

            # **THE FIX**: Convert the event's keyval to lowercase for comparison,
            # as Gtk.accelerator_parse normalizes the key.
            event_keyval_lower = Gdk.keyval_to_lower(keyval)

            # Compare the key and modifiers from the event with the configured shortcut
            if event_keyval_lower == parsed_keyval and event_mods == parsed_mods:
                self.sidebar_popover.popdown()
                return Gdk.EVENT_STOP

        return Gdk.EVENT_PROPAGATE

    def _on_window_size_changed(self, window, param):
        if self.sidebar_popover.get_visible():
            self._update_popover_size()

    def _on_sidebar_folded_changed(self, flap, param):
        self._update_sidebar_responsive_design()

    def _on_search_changed(self, search_entry: Gtk.SearchEntry) -> None:
        search_text = search_entry.get_text().lower()
        if not search_text:
            self.session_tree.clear_search()
        else:
            self.session_tree.set_filter_text(search_text)
        self.update_sidebar_sizes()

    def _on_search_key_pressed(self, _, keyval, *args) -> bool:
        if keyval == Gdk.KEY_Down:
            tree_widget = self.session_tree.get_widget()
            if tree_widget:
                tree_widget.grab_focus()
                # Select the first item
                if self.session_tree.filter_model.get_n_items() > 0:
                    self.session_tree.selection_model.select_item(0, True)
            return Gdk.EVENT_STOP
        elif keyval == Gdk.KEY_Up:
            tree_widget = self.session_tree.get_widget()
            if tree_widget:
                tree_widget.grab_focus()
                # Select the last item
                n_items = self.session_tree.filter_model.get_n_items()
                if n_items > 0:
                    self.session_tree.selection_model.select_item(n_items - 1, True)
            return Gdk.EVENT_STOP
        return Gdk.EVENT_PROPAGATE

    def _update_sidebar_button_icon(self) -> None:
        auto_hide = self.settings_manager.get("auto_hide_sidebar", False)
        if auto_hide:
            set_button_icon(self.toggle_sidebar_button, "computer-symbolic")
            get_tooltip_helper().add_tooltip(
                self.toggle_sidebar_button, _("Show Sessions")
            )
        else:
            is_visible = self.flap.get_reveal_flap()
            icon_name = (
                "sidebar-hide-symbolic" if is_visible else "sidebar-show-symbolic"
            )
            set_button_icon(self.toggle_sidebar_button, icon_name)
            tooltip_text = (
                _("Hide Sessions Panel") if is_visible else _("Show Sessions Panel")
            )
            get_tooltip_helper().add_tooltip(self.toggle_sidebar_button, tooltip_text)

    def _focus_first_item(self):
        """Focus on the first item in the session tree for keyboard navigation."""
        tree_widget = self.session_tree.get_widget()
        if tree_widget:
            tree_widget.grab_focus()
            # Select the first item if nothing is selected
            if self.session_tree.selection_model.get_selection().get_size() == 0:
                if self.session_tree.filter_model.get_n_items() > 0:
                    self.session_tree.selection_model.select_item(0, True)
        return False

    def update_sidebar_sizes(self):
        """Update sidebar sizes for both flap and popover modes."""
        auto_hide = self.settings_manager.get("auto_hide_sidebar", False)
        if auto_hide:
            if self.sidebar_popover.get_visible():
                self._update_popover_size()
        else:
            if self.flap.get_reveal_flap():
                self._update_flap_size()

    def _update_flap_size(self):
        natural_width = self._calculate_sidebar_natural_width()
        self.sidebar_box.set_size_request(natural_width, -1)
        self.logger.debug(f"Updated flap sidebar width to {natural_width}px")

    def _update_popover_size(self):
        sidebar_width = self._calculate_sidebar_natural_width()
        window_width = self.window.get_width()
        window_height = self.window.get_height()
        popover_width = min(sidebar_width, int(window_width * 0.8))
        popover_height = int(window_height * 0.9)
        self.sidebar_popover.set_size_request(popover_width, popover_height)

    def _update_sidebar_responsive_design(self):
        sidebar_width = self.sidebar_box.get_width()
        if sidebar_width == 0:
            sidebar_width = self._calculate_sidebar_natural_width()
        if sidebar_width < 260:
            self.sidebar_box.add_css_class("sidebar-compact")
        else:
            self.sidebar_box.remove_css_class("sidebar-compact")

    def _calculate_sidebar_natural_width(self) -> int:
        """Calculate the natural width required for the sidebar content."""
        min_width, max_width, padding = 220, 500, 32
        tree_widget = self.session_tree.get_widget()
        if not tree_widget:
            return 300

        tree_req = tree_widget.get_preferred_size()
        tree_width = (
            tree_req.natural_size.width
            if tree_req and tree_req.natural_size.width > 0
            else 250
        )
        content_width = self._measure_tree_content_width()
        tree_width = max(tree_width, content_width)

        content_width = max(tree_width, 200, 200) + padding  # toolbar, search
        return max(min_width, min(content_width, max_width))

    def _measure_tree_content_width(self) -> int:
        """Measure the width of the widest visible text content in the session tree."""
        temp_label = Gtk.Label(use_markup=False)
        temp_label.add_css_class("sidebar-session-tree")
        max_width = 0
        expanded_paths = set(self.settings_manager.get("tree_expanded_folders", []))
        visible_items = self._get_visible_tree_items(expanded_paths)

        for item, depth in visible_items:
            if hasattr(item, "name") and item.name:
                temp_label.set_text(item.name)
                req = temp_label.get_preferred_size()
                if req and req.natural_size.width > 0:
                    text_width = req.natural_size.width
                    total_width = (
                        text_width + 20 + (depth * 20) + 24
                    )  # icon, indent, margins
                    if total_width > max_width:
                        max_width = total_width
        return max(int(max_width), 180)

    def _get_visible_tree_items(self, expanded_paths: set) -> List[Tuple[Any, int]]:
        """Get all currently visible items in the tree with their indentation depth."""
        visible_items = []
        for item in list(self.window.session_store) + list(self.window.layouts):
            if not getattr(item, "folder_path", ""):
                visible_items.append((item, 0))
        for folder in self.window.folder_store:
            if not getattr(folder, "parent_path", ""):
                visible_items.append((folder, 0))
                if folder.path in expanded_paths:
                    visible_items.extend(
                        self._get_folder_children(folder.path, expanded_paths, 1)
                    )
        return visible_items

    def _get_folder_children(
        self, parent_path: str, expanded_paths: set, depth: int
    ) -> List[Tuple[Any, int]]:
        """Recursively get children of a folder at a given depth."""
        children = []
        for item in list(self.window.session_store) + list(self.window.layouts):
            if getattr(item, "folder_path", "") == parent_path:
                children.append((item, depth))
        for folder in self.window.folder_store:
            if getattr(folder, "parent_path", "") == parent_path:
                children.append((folder, depth))
                if folder.path in expanded_paths:
                    children.extend(
                        self._get_folder_children(
                            folder.path, expanded_paths, depth + 1
                        )
                    )
        return children
