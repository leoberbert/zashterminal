# zashterminal/ui/color_scheme_dialog.py

from typing import Dict, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GObject, Gtk, Pango, PangoCairo

from ..helpers import generate_unique_name
from ..settings.manager import SettingsManager
from ..utils.logger import get_logger
from ..utils.translation_utils import _


# NEW: Custom widget for editing a color with a hex entry field.
class _ColorEditRow(Adw.ActionRow):
    """A row for editing a color, showing a swatch and a hex code entry."""

    def __init__(self, title: str):
        super().__init__(title=title)
        self.color_button = Gtk.ColorButton(valign=Gtk.Align.CENTER)
        self.hex_entry = Gtk.Entry(valign=Gtk.Align.CENTER, width_chars=9, max_length=9)
        self.hex_entry.add_css_class("monospace")

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.append(self.hex_entry)
        box.append(self.color_button)
        self.add_suffix(box)
        self.set_activatable_widget(self.hex_entry)

        self.color_button.connect("color-set", self._on_color_set)
        self.hex_entry.connect("changed", self._on_hex_changed)

    def get_hex_color(self) -> Optional[str]:
        """Returns the validated hex color string or None if invalid."""
        text = self.hex_entry.get_text().lower()
        rgba = Gdk.RGBA()
        if rgba.parse(text) and len(text) in [7, 9]:  # #RRGGBB or #RRGGBBAA
            return self.rgba_to_hex(rgba)
        return None

    def set_hex_color(self, hex_str: str):
        """
        Sets the color from a hex string. This is the single entry point for
        programmatically setting the color.
        """
        # FIX: Directly set the text in the entry. The 'changed' signal handler
        # (_on_hex_changed) will then automatically validate the text and update
        # the color button's swatch, ensuring a consistent state.
        self.hex_entry.get_buffer().set_text(hex_str, -1)

    def rgba_to_hex(self, rgba: Gdk.RGBA) -> str:
        """Converts a Gdk.RGBA object to a #RRGGBB hex string."""
        return "#{:02x}{:02x}{:02x}".format(
            int(rgba.red * 255), int(rgba.green * 255), int(rgba.blue * 255)
        )

    def _on_color_set(self, button: Gtk.ColorButton):
        """When color is picked from chooser, update the hex entry."""
        rgba = button.get_rgba()
        hex_str = self.rgba_to_hex(rgba)
        # Check if update is needed to prevent signal loops
        if self.hex_entry.get_text() != hex_str:
            self.hex_entry.get_buffer().set_text(hex_str, -1)

    def _on_hex_changed(self, entry: Gtk.Entry):
        """When hex entry changes, update the color button and validate."""
        text = entry.get_text()
        rgba = Gdk.RGBA()
        if rgba.parse(text):
            # Check if update is needed to prevent signal loops
            current_hex = self.rgba_to_hex(self.color_button.get_rgba())
            if text.lower() != current_hex.lower():
                self.color_button.set_rgba(rgba)
            entry.remove_css_class("error")
        else:
            entry.add_css_class("error")


