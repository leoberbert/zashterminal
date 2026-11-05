# zashterminal/ui/sidebar_manager.py

from typing import TYPE_CHECKING, Any, List, Tuple

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk

from ..sessions.models import SessionFolder, SessionItem
from ..utils.icons import set_button_icon
from ..utils.logger import get_logger
from ..utils.tooltip_helper import get_tooltip_helper
from ..utils.translation_utils import _

if TYPE_CHECKING:
    from ..sessions.tree import SessionTreeView
    from ..settings.manager import SettingsManager
    from ..window import ZashTerminalWindow
    from .window_ui import WindowUIBuilder


class SidebarManager:
    """
    Manages the state and interactions of the main window's sidebar,
    including the docked or overlay split view, search, and sizing.
    """

    def __init__(self, window: "ZashTerminalWindow", ui: "WindowUIBuilder"):
        self.window = window
        self.settings_manager: "SettingsManager" = window.settings_manager
        self.session_tree: "SessionTreeView" = window.session_tree
        self.logger = get_logger("zashterminal.ui.sidebar")

        # Get widgets from the UI builder
        self.split = ui.split_view
        self.sidebar_box = ui.sidebar_box
        self.toggle_sidebar_button = ui.toggle_sidebar_button
        self.pin_sidebar_button = ui.pin_sidebar_button
        self._updating_pin = False
        self._updating_toggle = False
        self._updating_width = False
        self._switching_mode = False
        self._width_save_id = 0
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
        self.pin_sidebar_button.connect("toggled", self._on_pin_toggled)
        self.split.connect("notify::show-sidebar", self._on_show_sidebar_changed)
        self.split.connect(
            "notify::sidebar-width-fraction", self._on_sidebar_width_changed
        )

        sidebar_key_controller = Gtk.EventControllerKey.new()
        sidebar_key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        sidebar_key_controller.connect("key-pressed", self._on_sidebar_key_pressed)
        self.sidebar_box.add_controller(sidebar_key_controller)

        self.sidebar_box.connect("notify::width", self._on_sidebar_folded_changed)

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


    # NEW: Callback methods that close the popover before calling the action
    def _close_popover_if_active(self):
        """Close an unpinned overlay before a dialog, so the dialog is not covered."""
        if self.split.get_collapsed() and self.split.get_show_sidebar():
            self.split.set_show_sidebar(False)

    def close_if_unpinned(self) -> None:
        """Hide the sessions panel after opening a session when the pin is off."""
        if self.split.get_collapsed():
            self.split.set_show_sidebar(False)

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
        auto_hide = self.settings_manager.get("auto_hide_sidebar", True)
        self.handle_auto_hide_change(auto_hide, is_initial_setup=True)
        self._width_tries = 0
        GLib.timeout_add(50, self._apply_width_when_mapped)

    def handle_auto_hide_change(
        self, auto_hide_enabled: bool, is_initial_setup: bool = False
    ):
        """Pin off overlays the terminal. Pin on docks the panel beside it."""
        was_open = self.split.get_show_sidebar()
        self._switching_mode = True
        try:
            self.split.set_collapsed(bool(auto_hide_enabled))
            if auto_hide_enabled:
                self.split.set_show_sidebar(False if is_initial_setup else was_open)
            else:
                visible = was_open or self.settings_manager.get_sidebar_visible()
                if is_initial_setup:
                    visible = self.settings_manager.get_sidebar_visible()
                self.split.set_show_sidebar(bool(visible))
                if (
                    self.split.get_show_sidebar()
                    and not self.settings_manager.get_sidebar_visible()
                ):
                    self.settings_manager.set_sidebar_visible(True)
        finally:
            self._switching_mode = False
        if (
            not is_initial_setup
            and self.split.get_collapsed()
            and self.split.get_show_sidebar()
        ):
            GLib.idle_add(self._focus_first_item)
        self._sync_pin_button()
        self._update_sidebar_button_icon()

    def _on_pin_toggled(self, button: Gtk.ToggleButton) -> None:
        if self._updating_pin:
            return
        pinned = button.get_active()
        auto_hide = not pinned
        if self.settings_manager.get("auto_hide_sidebar", True) == auto_hide:
            self._sync_pin_button()
            return
        self.settings_manager.set("auto_hide_sidebar", auto_hide)

    def _sync_pin_button(self) -> None:
        pinned = not bool(self.settings_manager.get("auto_hide_sidebar", True))
        if self.pin_sidebar_button.get_active() != pinned:
            self._updating_pin = True
            self.pin_sidebar_button.set_active(pinned)
            self._updating_pin = False
        tooltip = (
            _("Unpin the sessions panel") if pinned else _("Pin the sessions panel")
        )
        get_tooltip_helper().add_tooltip(self.pin_sidebar_button, tooltip)

    def _on_toggle_sidebar(self, button: Gtk.ToggleButton) -> None:
        """Show or hide the sessions panel in both pin states."""
        if self._updating_toggle:
            return
        self.split.set_show_sidebar(button.get_active())

    def _on_show_sidebar_changed(self, _split, _param) -> None:
        shown = self.split.get_show_sidebar()
        if self.toggle_sidebar_button.get_active() != shown:
            self._updating_toggle = True
            self.toggle_sidebar_button.set_active(shown)
            self._updating_toggle = False
        self._update_sidebar_button_icon()
        if self._switching_mode:
            return
        if not self.split.get_collapsed():
            if self.settings_manager.get_sidebar_visible() != shown:
                self.settings_manager.set_sidebar_visible(shown)
        if shown:
            self.update_sidebar_sizes()
            if self.split.get_collapsed():
                GLib.idle_add(self._focus_first_item)
            return
        if self.split.get_collapsed():
            self.search_entry.set_text("")
            self.session_tree.clear_search()
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

    def _on_sidebar_key_pressed(self, _, keyval, _keycode, state) -> bool:
        if (
            keyval == Gdk.KEY_Escape
            and self.split.get_collapsed()
            and self.split.get_show_sidebar()
        ):
            self.split.set_show_sidebar(False)
            return Gdk.EVENT_STOP

        shortcut_string = self.settings_manager.get_shortcut("toggle-sidebar")
        if not shortcut_string:
            return Gdk.EVENT_PROPAGATE

        success, parsed_keyval, parsed_mods = Gtk.accelerator_parse(shortcut_string)
        if success:
            event_mods = state & Gtk.accelerator_get_default_mod_mask()
            event_keyval_lower = Gdk.keyval_to_lower(keyval)
            if event_keyval_lower == parsed_keyval and event_mods == parsed_mods:
                self.split.set_show_sidebar(not self.split.get_show_sidebar())
                return Gdk.EVENT_STOP

        return Gdk.EVENT_PROPAGATE

    def _on_sidebar_folded_changed(self, flap, param):
        self._update_sidebar_responsive_design()

    def _on_search_changed(self, search_entry: Gtk.SearchEntry) -> None:
        search_text = search_entry.get_text().lower()
        if not search_text:
            self.session_tree.clear_search()
        else:
            self.session_tree.set_filter_text(search_text)

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
        set_button_icon(self.toggle_sidebar_button, "computer-symbolic")
        get_tooltip_helper().add_tooltip(self.toggle_sidebar_button, _("Show Sessions"))

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
        """Apply the compact pixel width. The fraction property is a ratio."""
        if not self.split.get_show_sidebar():
            return
        self._set_split_width(self._preferred_sidebar_width())

    def _preferred_sidebar_width(self) -> int:
        # 260 is about half of the previous 560px clamp, just past the toolbar.
        saved = int(self.settings_manager.get("sidebar_width", 300) or 300)
        if saved == 300:
            saved = 260
        return max(220, min(saved, 280))

    def _set_split_width(self, width: int) -> None:
        width = max(220, min(int(width), 280))
        total = self.split.get_width()
        if total <= width:
            return
        self._updating_width = True
        self.split.set_sidebar_width_fraction(width / total)
        self._updating_width = False

    def _apply_width_when_mapped(self) -> bool:
        self._width_tries += 1
        total = self.split.get_width()
        target = self._preferred_sidebar_width()
        if total <= target:
            return self._width_tries < 40
        current = int(round(self.split.get_sidebar_width_fraction() * total))
        if abs(current - target) > 8:
            self._set_split_width(target)
            return self._width_tries < 40
        return GLib.SOURCE_REMOVE

    def _on_sidebar_width_changed(self, *_args) -> None:
        if self._updating_width:
            return
        if self._width_save_id:
            GLib.source_remove(self._width_save_id)
        self._width_save_id = GLib.timeout_add(400, self._save_sidebar_width)

    def _save_sidebar_width(self) -> bool:
        self._width_save_id = 0
        total = self.split.get_width() or self.window.get_width()
        if total <= 0:
            return GLib.SOURCE_REMOVE
        width = int(round(self.split.get_sidebar_width_fraction() * total))
        width = max(220, min(width, 280))
        current = int(self.settings_manager.get("sidebar_width", 300) or 300)
        if current != width:
            self.settings_manager.set("sidebar_width", width)
        return GLib.SOURCE_REMOVE

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
