# zashterminal/sessions/tree.py

from typing import Callable, List, Optional, Set, Union

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, GObject, Graphene, Gtk

from ..core.signals import AppSignals
from ..helpers import create_themed_popover_menu
# Lazy imports for menus - only loaded when context menus are actually needed
# from ..ui.menus import create_folder_menu, create_root_menu, create_session_menu
from ..utils.logger import get_logger


# Lazy menu creation functions
def _get_create_session_menu():
    """Lazy import for create_session_menu."""
    from ..ui.menus import create_session_menu

    return create_session_menu


def _get_create_folder_menu():
    """Lazy import for create_folder_menu."""
    from ..ui.menus import create_folder_menu

    return create_folder_menu


def _get_create_root_menu():
    """Lazy import for create_root_menu."""
    from ..ui.menus import create_root_menu

    return create_root_menu


def _get_inline_context_menu():
    """Lazy import for InlineContextMenu."""
    from ..ui.widgets.inline_context_menu import InlineContextMenu

    return InlineContextMenu


from ..utils.translation_utils import _
from .models import LayoutItem, SessionFolder, SessionItem
from .operations import SessionOperations


def _get_children_model(
    item: GObject.GObject, user_data: object
) -> Optional[Gio.ListStore]:
    """Callback for Gtk.TreeListModel to get children of an item."""
    return getattr(item, "children", None)