class _SchemeEditorDialog(Adw.Window):
    """A sub-dialog for creating or editing a single color scheme."""

    __gsignals__ = {
        "save-requested": (
            GObject.SignalFlags.RUN_FIRST,
            None,
            (str, str, GObject.TYPE_PYOBJECT),
        ),
    }

    def __init__(
        self,
        parent,
        settings_manager: SettingsManager,
        scheme_key: str,
        scheme_data: Dict,
        is_new: bool,
    ):
        super().__init__(
            transient_for=parent, modal=True, default_width=700, default_height=600
        )
        # Add CSS class for theming
        self.add_css_class("zashterminal-dialog")

        self.settings_manager = settings_manager
        self.original_key = scheme_key if not is_new else None
        self.is_new = is_new
        self.set_title(_("Edit Scheme") if not is_new else _("New Scheme"))

        self.name_entry = Adw.EntryRow(title=_("Scheme Name"))
        self.name_entry.set_text(scheme_data.get("name", scheme_key))

        self.fg_row = _ColorEditRow(_("Foreground"))
        self.bg_row = _ColorEditRow(_("Background"))
        self.headerbar_row = _ColorEditRow(_("Headerbar Background"))
        self.cursor_row = _ColorEditRow(_("Cursor"))
        self.palette_rows: list[_ColorEditRow] = []

        self._build_ui()
        self._populate_colors(scheme_data)

        # Connect signals for live preview update
        for row in [self.fg_row, self.bg_row]:
            row.color_button.connect(
                "color-set", lambda *_: self.preview_area.queue_draw()
            )
            row.hex_entry.connect("changed", lambda *_: self.preview_area.queue_draw())

    def _build_ui(self):
        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        header = Adw.HeaderBar()
        toolbar_view.add_top_bar(header)

        cancel_button = Gtk.Button(label=_("Cancel"))
        cancel_button.set_valign(Gtk.Align.CENTER)
        cancel_button.connect("clicked", lambda _: self.close())
        header.pack_start(cancel_button)

        save_button = Gtk.Button(label=_("Save"), css_classes=["suggested-action"])
        save_button.set_valign(Gtk.Align.CENTER)
        save_button.connect("clicked", self._on_save)
        header.pack_end(save_button)

        page = Adw.PreferencesPage()

        preview_group = Adw.PreferencesGroup(title=_("Live Preview"))
        self.preview_area = Gtk.DrawingArea(
            content_height=80, margin_top=12, margin_bottom=12
        )
        self.preview_area.set_draw_func(self._draw_preview, None)
        preview_group.add(self.preview_area)
        page.add(preview_group)

        main_group = Adw.PreferencesGroup(title=_("General Colors"))
        page.add(main_group)

        main_group.add(self.name_entry)
        main_group.add(self.fg_row)
        main_group.add(self.bg_row)
        main_group.add(self.headerbar_row)
        main_group.add(self.cursor_row)

        palette_group = Adw.PreferencesGroup(title=_("16-Color Palette"))
        page.add(palette_group)

        grid = Gtk.Grid(
            column_spacing=24, row_spacing=12, margin_top=12, margin_bottom=12
        )
        palette_group.add(grid)

        for i in range(16):
            color_row = _ColorEditRow(f"Color {i}")
            self.palette_rows.append(color_row)
            grid.attach(color_row, i % 2, i // 2, 1, 1)

        scrolled = Gtk.ScrolledWindow(child=page, vexpand=True)
        toolbar_view.set_content(scrolled)

    def _populate_colors(self, scheme_data: Dict):
        self.fg_row.set_hex_color(scheme_data.get("foreground", "#FFFFFF"))
        self.bg_row.set_hex_color(scheme_data.get("background", "#000000"))
        headerbar_bg = scheme_data.get(
            "headerbar_background", scheme_data.get("background", "#000000")
        )
        self.headerbar_row.set_hex_color(headerbar_bg)
        self.cursor_row.set_hex_color(scheme_data.get("cursor", "#FFFFFF"))

        palette = scheme_data.get("palette", [])
        for i, row in enumerate(self.palette_rows):
            if i < len(palette):
                row.set_hex_color(palette[i])

    def _draw_preview(self, area, cr, width, height, _user_data):
        """Draw function for the live preview area."""
        bg_rgba = Gdk.RGBA()
        fg_rgba = Gdk.RGBA()

        bg_hex = self.bg_row.get_hex_color() or "#000000"
        fg_hex = self.fg_row.get_hex_color() or "#FFFFFF"

        bg_rgba.parse(bg_hex)
        fg_rgba.parse(fg_hex)

        Gdk.cairo_set_source_rgba(cr, bg_rgba)
        cr.rectangle(0, 0, width, height)
        cr.fill()

        Gdk.cairo_set_source_rgba(cr, fg_rgba)
        layout = area.create_pango_layout(
            _("user@host:~$ ls -l\n<span weight='bold'>Bold Text</span>")
        )
        font_desc = Pango.FontDescription.from_string("Monospace 12")
        layout.set_font_description(font_desc)
        cr.move_to(10, 10)
        PangoCairo.show_layout(cr, layout)

    def _show_error_dialog(self, title: str, message: str):
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=title,
            body=message,
        )
        dialog.add_response("ok", _("OK"))
        dialog.present()

    def _on_save(self, button):
        new_name = self.name_entry.get_text().strip()
        if not new_name:
            self._show_error_dialog(_("Name Error"), _("Scheme name cannot be empty."))
            return

        all_schemes = self.settings_manager.get_all_schemes()
        existing_display_names = {s_data.get("name") for s_data in all_schemes.values()}

        if not self.is_new and self.original_key:
            original_scheme_data = all_schemes.get(self.original_key)
            if original_scheme_data:
                original_display_name = original_scheme_data.get("name")
                existing_display_names.discard(original_display_name)

        if new_name in existing_display_names:
            self._show_error_dialog(
                _("Name Conflict"),
                _(
                    "A color scheme with the name '{name}' already exists. Please choose a different name."
                ).format(name=new_name),
            )
            return

        new_key = new_name.lower().replace(" ", "_")
        if new_key in all_schemes and (self.is_new or new_key != self.original_key):
            self._show_error_dialog(
                _("Name Conflict"),
                _(
                    "A scheme with a similar internal name ('{key}') already exists. Please choose a slightly different name."
                ).format(key=new_key),
            )
            return

        all_rows = [
            self.fg_row,
            self.bg_row,
            self.headerbar_row,
            self.cursor_row,
        ] + self.palette_rows
        all_hex_values = [row.get_hex_color() for row in all_rows]

        if not all(all_hex_values):
            self._show_error_dialog(
                _("Invalid Color"),
                _("One or more hex codes are invalid. Please correct them."),
            )
            return

        new_data = {
            "name": new_name,
            "foreground": all_hex_values[0],
            "background": all_hex_values[1],
            "headerbar_background": all_hex_values[2],
            "cursor": all_hex_values[3],
            "palette": all_hex_values[4:],
        }

        self.emit("save-requested", self.original_key, new_key, new_data)
        self.close()


