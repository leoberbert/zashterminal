# zashterminal/state/window_state.py

import json
import os
from typing import TYPE_CHECKING, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import Adw, Gtk, Vte

from ..sessions.models import LayoutItem, SessionItem
from ..settings.config import LAYOUT_DIR, STATE_FILE
from ..utils.logger import get_logger
from ..utils.security import InputSanitizer
from ..utils.translation_utils import _

if TYPE_CHECKING:
    from ..window import CommTerminalWindow


class WindowStateManager:
    """
    Manages saving and restoring the window's state, including tabs,
    splits, and user-defined layouts.
    """

    def __init__(self, window: "CommTerminalWindow"):
        self.window = window
        self.settings_manager = window.settings_manager
        self.tab_manager = window.tab_manager
        self.terminal_manager = window.terminal_manager
        self.logger = get_logger("zashterminal.state")

    def save_session_state(self):
        """Serializes the current tab and pane layout to a state file."""
        state = {"tabs": []}
        for page in self.tab_manager.pages.values():
            tab_content = page.get_child()
            if tab_content:
                tab_structure = self._serialize_widget_tree(tab_content)
                if tab_structure:
                    state["tabs"].append(tab_structure)

        try:
            with open(STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
            self.logger.info("Session state saved successfully.")
        except Exception as e:
            self.logger.error(f"Failed to save session state: {e}")

    def restore_session_state(self) -> bool:
        """Restores the window layout from the state file if applicable."""
        policy = self.settings_manager.get("session_restore_policy", "never")
        if policy == "never" or not os.path.exists(STATE_FILE):
            self.clear_session_state()
            return False

        try:
            with open(STATE_FILE, "r") as f:
                state = json.load(f)
        except Exception as e:
            self.logger.error(f"Failed to read session state file: {e}")
            return False

        if not state.get("tabs"):
            return False

        self.logger.info(f"Restoring {len(state['tabs'])} tabs from previous session.")
        for tab_structure in state["tabs"]:
            self.tab_manager.recreate_tab_from_structure(tab_structure)

        self.clear_session_state()
        return True

    def clear_session_state(self):
        """Removes the state file to prevent restoration on next startup."""
        if os.path.exists(STATE_FILE):
            try:
                os.remove(STATE_FILE)
                self.logger.info("Session state file removed.")
            except OSError as e:
                self.logger.error(f"Failed to remove state file: {e}")

    def save_current_layout(self):
        """Prompts for a name and saves the current window layout."""
        dialog = Adw.MessageDialog(
            transient_for=self.window,
            heading=_("Save Layout"),
            body=_("Enter a name for the current layout:"),
            close_response="cancel",
        )
        entry = Gtk.Entry(
            placeholder_text=_("e.g., 'My Dev Setup'"),
            hexpand=True,
            activates_default=True,
        )
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("save", _("Save"))
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.connect("response", self._on_save_layout_dialog_response, entry)
        dialog.present()

    def _on_save_layout_dialog_response(self, dialog, response_id, entry):
        dialog.close()
        if response_id != "save":
            return

        layout_name = entry.get_text().strip()
        if not layout_name:
            self.window.toast_overlay.add_toast(
                Adw.Toast(title=_("Layout name cannot be empty."))
            )
            return

        sanitized_name = InputSanitizer.sanitize_filename(layout_name).replace(" ", "_")
        target_file = os.path.join(LAYOUT_DIR, f"{sanitized_name}.json")

        if os.path.exists(target_file):
            self.logger.warning(f"Overwriting existing layout: {sanitized_name}")

        state = {"tabs": [], "folder_path": ""}
        for page in self.tab_manager.pages.values():
            tab_content = page.get_child()
            if tab_content:
                tab_structure = self._serialize_widget_tree(tab_content)
                if tab_structure:
                    state["tabs"].append(tab_structure)

        try:
            with open(target_file, "w") as f:
                json.dump(state, f, indent=2)
            self.logger.info(f"Layout '{layout_name}' saved successfully.")
            self.window.toast_overlay.add_toast(Adw.Toast(title=_("Layout Saved")))
            self.load_layouts()
            self.window.refresh_tree()
        except Exception as e:
            self.logger.error(f"Failed to save layout '{layout_name}': {e}")
            self.window._show_error_dialog(_("Error Saving Layout"), str(e))

    def restore_saved_layout(self, layout_name: str):
        """Restores a previously saved layout, replacing the current one."""
        sanitized_name = InputSanitizer.sanitize_filename(layout_name).replace(" ", "_")
        layout_file = os.path.join(LAYOUT_DIR, f"{sanitized_name}.json")

        if not os.path.exists(layout_file):
            self.window.toast_overlay.add_toast(
                Adw.Toast(title=_("Saved layout not found."))
            )
            return

        dialog = Adw.MessageDialog(
            transient_for=self.window,
            heading=_("Restore Saved Layout?"),
            body=_(
                "This will close all current tabs and restore the '{name}' layout. Are you sure?"
            ).format(name=layout_name),
            close_response="cancel",
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("restore", _("Restore Layout"))
        dialog.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.connect("response", self._on_restore_layout_dialog_response, layout_file)
        dialog.present()

    def _on_restore_layout_dialog_response(self, dialog, response_id, layout_file):
        dialog.close()
        if response_id == "restore":
            self._perform_layout_restore(layout_file)

    def _perform_layout_restore(self, layout_file: str):
        try:
            with open(layout_file, "r") as f:
                state = json.load(f)
        except Exception as e:
            self.logger.error(f"Failed to read layout file: {layout_file}: {e}")
            self.window._show_error_dialog(_("Error Restoring Layout"), str(e))
            return

        if not state.get("tabs"):
            self.logger.warning(f"Layout file '{layout_file}' is empty or invalid.")
            return

        self.window.tab_manager.close_all_tabs()
        self.logger.info(f"Restoring {len(state['tabs'])} tabs from saved layout.")
        for tab_structure in state["tabs"]:
            self.tab_manager.recreate_tab_from_structure(tab_structure)

    def delete_saved_layout(self, layout_name: str, confirm: bool = True):
        """Deletes a saved layout file."""
        if confirm:
            dialog = Adw.MessageDialog(
                transient_for=self.window,
                heading=_("Delete Layout?"),
                body=_(
                    "Are you sure you want to permanently delete the layout '{name}'?"
                ).format(name=layout_name),
                close_response="cancel",
            )
            dialog.add_response("cancel", _("Cancel"))
            dialog.add_response("delete", _("Delete"))
            dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.connect(
                "response", self._on_delete_layout_dialog_response, layout_name
            )
            dialog.present()
        else:
            self._perform_delete_layout(layout_name)

    def _on_delete_layout_dialog_response(self, dialog, response_id, layout_name):
        dialog.close()
        if response_id == "delete":
            self._perform_delete_layout(layout_name)

    def _perform_delete_layout(self, layout_name: str):
        try:
            sanitized_name = InputSanitizer.sanitize_filename(layout_name).replace(
                " ", "_"
            )
            layout_file = os.path.join(LAYOUT_DIR, f"{sanitized_name}.json")
            os.remove(layout_file)
            self.logger.info(f"Layout '{layout_name}' deleted.")
            self.window.toast_overlay.add_toast(Adw.Toast(title=_("Layout Deleted")))
            self.load_layouts()
            self.window.refresh_tree()
        except Exception as e:
            self.logger.error(f"Failed to delete layout '{layout_name}': {e}")
            self.window._show_error_dialog(_("Error Deleting Layout"), str(e))

    def load_layouts(self):
        """Loads all saved layouts from the layout directory into the window's list."""
        self.window.layouts.clear()
        if not os.path.exists(LAYOUT_DIR):
            return
        for layout_file in sorted(os.listdir(LAYOUT_DIR)):
            if layout_file.endswith(".json"):
                layout_name = os.path.splitext(layout_file)[0].replace("_", " ")
                folder_path = ""
                try:
                    with open(os.path.join(LAYOUT_DIR, layout_file), "r") as f:
                        data = json.load(f)
                        folder_path = data.get("folder_path", "")
                except Exception as e:
                    self.logger.warning(
                        f"Could not read folder_path from {layout_file}: {e}"
                    )
                self.window.layouts.append(
                    LayoutItem(name=layout_name, folder_path=folder_path)
                )

    def move_layout(self, layout_name: str, old_folder: str, new_folder: str):
        """Moves a layout to a new virtual folder by updating its JSON file."""
        if old_folder == new_folder:
            return

        sanitized_name = InputSanitizer.sanitize_filename(layout_name).replace(" ", "_")
        layout_file = os.path.join(LAYOUT_DIR, f"{sanitized_name}.json")

        try:
            state = {}
            if os.path.exists(layout_file):
                with open(layout_file, "r") as f:
                    state = json.load(f)

            state["folder_path"] = new_folder

            with open(layout_file, "w") as f:
                json.dump(state, f, indent=2)

            self.logger.info(f"Moved layout '{layout_name}' to folder '{new_folder}'")
            self.load_layouts()
            self.window.refresh_tree()
        except Exception as e:
            self.logger.error(f"Failed to move layout '{layout_name}': {e}")
            self.window._show_error_dialog(_("Error Moving Layout"), str(e))

    def _serialize_widget_tree(self, widget) -> Optional[dict]:
        """Recursively serializes the widget tree into a dictionary."""
        if isinstance(widget, Gtk.Paned):
            child1_node = self._serialize_widget_tree(widget.get_start_child())
            child2_node = self._serialize_widget_tree(widget.get_end_child())

            # If child2 failed to serialize (e.g., it's a file manager or empty),
            # this is not a layout split. Just return the serialization of child1.
            if child2_node is None:
                return child1_node

            # Otherwise, it's a real split. Proceed as before.
            position = widget.get_position()
            orientation = widget.get_orientation()
            total_size = (
                widget.get_width()
                if orientation == Gtk.Orientation.HORIZONTAL
                else widget.get_height()
            )
            position_ratio = position / total_size if total_size > 0 else 0.5

            return {
                "type": "paned",
                "orientation": "horizontal"
                if orientation == Gtk.Orientation.HORIZONTAL
                else "vertical",
                "position_ratio": position_ratio,
                "child1": child1_node,
                "child2": child2_node,
            }

        terminal = None
        if isinstance(widget, Gtk.ScrolledWindow) and isinstance(
            widget.get_child(), Vte.Terminal
        ):
            terminal = widget.get_child()
        elif hasattr(widget, "terminal") and isinstance(widget.terminal, Vte.Terminal):
            terminal = widget.terminal
        elif isinstance(widget, Adw.Bin):
            return self._serialize_widget_tree(widget.get_child())

        if terminal:
            terminal_id = getattr(terminal, "terminal_id", None)
            info = self.terminal_manager.registry.get_terminal_info(terminal_id)
            if not info:
                return None

            uri = terminal.get_current_directory_uri()
            working_dir = None
            if uri:
                from urllib.parse import unquote, urlparse

                parsed_uri = urlparse(uri)
                if parsed_uri.scheme == "file":
                    working_dir = unquote(parsed_uri.path)

            session_info = info.get("identifier")
            if isinstance(session_info, SessionItem):
                return {
                    "type": "terminal",
                    "session_type": "ssh" if session_info.is_ssh() else "local",
                    "session_name": session_info.name,
                    "working_dir": working_dir,
                }
            else:
                return {
                    "type": "terminal",
                    "session_type": "local",
                    "session_name": str(session_info),
                    "working_dir": working_dir,
                }
        return None