class SessionTreeView:
    """Manages a modern tree view for sessions and folders using Gtk.ColumnView."""

    def __init__(
        self,
        parent_window: Gtk.Window,
        session_store: Gio.ListStore,
        folder_store: Gio.ListStore,
        settings_manager,
        operations: SessionOperations,
    ):
        self.logger = get_logger("zashterminal.sessions.tree")
        self.parent_window = parent_window
        self.session_store = session_store
        self.folder_store = folder_store
        self.settings_manager = settings_manager
        self.operations = operations

        self.root_store = Gio.ListStore.new(GObject.GObject)
        self.tree_model = Gtk.TreeListModel.new(
            self.root_store,
            passthrough=False,
            autoexpand=False,
            create_func=_get_children_model,
            user_data=None,
        )

        # Set up filtering
        self.filter = Gtk.CustomFilter.new(self._filter_func)
        self.filter_model = Gtk.FilterListModel(
            model=self.tree_model, filter=self.filter
        )
        self.selection_model = Gtk.MultiSelection(model=self.filter_model)
        self.column_view = self._create_column_view()

        self._clipboard_item: Optional[Union[SessionItem, SessionFolder]] = None
        self._clipboard_is_cut: bool = False
        self._is_restoring_state: bool = False
        self._populated_folders = set()
        self._filter_text = ""
        self._saved_expansion_state: Optional[Set[str]] = (
            None  # Save expansion state before search
        )
        self.on_session_activated: Optional[Callable[[SessionItem], None]] = None
        self.on_layout_activated: Optional[Callable[[str], None]] = None
        self.on_folder_expansion_changed: Optional[Callable[[], None]] = None

        # Subscribe to AppSignals for decoupled updates
        signals = AppSignals.get()
        signals.connect("session-created", self._on_session_signal)
        signals.connect("session-updated", self._on_session_signal)
        signals.connect("session-deleted", self._on_session_signal)
        signals.connect("folder-created", self._on_folder_signal)
        signals.connect("folder-updated", self._on_folder_signal)
        signals.connect("folder-deleted", self._on_folder_signal)
        signals.connect("request-tree-refresh", self._on_request_tree_refresh)

        self.refresh_tree()
        self.logger.info("SessionTreeView (ColumnView) initialized")

    def _filter_func(self, item: GObject.GObject) -> bool:
        """Filter function that determines if an item should be visible."""
        if not self._filter_text:
            return True

        # Get the actual item from the tree list row
        if hasattr(item, "get_item"):
            tree_list_row = item
            actual_item = tree_list_row.get_item()
        else:
            actual_item = item

        # Check if the item name contains the filter text
        item_name = getattr(actual_item, "name", "").lower()
        if self._filter_text in item_name:
            return True

        # For folders, also check if any children match the filter
        if isinstance(actual_item, SessionFolder):
            return self._folder_contains_matching_items(actual_item)

        return False

    def _folder_contains_matching_items(self, folder: SessionFolder) -> bool:
        """Recursively check if a folder contains any items matching the current filter."""
        if not self._filter_text:
            return False

        # Ensure folder children are populated
        if folder.path not in self._populated_folders:
            self._populate_folder_children(folder)

        # Check all children recursively
        for child in folder.children:
            # Check if child name matches
            child_name = getattr(child, "name", "").lower()
            if self._filter_text in child_name:
                return True

            # If child is a folder, check its children recursively
            if isinstance(child, SessionFolder):
                if self._folder_contains_matching_items(child):
                    return True

        return False

    def set_filter_text(self, text: str) -> None:
        """Updates the filter text and refreshes the filter."""
        old_filter_text = self._filter_text
        self._filter_text = text.lower()

        # If we're starting a new search (text was added to empty search), save current expansion state
        if text and not old_filter_text:
            self._save_current_expansion_state()

        # If we're starting a new search (text was added), expand folders with matches
        if text and (not old_filter_text or text.startswith(old_filter_text)):
            self._expand_folders_with_matches()

        self.filter.changed(Gtk.FilterChange.DIFFERENT)

    def _expand_folders_with_matches(self) -> None:
        """Automatically expand folders that contain items matching the current filter."""
        if not self._filter_text:
            return

        def expand_matching_folders_recursively(model, parent_path=""):
            """Recursively expand folders that contain matching items."""
            for i in range(model.get_n_items()):
                row = model.get_item(i)
                if not row:
                    continue

                item = row.get_item()
                if isinstance(item, SessionFolder):
                    # Check if this folder contains matching items
                    if self._folder_contains_matching_items(item):
                        # Expand this folder to show matching children
                        if not row.get_expanded():
                            row.set_expanded(True)

                        # Also expand any child folders that contain matches
                        if item.path not in self._populated_folders:
                            self._populate_folder_children(item)

                        # Since TreeListModel creates child models automatically when rows are expanded,
                        # and we've already populated the children, we don't need to manually recurse
                        # into child models. The TreeListModel will handle creating the child rows
                        # from the folder's children list.

        # Start expansion from root
        expand_matching_folders_recursively(self.tree_model)

    def clear_search(self) -> None:
        """Clears the search filter and restores original expansion state."""
        if self._filter_text:
            self._filter_text = ""
            self._restore_saved_expansion_state()
            self.filter.changed(Gtk.FilterChange.DIFFERENT)

    def _save_current_expansion_state(self) -> None:
        """Saves the current expansion state of all folders before search begins."""
        self._saved_expansion_state = set()

        def collect_expanded_folders(model):
            """Recursively collect all expanded folder paths."""
            for i in range(model.get_n_items()):
                row = model.get_item(i)
                if not row:
                    continue

                item = row.get_item()
                if isinstance(item, SessionFolder):
                    if row.get_expanded():
                        self._saved_expansion_state.add(item.path)

                    # If expanded, check children too
                    if row.get_expanded():
                        try:
                            child_model = row.get_model()
                            if child_model:
                                collect_expanded_folders(child_model)
                        except AttributeError:
                            pass

        collect_expanded_folders(self.tree_model)
        self.logger.debug(f"Saved expansion state: {self._saved_expansion_state}")

    def _restore_saved_expansion_state(self) -> None:
        """Restores the expansion state that was saved before search began."""
        if self._saved_expansion_state is None:
            # If no saved state, fall back to settings
            self._apply_expansion_state()
            return

        self.logger.debug(
            f"Restoring saved expansion state: {self._saved_expansion_state}"
        )

        def restore_expansion_state(model):
            """Recursively restore expansion state for all folders."""
            for i in range(model.get_n_items()):
                row = model.get_item(i)
                if not row:
                    continue

                item = row.get_item()
                if isinstance(item, SessionFolder):
                    should_be_expanded = item.path in self._saved_expansion_state
                    current_expanded = row.get_expanded()

                    if should_be_expanded and not current_expanded:
                        row.set_expanded(True)
                    elif not should_be_expanded and current_expanded:
                        row.set_expanded(False)

                    # If this folder should be expanded, check its children
                    if should_be_expanded:
                        try:
                            child_model = row.get_model()
                            if child_model:
                                restore_expansion_state(child_model)
                        except AttributeError:
                            pass

        # Set restoring state flag to prevent saving during restoration
        self._is_restoring_state = True
        try:
            restore_expansion_state(self.tree_model)
        finally:
            self._is_restoring_state = False

        # Clear the saved state
        self._saved_expansion_state = None

    def get_widget(self) -> Gtk.ListView:
        """Returns the list view widget."""
        return self.column_view

    def _create_column_view(self) -> Gtk.ListView:
        """Creates and configures a Gtk.ListView widget (no headers)."""
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_factory_setup)
        factory.connect("bind", self._on_factory_bind)
        factory.connect("unbind", self._on_factory_unbind)

        list_view = Gtk.ListView(model=self.selection_model, factory=factory)
        list_view.set_show_separators(False)
        list_view.set_focusable(True)
        list_view.connect("activate", self._on_row_activated)

        # Handle focus to ensure navigation works
        focus_controller = Gtk.EventControllerFocus.new()
        focus_controller.connect("enter", self._on_column_view_focus_enter)
        list_view.add_controller(focus_controller)

        empty_area_gesture = Gtk.GestureClick.new()
        empty_area_gesture.set_button(Gdk.BUTTON_SECONDARY)
        empty_area_gesture.connect("pressed", self._on_empty_area_right_click)
        list_view.add_controller(empty_area_gesture)

        root_drop_target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        root_drop_target.connect("accept", lambda _, __: True)
        root_drop_target.connect("drop", self._on_root_drop)
        list_view.add_controller(root_drop_target)

        key_controller = Gtk.EventControllerKey.new()
        key_controller.connect("key-pressed", self._on_key_pressed)
        list_view.add_controller(key_controller)
        return list_view

    def _on_column_view_focus_enter(self, controller: Gtk.EventControllerFocus) -> None:
        """Handle focus entering the column view to ensure proper navigation."""
        if self.selection_model.get_selection().get_size() == 0:
            # Select first item if nothing is selected
            if self.filter_model.get_n_items() > 0:
                self.selection_model.select_item(0, True)

    def _on_factory_setup(
        self, factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem
    ) -> None:
        """Sets up the widget structure for each row in the ColumnView."""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, hexpand=True)

        # MODIFIED: Add a dedicated spacer for indentation
        spacer = Gtk.Box()
        spacer.set_name("indent-spacer")  # Assign a name to find it later
        box.append(spacer)

        icon = Gtk.Image()
        label = Gtk.Label(xalign=0.0, hexpand=True)
        box.append(icon)
        box.append(label)
        list_item.set_child(box)

        right_click = Gtk.GestureClick.new()
        right_click.set_button(Gdk.BUTTON_SECONDARY)
        right_click.connect("pressed", self._on_item_right_click, list_item)
        box.add_controller(right_click)

        drag_source = Gtk.DragSource.new()
        drag_source.set_actions(Gdk.DragAction.MOVE)
        drag_source.connect("prepare", self._on_drag_prepare, list_item)
        drag_source.connect("drag-begin", self._on_drag_begin, list_item)
        box.add_controller(drag_source)

        drop_target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        drop_target.connect("accept", self._on_folder_drop_accept, list_item)
        drop_target.connect("drop", self._on_folder_drop, list_item)
        drop_target.connect("enter", self._on_folder_drag_enter, list_item)
        drop_target.connect("leave", self._on_folder_drag_leave, list_item)
        box.add_controller(drop_target)

    def _on_factory_bind(
        self, factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem
    ) -> None:
        """Binds data from a model item to a row widget."""
        box = list_item.get_child()
        # MODIFIED: Find widgets by their position/type
        spacer = box.get_first_child()
        icon = spacer.get_next_sibling()
        label = icon.get_next_sibling()

        tree_list_row = list_item.get_item()
        item = tree_list_row.get_item()
        label.set_label(item.name)

        # MODIFIED: Dynamic indentation using the spacer widget
        depth = tree_list_row.get_depth()
        indent_width = depth * 20  # 20 pixels per indentation level
        spacer.set_size_request(indent_width, -1)

        if isinstance(item, SessionItem):
            icon.set_from_icon_name(
                "computer-symbolic" if item.is_local() else "network-server-symbolic"
            )
        elif isinstance(item, LayoutItem):
            icon.set_from_icon_name("view-restore-symbolic")
        elif isinstance(item, SessionFolder):

            def update_folder_icon(row: Gtk.TreeListRow, _=None) -> None:
                if (
                    row.get_expanded()
                    and row.get_item().path not in self._populated_folders
                ):
                    self._populate_folder_children(row.get_item())

                has_children = (
                    any(s.folder_path == item.path for s in self.session_store)
                    or any(f.parent_path == item.path for f in self.folder_store)
                    or any(
                        isinstance(layout, LayoutItem)
                        and layout.folder_path == item.path
                        for layout in self.parent_window.layouts
                    )
                )

                icon_name = (
                    "folder-open-symbolic"
                    if row.get_expanded()
                    else ("folder-new-symbolic" if has_children else "folder-symbolic")
                )
                icon.set_from_icon_name(icon_name)

            update_folder_icon(tree_list_row)
            icon_handler_id = tree_list_row.connect(
                "notify::expanded", update_folder_icon
            )
            expansion_handler_id = tree_list_row.connect(
                "notify::expanded", self._on_folder_expansion_changed
            )
            list_item.handler_ids = [icon_handler_id, expansion_handler_id]

    def _on_factory_unbind(
        self, factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem
    ) -> None:
        """Unbinds a row, disconnecting signal handlers."""
        if hasattr(list_item, "handler_ids"):
            row = list_item.get_item()
            if row:
                for handler_id in list_item.handler_ids:
                    if GObject.signal_handler_is_connected(row, handler_id):
                        row.disconnect(handler_id)
            del list_item.handler_ids

    def _on_folder_expansion_changed(
        self, tree_list_row: Gtk.TreeListRow, _param
    ) -> None:
        """Saves the expansion state of a folder when it's changed by the user."""
        if self._is_restoring_state:
            return
        try:
            folder = tree_list_row.get_item()
            if not isinstance(folder, SessionFolder):
                return
            expanded_paths = set(self.settings_manager.get("tree_expanded_folders", []))
            if tree_list_row.get_expanded():
                expanded_paths.add(folder.path)
            else:
                expanded_paths.discard(folder.path)
            self.settings_manager.set("tree_expanded_folders", list(expanded_paths))

            # Notify parent window about expansion change for dynamic sizing
            if self.on_folder_expansion_changed:
                self.on_folder_expansion_changed()
        except Exception as e:
            self.logger.error(f"Failed to save folder expansion state: {e}")

    def _on_drag_prepare(
        self, source: Gtk.DragSource, x: float, y: float, list_item: Gtk.ListItem
    ) -> Optional[Gdk.ContentProvider]:
        """Prepares the data for a drag operation."""
        item = list_item.get_item().get_item()
        if isinstance(item, SessionItem):
            data_string = f"session|{item.name}|{item.folder_path}"
        elif isinstance(item, SessionFolder):
            data_string = f"folder|{item.name}|{item.path}"
        elif isinstance(item, LayoutItem):
            data_string = f"layout|{item.name}|{item.folder_path}"
        else:
            return None
        return Gdk.ContentProvider.new_for_value(data_string)

    def _on_drag_begin(
        self, source: Gtk.DragSource, _drag: Gdk.Drag, list_item: Gtk.ListItem
    ) -> None:
        """Sets the drag icon when a drag begins."""
        item = list_item.get_item().get_item()
        paintable = Gtk.WidgetPaintable.new(
            Gtk.Label(label=item.name, css_classes=["drag-icon"])
        )
        source.set_icon(paintable, 0, 0)

    def _on_folder_drop_accept(
        self, _target: Gtk.DropTarget, _drop: Gdk.Drop, list_item: Gtk.ListItem
    ) -> bool:
        """Accepts a drop only if the target is a folder."""
        return isinstance(list_item.get_item().get_item(), SessionFolder)

    def _on_folder_drag_enter(
        self, _target: Gtk.DropTarget, x: float, y: float, list_item: Gtk.ListItem
    ) -> Gdk.DragAction:
        """Adds a CSS class to highlight the drop target folder."""
        if isinstance(list_item.get_item().get_item(), SessionFolder):
            list_item.get_child().add_css_class("drop-target")
            return Gdk.DragAction.MOVE
        return Gdk.DragAction.DEFAULT

    def _on_folder_drag_leave(
        self, _target: Gtk.DropTarget, list_item: Gtk.ListItem
    ) -> None:
        """Removes the highlight CSS class when the drag leaves a folder."""
        list_item.get_child().remove_css_class("drop-target")

    def _on_folder_drop(
        self,
        _target: Gtk.DropTarget,
        value: str,
        x: float,
        y: float,
        list_item: Gtk.ListItem,
    ) -> bool:
        """Handles a drop onto a folder."""
        target_folder = list_item.get_item().get_item()
        list_item.get_child().remove_css_class("drop-target")
        self._perform_move(value, target_folder.path)
        return True

    def _on_root_drop(
        self, _target: Gtk.DropTarget, value: str, x: float, y: float
    ) -> bool:
        """Handles a drop onto the empty (root) area."""
        self._perform_move(value, "")
        return True

    def _perform_move(self, data_string: str, target_folder_path: str) -> None:
        """
        Delegates the logic to move a session or folder to the operations layer.
        """
        try:
            item_type, name, source_path = data_string.split("|", 2)
            result = None
            if item_type == "session":
                session, _ = self.operations.find_session_by_name_and_path(
                    name, source_path
                )
                if session:
                    result = self.operations.move_session_to_folder(
                        session, target_folder_path
                    )
            elif item_type == "folder":
                folder, _ = self.operations.find_folder_by_path(source_path)
                if folder:
                    result = self.operations.move_folder(folder, target_folder_path)
            elif item_type == "layout":
                self.parent_window.move_layout(name, source_path, target_folder_path)
                return  # move_layout handles its own refresh

            if result and result.success:
                self.refresh_tree()
            elif result:
                if hasattr(self.parent_window, "_show_error_dialog"):
                    self.parent_window._show_error_dialog(
                        _("Move Error"), result.message
                    )
        except Exception as e:
            self.logger.error(f"Drag-and-drop move error: {e}")
            if hasattr(self.parent_window, "_show_error_dialog"):
                self.parent_window._show_error_dialog(_("Move Error"), str(e))

    def refresh_tree(self) -> None:
        """Rebuilds the entire tree view from the session and folder stores."""
        self._is_restoring_state = True
        self._populated_folders.clear()
        self.root_store.remove_all()
        for i in range(self.folder_store.get_n_items()):
            self.folder_store.get_item(i).clear_children()

        root_items = []
        # Add sessions, folders, and layouts to their parent (or root)
        all_items = (
            list(self.session_store)
            + list(self.folder_store)
            + self.parent_window.layouts
        )

        for item in all_items:
            parent_path = getattr(item, "parent_path", None)
            if parent_path is None:  # SessionItem or LayoutItem
                parent_path = getattr(item, "folder_path", "")

            if not parent_path:
                root_items.append(item)

        sorted_root = sorted(
            root_items,
            key=lambda item: (
                isinstance(item, SessionItem),
                isinstance(item, LayoutItem),
                item.name,
            ),
        )
        for item in sorted_root:
            self.root_store.append(item)

        GLib.idle_add(self._apply_expansion_state)

    def _on_session_signal(self, signals, data):
        """Handle session-related signals from AppSignals."""
        self.refresh_tree()

    def _on_folder_signal(self, signals, data):
        """Handle folder-related signals from AppSignals."""
        self.refresh_tree()

    def _on_request_tree_refresh(self, signals):
        """Handle explicit tree refresh request."""
        self.refresh_tree()

    def _populate_folder_children(self, folder: SessionFolder):
        """Populates the children of a specific folder on-demand."""
        if folder.path in self._populated_folders:
            return
        folder.clear_children()
        children = []

        all_items = (
            list(self.session_store)
            + list(self.folder_store)
            + self.parent_window.layouts
        )
        for item in all_items:
            parent_path = getattr(item, "parent_path", None)
            if parent_path is None:
                parent_path = getattr(item, "folder_path", "")

            if parent_path == folder.path:
                children.append(item)

        sorted_children = sorted(
            children,
            key=lambda item: (
                isinstance(item, SessionItem),
                isinstance(item, LayoutItem),
                item.name,
            ),
        )
        for child in sorted_children:
            folder.add_child(child)
        self._populated_folders.add(folder.path)

    def _apply_expansion_state(self) -> bool:
        """Restores the expanded state of folders from settings."""
        try:
            expanded_paths = self.settings_manager.get("tree_expanded_folders", [])
            if not expanded_paths:
                self._is_restoring_state = False
                return False
            sorted_paths = sorted(expanded_paths, key=lambda p: p.count("/"))
            for path in sorted_paths:
                row_to_expand = self._find_row_recursively(self.tree_model, path)
                if row_to_expand and not row_to_expand.get_expanded():
                    row_to_expand.set_expanded(True)
        except Exception as e:
            self.logger.error(f"Failed to apply tree expansion state: {e}")
        finally:
            self._is_restoring_state = False
        return GLib.SOURCE_REMOVE

    def _find_row_recursively(self, model, path_to_find):
        for i in range(model.get_n_items()):
            row = model.get_item(i)
            if not row:
                continue
            item = row.get_item()
            if isinstance(item, SessionFolder):
                if item.path == path_to_find:
                    return row
                if path_to_find.startswith(item.path + "/"):
                    if item.path not in self._populated_folders:
                        self._populate_folder_children(item)

                    # Since TreeListModel creates child models automatically when rows are expanded,
                    # we need to expand the row first to access its children
                    if not row.get_expanded():
                        row.set_expanded(True)

                    # Try to get the child model
                    try:
                        child_model = row.get_model()
                        if child_model:
                            if found_in_child := self._find_row_recursively(
                                child_model, path_to_find
                            ):
                                return found_in_child
                    except AttributeError:
                        # If get_model doesn't exist or fails, continue without recursing
                        pass
        return None

    def _on_row_activated(self, _list_view: Gtk.ListView, position: int) -> None:
        """Handles item activation (e.g., double-click or Enter key)."""
        if not (tree_list_row := self.filter_model.get_item(position)):
            return
        item = tree_list_row.get_item()
        if isinstance(item, SessionItem):
            if self.on_session_activated:
                self.on_session_activated(item)
        elif isinstance(item, LayoutItem):
            if self.on_layout_activated:
                self.on_layout_activated(item.name)
        elif isinstance(item, SessionFolder):
            # For folders, just toggle expansion without triggering auto-hide
            tree_list_row.set_expanded(not tree_list_row.get_expanded())
            return  # Don't trigger auto-hide for folder operations

        # Auto-hide sidebar if enabled (only for non-folder items)
        if self.settings_manager.get("auto_hide_sidebar", False):
            # Use a longer delay to ensure the activation is fully processed
            def delayed_auto_hide():
                # Double-check we're still in auto-hide mode and popover is visible
                if (
                    self.settings_manager.get("auto_hide_sidebar", False)
                    and hasattr(self.parent_window, "sidebar_popover")
                    and self.parent_window.sidebar_popover.get_visible()
                ):
                    # We're in popup mode, close the popover
                    self.parent_window.sidebar_popover.popdown()
                    self.parent_window.toggle_sidebar_button.set_active(False)
                else:
                    # We're in normal flap mode
                    self.parent_window.flap.set_reveal_flap(False)
                    self.parent_window.settings_manager.set_sidebar_visible(False)
                    self.parent_window.toggle_sidebar_button.set_active(False)
                return GLib.SOURCE_REMOVE

            # Use a longer timeout to ensure activation is fully processed
            GLib.timeout_add(100, delayed_auto_hide)

    def _on_key_pressed(
        self,
        controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        state: Gdk.ModifierType,
    ) -> bool:
        """Handles key presses for shortcuts."""
        # Check for alphanumeric keys to start search
        if not state & (
            Gdk.ModifierType.CONTROL_MASK
            | Gdk.ModifierType.ALT_MASK
            | Gdk.ModifierType.SHIFT_MASK
        ):
            # Get the Unicode character for the key
            unicode_val = Gdk.keyval_to_unicode(keyval)
            if unicode_val != 0:  # Valid Unicode character
                unicode_char = chr(unicode_val)
                if unicode_char.isalnum() or unicode_char in " -_":
                    # Move focus to search entry and start typing
                    if hasattr(self.parent_window, "search_entry"):
                        self.parent_window.search_entry.grab_focus()
                        self.parent_window.search_entry.set_text(unicode_char)
                        self.parent_window.search_entry.set_position(
                            -1
                        )  # Move cursor to end
                    return Gdk.EVENT_STOP

        if state & Gdk.ModifierType.CONTROL_MASK:
            if keyval in (Gdk.KEY_a, Gdk.KEY_A):
                self.selection_model.select_all()
                return Gdk.EVENT_STOP
            if keyval in (Gdk.KEY_c, Gdk.KEY_C):
                self._copy_selected_item()
                return Gdk.EVENT_STOP
            if keyval in (Gdk.KEY_x, Gdk.KEY_X):
                self._cut_selected_item()
                return Gdk.EVENT_STOP
            if keyval in (Gdk.KEY_v, Gdk.KEY_V):
                target_path = ""
                if selected_item := self.get_selected_item():
                    target_path = (
                        selected_item.path
                        if isinstance(selected_item, SessionFolder)
                        else selected_item.folder_path
                    )
                self._paste_item(target_path)
                return Gdk.EVENT_STOP
        if keyval == Gdk.KEY_Delete:
            if hasattr(self.parent_window, "action_handler"):
                self.parent_window.action_handler.delete_selected_items()
            return Gdk.EVENT_STOP
        if keyval == Gdk.KEY_BackSpace:
            # Remove last character from search entry
            if hasattr(self.parent_window, "search_entry"):
                current_text = self.parent_window.search_entry.get_text()
                if current_text:
                    new_text = current_text[:-1]
                    self.parent_window.search_entry.set_text(new_text)
                    self.parent_window.search_entry.set_position(-1)
            return Gdk.EVENT_STOP
        return Gdk.EVENT_PROPAGATE

    def get_selected_item(
        self,
    ) -> Optional[Union[SessionItem, SessionFolder, LayoutItem]]:
        """Gets the single selected item, or None if multiple/none are selected."""
        selection = self.selection_model.get_selection()
        if selection.get_size() == 1:
            if row := self.filter_model.get_item(selection.get_nth(0)):
                return row.get_item()
        return None

    def get_selected_items(self) -> List[Union[SessionItem, SessionFolder, LayoutItem]]:
        """Gets all selected items from the tree view."""
        items = []
        selection = self.selection_model.get_selection()
        size = selection.get_size()
        for i in range(size):
            position = selection.get_nth(i)
            if row := self.filter_model.get_item(position):
                items.append(row.get_item())
        return items

    def _on_item_right_click(
        self,
        gesture: Gtk.GestureClick,
        _n_press: int,
        x: float,
        y: float,
        list_item: Gtk.ListItem,
    ) -> None:
        """Shows a context menu for a specific item."""
        pos = list_item.get_position()
        self.logger.debug(f"_on_item_right_click called for position: {pos}")
        if not self.selection_model.is_selected(pos):
            self.selection_model.unselect_all()
            self.selection_model.select_item(pos, True)
        tree_list_row = self.filter_model.get_item(pos)
        if not tree_list_row:
            self.logger.debug("tree_list_row is None, returning")
            return

        item = tree_list_row.get_item()
        self.logger.debug(
            f"Item type: {type(item).__name__}, name: {getattr(item, 'name', 'N/A')}"
        )

        # Check if sidebar popover is visible - use inline context menu if so
        sidebar_popover = getattr(
            getattr(self.parent_window, "ui_builder", None), "sidebar_popover", None
        )

        popover_visible = sidebar_popover and sidebar_popover.get_visible()
        self.logger.debug(f"Sidebar popover visible: {popover_visible}")

        if popover_visible:
            # Show inline context menu within the popover
            self.logger.debug("Showing inline context menu")
            self._show_inline_context_menu(item)
        else:
            # Show traditional popover menu (for non-popover sidebar mode)
            self.logger.debug("Showing traditional popover context menu")
            self._show_popover_context_menu(item, list_item, x, y)

        # Stop event propagation to prevent the empty area handler from being triggered
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _show_inline_context_menu(
        self, item: Union[SessionItem, SessionFolder, LayoutItem]
    ) -> None:
        """Show the inline context menu within the sidebar popover."""
        self.logger.debug(
            f"_show_inline_context_menu called with item: {type(item).__name__}"
        )
        ui_builder = getattr(self.parent_window, "ui_builder", None)
        if not ui_builder:
            self.logger.debug("ui_builder is None")
            return

        content_stack = getattr(ui_builder, "sidebar_content_stack", None)
        inline_menu_box = getattr(ui_builder, "inline_context_menu_box", None)

        self.logger.debug(
            f"content_stack: {content_stack}, inline_menu_box: {inline_menu_box}"
        )

        if not content_stack or not inline_menu_box:
            self.logger.debug("content_stack or inline_menu_box is None")
            return

        # Clear previous inline menu content
        while (child := inline_menu_box.get_first_child()) is not None:
            inline_menu_box.remove(child)

        # Create inline context menu widget
        InlineContextMenu = _get_inline_context_menu()
        inline_menu = InlineContextMenu(self.parent_window)

        # Set up go back callback
        def go_back():
            content_stack.set_visible_child_name("normal")

        inline_menu.set_go_back_callback(go_back)

        # Show menu for the specific item type
        if isinstance(item, SessionItem):
            self.logger.debug("Showing menu for SessionItem")
            inline_menu.show_for_session(
                item, self.folder_store, self.has_clipboard_content()
            )
        elif isinstance(item, SessionFolder):
            self.logger.debug("Showing menu for SessionFolder")
            inline_menu.show_for_folder(item, self.has_clipboard_content())
        elif isinstance(item, LayoutItem):
            self.logger.debug("Showing menu for LayoutItem")
            inline_menu.show_for_layout(item)
        else:
            self.logger.debug(f"Unknown item type: {type(item)}")

        inline_menu_box.append(inline_menu)

        # Switch to context menu view
        self.logger.debug("Switching to context-menu view")
        content_stack.set_visible_child_name("context-menu")

    def _show_popover_context_menu(
        self,
        item: Union[SessionItem, SessionFolder, LayoutItem],
        list_item: Gtk.ListItem,
        x: float,
        y: float,
    ) -> None:
        """Show the traditional popover context menu."""
        menu_model = None
        if isinstance(item, SessionItem):
            found, position = self.session_store.find(item)
            if found:
                menu_model = _get_create_session_menu()(
                    item,
                    self.session_store,
                    position,
                    self.folder_store,
                    self.has_clipboard_content(),
                )
        elif isinstance(item, SessionFolder):
            found, position = self.folder_store.find(item)
            if found:
                menu_model = _get_create_folder_menu()(
                    item,
                    self.folder_store,
                    position,
                    self.session_store,
                    self.has_clipboard_content(),
                )
        elif isinstance(item, LayoutItem):
            menu_model = Gio.Menu()
            menu_model.append(_("Restore Layout"), f"win.restore_layout('{item.name}')")
            menu_model.append(
                _("Move to Folder..."), f"win.move-layout-to-folder('{item.name}')"
            )
            menu_model.append_section(None, Gio.Menu())
            menu_model.append(_("Delete Layout"), f"win.delete_layout('{item.name}')")

        if menu_model:
            anchor_widget = list_item.get_child()
            popover = create_themed_popover_menu(menu_model, self.parent_window)

            point = Graphene.Point()
            point.x = x
            point.y = y

            rect = Gdk.Rectangle()
            success, translated = anchor_widget.compute_point(self.parent_window, point)

            if success:
                rect.x = int(translated.x)
                rect.y = int(translated.y)
            else:
                rect.x = int(x)
                rect.y = int(y)

            rect.width = 1
            rect.height = 1
            popover.set_pointing_to(rect)
            popover.popup()

    def _on_empty_area_right_click(
        self, _gesture: Gtk.GestureClick, _n_press: int, x: float, y: float
    ) -> None:
        """Shows a context menu for the empty area (root)."""
        self.logger.debug(f"_on_empty_area_right_click called at x={x}, y={y}")
        self.selection_model.unselect_all()

        # Check if sidebar popover is visible - use inline context menu if so
        sidebar_popover = getattr(
            getattr(self.parent_window, "ui_builder", None), "sidebar_popover", None
        )

        popover_visible = sidebar_popover and sidebar_popover.get_visible()
        self.logger.debug(f"Sidebar popover visible: {popover_visible}")

        if popover_visible:
            # Show inline context menu within the popover
            self.logger.debug("Showing inline root context menu")
            self._show_inline_root_context_menu()
        else:
            # Show traditional popover menu (for non-popover sidebar mode)
            self.logger.debug("Showing traditional popover root context menu")
            self._show_popover_root_context_menu(x, y)

    def _show_inline_root_context_menu(self) -> None:
        """Show the inline context menu for root (empty area) within the sidebar popover."""
        ui_builder = getattr(self.parent_window, "ui_builder", None)
        if not ui_builder:
            return

        content_stack = getattr(ui_builder, "sidebar_content_stack", None)
        inline_menu_box = getattr(ui_builder, "inline_context_menu_box", None)

        if not content_stack or not inline_menu_box:
            return

        # Clear previous inline menu content
        while (child := inline_menu_box.get_first_child()) is not None:
            inline_menu_box.remove(child)

        # Create inline context menu widget
        InlineContextMenu = _get_inline_context_menu()
        inline_menu = InlineContextMenu(self.parent_window)

        # Set up go back callback
        def go_back():
            content_stack.set_visible_child_name("normal")

        inline_menu.set_go_back_callback(go_back)
        inline_menu.show_for_root(self.has_clipboard_content())

        inline_menu_box.append(inline_menu)

        # Switch to context menu view
        content_stack.set_visible_child_name("context-menu")

    def _show_popover_root_context_menu(self, x: float, y: float) -> None:
        """Show the traditional popover context menu for root."""
        menu_model = _get_create_root_menu()(self.has_clipboard_content())
        popover = create_themed_popover_menu(menu_model, self.parent_window)

        point = Graphene.Point()
        point.x = x
        point.y = y

        rect = Gdk.Rectangle()
        success, translated = self.column_view.compute_point(self.parent_window, point)

        if success:
            rect.x = int(translated.x)
            rect.y = int(translated.y)
        else:
            rect.x = int(x)
            rect.y = int(y)

        rect.width = 1
        rect.height = 1
        popover.set_pointing_to(rect)
        popover.popup()

    def has_clipboard_content(self) -> bool:
        """Checks if there is a valid item in the clipboard."""
        is_valid = self._clipboard_item is not None
        if not is_valid:
            self._clipboard_item = None
        return is_valid

    def _copy_selected_item(self) -> None:
        """Copies the selected item to the internal clipboard."""
        if item := self.get_selected_item():
            if isinstance(item, (SessionItem, SessionFolder)):
                self._clipboard_item = item
                self._clipboard_is_cut = False

    def _cut_selected_item(self) -> None:
        """Marks the selected item for cutting."""
        if item := self.get_selected_item():
            if isinstance(item, (SessionItem, SessionFolder)):
                self._clipboard_item = item
                self._clipboard_is_cut = True

    def _paste_item(self, target_folder_path: str) -> None:
        """
        Delegates the paste logic to the operations layer.
        """
        if not self.has_clipboard_content():
            return

        item_to_paste = self._clipboard_item
        is_cut = self._clipboard_is_cut
        self._clipboard_item, self._clipboard_is_cut = None, False

        try:
            result = self.operations.paste_item(
                item_to_paste, target_folder_path, is_cut
            )
            if result and result.success:
                self.refresh_tree()
            elif result:
                if hasattr(self.parent_window, "_show_error_dialog"):
                    self.parent_window._show_error_dialog(
                        _("Paste Error"), result.message
                    )
        except Exception as e:
            self.logger.error(f"Paste operation failed: {e}")
            if hasattr(self.parent_window, "_show_error_dialog"):
                self.parent_window._show_error_dialog(_("Paste Error"), str(e))