class _SchemePreviewRow(Adw.ActionRow):
    """
    A custom row for the ListBox showing a rich, well-designed theme preview.
    """

    def __init__(
        self, scheme_key: str, scheme_data: Dict, is_custom: bool, settings_manager=None
    ):
        super().__init__(
            title=scheme_data.get("name", scheme_key),
            subtitle=_("Custom") if is_custom else _("Built-in"),
        )
        self.scheme_key = scheme_key
        self.scheme_data = scheme_data
        self.is_custom = is_custom
        self._settings_manager = settings_manager

        preview_area = Gtk.DrawingArea(
            content_width=200, content_height=80, margin_end=12
        )
        preview_area.add_css_class("scheme-preview-canvas")
        preview_area.set_draw_func(self._draw_preview, None)
        self.add_prefix(preview_area)

        self.checkmark_icon = Gtk.Image.new_from_icon_name("object-select-symbolic")
        self.add_suffix(self.checkmark_icon)

    def set_selected(self, selected: bool):
        self.checkmark_icon.set_visible(selected)

    def _draw_rounded_rect(self, cr, x, y, width, height, radius):
        cr.new_sub_path()
        cr.arc(x + radius, y + radius, radius, 3.14159, 1.5 * 3.14159)
        cr.arc(x + width - radius, y + radius, radius, 1.5 * 3.14159, 2 * 3.14159)
        cr.arc(x + width - radius, y + height - radius, radius, 0, 0.5 * 3.14159)
        cr.arc(x + radius, y + height - radius, radius, 0.5 * 3.14159, 3.14159)
        cr.close_path()

    def _draw_preview(self, area, cr, width, height, _user_data):
        bg_rgba = Gdk.RGBA()
        fg_rgba = Gdk.RGBA()
        cursor_rgba = Gdk.RGBA()
        bold_rgba = Gdk.RGBA()
        bg_rgba.parse(self.scheme_data.get("background", "#000000"))
        fg_rgba.parse(self.scheme_data.get("foreground", "#FFFFFF"))
        cursor_rgba.parse(self.scheme_data.get("cursor", "#FFFFFF"))

        # Determine bold text color based on bold_is_bright setting
        bold_is_bright = (
            self._settings_manager.get("bold_is_bright", False)
            if self._settings_manager
            else False
        )
        palette = self.scheme_data.get("palette", [])

        if bold_is_bright and len(palette) > 15:
            # Use bright white (palette[15]) for bold text when bold_is_bright is enabled
            bold_rgba.parse(palette[15])
        else:
            # Use regular foreground color for bold
            bold_rgba.parse(self.scheme_data.get("foreground", "#FFFFFF"))

        Gdk.cairo_set_source_rgba(cr, bg_rgba)
        self._draw_rounded_rect(cr, 0, 0, width, height, 8)
        cr.fill()

        font_desc = Pango.FontDescription.from_string("Monospace 10")

        Gdk.cairo_set_source_rgba(cr, fg_rgba)
        layout = area.create_pango_layout("Normal Text")
        layout.set_font_description(font_desc)
        cr.move_to(12, 12)
        PangoCairo.show_layout(cr, layout)

        ink_rect, logical_rect = layout.get_pixel_extents()
        cursor_x = 12 + logical_rect.x + logical_rect.width + 2
        cursor_y = 12 + logical_rect.y
        cursor_height = logical_rect.height

        # Draw bold text with appropriate color
        Gdk.cairo_set_source_rgba(cr, bold_rgba)
        font_desc.set_weight(Pango.Weight.BOLD)
        layout.set_font_description(font_desc)
        layout.set_text(_("Bold Text"))
        cr.move_to(12, 34)
        PangoCairo.show_layout(cr, layout)

        Gdk.cairo_set_source_rgba(cr, cursor_rgba)
        cr.rectangle(cursor_x, cursor_y, 7, cursor_height)
        cr.fill()

        num_colors = 8
        spacing = 4
        y_pos = height - 28
        swatch_area_width = width - 16
        swatch_size = (swatch_area_width - (spacing * (num_colors - 1))) / num_colors

        for i in range(num_colors):
            rgba = Gdk.RGBA()
            hex_color = palette[i] if i < len(palette) else "#000000"
            rgba.parse(hex_color)
            Gdk.cairo_set_source_rgba(cr, rgba)
            x_pos = 8 + i * (swatch_size + spacing)
            self._draw_rounded_rect(cr, x_pos, y_pos, swatch_size, 20, 3)
            cr.fill()


