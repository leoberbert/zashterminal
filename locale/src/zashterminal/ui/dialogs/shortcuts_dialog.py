# zashterminal/ui/dialogs/shortcuts_dialog.py

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk

from ...helpers import accelerator_to_label
from ...utils.tooltip_helper import get_tooltip_helper
from ...utils.translation_utils import _

# Centralized data structure for shortcuts.
# This makes adding, removing, or reorganizing shortcuts much easier.
SHORTCUT_DATA = [
    {
        "group_title": _("Tab &amp; Pane Management"),
        "shortcuts": [
            ("new-local-tab", _("New Tab")),
            ("close-tab", _("Close Tab")),
            ("next-tab", _("Next Tab")),
            ("previous-tab", _("Previous Tab")),
        ],
    },
    {
        "group_title": _("Splitting"),
        "shortcuts": [
            ("split-horizontal", _("Split Horizontally")),
            ("split-vertical", _("Split Vertically")),
            ("close-pane", _("Close Active Pane")),
        ],
    },
    {
        "group_title": _("Terminal Interaction"),
        "shortcuts": [
            ("copy", _("Copy")),
            ("paste", _("Paste")),
            ("select-all", _("Select All")),
            ("clear-session", _("Clear Session")),
            ("toggle-search", _("Search in Terminal")),
            ("toggle-broadcast", _("Send Command to All Tabs")),
            ("ai-assistant", _("Ask AI Assistant")),
        ],
    },
    {
        "group_title": _("Zoom"),
        "shortcuts": [
            ("zoom-in", _("Zoom In")),
            ("zoom-out", _("Zoom Out")),
            ("zoom-reset", _("Reset Zoom")),
        ],
    },
    {
        "group_title": _("Application &amp; Window"),
        "shortcuts": [
            ("toggle-sidebar", _("Sessions Panel")),
            ("toggle-file-manager", _("File Manager")),
            ("show-command-manager", _("Command Manager")),
            ("new-window", _("New Window")),
            ("preferences", _("Preferences")),
            ("quit", _("Quit Application")),
        ],
    },
]


