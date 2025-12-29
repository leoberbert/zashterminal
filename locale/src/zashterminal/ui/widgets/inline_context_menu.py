# zashterminal/ui/widgets/inline_context_menu.py

"""
Inline context menu widget for displaying context menu options
within the sidebar popover instead of as a separate popup.
"""

from typing import TYPE_CHECKING, Callable, Optional, Union

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gio, GLib, Gtk

from ...sessions.models import LayoutItem, SessionFolder, SessionItem
from ...utils.logger import get_logger
from ...utils.translation_utils import _

if TYPE_CHECKING:
    from ...window import CommTerminalWindow


class InlineContextMenu(Gtk.Box):
    """
    A widget that displays context menu options inline within the sidebar.
    
    Instead of showing a popup menu that closes the popover, this widget
    replaces the session list content with a list of action buttons and
    a "Go Back" button to return to the session list.
    """

    def __init__(self, parent_window: "CommTerminalWindow"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.logger = get_logger("zashterminal.ui.inline_context_menu")
        self.parent_window = parent_window
        self._current_item: Optional[Union[SessionItem, SessionFolder, LayoutItem]] = None
        self._on_go_back: Optional[Callable[[], None]] = None

        self.add_css_class("inline-context-menu")
        self.set_vexpand(True)

        # Back button row (on its own line)
        back_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            margin_start=8,
            margin_end=8,
            margin_top=8,
            margin_bottom=4
        )

        self._back_button = Gtk.Button()
        back_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        back_icon = Gtk.Image.new_from_icon_name("go-previous-symbolic")
        back_label = Gtk.Label(label=_("Back"))
        back_content.append(back_icon)
        back_content.append(back_label)
        self._back_button.set_child(back_content)
        self._back_button.add_css_class("flat")
        self._back_button.add_css_class("circular")
        self._back_button.connect("clicked", self._on_back_clicked)
        back_row.append(self._back_button)

        self.append(back_row)

        # Item name/title row
        title_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            margin_start=12,
            margin_end=12,
            margin_top=4,
            margin_bottom=8
        )

        self._item_label = Gtk.Label(
            xalign=0.0,
            hexpand=True,
            ellipsize=3  # Pango.EllipsizeMode.END
        )
        self._item_label.add_css_class("title-3")
        title_row.append(self._item_label)

        self.append(title_row)
        self.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # Scrolled window for action buttons
        self._scrolled_window = Gtk.ScrolledWindow(vexpand=True)
        self._scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._actions_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=2,
            margin_start=8,
            margin_end=8,
            margin_top=8,
            margin_bottom=8
        )
        self._scrolled_window.set_child(self._actions_box)
        self.append(self._scrolled_window)

    def set_go_back_callback(self, callback: Callable[[], None]) -> None:
        """Set the callback to be called when the back button is clicked."""
        self._on_go_back = callback

    def _on_back_clicked(self, _button: Gtk.Button) -> None:
        """Handle back button click."""
        if self._on_go_back:
            self._on_go_back()

    def _clear_actions(self) -> None:
        """Clear all action buttons from the menu."""
        while (child := self._actions_box.get_first_child()) is not None:
            self._actions_box.remove(child)

    def show_for_session(
        self,
        session_item: SessionItem,
        folder_store: Gio.ListStore,
        has_clipboard: bool
    ) -> None:
        """Display context menu options for a session item."""
        self.logger.debug(f"show_for_session called for: {session_item.name}")
        self._current_item = session_item
        self._item_label.set_label(session_item.name)
        self._clear_actions()

        # SFTP option for SSH sessions
        if session_item.is_ssh():
            self.logger.debug("Adding SFTP option (SSH session)")
            self._add_action_button(
                _("Connect with SFTP"),
                "folder-remote-symbolic",
                "win.connect-sftp"
            )
            self._add_separator()

        # Standard session actions
        self.logger.debug("Adding standard session actions")
        self._add_action_button(_("Edit"), "document-edit-symbolic", "win.edit-session")
        self._add_action_button(_("Duplicate"), "edit-copy-symbolic", "win.duplicate-session")
        self._add_action_button(_("Rename"), "text-editor-symbolic", "win.rename-session")

        # Move to folder (if folders exist)
        if folder_store and folder_store.get_n_items() > 0:
            self._add_separator()
            self._add_action_button(
                _("Move to Folder..."),
                "folder-symbolic",
                "win.move-session-to-folder"
            )

        # Delete action
        self._add_separator()
        self._add_action_button(
            _("Delete"),
            "user-trash-symbolic",
            "win.delete-session",
            is_destructive=True
        )
        self.logger.debug("Finished adding actions for session")

    def show_for_folder(
        self,
        folder_item: SessionFolder,
        has_clipboard: bool
    ) -> None:
        """Display context menu options for a folder item."""
        self._current_item = folder_item
        self._item_label.set_label(folder_item.name)
        self._clear_actions()

        # Standard folder actions
        self._add_action_button(_("Edit"), "document-edit-symbolic", "win.edit-folder")
        self._add_action_button(
            _("Add Session Here"),
            "list-add-symbolic",
            "win.add-session-to-folder"
        )
        self._add_action_button(_("Rename"), "text-editor-symbolic", "win.rename-folder")

        # Paste option (if clipboard has content)
        if has_clipboard:
            self._add_separator()
            self._add_action_button(_("Paste"), "edit-paste-symbolic", "win.paste-item")

        # Delete action
        self._add_separator()
        self._add_action_button(
            _("Delete"),
            "user-trash-symbolic",
            "win.delete-folder",
            is_destructive=True
        )

    def show_for_layout(self, layout_item: LayoutItem) -> None:
        """Display context menu options for a layout item."""
        self._current_item = layout_item
        self._item_label.set_label(layout_item.name)
        self._clear_actions()

        # Layout actions
        self._add_action_button(
            _("Restore Layout"),
            "view-restore-symbolic",
            f"win.restore_layout('{layout_item.name}')"
        )
        self._add_action_button(
            _("Move to Folder..."),
            "folder-symbolic",
            f"win.move-layout-to-folder('{layout_item.name}')"
        )

        # Delete action
        self._add_separator()
        self._add_action_button(
            _("Delete Layout"),
            "user-trash-symbolic",
            f"win.delete_layout('{layout_item.name}')",
            is_destructive=True
        )

    def show_for_root(self, has_clipboard: bool) -> None:
        """Display context menu options for the root (empty area)."""
        self._current_item = None
        self._item_label.set_label(_("Sessions"))
        self._clear_actions()

        # Root actions
        self._add_action_button(_("Add Session"), "list-add-symbolic", "win.add-session-root")
        self._add_action_button(_("Add Folder"), "folder-new-symbolic", "win.add-folder-root")

        # Paste option (if clipboard has content)
        if has_clipboard:
            self._add_separator()
            self._add_action_button(
                _("Paste to Root"),
                "edit-paste-symbolic",
                "win.paste-item-root"
            )

    def _add_action_button(
        self,
        label: str,
        icon_name: str,
        action_name: str,
        is_destructive: bool = False
    ) -> None:
        """Add an action button to the menu."""
        button = Gtk.Button()
        button.add_css_class("flat")
        button.set_halign(Gtk.Align.FILL)

        if is_destructive:
            button.add_css_class("destructive-action")

        content_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=10,
            margin_start=4,
            margin_end=4,
            margin_top=6,
            margin_bottom=6
        )

        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_icon_size(Gtk.IconSize.NORMAL)
        content_box.append(icon)

        action_label = Gtk.Label(label=label, xalign=0.0, hexpand=True)
        content_box.append(action_label)

        button.set_child(content_box)

        # Connect action - the action includes parameters for some layout actions
        if "(" in action_name:
            # Complex action with parameter
            button.set_action_name(action_name.split("(")[0])
            param_str = action_name.split("(")[1].rstrip(")")
            param_str = param_str.strip("'\"")
            button.set_action_target_value(GLib.Variant.new_string(param_str))
        else:
            button.set_action_name(action_name)

        # Go back after action is triggered
        button.connect("clicked", self._on_action_clicked)

        self._actions_box.append(button)

    def _add_separator(self) -> None:
        """Add a separator to the menu."""
        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        separator.set_margin_top(4)
        separator.set_margin_bottom(4)
        self._actions_box.append(separator)

    def _on_action_clicked(self, _button: Gtk.Button) -> None:
        """Handle action button click - go back to session list after action."""
        # Use idle_add to ensure the action is processed before going back
        GLib.idle_add(self._delayed_go_back)

    def _delayed_go_back(self) -> bool:
        """Go back to session list after a short delay."""
        if self._on_go_back:
            self._on_go_back()
        return GLib.SOURCE_REMOVE