class ColorSchemeDialog(Adw.PreferencesWindow):
    """A dialog for managing, editing, and selecting terminal color schemes."""

    __gsignals__ = {
        "scheme-changed": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
    }

    def __init__(self, parent_window, settings_manager: SettingsManager, main_window):
        super().__init__(
            transient_for=parent_window,
            modal=False,
            title=_("Color Schemes"),
            default_width=680,
            default_height=720,
            search_enabled=True,
        )
        # Add CSS class for theming
        self.add_css_class("zashterminal-dialog")

        self.settings_manager = settings_manager
        self.main_window = main_window
        self.logger = get_logger("zashterminal.ui.color_scheme_dialog")

        self._build_ui()
        self._populate_schemes_list()
        self._update_button_sensitivity()

    def _build_ui(self):
        # CSS for .scheme-preview-canvas is now in window.css

        page = Adw.PreferencesPage()
        self.add(page)

        schemes_group = Adw.PreferencesGroup(
            title=_("Available Schemes"),
            description=_("Select a scheme to apply it instantly."),
        )
        page.add(schemes_group)

        scrolled_window = Gtk.ScrolledWindow(
            vexpand=True, min_content_height=400, css_classes=["frame"]
        )
        self.schemes_listbox = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.SINGLE, css_classes=["boxed-list"]
        )
        self.schemes_listbox.connect("row-selected", self._on_row_selected)
        self.schemes_listbox.connect("row-activated", self._on_edit_clicked)
        scrolled_window.set_child(self.schemes_listbox)
        schemes_group.add(scrolled_window)

        actions_group = Adw.PreferencesGroup()
        page.add(actions_group)

        actions_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            halign=Gtk.Align.CENTER,
            css_classes=["linked"],
        )
        actions_group.add(actions_box)

        new_button = Gtk.Button.new_with_label(_("New"))
        new_button.set_valign(Gtk.Align.CENTER)
        new_button.connect("clicked", self._on_new_clicked)
        actions_box.append(new_button)

        self.edit_button = Gtk.Button.new_with_label(_("Edit"))
        self.edit_button.set_valign(Gtk.Align.CENTER)
        self.edit_button.connect("clicked", self._on_edit_clicked)
        actions_box.append(self.edit_button)

        self.delete_button = Gtk.Button.new_with_label(_("Delete"))
        self.delete_button.set_valign(Gtk.Align.CENTER)
        self.delete_button.add_css_class("destructive-action")
        self.delete_button.connect("clicked", self._on_delete_clicked)
        actions_box.append(self.delete_button)

    def _populate_schemes_list(self):
        while child := self.schemes_listbox.get_first_child():
            self.schemes_listbox.remove(child)

        all_schemes = self.settings_manager.get_all_schemes()
        scheme_order = self.settings_manager.get_scheme_order()
        custom_schemes = self.settings_manager.custom_schemes.keys()
        current_scheme_key = self.settings_manager.get_color_scheme_name()

        for scheme_key in scheme_order:
            if scheme_key in all_schemes:
                scheme_data = all_schemes[scheme_key]
                is_custom = scheme_key in custom_schemes
                row = _SchemePreviewRow(
                    scheme_key,
                    scheme_data,
                    is_custom,
                    settings_manager=self.settings_manager,
                )
                self.schemes_listbox.append(row)
                if scheme_key == current_scheme_key:
                    self.schemes_listbox.select_row(row)

    def _on_row_selected(self, listbox, row: Optional[_SchemePreviewRow]):
        for r in listbox:
            if isinstance(r, _SchemePreviewRow):
                r.set_selected(r == row)

        if row:
            selected_index = self.settings_manager.get_scheme_order().index(
                row.scheme_key
            )
            self.settings_manager.set("color_scheme", selected_index)
            self.logger.info(f"Color scheme set to: {row.scheme_key}")
            self.emit("scheme-changed", selected_index)

        self._update_button_sensitivity()

    def _update_button_sensitivity(self):
        selected_row = self.schemes_listbox.get_selected_row()
        is_custom = selected_row and selected_row.is_custom
        self.edit_button.set_sensitive(selected_row is not None)
        self.delete_button.set_sensitive(is_custom)

    def _on_new_clicked(self, button):
        selected_row = self.schemes_listbox.get_selected_row()
        template_scheme = (
            selected_row.scheme_data
            if selected_row
            else self.settings_manager.get_all_schemes()["dark"]
        )

        all_names = [
            s["name"] for s in self.settings_manager.get_all_schemes().values()
        ]
        new_name = generate_unique_name(
            f"Copy of {template_scheme['name']}", set(all_names)
        )

        new_scheme_data = template_scheme.copy()
        new_scheme_data["name"] = new_name

        editor = _SchemeEditorDialog(
            self, self.settings_manager, new_name, new_scheme_data, is_new=True
        )
        editor.connect("save-requested", self._on_editor_save)
        editor.present()

    def _on_edit_clicked(self, _widget, row=None):
        selected_row = row or self.schemes_listbox.get_selected_row()
        if not selected_row:
            return

        if selected_row.is_custom:
            editor = _SchemeEditorDialog(
                self,
                self.settings_manager,
                selected_row.scheme_key,
                selected_row.scheme_data,
                is_new=False,
            )
        else:
            template_scheme = selected_row.scheme_data
            all_names = {
                s_data["name"]
                for s_data in self.settings_manager.get_all_schemes().values()
            }
            new_name = generate_unique_name(template_scheme["name"], set(all_names))
            new_scheme_data = template_scheme.copy()
            new_scheme_data["name"] = new_name
            new_key = new_name.lower().replace(" ", "_")

            editor = _SchemeEditorDialog(
                self,
                self.settings_manager,
                new_key,
                new_scheme_data,
                is_new=True,
            )

        editor.connect("save-requested", self._on_editor_save)
        editor.present()

    def _on_editor_save(
        self, editor, original_key: Optional[str], new_key: str, new_data: Dict
    ):
        if original_key and original_key != new_key:
            if original_key in self.settings_manager.custom_schemes:
                del self.settings_manager.custom_schemes[original_key]

        self.settings_manager.custom_schemes[new_key] = new_data
        self.settings_manager.save_custom_schemes()
        self._populate_schemes_list()

        # Find and select the newly saved row
        new_row_to_select = None
        for row in self.schemes_listbox:
            if isinstance(row, _SchemePreviewRow) and row.scheme_key == new_key:
                new_row_to_select = row
                break

        if new_row_to_select:
            self.schemes_listbox.select_row(new_row_to_select)

        # Show a modal dialog notification
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Theme Saved"),
            body=_("The theme '{name}' has been created and applied.").format(
                name=new_data["name"]
            ),
        )
        dialog.add_response("ok", _("OK"))
        dialog.present()

    def _on_delete_clicked(self, button):
        selected_row = self.schemes_listbox.get_selected_row()
        if not selected_row or not selected_row.is_custom:
            return

        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Delete Scheme?"),
            body=_(
                "Are you sure you want to delete the scheme '{name}'? This cannot be undone."
            ).format(name=selected_row.get_title()),
            default_response="cancel",
            close_response="cancel",
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_delete_confirm, selected_row.scheme_key)
        dialog.present()

    def _on_delete_confirm(self, dialog, response, scheme_key):
        if response == "delete":
            del self.settings_manager.custom_schemes[scheme_key]
            self.settings_manager.save_custom_schemes()
            self._populate_schemes_list()
            self.emit("scheme-changed", 0)
