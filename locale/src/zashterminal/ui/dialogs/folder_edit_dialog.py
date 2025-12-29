# zashterminal/ui/dialogs/folder_edit_dialog.py

from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from ...sessions.models import SessionFolder
from ...utils.platform import normalize_path
from ...utils.translation_utils import _
from .base_dialog import BaseDialog


class FolderEditDialog(BaseDialog):
    def __init__(
        self,
        parent_window,
        folder_store,
        folder_item: Optional[SessionFolder] = None,
        position: Optional[int] = None,
        is_new: bool = False,
    ):
        self.is_new_item = is_new
        title = _("Add Folder") if self.is_new_item else _("Edit Folder")
        super().__init__(parent_window, title, default_width=600, default_height=500)
        self.folder_store = folder_store
        self.original_folder = folder_item if not self.is_new_item else None
        self.editing_folder = (
            SessionFolder.from_dict(folder_item.to_dict())
            if not self.is_new_item
            else folder_item
        )
        self.position = position
        self.old_path = folder_item.path if folder_item else None
        self.parent_paths_map: dict[str, str] = {}
        self._setup_ui()
        self.connect("map", self._on_map)
        self.logger.info(
            f"Folder edit dialog opened: {self.editing_folder.name} ({'new' if self.is_new_item else 'edit'})"
        )

    def _on_map(self, widget):
        if self.name_entry:
            self.name_entry.grab_focus()

    def _setup_ui(self) -> None:
        try:
            main_box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=16,
                margin_top=24,
                margin_bottom=24,
                margin_start=24,
                margin_end=24,
            )
            self._create_folder_section(main_box)
            action_bar = self._create_action_bar()
            content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            scrolled_window = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
            scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scrolled_window.set_child(main_box)
            content_box.append(scrolled_window)
            content_box.append(action_bar)
            self.set_content(content_box)
        except Exception as e:
            self.logger.error(f"Failed to setup UI: {e}")
            self._show_error_dialog(
                _("UI Error"), _("Failed to initialize dialog interface")
            )
            self.close()

    def _create_folder_section(self, parent: Gtk.Box) -> None:
        # Folder Information Section
        folder_info_group = Adw.PreferencesGroup(title=_("Folder Information"))
        self._create_name_row(folder_info_group)
        parent.append(folder_info_group)

        # Organization Section
        if self.folder_store and self.folder_store.get_n_items() > 0:
            organization_group = Adw.PreferencesGroup(title=_("Organization"))
            self._create_parent_row(organization_group)
            parent.append(organization_group)

    def _create_name_row(self, parent: Adw.PreferencesGroup) -> None:
        name_row = Adw.ActionRow(
            title=_("Folder Name"),
            subtitle=_("A descriptive name for organizing sessions"),
        )
        self.name_entry = Gtk.Entry(
            text=self.editing_folder.name,
            placeholder_text=_("Enter folder name..."),
            hexpand=True,
        )
        self.name_entry.connect("changed", self._on_name_changed)
        self.name_entry.connect("activate", self._on_save_clicked)
        name_row.add_suffix(self.name_entry)
        name_row.set_activatable_widget(self.name_entry)
        parent.add(name_row)

    def _create_parent_row(self, parent: Adw.PreferencesGroup) -> None:
        parent_row = Adw.ComboRow(
            title=_("Parent Folder"),
            subtitle=_("Choose a parent folder for organization"),
        )
        parent_model = Gtk.StringList()
        parent_model.append(_("Root"))
        self.parent_paths_map = {_("Root"): ""}
        folders = sorted(
            [
                self.folder_store.get_item(i)
                for i in range(self.folder_store.get_n_items())
            ],
            key=lambda f: f.path,
        )
        selected_index = 0
        for folder in folders:
            display_name = f"{'  ' * folder.path.count('/')}{folder.name}"
            parent_model.append(display_name)
            self.parent_paths_map[display_name] = folder.path
            if self.editing_folder and folder.path == self.editing_folder.parent_path:
                selected_index = parent_model.get_n_items() - 1
        parent_row.set_model(parent_model)
        parent_row.set_selected(selected_index)
        parent_row.connect("notify::selected", self._on_parent_changed)
        self.parent_combo = parent_row
        parent.add(parent_row)

    def _create_action_bar(self) -> Gtk.ActionBar:
        action_bar = Gtk.ActionBar()
        cancel_button = Gtk.Button(label=_("Cancel"))
        cancel_button.set_valign(Gtk.Align.CENTER)
        cancel_button.connect("clicked", self._on_cancel_clicked)
        action_bar.pack_start(cancel_button)
        save_button = Gtk.Button(label=_("Save"), css_classes=["suggested-action"])
        save_button.set_valign(Gtk.Align.CENTER)
        save_button.connect("clicked", self._on_save_clicked)
        action_bar.pack_end(save_button)
        self.set_default_widget(save_button)
        return action_bar

    def _on_name_changed(self, entry: Gtk.Entry) -> None:
        entry.remove_css_class("error")
        self._mark_changed()

    def _on_parent_changed(self, combo_row, param) -> None:
        self._mark_changed()

    def _on_cancel_clicked(self, button) -> None:
        if self._has_changes:
            self._show_warning_dialog(
                _("Unsaved Changes"),
                _("You have unsaved changes. Are you sure you want to cancel?"),
                lambda: self.close(),
            )
        else:
            self.close()

    def _on_save_clicked(self, button) -> None:
        try:
            operations = self.parent_window.session_tree.operations
            updated_folder = self._build_updated_folder()
            if not updated_folder:
                return
            result = (
                operations.add_folder(updated_folder)
                if self.is_new_item
                else operations.update_folder(self.position, updated_folder)
            )
            if result and result.success:
                self.logger.info(
                    f"Folder {'created' if self.is_new_item else 'updated'}: {updated_folder.name}"
                )
                # Tree refresh is handled automatically via AppSignals
                self.close()
            elif result:
                self._show_error_dialog(_("Save Error"), result.message)
        except Exception as e:
            self.logger.error(f"Save handling failed: {e}")
            self._show_error_dialog(
                _("Save Error"), _("Failed to save folder: {}").format(e)
            )

    def _build_updated_folder(self) -> Optional[SessionFolder]:
        self._clear_validation_errors()
        if not self._validate_required_field(self.name_entry, _("Folder name")):
            return None
        name = self.name_entry.get_text().strip()
        parent_path = ""
        if hasattr(self, "parent_combo") and self.parent_combo:
            selected_item = self.parent_combo.get_selected_item()
            if selected_item:
                parent_path = self.parent_paths_map.get(selected_item.get_string(), "")
        new_path = normalize_path(
            f"{parent_path}/{name}" if parent_path else f"/{name}"
        )
        updated_data = self.editing_folder.to_dict()
        updated_data.update({
            "name": name,
            "parent_path": parent_path,
            "path": str(new_path),
        })
        return SessionFolder.from_dict(updated_data)