class ShortcutsDialog(Adw.PreferencesWindow):
    """
    A dialog for viewing and editing keyboard shortcuts.
    Uses native PreferencesWindow scrolling for a clean UI.
    """

    def __init__(self, parent_window):
        super().__init__(
            transient_for=parent_window,
            title=_("Keyboard Shortcuts"),
            search_enabled=False,
            default_width=700,
            default_height=600,
        )
        self.add_css_class("zashterminal-dialog")
        self.app = parent_window.get_application()
        self.settings_manager = parent_window.settings_manager
        self.shortcut_groups = []
        self.shortcut_rows = {}  # Store row references by action_name

        # Add reset button to header bar
        reset_button = Gtk.Button(label=_("Reset All"))
        get_tooltip_helper().add_tooltip(
            reset_button, _("Reset all shortcuts to default values")
        )
        reset_button.connect("clicked", self._on_reset_all_clicked)

        # Try to add button to existing header bar
        header_bar = self.get_titlebar()
        if header_bar:
            # Try different methods to add the button
            if hasattr(header_bar, 'pack_end'):
                header_bar.pack_end(reset_button)
            elif hasattr(header_bar, 'add'):
                header_bar.add(reset_button)
            else:
                # If we can't modify the header bar, add button as a group
                self._add_reset_button_as_group(reset_button)
        else:
            # No header bar available, add as group
            self._add_reset_button_as_group(reset_button)

        self._build_ui()

    def _add_reset_button_as_group(self, reset_button):
        """Add reset button as a preferences group when header bar modification fails."""
        self.reset_button = reset_button  # Store for later use in _build_ui

    def _build_ui(self):
        """Builds the UI by creating groups and setting up the responsive grid."""
        page = Adw.PreferencesPage()
        self.add(page)

        # Add reset button as a group if it wasn't added to header bar
        if hasattr(self, 'reset_button'):
            reset_group = Adw.PreferencesGroup()
            reset_row = Adw.ActionRow(title=_("Reset all shortcuts to defaults"), activatable=False)
            reset_row.add_suffix(self.reset_button)
            reset_group.add(reset_row)
            page.add(reset_group)

        # Create all shortcut groups directly on the preferences page
        # The PreferencesWindow handles scrolling natively
        for group_data in SHORTCUT_DATA:
            group = Adw.PreferencesGroup(title=group_data["group_title"])
            for action_name, title in group_data["shortcuts"]:
                row = self._create_shortcut_row(action_name, title)
                group.add(row)
                # Store row reference for direct access during reset
                self.shortcut_rows[action_name] = row
            self.shortcut_groups.append(group)
            page.add(group)

    def _create_shortcut_row(self, action_name: str, title: str) -> Adw.ActionRow:
        """Creates a single row for a shortcut with an edit button."""
        action_prefix = "app" if action_name in ["quit"] else "win"
        full_action_name = f"{action_prefix}.{action_name}"
        accels = self.app.get_accels_for_action(full_action_name)
        current_accel = accels[0] if accels else ""

        row = Adw.ActionRow(
            title=title,
            subtitle=accelerator_to_label(current_accel) or _("Not set"),
            activatable=False,
        )
        row.set_title_selectable(False)
        get_tooltip_helper().add_tooltip(row, _("Shortcut for: ") + title)

        button = Gtk.Button(label=_("Edit"))
        button.set_valign(Gtk.Align.CENTER)
        get_tooltip_helper().add_tooltip(
            button, _("Click to change the keyboard shortcut for this action")
        )
        button.connect("clicked", self._on_edit_clicked, action_name, row)
        row.add_suffix(button)

        return row

    def _on_edit_clicked(self, button, shortcut_key, row_to_update):
        """Handles the click on the 'Edit' button for a shortcut."""
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Set New Shortcut"),
            body=_("Press the new key combination for '{}', or Esc to cancel.").format(
                row_to_update.get_title()
            ),
            close_response="cancel",
        )

        current_shortcut = self.settings_manager.get_shortcut(shortcut_key)
        current_label = accelerator_to_label(current_shortcut) or _("Not set")
        feedback_label = Gtk.Label(
            label=_("Current: {}\nNew: (press keys)").format(current_label)
        )
        dialog.set_extra_child(feedback_label)

        key_controller = Gtk.EventControllerKey.new()
        new_shortcut_ref = [None]

        def on_key_pressed(_controller, keyval, _keycode, state):
            # Ignore modifier-only key presses
            if keyval in (
                Gdk.KEY_Control_L,
                Gdk.KEY_Control_R,
                Gdk.KEY_Shift_L,
                Gdk.KEY_Shift_R,
                Gdk.KEY_Alt_L,
                Gdk.KEY_Alt_R,
                Gdk.KEY_Super_L,
                Gdk.KEY_Super_R,
            ):
                return Gdk.EVENT_PROPAGATE

            if keyval == Gdk.KEY_Escape:
                new_shortcut_ref[0] = "cancel"
                dialog.response("cancel")
                return Gdk.EVENT_STOP

            shortcut_string = Gtk.accelerator_name(
                keyval, state & Gtk.accelerator_get_default_mod_mask()
            )
            new_shortcut_ref[0] = shortcut_string
            label_text = accelerator_to_label(shortcut_string)

            # Check for conflicts
            conflict_action = None
            for action in self.app.list_actions():
                accels = self.app.get_accels_for_action(action)
                if shortcut_string in accels:
                    conflict_action = action
                    break

            if conflict_action:
                feedback_label.set_label(
                    _("Current: {}\nNew: {} (Conflicts with {})").format(
                        current_label, label_text, conflict_action.split('.')[-1].replace('-', ' ').title()
                    )
                )
            else:
                feedback_label.set_label(
                    _("Current: {}\nNew: {}").format(current_label, label_text)
                )
            return Gdk.EVENT_STOP

        key_controller.connect("key-pressed", on_key_pressed)
        dialog.add_controller(key_controller)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("clear", _("Clear"))
        dialog.add_response("save", _("Set Shortcut"))
        dialog.set_default_response("save")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(dlg, response_id):
            action_prefix = "app" if shortcut_key in ["quit"] else "win"
            full_action_name = f"{action_prefix}.{shortcut_key}"
            new_shortcut = ""

            if (
                response_id == "save"
                and new_shortcut_ref[0]
                and new_shortcut_ref[0] != "cancel"
            ):
                new_shortcut = new_shortcut_ref[0]

            if response_id in ["save", "clear"]:
                self.settings_manager.set_shortcut(shortcut_key, new_shortcut)
                self.app.set_accels_for_action(
                    full_action_name, [new_shortcut] if new_shortcut else []
                )
                row_to_update.set_subtitle(
                    accelerator_to_label(new_shortcut) or _("Not set")
                )

            dlg.close()

        dialog.connect("response", on_response)
        dialog.present()

    def _on_reset_all_clicked(self, button):
        """Handles the click on the 'Reset All' button."""
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Reset All Shortcuts"),
            body=_("This will reset all keyboard shortcuts to their default values. Continue?"),
            close_response="cancel",
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("reset", _("Reset"))
        dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")

        def on_reset_response(dlg, response_id):
            if response_id == "reset":
                # Reset all shortcuts to defaults
                self.settings_manager.reset_to_defaults(["shortcuts"])

                # Update all rows and accelerators immediately using stored references
                for action_name in self.shortcut_rows:
                    # Get the reset shortcut from settings
                    current_shortcut = self.settings_manager.get_shortcut(action_name)

                    # Update the app accelerators
                    action_prefix = "app" if action_name in ["quit"] else "win"
                    full_action_name = f"{action_prefix}.{action_name}"
                    self.app.set_accels_for_action(
                        full_action_name, [current_shortcut] if current_shortcut else []
                    )

                    # Update the corresponding row in the UI directly
                    row = self.shortcut_rows[action_name]
                    row.set_subtitle(accelerator_to_label(current_shortcut) or _("Not set"))
            dlg.close()

        dialog.connect("response", on_reset_response)
        dialog.present()
