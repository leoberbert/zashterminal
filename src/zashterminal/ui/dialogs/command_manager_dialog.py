# zashterminal/ui/dialogs/command_manager_dialog.py
"""
Command Manager Dialog - A redesigned command guide with button-based commands,
form dialogs, and integrated "Send to All" functionality.
"""

from typing import List, Optional, Dict, Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, GObject, Gtk, Pango, Gio

from ...data.command_manager_models import (
    CommandButton,
    CommandFormField,
    ExecutionMode,
    DisplayMode,
    FieldType,
    get_command_button_manager,
    generate_id,
)
from ...settings.manager import SettingsManager
from ...utils.syntax_utils import get_bash_pango_markup
from ...utils.tooltip_helper import get_tooltip_helper
from ...utils.translation_utils import _
from ..widgets.bash_text_view import BashTextView
from ..widgets.form_widget_builder import create_field_from_form_field


class CommandFormDialog(Adw.Window):
    """
    Dialog that displays form fields for a command and builds the final command string.
    """

    __gsignals__ = {
        "command-ready": (GObject.SignalFlags.RUN_FIRST, None, (str, bool, bool)),  # (command, execute, send_to_all)
    }

    def __init__(self, parent, command: CommandButton, send_to_all: bool = False,
                 settings_manager: Optional[SettingsManager] = None):
        self._command_manager = get_command_button_manager()
        self._settings_manager = settings_manager
        # Load saved size or calculate smart default
        saved_width = self._command_manager.get_command_pref(command.id, "dialog_width")
        saved_height = self._command_manager.get_command_pref(command.id, "dialog_height")

        if saved_width and saved_height:
            dialog_width = saved_width
            dialog_height = saved_height
        else:
            dialog_width = 550
            dialog_height = self._calculate_dialog_height(command)

        super().__init__(
            transient_for=parent,
            modal=True,
            default_width=dialog_width,
            default_height=dialog_height,
        )
        self.add_css_class("zashterminal-dialog")
        self.add_css_class("command-form-dialog")
        self.command = command

        # Use the passed send_to_all value (from context menu or default)
        self.send_to_all = send_to_all

        self.field_widgets: Dict[str, Gtk.Widget] = {}

        self.set_title(command.name)
        self._build_ui()
        self._apply_color_scheme()

        # Keyboard handler
        key_controller = Gtk.EventControllerKey.new()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_controller)

        # Save size when dialog is closed
        self.connect("close-request", self._on_close_save_size)

    def _apply_color_scheme(self):
        """Dialog theming is handled globally via the zashterminal-dialog class.
        
        This method is kept for potential future customization needs but
        currently relies on global CSS from apply_gtk_terminal_theme.
        """
        pass

    def _on_close_save_size(self, widget):
        """Save dialog size when closing."""
        width = self.get_width()
        height = self.get_height()

        # Only save if size is reasonable
        if width > 300 and height > 200:
            self._command_manager.set_command_pref(self.command.id, "dialog_width", width)
            self._command_manager.set_command_pref(self.command.id, "dialog_height", height)

        return False  # Allow close to proceed

    @staticmethod
    def _calculate_dialog_height(command: CommandButton) -> int:
        """Calculate optimal dialog height based on field types and count."""
        base_height = 180  # Header + preview + margins

        if not command.form_fields:
            return base_height + 50

        # Height estimates per field type
        height_map = {
            FieldType.TEXT: 56,
            FieldType.PASSWORD: 56,
            FieldType.TEXT_AREA: 120,
            FieldType.SWITCH: 50,
            FieldType.DROPDOWN: 56,
            FieldType.NUMBER: 56,
            FieldType.SLIDER: 70,
            FieldType.FILE_PATH: 56,
            FieldType.DIRECTORY_PATH: 56,
            FieldType.DATE_TIME: 56,
            FieldType.COLOR: 56,
        }

        fields_height = 50  # Group title
        for field in command.form_fields:
            if field.field_type == FieldType.RADIO:
                # Radio buttons: base + per-option
                num_options = len(field.options) if field.options else 2
                fields_height += 50 + (num_options * 36)
            elif field.field_type == FieldType.MULTI_SELECT:
                num_options = len(field.options) if field.options else 2
                fields_height += 50 + (num_options * 36)
            else:
                fields_height += height_map.get(field.field_type, 56)

        # Add description height if present
        if command.description:
            fields_height += 40

        # Clamp between reasonable bounds
        total = base_height + fields_height
        return max(350, min(total, 800))

    def _on_key_pressed(self, controller, keyval, _keycode, state):
        if keyval == Gdk.KEY_Escape:
            self.close()
            return Gdk.EVENT_STOP
        elif keyval == Gdk.KEY_Return and (state & Gdk.ModifierType.CONTROL_MASK):
            self._on_execute_clicked(None)
            return Gdk.EVENT_STOP
        return Gdk.EVENT_PROPAGATE

    def _build_ui(self):
        # Header bar
        header = Adw.HeaderBar()

        cancel_button = Gtk.Button(label=_("Cancel"))
        cancel_button.connect("clicked", lambda _: self.close())
        header.pack_start(cancel_button)

        # Action buttons
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        insert_button = Gtk.Button(label=_("Insert"))
        get_tooltip_helper().add_tooltip(insert_button, _("Insert command into terminal without executing"))
        insert_button.connect("clicked", self._on_insert_clicked)
        button_box.append(insert_button)

        execute_button = Gtk.Button(label=_("Execute"), css_classes=["suggested-action"])
        get_tooltip_helper().add_tooltip(execute_button, _("Insert and execute command (Ctrl+Enter)"))
        execute_button.connect("clicked", self._on_execute_clicked)
        button_box.append(execute_button)

        header.pack_end(button_box)

        # Content area
        content_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=12,
            margin_bottom=12,
            margin_start=16,
            margin_end=16,
        )

        # Command Preview at top - with title and compact display
        preview_container = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4,
        )

        preview_title = Gtk.Label(
            label=_("Command Preview"),
            xalign=0.0,
            css_classes=["dim-label", "caption"],
        )
        preview_container.append(preview_title)

        preview_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=0,
            css_classes=["card", "command-preview"],
        )

        self.preview_label = Gtk.Label(
            label="",
            use_markup=True,
            wrap=True,
            wrap_mode=Pango.WrapMode.CHAR,
            xalign=0.0,
            hexpand=True,
            selectable=False,  # Don't select on start
            css_classes=["monospace"],
            margin_start=10,
            margin_end=10,
            margin_top=5,
            margin_bottom=5,
        )
        preview_box.append(self.preview_label)
        preview_container.append(preview_box)

        content_box.append(preview_container)

        # Description
        if self.command.description:
            desc_label = Gtk.Label(
                label=self.command.description,
                wrap=True,
                wrap_mode=Pango.WrapMode.WORD_CHAR,
                xalign=0.0,
                css_classes=["dim-label"],
            )
            content_box.append(desc_label)

        # Form fields - no extra title header to save space
        if self.command.form_fields:
            form_group = Adw.PreferencesGroup()

            for form_field in self.command.form_fields:
                row = self._create_field_row(form_field)
                if row:
                    form_group.add(row)

            content_box.append(form_group)

        # Scrolled window for content
        scrolled = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            min_content_height=200,
            max_content_height=500,
        )
        scrolled.set_child(content_box)

        # Assemble
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)
        toolbar_view.set_content(scrolled)
        self.set_content(toolbar_view)

        # Update preview initially
        self._update_preview()

    def _create_field_row(self, form_field: CommandFormField) -> Optional[Gtk.Widget]:
        """Create the appropriate row widget for a form field.
        
        Delegates to FormWidgetBuilder for widget creation.
        """
        # Use the centralized form widget builder
        row, value_widget = create_field_from_form_field(
            form_field, on_change=self._update_preview
        )

        # Store value widget for later retrieval
        self.field_widgets[form_field.id] = value_widget

        # Special handling for file/directory path browse buttons
        if form_field.field_type in (FieldType.FILE_PATH, FieldType.DIRECTORY_PATH):
            if hasattr(value_widget, '_browse_button'):
                value_widget._browse_button.connect(
                    "clicked", self._on_browse_clicked, value_widget, form_field.field_type
                )

        return row

    def _on_browse_clicked(self, button, entry, field_type):
        """Open file/directory chooser dialog."""
        if field_type == FieldType.DIRECTORY_PATH:
            action = Gtk.FileChooserAction.SELECT_FOLDER
        else:
            action = Gtk.FileChooserAction.OPEN

        dialog = Gtk.FileChooserNative.new(
            _("Select Path"),
            self,
            action,
            _("Select"),
            _("Cancel"),
        )
        dialog.connect("response", self._on_file_chooser_response, entry)
        dialog.show()

    def _on_file_chooser_response(self, dialog, response, entry):
        if response == Gtk.ResponseType.ACCEPT:
            file = dialog.get_file()
            if file:
                entry.set_text(file.get_path())

    def _get_field_values(self) -> Dict[str, Any]:
        """Collect current values from all form fields."""
        values = {}

        for form_field in self.command.form_fields:
            widget = self.field_widgets.get(form_field.id)
            if not widget:
                continue

            if form_field.field_type == FieldType.SWITCH:
                values[form_field.id] = widget.get_active()

            elif form_field.field_type == FieldType.DROPDOWN:
                selected_idx = widget.get_selected()
                if selected_idx >= 0 and hasattr(widget, '_options'):
                    values[form_field.id] = widget._options[selected_idx][0]
                else:
                    values[form_field.id] = ""

            elif form_field.field_type == FieldType.RADIO:
                # Radio group - get selected value
                if hasattr(widget, '_selected_value'):
                    values[form_field.id] = widget._selected_value
                else:
                    values[form_field.id] = ""

            elif form_field.field_type == FieldType.MULTI_SELECT:
                # Multi-select - get all selected values
                selected = []
                if hasattr(widget, '_checkboxes'):
                    for check in widget._checkboxes:
                        if check.get_active() and hasattr(check, '_value'):
                            selected.append(check._value)
                values[form_field.id] = " ".join(selected)

            elif form_field.field_type == FieldType.TEXT_AREA:
                # TextView - get buffer text
                buffer = widget.get_buffer()
                values[form_field.id] = buffer.get_text(
                    buffer.get_start_iter(),
                    buffer.get_end_iter(),
                    False
                )

            elif form_field.field_type == FieldType.SLIDER:
                values[form_field.id] = str(int(widget.get_value()))

            elif form_field.field_type == FieldType.COLOR:
                rgba = widget.get_rgba()
                color_format = getattr(widget, '_color_format', 'hex')
                if color_format == "rgb":
                    values[form_field.id] = "{},{},{}".format(
                        int(rgba.red * 255),
                        int(rgba.green * 255),
                        int(rgba.blue * 255)
                    )
                else:
                    # Default to hex
                    values[form_field.id] = "#{:02x}{:02x}{:02x}".format(
                        int(rgba.red * 255),
                        int(rgba.green * 255),
                        int(rgba.blue * 255)
                    )

            elif form_field.field_type in (FieldType.TEXT, FieldType.NUMBER, FieldType.PASSWORD, FieldType.DATE_TIME):
                values[form_field.id] = widget.get_text()

            elif form_field.field_type in (FieldType.FILE_PATH, FieldType.DIRECTORY_PATH):
                values[form_field.id] = widget.get_text()

        return values

    def _update_preview(self):
        """Update the command preview based on current field values."""
        values = self._get_field_values()

        # Build command with special handling for find command
        command = self._build_command_from_values(values)
        # Apply syntax highlighting with terminal color scheme
        palette = None
        fg_color = "#ffffff"
        if self._settings_manager and self._settings_manager.get("gtk_theme", "") == "terminal":
            scheme = self._settings_manager.get_color_scheme_data()
            palette = scheme.get("palette", [])
            fg_color = scheme.get("foreground", "#ffffff")
        highlighted = get_bash_pango_markup(command, palette, fg_color)
        self.preview_label.set_markup(highlighted)

    def _build_command_from_values(self, values: Dict[str, Any]) -> str:
        """Build the command string from form values with proper flag handling."""
        # Handle special commands
        if self.command.id == "builtin_find":
            return self._build_find_command(values)
        elif self.command.id == "builtin_compress":
            return self._build_compress_command(values)
        elif self.command.id == "builtin_extract":
            return self._build_extract_command(values)
        elif self.command.id == "builtin_systemctl":
            return self._build_systemctl_command(values)
        elif self.command.id == "builtin_journalctl":
            return self._build_journalctl_command(values)
        elif self.command.id == "builtin_pacman":
            return self._build_pacman_command(values)

        # Generic handling for other commands
        return self.command.build_command(values)

    def _build_find_command(self, values: Dict[str, Any]) -> str:
        """Build the find command with proper flag handling."""
        parts = ["find"]

        # Path
        path = values.get("path", ".").strip() or "."
        parts.append(path)

        # Name pattern
        name_pattern = values.get("name_pattern", "").strip()
        if name_pattern:
            parts.append(f"-name '{name_pattern}'")

        # Recursive (actually, non-recursive = maxdepth 1)
        if not values.get("recursive", True):
            parts.append("-maxdepth 1")

        # File type
        file_type = values.get("file_type", "").strip()
        if file_type:
            parts.append(file_type)

        # Size filter
        size_filter = values.get("size_filter", "").strip()
        if size_filter:
            parts.append(f"-size {size_filter}")

        # Date filter with time unit support
        # Only add date filter if value is a positive integer > 0
        date_value = values.get("date_value", "").strip()
        date_unit = values.get("date_unit", "days")
        if date_value:
            try:
                value = int(date_value)
                if value > 0:  # Only apply filter for positive values
                    if date_unit == "minutes":
                        parts.append(f"-mmin -{value}")
                    elif date_unit == "hours":
                        # Convert hours to minutes for find command
                        parts.append(f"-mmin -{value * 60}")
                    else:  # days
                        parts.append(f"-mtime -{value}")
            except (ValueError, TypeError):
                pass

        # Grep pattern (search in content)
        grep_pattern = values.get("grep_pattern", "").strip()
        if grep_pattern:
            parts.append(f"-exec grep -l '{grep_pattern}' {{}} \\;")

        return " ".join(parts)

    def _build_compress_command(self, values: Dict[str, Any]) -> str:
        """Build compression command based on format."""
        input_path = values.get("input", "").strip()
        output_path = values.get("output", "").strip()
        archive_format = values.get("format", "tar.xz")

        # Use placeholder if no input specified
        input_display = input_path if input_path else "<files>"

        # Generate default output name based on format if not specified
        if not output_path:
            output_path = f"archive.{archive_format}"

        if archive_format == "zip":
            return f"zip -r {output_path} {input_display}"
        elif archive_format == "tar":
            return f"tar -cvf {output_path} {input_display}"
        elif archive_format == "tar.gz":
            return f"tar -czvf {output_path} {input_display}"
        elif archive_format == "tar.bz2":
            return f"tar -cjvf {output_path} {input_display}"
        elif archive_format == "tar.xz":
            return f"tar -cJvf {output_path} {input_display}"
        elif archive_format == "tar.zst":
            return f"tar -cvf - {input_display} | zstd -o {output_path}"
        elif archive_format == "tar.lzma":
            return f"tar -cvf - {input_display} | lzma -c > {output_path}"
        else:
            return f"tar -cJvf {output_path} {input_display}"

    def _build_extract_command(self, values: Dict[str, Any]) -> str:
        """Build extraction command based on archive file extension."""
        input_path = values.get("input", "").strip()
        output_path = values.get("output", "").strip()

        # Use placeholder if no input
        input_display = input_path if input_path else "<archive>"

        # Destination directory flag
        if output_path:
            dest_flag = f"-C {output_path}"
        else:
            dest_flag = ""

        # Auto-detect format from filename
        if input_path.endswith(".zip"):
            if output_path:
                return f"unzip {input_display} -d {output_path}"
            return f"unzip {input_display}"
        elif input_path.endswith(".tar"):
            return f"tar -xvf {input_display} {dest_flag}".strip()
        elif input_path.endswith(".tar.gz") or input_path.endswith(".tgz"):
            return f"tar -xzvf {input_display} {dest_flag}".strip()
        elif input_path.endswith(".tar.bz2") or input_path.endswith(".tbz2"):
            return f"tar -xjvf {input_display} {dest_flag}".strip()
        elif input_path.endswith(".tar.xz") or input_path.endswith(".txz"):
            return f"tar -xJvf {input_display} {dest_flag}".strip()
        elif input_path.endswith(".tar.zst") or input_path.endswith(".tzst"):
            if output_path:
                return f"zstd -d {input_display} -c | tar -xvf - -C {output_path}"
            return f"zstd -d {input_display} -c | tar -xvf -"
        elif input_path.endswith(".tar.lzma") or input_path.endswith(".tlz"):
            if output_path:
                return f"lzma -d -c {input_display} | tar -xvf - -C {output_path}"
            return f"lzma -d -c {input_display} | tar -xvf -"
        elif input_path.endswith(".gz"):
            return f"gunzip {input_display}"
        elif input_path.endswith(".bz2"):
            return f"bunzip2 {input_display}"
        elif input_path.endswith(".xz"):
            return f"unxz {input_display}"
        elif input_path.endswith(".zst"):
            return f"zstd -d {input_display}"
        elif input_path.endswith(".lzma"):
            return f"lzma -d {input_display}"
        else:
            # Default to tar with auto-detection
            return f"tar -xvf {input_display} {dest_flag}".strip()

    def _build_systemctl_command(self, values: Dict[str, Any]) -> str:
        """Build systemctl command with proper handling of service name."""
        parts = ["systemctl"]

        # User scope flag
        if values.get("user_scope", False):
            parts.append("--user")

        action = values.get("action", "status").strip()
        service = values.get("service", "").strip()

        # Some actions don't need a service name
        list_actions = ["list-units --type=service",
                       "list-units --type=service --state=running",
                       "list-units --type=service --state=failed"]

        parts.append(action)

        if action not in list_actions and service:
            parts.append(service)

        return " ".join(parts)

    def _build_journalctl_command(self, values: Dict[str, Any]) -> str:
        """Build journalctl command with proper flag handling."""
        parts = ["journalctl"]

        # Unit filter
        unit = values.get("unit", "").strip()
        if unit:
            parts.append(f"-u {unit}")

        # Follow flag
        if values.get("follow", False):
            parts.append("-f")

        # Number of lines
        lines = values.get("lines", "").strip()
        if lines:
            try:
                n = int(lines)
                if n > 0:
                    parts.append(f"-n {n}")
            except (ValueError, TypeError):
                pass

        # Priority filter
        priority = values.get("priority", "").strip()
        if priority:
            parts.append(priority)

        # Since filter
        since = values.get("since", "").strip()
        if since:
            parts.append(since)

        return " ".join(parts)

    def _build_pacman_command(self, values: Dict[str, Any]) -> str:
        """Build pacman command with proper handling of package name."""
        action = values.get("action", "-S").strip()
        package = values.get("package", "").strip()

        # Actions that don't require a package name
        no_pkg_actions = ["-Syu", "-Syyu", "-Sc", "-Scc", "-Q", "-Qe", "-Qdt"]

        if action in no_pkg_actions:
            return f"sudo pacman {action}"
        elif package:
            return f"sudo pacman {action} {package}"
        else:
            # Show command template even without package
            return f"sudo pacman {action}"

    def _on_insert_clicked(self, button):
        """Insert command without executing."""
        values = self._get_field_values()
        command = self._build_command_from_values(values)
        self.emit("command-ready", command, False, self.send_to_all)
        self.close()

    def _on_execute_clicked(self, button):
        """Insert and execute command."""
        values = self._get_field_values()
        command = self._build_command_from_values(values)
        self.emit("command-ready", command, True, self.send_to_all)
        self.close()


class CommandButtonWidget(Gtk.Button):
    """
    A button widget representing a command in the Command Manager.
    Supports editing, deleting, restoring defaults, hiding, duplicating, and pinning.
    """

    __gsignals__ = {
        "command-activated": (GObject.SignalFlags.RUN_FIRST, None, (GObject.TYPE_PYOBJECT,)),
        "command-activated-all": (GObject.SignalFlags.RUN_FIRST, None, (GObject.TYPE_PYOBJECT,)),  # Execute in all terminals
        "edit-requested": (GObject.SignalFlags.RUN_FIRST, None, (GObject.TYPE_PYOBJECT,)),
        "delete-requested": (GObject.SignalFlags.RUN_FIRST, None, (GObject.TYPE_PYOBJECT,)),
        "restore-requested": (GObject.SignalFlags.RUN_FIRST, None, (GObject.TYPE_PYOBJECT,)),
        "hide-requested": (GObject.SignalFlags.RUN_FIRST, None, (GObject.TYPE_PYOBJECT,)),
        "duplicate-requested": (GObject.SignalFlags.RUN_FIRST, None, (GObject.TYPE_PYOBJECT,)),
        "pin-requested": (GObject.SignalFlags.RUN_FIRST, None, (GObject.TYPE_PYOBJECT,)),
        "unpin-requested": (GObject.SignalFlags.RUN_FIRST, None, (GObject.TYPE_PYOBJECT,)),
    }

    def __init__(self, command: CommandButton):
        super().__init__()
        self.command = command
        self.add_css_class("command-button")

        self._build_ui()
        self._setup_context_menu()

        self.connect("clicked", self._on_clicked)

    def _build_ui(self):
        """Build the button content based on display mode."""
        content_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        if self.command.display_mode in (DisplayMode.ICON_ONLY, DisplayMode.ICON_AND_TEXT):
            icon = Gtk.Image.new_from_icon_name(self.command.icon_name)
            icon.set_icon_size(Gtk.IconSize.NORMAL)
            content_box.append(icon)

        if self.command.display_mode in (DisplayMode.TEXT_ONLY, DisplayMode.ICON_AND_TEXT):
            label = Gtk.Label(label=self.command.name)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            content_box.append(label)

        self.set_child(content_box)
        get_tooltip_helper().add_tooltip(self, self.command.description)

    def _setup_context_menu(self):
        """Setup right-click context menu for all buttons (builtin and custom)."""
        menu = Gio.Menu()

        # Execute in all terminals - available for all commands
        menu.append(_("Execute in All Terminals"), "button.execute_all")

        # Edit option - available for all commands
        menu.append(_("Edit"), "button.edit")

        # Duplicate option - available for all commands
        menu.append(_("Duplicate"), "button.duplicate")

        # Get command manager to check customization/pin status
        command_manager = get_command_button_manager()

        # Pin/Unpin option - available for all commands
        if command_manager.is_command_pinned(self.command.id):
            menu.append(_("Unpin from Toolbar"), "button.unpin")
        else:
            menu.append(_("Pin to Toolbar"), "button.pin")

        if self.command.is_builtin:
            # For builtin commands: show restore if customized, always show hide
            if command_manager.is_builtin_customized(self.command.id):
                menu.append(_("Restore Default"), "button.restore")
            menu.append(_("Hide"), "button.hide")
        else:
            # For custom commands: show delete
            menu.append(_("Delete"), "button.delete")

        # Action group
        action_group = Gio.SimpleActionGroup()

        # Execute in all terminals action
        execute_all_action = Gio.SimpleAction.new("execute_all", None)
        execute_all_action.connect("activate", lambda *_: self.emit("command-activated-all", self.command))
        action_group.add_action(execute_all_action)

        edit_action = Gio.SimpleAction.new("edit", None)
        edit_action.connect("activate", lambda *_: self.emit("edit-requested", self.command))
        action_group.add_action(edit_action)

        duplicate_action = Gio.SimpleAction.new("duplicate", None)
        duplicate_action.connect("activate", lambda *_: self.emit("duplicate-requested", self.command))
        action_group.add_action(duplicate_action)

        # Pin/Unpin actions
        if command_manager.is_command_pinned(self.command.id):
            unpin_action = Gio.SimpleAction.new("unpin", None)
            unpin_action.connect("activate", lambda *_: self.emit("unpin-requested", self.command))
            action_group.add_action(unpin_action)
        else:
            pin_action = Gio.SimpleAction.new("pin", None)
            pin_action.connect("activate", lambda *_: self.emit("pin-requested", self.command))
            action_group.add_action(pin_action)

        if self.command.is_builtin:
            if command_manager.is_builtin_customized(self.command.id):
                restore_action = Gio.SimpleAction.new("restore", None)
                restore_action.connect("activate", lambda *_: self.emit("restore-requested", self.command))
                action_group.add_action(restore_action)

            hide_action = Gio.SimpleAction.new("hide", None)
            hide_action.connect("activate", lambda *_: self.emit("hide-requested", self.command))
            action_group.add_action(hide_action)
        else:
            delete_action = Gio.SimpleAction.new("delete", None)
            delete_action.connect("activate", lambda *_: self.emit("delete-requested", self.command))
            action_group.add_action(delete_action)

        self.insert_action_group("button", action_group)

        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.add_css_class("zashterminal-popover")
        popover.set_parent(self)

        # Right-click gesture
        gesture = Gtk.GestureClick.new()
        gesture.set_button(3)  # Right click
        gesture.connect("pressed", lambda g, n, x, y: popover.popup())
        self.add_controller(gesture)

    def _on_clicked(self, button):
        """Handle button click."""
        self.emit("command-activated", self.command)


class CommandEditorDialog(Adw.Window):
    """
    Dialog for creating or editing a custom command button.
    Redesigned with simplified UI and modern UX patterns.
    """

    __gsignals__ = {
        "save-requested": (GObject.SignalFlags.RUN_FIRST, None, (GObject.TYPE_PYOBJECT,)),
    }

    def __init__(self, parent, command: Optional[CommandButton] = None,
                 settings_manager: Optional[SettingsManager] = None):
        # Match the Command Manager dialog size
        parent_width = parent.get_width() if parent else 800
        parent_height = parent.get_height() if parent else 600

        super().__init__(
            transient_for=parent,
            modal=True,
            default_width=int(parent_width * 0.7),
            default_height=int(parent_height * 0.75),
        )
        self.add_css_class("zashterminal-dialog")
        self.add_css_class("command-editor-dialog")
        self.command = command
        self.is_new = command is None
        self._selected_icon = "utilities-terminal-symbolic"
        self._settings_manager = settings_manager

        self.set_title(_("New Command") if self.is_new else _("Edit Command"))
        self._build_ui()
        self._apply_color_scheme()

        if not self.is_new:
            self._populate_from_command()

        # Keyboard handler
        key_controller = Gtk.EventControllerKey.new()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_controller)

    def _apply_color_scheme(self):
        """Apply terminal color scheme to BashTextView when gtk_theme is 'terminal'.
        
        Dialog theming is handled globally via the zashterminal-dialog class.
        This method only updates the syntax highlighting in the BashTextView.
        """
        if not self._settings_manager:
            return

        gtk_theme = self._settings_manager.get("gtk_theme", "")
        if gtk_theme != "terminal":
            return

        # Update BashTextView syntax highlighting colors
        scheme = self._settings_manager.get_color_scheme_data()
        palette = scheme.get("palette", [])
        fg_color = scheme.get("foreground", "#ffffff")
        if palette:
            if hasattr(self, 'simple_command_textview') and self.simple_command_textview:
                self.simple_command_textview.update_colors_from_scheme(palette, fg_color)
            if hasattr(self, 'command_textview') and self.command_textview:
                self.command_textview.update_colors_from_scheme(palette, fg_color)

    def _on_key_pressed(self, controller, keyval, _keycode, state):
        if keyval == Gdk.KEY_Escape:
            self.close()
            return Gdk.EVENT_STOP
        return Gdk.EVENT_PROPAGATE

    def _build_ui(self):
        # Initialize data structures
        self.form_fields_data: List[Dict] = []
        self._selected_icon = "utilities-terminal-symbolic"
        self._command_type = "simple"  # "simple" or "form"

        # Header with navigation
        header = Adw.HeaderBar()

        # Back button
        self.back_button = Gtk.Button(
            icon_name="go-previous-symbolic",
        )
        get_tooltip_helper().add_tooltip(self.back_button, _("Back"))
        self.back_button.connect("clicked", self._on_back_clicked)
        self.back_button.set_visible(False)
        header.pack_start(self.back_button)

        cancel_button = Gtk.Button(label=_("Cancel"))
        cancel_button.connect("clicked", lambda _: self.close())
        header.pack_start(cancel_button)

        # Save button
        self.save_button = Gtk.Button(label=_("Save"), css_classes=["suggested-action"])
        self.save_button.connect("clicked", self._save_command)
        self.save_button.set_visible(False)
        header.pack_end(self.save_button)

        # Continue button (for form wizard)
        self.continue_button = Gtk.Button(label=_("Continue"))
        self.continue_button.connect("clicked", self._on_continue_clicked)
        self.continue_button.set_visible(False)
        header.pack_end(self.continue_button)

        # Stack for wizard steps
        self.wizard_stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT,
        )

        # Step 0: Choose command type
        self._build_step_type_choice()

        # Step 1: Simple command (basic info + command)
        self._build_step_simple()

        # Step 2a: Form command - Basic info only
        self._build_step_form_info()

        # Step 2b: Form builder (for GUI commands)
        self._build_step_form_builder()

        # Scrolled window
        scrolled = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vexpand=True,
        )
        scrolled.set_child(self.wizard_stack)

        # Setup actions for adding fields - all field types
        action_group = Gio.SimpleActionGroup()
        all_field_types = [
            "command_text", "text", "text_area", "password",
            "number", "slider",
            "switch", "dropdown", "radio", "multi_select",
            "file_path", "directory_path",
            "date_time", "color"
        ]
        for field_type in all_field_types:
            action = Gio.SimpleAction.new(f"add-{field_type.replace('_', '-')}-field", None)
            action.connect("activate", lambda *_, ft=field_type: self._add_form_field(ft))
            action_group.add_action(action)
        self.insert_action_group("editor", action_group)

        # Assemble
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)
        toolbar_view.set_content(scrolled)
        self.set_content(toolbar_view)

    def _build_step_type_choice(self):
        """Build Step 0: Choose command type."""
        step0 = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=24,
            margin_top=32,
            margin_bottom=32,
            margin_start=24,
            margin_end=24,
            valign=Gtk.Align.CENTER,
        )

        title = Gtk.Label(
            label=_("What type of command do you want to create?"),
            css_classes=["title-2"],
        )
        step0.append(title)

        buttons_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=16,
        )

        # Simple command option
        simple_btn = Gtk.Button(css_classes=["card", "flat"])
        simple_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=8,
            margin_top=16,
            margin_bottom=16,
            margin_start=16,
            margin_end=16,
        )
        simple_icon = Gtk.Image.new_from_icon_name("utilities-terminal-symbolic")
        simple_icon.set_pixel_size(48)
        simple_box.append(simple_icon)
        simple_title = Gtk.Label(label=_("Simple Command"), css_classes=["title-3"])
        simple_box.append(simple_title)
        simple_desc = Gtk.Label(
            label=_("A button that runs a command directly"),
            css_classes=["dim-label"],
            wrap=True,
        )
        simple_box.append(simple_desc)
        simple_btn.set_child(simple_box)
        simple_btn.connect("clicked", lambda _: self._select_command_type("simple"))
        buttons_box.append(simple_btn)

        # Form command option
        form_btn = Gtk.Button(css_classes=["card", "flat"])
        form_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=8,
            margin_top=16,
            margin_bottom=16,
            margin_start=16,
            margin_end=16,
        )
        form_icon = Gtk.Image.new_from_icon_name("view-paged-symbolic")
        form_icon.set_pixel_size(48)
        form_box.append(form_icon)
        form_title = Gtk.Label(label=_("Command with Form"), css_classes=["title-3"])
        form_box.append(form_title)
        form_desc = Gtk.Label(
            label=_("A button that shows a form to configure the command"),
            css_classes=["dim-label"],
            wrap=True,
        )
        form_box.append(form_desc)
        form_btn.set_child(form_box)
        form_btn.connect("clicked", lambda _: self._select_command_type("form"))
        buttons_box.append(form_btn)

        step0.append(buttons_box)
        self.wizard_stack.add_named(step0, "type_choice")

    def _select_command_type(self, cmd_type: str):
        """Handle command type selection."""
        self._command_type = cmd_type
        self.back_button.set_visible(True)

        if cmd_type == "simple":
            self.save_button.set_visible(True)
            self.continue_button.set_visible(False)
            self.wizard_stack.set_visible_child_name("simple")
        else:
            # Form command: first show basic info, then form builder
            self.save_button.set_visible(False)
            self.continue_button.set_visible(True)
            self.wizard_stack.set_visible_child_name("form_info")

    def _on_continue_clicked(self, button):
        """Continue to form builder step."""
        # Validate basic info
        name = self.form_name_row.get_text().strip()
        if not name:
            self.form_name_row.add_css_class("error")
            return
        self.form_name_row.remove_css_class("error")

        # Go to form builder
        self.continue_button.set_visible(False)
        self.save_button.set_visible(True)
        self.wizard_stack.set_visible_child_name("form_builder")
        self._update_preview()
        self._update_form_preview()

    def _build_step_simple(self):
        """Build Simple Command step: Basic info + Command only."""
        simple_step = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=16,
            margin_top=16,
            margin_bottom=16,
            margin_start=16,
            margin_end=16,
        )

        # Basic Information
        basic_group = Adw.PreferencesGroup(title=_("Basic Information"))

        self.simple_name_row = Adw.EntryRow(title=_("Name"))
        basic_group.add(self.simple_name_row)

        self.simple_description_row = Adw.EntryRow(title=_("Description"))
        basic_group.add(self.simple_description_row)

        # Icon row
        icon_row = Adw.ActionRow(title=_("Icon"))
        self.simple_icon_preview = Gtk.Image.new_from_icon_name("utilities-terminal-symbolic")
        self.simple_icon_preview.set_pixel_size(24)
        icon_row.add_prefix(self.simple_icon_preview)

        icon_picker_btn = Gtk.Button(
            icon_name="view-grid-symbolic",
            css_classes=["flat"],
            valign=Gtk.Align.CENTER,
        )
        get_tooltip_helper().add_tooltip(icon_picker_btn, _("Choose icon"))
        icon_picker_btn.connect("clicked", lambda _: self._on_pick_icon_clicked("simple"))
        icon_row.add_suffix(icon_picker_btn)

        self.simple_icon_entry = Gtk.Entry(
            placeholder_text="utilities-terminal-symbolic",
            width_chars=20,
            valign=Gtk.Align.CENTER,
        )
        self.simple_icon_entry.set_text("utilities-terminal-symbolic")
        self.simple_icon_entry.connect("changed", lambda e: self._on_icon_entry_changed(e, "simple"))
        icon_row.add_suffix(self.simple_icon_entry)
        basic_group.add(icon_row)

        # Display mode
        self.simple_display_mode_row = Adw.ComboRow(title=_("Display Mode"))
        display_modes = Gtk.StringList()
        for mode in [_("Icon and Text"), _("Icon Only"), _("Text Only")]:
            display_modes.append(mode)
        self.simple_display_mode_row.set_model(display_modes)
        basic_group.add(self.simple_display_mode_row)

        # Execution mode (Insert Only or Insert and Execute)
        self.simple_execution_mode_row = Adw.ComboRow(title=_("Execution Mode"))
        execution_modes = Gtk.StringList()
        for mode in [_("Insert Only"), _("Insert and Execute")]:
            execution_modes.append(mode)
        self.simple_execution_mode_row.set_model(execution_modes)
        self.simple_execution_mode_row.set_selected(0)  # Default: Insert Only
        basic_group.add(self.simple_execution_mode_row)

        simple_step.append(basic_group)

        # Command
        command_group = Adw.PreferencesGroup(title=_("Command"))

        help_label = Gtk.Label(
            label=_("Enter the bash command to execute:"),
            xalign=0.0,
            css_classes=["dim-label", "caption"],
            margin_start=4,
        )
        command_group.add(help_label)

        command_frame = Gtk.Frame(css_classes=["view"])
        command_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            min_content_height=80,
            max_content_height=150,
        )
        self.simple_command_textview = BashTextView()
        command_scroll.set_child(self.simple_command_textview)
        command_frame.set_child(command_scroll)
        command_group.add(command_frame)

        simple_step.append(command_group)

        self.wizard_stack.add_named(simple_step, "simple")

    def _build_step_form_info(self):
        """Build Form Info step: Basic information only."""
        form_info_step = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=16,
            margin_top=16,
            margin_bottom=16,
            margin_start=16,
            margin_end=16,
        )

        # Basic Information for form command
        basic_group = Adw.PreferencesGroup(title=_("Basic Information"))

        self.form_name_row = Adw.EntryRow(title=_("Name"))
        basic_group.add(self.form_name_row)

        self.form_description_row = Adw.EntryRow(title=_("Description"))
        basic_group.add(self.form_description_row)

        # Icon row
        icon_row = Adw.ActionRow(title=_("Icon"))
        self.form_icon_preview = Gtk.Image.new_from_icon_name("utilities-terminal-symbolic")
        self.form_icon_preview.set_pixel_size(24)
        icon_row.add_prefix(self.form_icon_preview)

        icon_picker_btn = Gtk.Button(
            icon_name="view-grid-symbolic",
            css_classes=["flat"],
            valign=Gtk.Align.CENTER,
        )
        get_tooltip_helper().add_tooltip(icon_picker_btn, _("Choose icon"))
        icon_picker_btn.connect("clicked", lambda _: self._on_pick_icon_clicked("form"))
        icon_row.add_suffix(icon_picker_btn)

        self.form_icon_entry = Gtk.Entry(
            placeholder_text="utilities-terminal-symbolic",
            width_chars=20,
            valign=Gtk.Align.CENTER,
        )
        self.form_icon_entry.set_text("utilities-terminal-symbolic")
        self.form_icon_entry.connect("changed", lambda e: self._on_icon_entry_changed(e, "form"))
        icon_row.add_suffix(self.form_icon_entry)
        basic_group.add(icon_row)

        # Display mode
        self.form_display_mode_row = Adw.ComboRow(title=_("Display Mode"))
        display_modes = Gtk.StringList()
        for mode in [_("Icon and Text"), _("Icon Only"), _("Text Only")]:
            display_modes.append(mode)
        self.form_display_mode_row.set_model(display_modes)
        basic_group.add(self.form_display_mode_row)

        form_info_step.append(basic_group)

        self.wizard_stack.add_named(form_info_step, "form_info")

    def _build_step_form_builder(self):
        """Build Form Builder step: Command parts and preview in split layout."""
        # Main horizontal box to split left/right
        form_builder_step = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=12,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )

        # LEFT SIDE: Command Parts
        left_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            hexpand=True,
        )

        # Header with title and add button in one line
        parts_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        parts_header.append(Gtk.Label(
            label=_("Add command parts"),
            xalign=0.0,
            hexpand=True,
            css_classes=["title-4"],
        ))

        add_field_button = Gtk.MenuButton(
            icon_name="list-add-symbolic",
            css_classes=["flat"],
        )
        get_tooltip_helper().add_tooltip(add_field_button, _("Add part"))
        add_menu = Gio.Menu()

        # Basic types section
        basic_section = Gio.Menu()
        basic_section.append(_("📝 Command Text"), "editor.add-command-text-field")
        basic_section.append(_("⌨️ Text Input"), "editor.add-text-field")
        basic_section.append(_("📝 Text Area"), "editor.add-text-area-field")
        basic_section.append(_("🔑 Password"), "editor.add-password-field")
        add_menu.append_section(_("Text"), basic_section)

        # Numeric types section
        numeric_section = Gio.Menu()
        numeric_section.append(_("🔢 Number"), "editor.add-number-field")
        numeric_section.append(_("📊 Slider"), "editor.add-slider-field")
        add_menu.append_section(_("Numbers"), numeric_section)

        # Selection types section
        selection_section = Gio.Menu()
        selection_section.append(_("🔘 Switch"), "editor.add-switch-field")
        selection_section.append(_("📋 Dropdown"), "editor.add-dropdown-field")
        selection_section.append(_("⚪ Radio Buttons"), "editor.add-radio-field")
        selection_section.append(_("☑️ Multi-Select"), "editor.add-multi-select-field")
        add_menu.append_section(_("Selection"), selection_section)

        # File types section
        file_section = Gio.Menu()
        file_section.append(_("📄 File Path"), "editor.add-file-path-field")
        file_section.append(_("📁 Directory Path"), "editor.add-directory-path-field")
        add_menu.append_section(_("Files"), file_section)

        # Special types section
        special_section = Gio.Menu()
        special_section.append(_("📅 Date/Time"), "editor.add-date-time-field")
        special_section.append(_("🎨 Color"), "editor.add-color-field")
        add_menu.append_section(_("Special"), special_section)

        add_field_button.set_menu_model(add_menu)
        parts_header.append(add_field_button)
        left_box.append(parts_header)

        # Scrolled list for fields
        fields_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vexpand=True,
        )
        self.form_fields_list = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE,
            css_classes=["boxed-list"],
        )
        fields_scroll.set_child(self.form_fields_list)
        left_box.append(fields_scroll)

        form_builder_step.append(left_box)

        # Separator
        separator = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        form_builder_step.append(separator)

        # RIGHT SIDE: Previews
        right_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            hexpand=True,
        )

        # Command preview
        preview_group = Adw.PreferencesGroup(title=_("Command Preview"))

        self.result_preview = Gtk.Label(
            xalign=0.0,
            selectable=True,
            wrap=True,
            css_classes=["monospace", "heading"],
            margin_start=4,
            margin_top=4,
            margin_bottom=4,
        )
        self.result_preview.set_text(_("(empty)"))
        preview_group.add(self.result_preview)

        right_box.append(preview_group)

        # Form Preview
        form_preview_group = Adw.PreferencesGroup(title=_("Form Preview"))

        # Scrolled list for form preview
        preview_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vexpand=True,
        )
        self.form_preview_list = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE,
            css_classes=["boxed-list"],
        )
        preview_scroll.set_child(self.form_preview_list)
        form_preview_group.add(preview_scroll)

        right_box.append(form_preview_group)
        form_builder_step.append(right_box)

        self.wizard_stack.add_named(form_builder_step, "form_builder")

    def _on_back_clicked(self, button):
        """Go back to previous step."""
        current = self.wizard_stack.get_visible_child_name()

        if current == "form_builder":
            # Go back to form info
            self.wizard_stack.set_visible_child_name("form_info")
            self.save_button.set_visible(False)
            self.continue_button.set_visible(True)
        elif current in ("simple", "form_info"):
            # Go back to type choice
            self.wizard_stack.set_visible_child_name("type_choice")
            self.back_button.set_visible(False)
            self.save_button.set_visible(False)
            self.continue_button.set_visible(False)

    def _on_icon_entry_changed(self, entry, mode: str):
        """Update icon preview when icon name changes."""
        icon_name = entry.get_text().strip()
        if icon_name:
            self._selected_icon = icon_name
            if mode == "simple":
                self.simple_icon_preview.set_from_icon_name(icon_name)
            else:
                self.form_icon_preview.set_from_icon_name(icon_name)

    def _on_pick_icon_clicked(self, mode: str):
        """Open system icon picker dialog."""
        # Create a simple icon chooser dialog
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Choose Icon"),
            body=_("Enter icon name or select from common icons:"),
            default_response="select",
            close_response="cancel",
        )

        # Common terminal/utility icons grid
        common_icons = [
            "utilities-terminal-symbolic",
            "system-run-symbolic",
            "document-save-symbolic",
            "folder-symbolic",
            "edit-copy-symbolic",
            "edit-paste-symbolic",
            "edit-find-symbolic",
            "view-refresh-symbolic",
            "preferences-system-symbolic",
            "network-server-symbolic",
            "drive-harddisk-symbolic",
            "application-x-executable-symbolic",
            "text-x-generic-symbolic",
            "emblem-system-symbolic",
            "media-playback-start-symbolic",
            "process-stop-symbolic",
        ]

        icons_grid = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.SINGLE,
            max_children_per_line=8,
            min_children_per_line=4,
            column_spacing=4,
            row_spacing=4,
            margin_top=12,
        )

        selected_icon_name = [self._selected_icon]

        for icon_name in common_icons:
            icon_btn = Gtk.Button(
                css_classes=["flat"],
            )
            get_tooltip_helper().add_tooltip(icon_btn, icon_name)
            icon_btn.set_child(Gtk.Image.new_from_icon_name(icon_name))
            icon_btn.connect("clicked", lambda b, n=icon_name: self._select_icon(n, dialog, selected_icon_name, mode))
            icons_grid.insert(icon_btn, -1)

        dialog.set_extra_child(icons_grid)

        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("select", _("Select"))
        dialog.set_response_appearance("select", Adw.ResponseAppearance.SUGGESTED)

        dialog.connect("response", lambda d, r: self._on_icon_dialog_response(d, r, selected_icon_name, mode))
        dialog.present()

    def _select_icon(self, icon_name, dialog, selected_ref, mode: str):
        """Handle icon selection from grid."""
        selected_ref[0] = icon_name
        if mode == "simple":
            self.simple_icon_entry.set_text(icon_name)
        else:
            self.form_icon_entry.set_text(icon_name)
        dialog.close()

    def _on_icon_dialog_response(self, dialog, response, selected_ref, mode: str):
        """Handle icon picker dialog response."""
        if response == "select":
            if mode == "simple":
                self.simple_icon_entry.set_text(selected_ref[0])
            else:
                self.form_icon_entry.set_text(selected_ref[0])

    def _update_preview(self):
        """Update the resulting command preview from form fields order."""
        # Build the command from form_fields_data in order
        parts = []
        for field_data in self.form_fields_data:
            field_type = field_data.get("type", "text")
            field_id = field_data.get("id", "")

            if field_type == "command_text":
                # Static command text - just use the value directly
                value = field_data.get("default", "")
                if value:
                    parts.append(value)
            elif field_type == "switch":
                # Show on/off indicator
                on_val = field_data.get("command_flag", "")
                off_val = field_data.get("off_value", "")
                if on_val:
                    parts.append(f"[{on_val}]")
                elif off_val:
                    parts.append(f"[{off_val}]")
                else:
                    parts.append(f"{{{field_id}}}")
            else:
                # Show meaningful preview
                label = field_data.get("label", "").strip()
                default = field_data.get("default", "")
                placeholder = field_data.get("placeholder", "")

                if default:
                    parts.append(str(default))
                elif placeholder:
                    parts.append(f"<{placeholder}>")
                elif label:
                    parts.append(f"<{label}>")
                else:
                    parts.append(f"{{{field_id}}}")

        template = " ".join(parts)
        self.result_preview.set_text(template if template else _("(empty)"))

    def _update_form_preview(self):
        """Update the form preview section with actual widget representations.
        
        Uses FormWidgetBuilder for consistent widget creation across dialogs.
        """
        from ..widgets.form_widget_builder import create_field_from_dict

        # Clear existing preview
        while True:
            row = self.form_preview_list.get_row_at_index(0)
            if row is None:
                break
            self.form_preview_list.remove(row)

        # Add preview widgets for each form field (skip command_text, it's not shown to user)
        for field_data in self.form_fields_data:
            field_type = field_data.get("type", "text")

            # Skip command_text - it's static and not shown in the form dialog
            if field_type == "command_text":
                continue

            # Use the centralized FormWidgetBuilder for preview (non-interactive)
            row, _ = create_field_from_dict(field_data, on_change=None, interactive=False)

            tooltip = field_data.get("tooltip", "")
            if tooltip and hasattr(row, "set_tooltip_text"):
                get_tooltip_helper().add_tooltip(row, tooltip)

            self.form_preview_list.append(row)

    def _on_template_text_changed(self, buffer):
        """Handle template text changes (for simple command mode)."""
        pass  # No special handling needed for immediate execution mode

    def _add_form_field(self, field_type: str):
        """Add a new form field (command part)."""
        # Generate a unique ID for the field
        field_num = len(self.form_fields_data) + 1
        field_id = f"part_{field_num}" if field_type == "command_text" else f"field_{field_num}"

        # Set appropriate defaults based on field type
        if field_type == "switch":
            default = False
        elif field_type == "slider":
            default = 50  # Numeric default for slider
        elif field_type == "color":
            default = "#000000"
        elif field_type == "date_time":
            default = ""
        elif field_type == "command_text":
            default = ""
        else:
            default = ""

        field_data = {
            "type": field_type,
            "id": field_id,
            "template_key": "",
            "label": "" if field_type != "command_text" else _("Command"),
            "default": default,
            "placeholder": "",
            "tooltip": "",
            "command_flag": "",
            "off_value": "",
            "options": [],
        }

        # Add type-specific defaults
        if field_type == "slider":
            field_data["min_value"] = 0
            field_data["max_value"] = 100
            field_data["step"] = 1
        elif field_type == "text_area":
            field_data["rows"] = 4
        elif field_type == "date_time":
            field_data["format"] = "%Y-%m-%d %H:%M"
        elif field_type == "color":
            field_data["color_format"] = "hex"

        self.form_fields_data.append(field_data)

        row = self._create_field_row(field_data, len(self.form_fields_data) - 1)
        self.form_fields_list.append(row)

        self._update_preview()
        self._update_form_preview()

    def _create_field_row(self, field_data: Dict, index: int) -> Adw.ExpanderRow:
        """Create a collapsible UI row for a command part / form field."""
        type_icons = {
            "command_text": "💬",
            "text": "⌨️",
            "text_area": "📝",
            "password": "🔑",
            "switch": "🔘",
            "dropdown": "📋",
            "radio": "⚪",
            "multi_select": "☑️",
            "number": "🔢",
            "slider": "📊",
            "file_path": "📄",
            "directory_path": "📁",
            "date_time": "📅",
            "color": "🎨",
        }
        type_names = {
            "command_text": _("Command Text"),
            "text": _("Text Input"),
            "text_area": _("Text Area"),
            "password": _("Password"),
            "switch": _("Switch"),
            "dropdown": _("Dropdown"),
            "radio": _("Radio Buttons"),
            "multi_select": _("Multi-Select"),
            "number": _("Number"),
            "slider": _("Slider"),
            "file_path": _("File"),
            "directory_path": _("Directory"),
            "date_time": _("Date/Time"),
            "color": _("Color"),
        }
        field_type = field_data.get("type", "text")
        field_id = field_data.get("id", f"field_{index + 1}")

        # Create expandable row
        if field_type == "command_text":
            # For command text, show the value in title
            value = field_data.get("default", "")
            title = f"{type_icons.get(field_type, '💬')} {value}" if value else f"{type_icons.get(field_type, '💬')} {_('(empty)')}"
        else:
            title = f"{type_icons.get(field_type, '📝')} {{{field_id}}}"

        expander = Adw.ExpanderRow(
            title=title,
            subtitle=type_names.get(field_type, _("Field")),
        )

        # Store index for move operations (will be updated when rebuilding)
        expander._field_index = index

        # Move up button
        up_btn = Gtk.Button(
            icon_name="go-up-symbolic",
            css_classes=["flat", "circular"],
            valign=Gtk.Align.CENTER,
        )
        get_tooltip_helper().add_tooltip(up_btn, _("Move up"))
        up_btn.connect("clicked", lambda b: self._move_field_up(expander._field_index))
        expander.add_suffix(up_btn)

        # Move down button
        down_btn = Gtk.Button(
            icon_name="go-down-symbolic",
            css_classes=["flat", "circular"],
            valign=Gtk.Align.CENTER,
        )
        get_tooltip_helper().add_tooltip(down_btn, _("Move down"))
        down_btn.connect("clicked", lambda b: self._move_field_down(expander._field_index))
        expander.add_suffix(down_btn)

        # Delete button
        delete_btn = Gtk.Button(
            icon_name="user-trash-symbolic",
            css_classes=["flat", "circular", "error"],
            valign=Gtk.Align.CENTER,
        )
        get_tooltip_helper().add_tooltip(delete_btn, _("Remove"))
        delete_btn.connect("clicked", lambda b: self._remove_form_field(expander._field_index, expander))
        expander.add_suffix(delete_btn)

        # Type-specific content
        if field_type == "command_text":
            # Simple text entry for static command text
            text_row = Adw.EntryRow(title=_("Command text"))
            text_row.set_text(str(field_data.get("default", "")))

            def on_cmd_text_changed(r):
                field_data["default"] = r.get_text()
                value = r.get_text()
                expander.set_title(f"{type_icons.get(field_type, '💬')} {value}" if value else f"{type_icons.get(field_type, '💬')} {_('(empty)')}")
                self._update_preview()

            text_row.connect("changed", on_cmd_text_changed)
            expander.add_row(text_row)
        else:
            # Regular form fields need ID and Label
            id_row = Adw.EntryRow(title=_("ID"))
            id_row.set_text(field_data.get("id", ""))

            def on_id_changed(row):
                new_id = row.get_text()
                field_data["id"] = new_id
                expander.set_title(f"{type_icons.get(field_type, '📝')} {{{new_id}}}")
                self._update_preview()

            id_row.connect("changed", on_id_changed)
            expander.add_row(id_row)

            label_row = Adw.EntryRow(title=_("Label"))
            label_row.set_text(field_data.get("label", ""))

            def on_label_changed(r):
                field_data["label"] = r.get_text()
                self._update_preview()
                self._update_form_preview()

            label_row.connect("changed", on_label_changed)
            expander.add_row(label_row)

        # Type-specific fields
        if field_type == "text":
            placeholder_row = Adw.EntryRow(title=_("Placeholder"))
            placeholder_row.set_text(str(field_data.get("placeholder", "")))
            def on_text_placeholder_changed(r):
                field_data["placeholder"] = r.get_text()
                self._update_form_preview()
            placeholder_row.connect("changed", on_text_placeholder_changed)
            expander.add_row(placeholder_row)

            default_row = Adw.EntryRow(title=_("Default"))
            default_row.set_text(str(field_data.get("default", "")))
            def on_text_default_changed(r):
                field_data["default"] = r.get_text()
                self._update_preview()
                self._update_form_preview()
            default_row.connect("changed", on_text_default_changed)
            expander.add_row(default_row)

        elif field_type == "number":
            default_row = Adw.EntryRow(title=_("Default"))
            default_row.set_text(str(field_data.get("default", "")))
            def on_num_default_changed(r):
                field_data["default"] = r.get_text()
                self._update_preview()
                self._update_form_preview()
            default_row.connect("changed", on_num_default_changed)
            expander.add_row(default_row)

        elif field_type == "switch":
            on_row = Adw.EntryRow(title=_("On value"))
            on_row.set_text(field_data.get("command_flag", ""))
            def on_switch_on_changed(r):
                field_data["command_flag"] = r.get_text()
                self._update_preview()
            on_row.connect("changed", on_switch_on_changed)
            expander.add_row(on_row)

            off_row = Adw.EntryRow(title=_("Off value"))
            off_row.set_text(field_data.get("off_value", ""))
            def on_switch_off_changed(r):
                field_data["off_value"] = r.get_text()
                self._update_preview()
            off_row.connect("changed", on_switch_off_changed)
            expander.add_row(off_row)

            default_row = Adw.SwitchRow(title=_("Default on"))
            default_row.set_active(bool(field_data.get("default", False)))
            def on_switch_default_changed(r, p):
                field_data["default"] = r.get_active()
                self._update_form_preview()
            default_row.connect("notify::active", on_switch_default_changed)
            expander.add_row(default_row)

        elif field_type == "dropdown":
            # Options management with listbox inside expander
            options_header = Adw.ActionRow(title=_("Options"))

            add_option_btn = Gtk.Button(
                icon_name="list-add-symbolic",
                css_classes=["flat", "circular"],
                valign=Gtk.Align.CENTER,
            )
            get_tooltip_helper().add_tooltip(add_option_btn, _("Add option"))
            options_header.add_suffix(add_option_btn)

            # Create a mini listbox for options inside the expander
            options_listbox = Gtk.ListBox(
                selection_mode=Gtk.SelectionMode.NONE,
                css_classes=["boxed-list"],
            )
            options_header.set_activatable(False)
            expander.add_row(options_header)

            # Box to wrap the listbox
            options_box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                margin_start=12,
                margin_end=12,
                margin_bottom=8,
            )
            options_box.append(options_listbox)
            expander.add_row(options_box)

            def add_option_row(value: str = "", label: str = ""):
                """Add an option row to the listbox."""
                opt_row = Adw.ActionRow()

                val_entry = Gtk.Entry(
                    placeholder_text=_("Value"),
                    width_chars=10,
                    valign=Gtk.Align.CENTER,
                )
                val_entry.set_text(value)
                opt_row.add_prefix(val_entry)

                lbl_entry = Gtk.Entry(
                    placeholder_text=_("Label"),
                    width_chars=15,
                    valign=Gtk.Align.CENTER,
                    hexpand=True,
                )
                lbl_entry.set_text(label)
                opt_row.add_suffix(lbl_entry)

                # Reorder buttons
                move_up_btn = Gtk.Button(
                    icon_name="go-up-symbolic",
                    css_classes=["flat", "circular"],
                    valign=Gtk.Align.CENTER,
                )
                get_tooltip_helper().add_tooltip(move_up_btn, _("Move up"))
                opt_row.add_suffix(move_up_btn)

                move_down_btn = Gtk.Button(
                    icon_name="go-down-symbolic",
                    css_classes=["flat", "circular"],
                    valign=Gtk.Align.CENTER,
                )
                get_tooltip_helper().add_tooltip(move_down_btn, _("Move down"))
                opt_row.add_suffix(move_down_btn)

                remove_btn = Gtk.Button(
                    icon_name="user-trash-symbolic",
                    css_classes=["flat", "circular", "error"],
                    valign=Gtk.Align.CENTER,
                )
                get_tooltip_helper().add_tooltip(remove_btn, _("Remove"))
                opt_row.add_suffix(remove_btn)

                def on_remove(_):
                    options_listbox.remove(opt_row)
                    sync_options()

                def on_change(*_):
                    sync_options()

                def on_move_up(_):
                    current_idx = opt_row.get_index()
                    if current_idx > 0:
                        options_listbox.remove(opt_row)
                        options_listbox.insert(opt_row, current_idx - 1)
                        sync_options()

                def on_move_down(_):
                    current_idx = opt_row.get_index()
                    row_count = 0
                    while options_listbox.get_row_at_index(row_count) is not None:
                        row_count += 1
                    if current_idx < row_count - 1:
                        options_listbox.remove(opt_row)
                        options_listbox.insert(opt_row, current_idx + 1)
                        sync_options()

                remove_btn.connect("clicked", on_remove)
                move_up_btn.connect("clicked", on_move_up)
                move_down_btn.connect("clicked", on_move_down)
                val_entry.connect("changed", on_change)
                lbl_entry.connect("changed", on_change)

                opt_row._val_entry = val_entry
                opt_row._lbl_entry = lbl_entry
                options_listbox.append(opt_row)

            def sync_options():
                """Sync listbox state to field_data."""
                opts = []
                idx = 0
                while True:
                    row = options_listbox.get_row_at_index(idx)
                    if row is None:
                        break
                    # Adw.ActionRow is a ListBoxRow, so row IS our ActionRow
                    if hasattr(row, '_val_entry') and hasattr(row, '_lbl_entry'):
                        val = row._val_entry.get_text().strip()
                        lbl = row._lbl_entry.get_text().strip()
                        if val or lbl:
                            opts.append((val or lbl, lbl or val))
                    idx += 1
                field_data["options"] = opts
                self._update_form_preview()

            def on_add_option(_):
                add_option_row()
                sync_options()

            add_option_btn.connect("clicked", on_add_option)

            # Populate existing options
            existing = field_data.get("options", [])
            for opt in existing:
                # Options can be tuples (value, label) or strings
                if isinstance(opt, (tuple, list)) and len(opt) >= 2:
                    add_option_row(str(opt[0]), str(opt[1]))
                elif isinstance(opt, (tuple, list)) and len(opt) == 1:
                    add_option_row(str(opt[0]), str(opt[0]))
                else:
                    add_option_row(str(opt), str(opt))

        elif field_type in ("file_path", "directory_path"):
            default_row = Adw.EntryRow(title=_("Default"))
            default_row.set_text(str(field_data.get("default", "")))
            def on_path_default_changed(r):
                field_data["default"] = r.get_text()
                self._update_preview()
                self._update_form_preview()
            default_row.connect("changed", on_path_default_changed)
            expander.add_row(default_row)

        elif field_type == "password":
            # Password has no default for security reasons
            placeholder_row = Adw.EntryRow(title=_("Placeholder"))
            placeholder_row.set_text(str(field_data.get("placeholder", "")))
            def on_pwd_placeholder_changed(r):
                field_data["placeholder"] = r.get_text()
                self._update_form_preview()
            placeholder_row.connect("changed", on_pwd_placeholder_changed)
            expander.add_row(placeholder_row)

        elif field_type == "text_area":
            placeholder_row = Adw.EntryRow(title=_("Placeholder"))
            placeholder_row.set_text(str(field_data.get("placeholder", "")))
            def on_textarea_placeholder_changed(r):
                field_data["placeholder"] = r.get_text()
                self._update_form_preview()
            placeholder_row.connect("changed", on_textarea_placeholder_changed)
            expander.add_row(placeholder_row)

            default_row = Adw.EntryRow(title=_("Default"))
            default_row.set_text(str(field_data.get("default", "")))
            def on_textarea_default_changed(r):
                field_data["default"] = r.get_text()
                self._update_preview()
                self._update_form_preview()
            default_row.connect("changed", on_textarea_default_changed)
            expander.add_row(default_row)

            rows_row = Adw.SpinRow.new_with_range(2, 20, 1)
            rows_row.set_title(_("Rows"))
            rows_row.set_value(field_data.get("rows", 4))
            def on_textarea_rows_changed(r, p):
                field_data["rows"] = int(r.get_value())
            rows_row.connect("notify::value", on_textarea_rows_changed)
            expander.add_row(rows_row)

        elif field_type == "slider":
            min_row = Adw.SpinRow.new_with_range(-9999, 9999, 1)
            min_row.set_title(_("Minimum"))
            try:
                min_val = float(field_data.get("min_value", 0))
            except (ValueError, TypeError):
                min_val = 0.0
            min_row.set_value(min_val)
            def on_slider_min_changed(r, p):
                field_data["min_value"] = r.get_value()
                self._update_form_preview()
            min_row.connect("notify::value", on_slider_min_changed)
            expander.add_row(min_row)

            max_row = Adw.SpinRow.new_with_range(-9999, 9999, 1)
            max_row.set_title(_("Maximum"))
            try:
                max_val = float(field_data.get("max_value", 100))
            except (ValueError, TypeError):
                max_val = 100.0
            max_row.set_value(max_val)
            def on_slider_max_changed(r, p):
                field_data["max_value"] = r.get_value()
                self._update_form_preview()
            max_row.connect("notify::value", on_slider_max_changed)
            expander.add_row(max_row)

            step_row = Adw.SpinRow.new_with_range(0.1, 100, 0.1)
            step_row.set_title(_("Step"))
            try:
                step_val = float(field_data.get("step", 1))
            except (ValueError, TypeError):
                step_val = 1.0
            step_row.set_value(step_val)
            def on_slider_step_changed(r, p):
                field_data["step"] = r.get_value()
            step_row.connect("notify::value", on_slider_step_changed)
            expander.add_row(step_row)

            default_row = Adw.SpinRow.new_with_range(-9999, 9999, 1)
            default_row.set_title(_("Default"))
            try:
                default_val = float(field_data.get("default", 50))
            except (ValueError, TypeError):
                default_val = 50.0
            default_row.set_value(default_val)
            def on_slider_default_changed(r, p):
                field_data["default"] = r.get_value()
                self._update_preview()
                self._update_form_preview()
            default_row.connect("notify::value", on_slider_default_changed)
            expander.add_row(default_row)

        elif field_type in ("radio", "multi_select"):
            # Similar to dropdown - options management
            options_header = Adw.ActionRow(title=_("Options"))

            add_option_btn = Gtk.Button(
                icon_name="list-add-symbolic",
                css_classes=["flat", "circular"],
                valign=Gtk.Align.CENTER,
            )
            get_tooltip_helper().add_tooltip(add_option_btn, _("Add option"))
            options_header.add_suffix(add_option_btn)

            options_listbox = Gtk.ListBox(
                selection_mode=Gtk.SelectionMode.NONE,
                css_classes=["boxed-list"],
            )
            options_header.set_activatable(False)
            expander.add_row(options_header)

            options_box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                margin_start=12,
                margin_end=12,
                margin_bottom=8,
            )
            options_box.append(options_listbox)
            expander.add_row(options_box)

            def add_option_row_rm(value: str = "", label: str = ""):
                """Add an option row to the listbox."""
                opt_row = Adw.ActionRow()

                val_entry = Gtk.Entry(
                    placeholder_text=_("Value"),
                    width_chars=10,
                    valign=Gtk.Align.CENTER,
                )
                val_entry.set_text(value)
                opt_row.add_prefix(val_entry)

                lbl_entry = Gtk.Entry(
                    placeholder_text=_("Label"),
                    width_chars=15,
                    valign=Gtk.Align.CENTER,
                    hexpand=True,
                )
                lbl_entry.set_text(label)
                opt_row.add_suffix(lbl_entry)

                # Reorder buttons
                move_up_btn = Gtk.Button(
                    icon_name="go-up-symbolic",
                    css_classes=["flat", "circular"],
                    valign=Gtk.Align.CENTER,
                )
                get_tooltip_helper().add_tooltip(move_up_btn, _("Move up"))
                opt_row.add_suffix(move_up_btn)

                move_down_btn = Gtk.Button(
                    icon_name="go-down-symbolic",
                    css_classes=["flat", "circular"],
                    valign=Gtk.Align.CENTER,
                )
                get_tooltip_helper().add_tooltip(move_down_btn, _("Move down"))
                opt_row.add_suffix(move_down_btn)

                remove_btn = Gtk.Button(
                    icon_name="user-trash-symbolic",
                    css_classes=["flat", "circular", "error"],
                    valign=Gtk.Align.CENTER,
                )
                get_tooltip_helper().add_tooltip(remove_btn, _("Remove"))
                opt_row.add_suffix(remove_btn)

                def on_remove(_):
                    options_listbox.remove(opt_row)
                    sync_options_rm()

                def on_change(*_):
                    sync_options_rm()

                def on_move_up_rm(_):
                    current_idx = opt_row.get_index()
                    if current_idx > 0:
                        options_listbox.remove(opt_row)
                        options_listbox.insert(opt_row, current_idx - 1)
                        sync_options_rm()

                def on_move_down_rm(_):
                    current_idx = opt_row.get_index()
                    row_count = 0
                    while options_listbox.get_row_at_index(row_count) is not None:
                        row_count += 1
                    if current_idx < row_count - 1:
                        options_listbox.remove(opt_row)
                        options_listbox.insert(opt_row, current_idx + 1)
                        sync_options_rm()

                remove_btn.connect("clicked", on_remove)
                move_up_btn.connect("clicked", on_move_up_rm)
                move_down_btn.connect("clicked", on_move_down_rm)
                val_entry.connect("changed", on_change)
                lbl_entry.connect("changed", on_change)

                opt_row._val_entry = val_entry
                opt_row._lbl_entry = lbl_entry
                options_listbox.append(opt_row)

            def sync_options_rm():
                """Sync listbox state to field_data."""
                opts = []
                idx = 0
                while True:
                    row = options_listbox.get_row_at_index(idx)
                    if row is None:
                        break
                    if hasattr(row, '_val_entry') and hasattr(row, '_lbl_entry'):
                        val = row._val_entry.get_text().strip()
                        lbl = row._lbl_entry.get_text().strip()
                        if val or lbl:
                            opts.append((val or lbl, lbl or val))
                    idx += 1
                field_data["options"] = opts
                self._update_form_preview()

            def on_add_option_rm(_):
                add_option_row_rm()
                sync_options_rm()

            add_option_btn.connect("clicked", on_add_option_rm)

            existing = field_data.get("options", [])
            for opt in existing:
                if isinstance(opt, (tuple, list)) and len(opt) >= 2:
                    add_option_row_rm(str(opt[0]), str(opt[1]))
                elif isinstance(opt, (tuple, list)) and len(opt) == 1:
                    add_option_row_rm(str(opt[0]), str(opt[0]))
                else:
                    add_option_row_rm(str(opt), str(opt))

        elif field_type == "date_time":
            format_row = Adw.EntryRow(title=_("Format"))
            format_row.set_text(field_data.get("format", "%Y-%m-%d %H:%M"))
            def on_datetime_format_changed(r):
                field_data["format"] = r.get_text()
                self._update_preview()
            format_row.connect("changed", on_datetime_format_changed)
            expander.add_row(format_row)

            # Help text
            help_label = Gtk.Label(
                label=_("Common: %Y-%m-%d %H:%M:%S, %d/%m/%Y, %H:%M"),
                xalign=0.0,
                css_classes=["dim-label", "caption"],
                margin_start=16,
                margin_bottom=8,
            )
            expander.add_row(help_label)

        elif field_type == "color":
            format_row = Adw.ComboRow(title=_("Format"))
            formats = Gtk.StringList()
            for fmt in [_("Hex (#RRGGBB)"), _("RGB (r,g,b)")]:
                formats.append(fmt)
            format_row.set_model(formats)
            color_format = field_data.get("color_format", "hex")
            format_row.set_selected(["hex", "rgb"].index(color_format) if color_format in ["hex", "rgb"] else 0)
            def on_color_format_changed(r, p):
                field_data["color_format"] = ["hex", "rgb"][r.get_selected()]
            format_row.connect("notify::selected", on_color_format_changed)
            expander.add_row(format_row)

            default_row = Adw.EntryRow(title=_("Default"))
            default_row.set_text(str(field_data.get("default", "#000000")))
            def on_color_default_changed(r):
                field_data["default"] = r.get_text()
                self._update_preview()
                self._update_form_preview()
            default_row.connect("changed", on_color_default_changed)
            expander.add_row(default_row)

        return expander

    def _move_field_up(self, index: int):
        """Move a field up in the list."""
        if index > 0 and index < len(self.form_fields_data):
            # Swap in data
            self.form_fields_data[index], self.form_fields_data[index - 1] = \
                self.form_fields_data[index - 1], self.form_fields_data[index]
            # Rebuild UI
            self._rebuild_form_fields_list()
            self._update_preview()
            self._update_form_preview()

    def _move_field_down(self, index: int):
        """Move a field down in the list."""
        if index >= 0 and index < len(self.form_fields_data) - 1:
            # Swap in data
            self.form_fields_data[index], self.form_fields_data[index + 1] = \
                self.form_fields_data[index + 1], self.form_fields_data[index]
            # Rebuild UI
            self._rebuild_form_fields_list()
            self._update_preview()
            self._update_form_preview()

    def _rebuild_form_fields_list(self):
        """Rebuild the form fields listbox from data."""
        # Clear
        while True:
            row = self.form_fields_list.get_row_at_index(0)
            if row is None:
                break
            self.form_fields_list.remove(row)

        # Rebuild
        for i, field_data in enumerate(self.form_fields_data):
            row = self._create_field_row(field_data, i)
            self.form_fields_list.append(row)

    def _remove_form_field(self, index: int, row: Gtk.ListBoxRow):
        """Remove a form field."""
        if 0 <= index < len(self.form_fields_data):
            self.form_fields_data.pop(index)
        self.form_fields_list.remove(row)
        # Update indices on remaining rows
        self._rebuild_form_fields_list()
        self._update_preview()
        self._update_form_preview()

    def _populate_from_command(self):
        """Fill form with existing command data."""
        # Determine command type
        is_form_command = self.command.execution_mode == ExecutionMode.SHOW_DIALOG
        self._command_type = "form" if is_form_command else "simple"

        # Skip type choice, go directly to the right step
        self.back_button.set_visible(True)
        self.save_button.set_visible(True)
        self.continue_button.set_visible(False)

        if is_form_command:
            # Form command - populate form steps
            self.form_name_row.set_text(self.command.name)
            self.form_description_row.set_text(self.command.description)
            self.form_icon_entry.set_text(self.command.icon_name)
            self._selected_icon = self.command.icon_name
            self.form_icon_preview.set_from_icon_name(self.command.icon_name)

            # Display mode
            mode_idx = {
                DisplayMode.ICON_AND_TEXT: 0,
                DisplayMode.ICON_ONLY: 1,
                DisplayMode.TEXT_ONLY: 2,
            }.get(self.command.display_mode, 0)
            self.form_display_mode_row.set_selected(mode_idx)

            # Populate form fields
            for field in self.command.form_fields:
                # Convert CommandFormField to dict for the UI
                field_data = {
                    "type": self._field_type_to_string(field.field_type),
                    "id": field.id,
                    "template_key": field.template_key or field.id,
                    "label": field.label,
                    "default": field.default_value,
                    "placeholder": field.placeholder,
                    "tooltip": field.tooltip or "",
                    "command_flag": field.command_flag or "",
                    "off_value": field.off_value or "",
                    "options": list(field.options) if field.options else [],
                }
                # Load extra_config fields
                extra = field.extra_config or {}
                if "rows" in extra:
                    field_data["rows"] = extra["rows"]
                if "min_value" in extra:
                    field_data["min_value"] = extra["min_value"]
                if "max_value" in extra:
                    field_data["max_value"] = extra["max_value"]
                if "step" in extra:
                    field_data["step"] = extra["step"]
                if "format" in extra:
                    field_data["format"] = extra["format"]
                if "color_format" in extra:
                    field_data["color_format"] = extra["color_format"]
                self.form_fields_data.append(field_data)

            # Rebuild UI
            self._rebuild_form_fields_list()
            self._update_preview()
            self._update_form_preview()

            # Go directly to form builder when editing
            self.wizard_stack.set_visible_child_name("form_builder")
        else:
            # Simple command - populate simple step
            self.simple_name_row.set_text(self.command.name)
            self.simple_description_row.set_text(self.command.description)
            self.simple_command_textview.set_text(self.command.command_template)
            self.simple_icon_entry.set_text(self.command.icon_name)
            self._selected_icon = self.command.icon_name
            self.simple_icon_preview.set_from_icon_name(self.command.icon_name)

            # Display mode
            mode_idx = {
                DisplayMode.ICON_AND_TEXT: 0,
                DisplayMode.ICON_ONLY: 1,
                DisplayMode.TEXT_ONLY: 2,
            }.get(self.command.display_mode, 0)
            self.simple_display_mode_row.set_selected(mode_idx)

            # Execution mode (0 = Insert Only, 1 = Insert and Execute)
            exec_mode_idx = 1 if self.command.execution_mode == ExecutionMode.INSERT_AND_EXECUTE else 0
            self.simple_execution_mode_row.set_selected(exec_mode_idx)

            self.wizard_stack.set_visible_child_name("simple")

    def _parse_template_to_fields(self, template: str):
        """Parse a command template string into form fields data."""
        # For now, form_fields_data is already populated from stored form_fields
        # This method is a placeholder for future parsing logic
        pass

    def _field_type_to_string(self, field_type: FieldType) -> str:
        """Convert FieldType enum to string."""
        return {
            FieldType.TEXT: "text",
            FieldType.PASSWORD: "password",
            FieldType.TEXT_AREA: "text_area",
            FieldType.SWITCH: "switch",
            FieldType.DROPDOWN: "dropdown",
            FieldType.RADIO: "radio",
            FieldType.MULTI_SELECT: "multi_select",
            FieldType.NUMBER: "number",
            FieldType.SLIDER: "slider",
            FieldType.FILE_PATH: "file_path",
            FieldType.DIRECTORY_PATH: "directory_path",
            FieldType.DATE_TIME: "date_time",
            FieldType.COLOR: "color",
        }.get(field_type, "text")

    def _save_command(self, *args):
        """Save the command."""
        # Get data based on command type
        if self._command_type == "simple":
            name = self.simple_name_row.get_text().strip()
            description = self.simple_description_row.get_text().strip()
            icon_name = self.simple_icon_entry.get_text().strip() or "utilities-terminal-symbolic"
            display_mode_idx = self.simple_display_mode_row.get_selected()
            command_template = self.simple_command_textview.get_text().strip()
            # Get execution mode from dropdown (0 = Insert Only, 1 = Insert and Execute)
            exec_mode_idx = self.simple_execution_mode_row.get_selected()
            exec_mode = ExecutionMode.INSERT_ONLY if exec_mode_idx == 0 else ExecutionMode.INSERT_AND_EXECUTE
            form_fields = []
        else:
            name = self.form_name_row.get_text().strip()
            description = self.form_description_row.get_text().strip()
            icon_name = self.form_icon_entry.get_text().strip() or "utilities-terminal-symbolic"
            display_mode_idx = self.form_display_mode_row.get_selected()
            exec_mode = ExecutionMode.SHOW_DIALOG

            # Build from form_fields_data
            parts = []
            for field_data in self.form_fields_data:
                field_type = field_data.get("type", "text")
                if field_type == "command_text":
                    # Static text
                    value = field_data.get("default", "")
                    if value:
                        parts.append(value)
                else:
                    # Field placeholder
                    parts.append(f"{{{field_data.get('id', '')}}}")
            command_template = " ".join(parts)

            # Build form fields list (skip command_text - it's not a real form field)
            form_fields = []
            for i, field_data in enumerate(self.form_fields_data):
                if field_data.get("type") == "command_text":
                    continue  # Skip static text

                field_type = {
                    "text": FieldType.TEXT,
                    "password": FieldType.PASSWORD,
                    "text_area": FieldType.TEXT_AREA,
                    "switch": FieldType.SWITCH,
                    "dropdown": FieldType.DROPDOWN,
                    "radio": FieldType.RADIO,
                    "multi_select": FieldType.MULTI_SELECT,
                    "number": FieldType.NUMBER,
                    "slider": FieldType.SLIDER,
                    "file_path": FieldType.FILE_PATH,
                    "directory_path": FieldType.DIRECTORY_PATH,
                    "date_time": FieldType.DATE_TIME,
                    "color": FieldType.COLOR,
                }.get(field_data.get("type", "text"), FieldType.TEXT)

                # Build extra config dict for special field types
                extra_config = {}
                ft = field_data.get("type", "text")
                if ft == "text_area":
                    extra_config["rows"] = field_data.get("rows", 4)
                elif ft == "slider":
                    extra_config["min_value"] = field_data.get("min_value", 0)
                    extra_config["max_value"] = field_data.get("max_value", 100)
                    extra_config["step"] = field_data.get("step", 1)
                elif ft == "date_time":
                    extra_config["format"] = field_data.get("format", "%Y-%m-%d %H:%M")
                elif ft == "color":
                    extra_config["color_format"] = field_data.get("color_format", "hex")

                form_fields.append(CommandFormField(
                    id=field_data.get("id", f"field_{i}"),
                    label=field_data.get("label", ""),
                    field_type=field_type,
                    default_value=str(field_data.get("default", "")),
                    placeholder=field_data.get("placeholder", ""),
                    tooltip=field_data.get("tooltip", ""),
                    required=False,
                    command_flag=field_data.get("command_flag", ""),
                    off_value=field_data.get("off_value", ""),
                    options=field_data.get("options", []),
                    template_key=field_data.get("template_key", ""),
                    extra_config=extra_config,
                ))

        if not name:
            if self._command_type == "simple":
                self.simple_name_row.add_css_class("error")
            else:
                self.form_name_row.add_css_class("error")
            return

        if not command_template:
            return

        # Get display mode
        display_modes = [DisplayMode.ICON_AND_TEXT, DisplayMode.ICON_ONLY, DisplayMode.TEXT_ONLY]
        display_mode = display_modes[display_mode_idx]

        # Preserve builtin status when editing existing commands
        is_builtin = self.command.is_builtin if self.command else False

        new_command = CommandButton(
            id=self.command.id if self.command else generate_id(),
            name=name,
            description=description,
            command_template=command_template,
            icon_name=icon_name,
            display_mode=display_mode,
            execution_mode=exec_mode,
            cursor_position=0,
            category=_("Custom"),
            is_builtin=is_builtin,
            form_fields=form_fields,
        )

        self.emit("save-requested", new_command)
        self.close()


class CommandManagerDialog(Adw.Window):
    """
    Main Command Manager dialog with button-based commands and send-to-all functionality.
    Redesigned for modern UI/UX with search, grid layout, and streamlined interactions.
    """

    __gsignals__ = {
        "command-selected": (GObject.SignalFlags.RUN_FIRST, None, (str, bool)),  # (command, execute)
    }

    def __init__(self, parent_window, settings_manager: Optional[SettingsManager] = None):
        super().__init__(
            transient_for=parent_window,
            modal=False,
        )
        self.add_css_class("zashterminal-dialog")
        self.add_css_class("command-manager-dialog")
        self.parent_window = parent_window
        self.command_manager = get_command_button_manager()
        self._settings_manager = settings_manager

        self._allow_destroy = False
        self._presenting = False
        self._search_filter = ""
        self._all_command_widgets: List[CommandButtonWidget] = []

        # Calculate size based on parent
        parent_width = parent_window.get_width()
        parent_height = parent_window.get_height()
        self.set_default_size(int(parent_width * 0.7), int(parent_height * 0.75))
        self.set_title(_("Command Manager"))

        self._build_ui()
        self._apply_color_scheme()
        self._populate_commands()

        # Connect signals
        self.connect("notify::is-active", self._on_active_changed)
        self.connect("close-request", self._on_close_request)

        if parent_window:
            parent_window.connect("destroy", self._on_parent_destroyed)

        # Keyboard handler
        key_controller = Gtk.EventControllerKey.new()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_controller)

    def present(self):
        self._presenting = True
        super().present()
        GLib.idle_add(lambda: setattr(self, '_presenting', False))

    def _apply_color_scheme(self):
        """Apply terminal color scheme to BashTextView when gtk_theme is 'terminal'.
        
        Dialog theming is handled globally via the zashterminal-dialog class.
        This method only updates the syntax highlighting in the BashTextView.
        """
        if not self._settings_manager:
            return

        gtk_theme = self._settings_manager.get("gtk_theme", "")
        if gtk_theme != "terminal":
            return

        # Update BashTextView syntax highlighting colors
        scheme = self._settings_manager.get_color_scheme_data()
        palette = scheme.get("palette", [])
        fg_color = scheme.get("foreground", "#ffffff")
        if palette and hasattr(self, 'command_textview') and self.command_textview:
            self.command_textview.update_colors_from_scheme(palette, fg_color)

    def _build_ui(self):
        # Static CSS is loaded from dialogs.css at application startup
        # Dynamic theme colors are applied via _apply_color_scheme()

        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        # Header bar with search on left side
        header = Adw.HeaderBar()
        toolbar_view.add_top_bar(header)

        # Search entry on left side of header
        self.search_entry = Gtk.SearchEntry(
            placeholder_text=_("Search..."),
            width_chars=20,
        )
        self.search_entry.connect("search-changed", self._on_search_changed)
        header.pack_start(self.search_entry)

        # Restore hidden button (only visible when there are hidden commands)
        self.restore_hidden_button = Gtk.Button(
            icon_name="view-reveal-symbolic",
        )
        get_tooltip_helper().add_tooltip(self.restore_hidden_button, _("Restore hidden commands"))
        self.restore_hidden_button.connect("clicked", self._on_restore_hidden_clicked)
        header.pack_end(self.restore_hidden_button)
        self._update_restore_hidden_visibility()

        # Main content
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Command input section - modern integrated design
        input_section = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
            margin_top=12,
            margin_bottom=10,
            margin_start=16,
            margin_end=16,
        )

        # Subtle hint label
        hint_label = Gtk.Label(
            label=_("Send to all terminals"),
            css_classes=["dim-label", "caption"],
            halign=Gtk.Align.START,
        )
        input_section.append(hint_label)

        # Unified container with text input and button using overlay
        input_overlay = Gtk.Overlay()

        # Main text input area
        input_frame = Gtk.Frame()
        input_frame.add_css_class("view")
        input_frame.add_css_class("command-input-frame")

        scrolled_text = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            min_content_height=60,
            max_content_height=200,
            propagate_natural_height=True,
        )
        self.command_textview = BashTextView()

        # Add right margin to make room for execute button
        self.command_textview.set_right_margin(85)

        # Placeholder handling
        placeholder_buffer = self.command_textview.get_buffer()
        placeholder_buffer.connect("changed", self._on_textview_changed)

        scrolled_text.set_child(self.command_textview)
        input_frame.set_child(scrolled_text)
        input_overlay.set_child(input_frame)

        # Execute button overlaid on the right side, vertically centered
        self.execute_button = Gtk.Button(
            label=_("Execute"),
            css_classes=["suggested-action"],
            halign=Gtk.Align.END,
            valign=Gtk.Align.CENTER,
            margin_end=8,
        )
        self.execute_button.connect("clicked", self._on_execute_clicked)
        input_overlay.add_overlay(self.execute_button)

        input_section.append(input_overlay)
        main_box.append(input_section)

        # Separator
        main_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL, margin_start=16, margin_end=16))

        # Scrolled area for commands (grid layout)
        scrolled = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vexpand=True,
        )

        # FlowBox for command buttons with improved spacing
        self.commands_flow_box = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            homogeneous=False,
            max_children_per_line=5,
            min_children_per_line=2,
            row_spacing=8,
            column_spacing=8,
            margin_top=14,
            margin_bottom=14,
            margin_start=16,
            margin_end=16,
            valign=Gtk.Align.START,
            css_classes=["commands-flow-box"],
        )
        self.commands_flow_box.set_filter_func(self._filter_command)

        scrolled.set_child(self.commands_flow_box)
        main_box.append(scrolled)

        # Bottom action bar with Add Command button
        bottom_bar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=12,
            margin_top=8,
            margin_bottom=12,
            margin_start=16,
            margin_end=16,
            halign=Gtk.Align.CENTER,
        )

        add_command_button = Gtk.Button(
            css_classes=["add-command-button"],
        )
        add_button_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        add_button_content.append(Gtk.Image.new_from_icon_name("list-add-symbolic"))
        add_button_content.append(Gtk.Label(label=_("Add Command")))
        add_command_button.set_child(add_button_content)
        add_command_button.connect("clicked", self._on_add_clicked)
        bottom_bar.append(add_command_button)

        main_box.append(bottom_bar)

        toolbar_view.set_content(main_box)

    def _on_textview_changed(self, buffer):
        """Handle textview content changes."""
        pass  # Placeholder for future functionality

    def _filter_command(self, flow_box_child) -> bool:
        """Filter function for command buttons based on search."""
        if not self._search_filter:
            return True

        button = flow_box_child.get_child()
        if not isinstance(button, CommandButtonWidget):
            return True

        # Check if command is hidden
        if hasattr(button, 'command') and button.command:
            if self.command_manager.is_command_hidden(button.command.id):
                return False

        search_lower = self._search_filter.lower()
        command = button.command

        # Search in name, description, and command template
        return (
            search_lower in command.name.lower() or
            search_lower in command.description.lower() or
            search_lower in command.command_template.lower()
        )

    def _on_search_changed(self, entry):
        """Handle search text changes."""
        self._search_filter = entry.get_text().strip()
        self.commands_flow_box.invalidate_filter()

    def _populate_commands(self):
        """Populate the dialog with command buttons in a flat grid (no categories)."""
        # Clear existing
        while child := self.commands_flow_box.get_first_child():
            self.commands_flow_box.remove(child)

        self._all_command_widgets.clear()

        # Get all commands and sort by name
        commands = self.command_manager.get_all_commands()
        visible_commands = [
            cmd for cmd in commands
            if not self.command_manager.is_command_hidden(cmd.id)
        ]

        # Sort commands alphabetically by name
        for cmd in sorted(visible_commands, key=lambda c: c.name.lower()):
            btn = CommandButtonWidget(cmd)
            btn.connect("command-activated", self._on_command_activated)
            btn.connect("command-activated-all", self._on_command_activated_all)
            btn.connect("edit-requested", self._on_edit_requested)
            btn.connect("delete-requested", self._on_delete_requested)
            btn.connect("restore-requested", self._on_restore_requested)
            btn.connect("hide-requested", self._on_hide_requested)
            btn.connect("duplicate-requested", self._on_duplicate_requested)
            btn.connect("pin-requested", self._on_pin_requested)
            btn.connect("unpin-requested", self._on_unpin_requested)
            self._all_command_widgets.append(btn)
            self.commands_flow_box.append(btn)

    def _on_command_activated(self, widget, command: CommandButton):
        """Handle command button click."""
        if command.execution_mode == ExecutionMode.SHOW_DIALOG:
            # Show form dialog first
            dialog = CommandFormDialog(
                self.parent_window, command, send_to_all=False,
                settings_manager=self._settings_manager
            )
            dialog.connect("command-ready", self._on_form_command_ready)
            dialog.present()
        else:
            # Send command directly to the current terminal
            cmd_text = command.command_template
            execute = command.execution_mode == ExecutionMode.INSERT_AND_EXECUTE
            self.emit("command-selected", cmd_text, execute)
            self.close()

    def _on_command_activated_all(self, widget, command: CommandButton):
        """Handle 'Execute in All Terminals' from context menu."""
        if command.execution_mode == ExecutionMode.SHOW_DIALOG:
            # Show form dialog with send_to_all=True
            dialog = CommandFormDialog(
                self.parent_window, command, send_to_all=True,
                settings_manager=self._settings_manager
            )
            dialog.connect("command-ready", self._on_form_command_ready)
            dialog.present()
        else:
            # Build command directly and show terminal selection with all pre-selected
            cmd_text = command.command_template
            execute = command.execution_mode == ExecutionMode.INSERT_AND_EXECUTE
            self._show_terminal_selection_dialog(cmd_text, execute, pre_select_all=True)

    def _on_form_command_ready(self, dialog, command: str, execute: bool, send_to_all: bool):
        """Handle command ready from form dialog."""
        if send_to_all:
            # Show terminal selection dialog with all terminals pre-selected
            self._show_terminal_selection_dialog(command, execute, pre_select_all=True)
        else:
            # Send directly to current terminal
            self.emit("command-selected", command, execute)
            self.close()

    def _send_to_all_terminals(self, command: str, execute: bool):
        """Send command to all terminals via parent window."""
        if hasattr(self.parent_window, '_broadcast_command_to_all'):
            # Add newline if executing
            cmd = command + "\n" if execute else command
            self.parent_window._broadcast_command_to_all(cmd)
        else:
            # Fallback to single terminal
            self.emit("command-selected", command, execute)

    def _show_terminal_selection_dialog(self, command: str, execute: bool, pre_select_all: bool = False):
        """Show dialog to select which terminals should receive the command."""
        if not hasattr(self.parent_window, 'tab_manager'):
            # Fallback - just send to all
            self._send_to_all_terminals(command, execute)
            self.close()
            return

        all_terminals = self.parent_window.tab_manager.get_all_terminals_across_tabs()
        if not all_terminals:
            if hasattr(self.parent_window, 'toast_overlay'):
                self.parent_window.toast_overlay.add_toast(
                    Adw.Toast(title=_("No open terminals found."))
                )
            return

        count = len(all_terminals)
        dialog = Adw.MessageDialog(
            transient_for=self.parent_window,
            heading=_("Confirm sending of command"),
            body=_(
                "Select which of the <b>{count}</b> open terminals should receive the command below."
            ).format(count=count),
            body_use_markup=True,
            close_response="cancel",
        )
        dialog.add_css_class("zashterminal-dialog")

        # Display the command for the user to review with syntax highlighting
        palette = None
        fg_color = "#ffffff"
        if self._settings_manager and self._settings_manager.get("gtk_theme", "") == "terminal":
            scheme = self._settings_manager.get_color_scheme_data()
            palette = scheme.get("palette", [])
            fg_color = scheme.get("foreground", "#ffffff")
        highlighted_cmd = get_bash_pango_markup(command, palette, fg_color)
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

        # Get active/current terminal to determine default selection
        current_terminal = None
        if hasattr(self.parent_window, 'tab_manager') and not pre_select_all:
            current_terminal = self.parent_window.tab_manager.get_selected_terminal()

        selection_controls = []
        for terminal in all_terminals:
            display_title = self._get_terminal_display_name(terminal)
            check_button = Gtk.CheckButton(label=display_title)
            # If pre_select_all is True, select all; otherwise only select current terminal
            if pre_select_all:
                check_button.set_active(True)
            else:
                check_button.set_active(terminal == current_terminal)
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
        dialog.set_extra_child(content_box)

        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("send", _("Send Command"))
        dialog.set_default_response("send")
        dialog.set_response_appearance("send", Adw.ResponseAppearance.SUGGESTED)

        dialog.connect(
            "response",
            self._on_terminal_selection_response,
            command,
            execute,
            selection_controls,
        )
        dialog.present()

    def _get_terminal_display_name(self, terminal) -> str:
        """Get display name for a terminal."""
        if hasattr(self.parent_window, '_get_terminal_display_name'):
            return self.parent_window._get_terminal_display_name(terminal)

        if hasattr(self.parent_window, 'terminal_manager'):
            terminal_id = self.parent_window.terminal_manager.registry.get_terminal_id(terminal)
            if terminal_id:
                terminal_info = self.parent_window.terminal_manager.registry.get_terminal_info(terminal_id)
                if terminal_info:
                    identifier = terminal_info.get("identifier")
                    if hasattr(identifier, 'name'):
                        return identifier.name
                    if isinstance(identifier, str):
                        return identifier

        return _("Terminal")

    def _on_terminal_selection_response(
        self,
        dialog,
        response_id: str,
        command: str,
        execute: bool,
        selection_controls: list,
    ):
        """Handle terminal selection dialog response."""
        if response_id == "send":
            selected_terminals = [
                terminal for terminal, check in selection_controls if check.get_active()
            ]

            if not selected_terminals:
                return

            # Send command to selected terminals
            cmd = command + "\n" if execute else command
            command_bytes = cmd.encode("utf-8")

            if not cmd.endswith("\n"):
                # Use bracketed paste for insertion without execution
                for terminal in selected_terminals:
                    paste_data = b"\x1b[200~" + command_bytes + b"\x1b[201~"
                    terminal.feed_child(paste_data)
            else:
                # Execute on selected terminals
                for terminal in selected_terminals:
                    terminal.feed_child(command_bytes)

            self.close()

    def _on_execute_clicked(self, button):
        """Handle execute button click - shows terminal selection dialog."""
        command = self.command_textview.get_text().strip()
        if not command:
            return

        self._show_terminal_selection_dialog(command, execute=True)
        self.command_textview.set_text("")

    def _on_add_clicked(self, button):
        """Open editor dialog for new command."""
        dialog = CommandEditorDialog(self.parent_window, settings_manager=self._settings_manager)
        dialog.connect("save-requested", self._on_save_new_command)
        dialog.present()

    def _on_edit_requested(self, widget, command: CommandButton):
        """Open editor dialog for existing command (builtin or custom)."""
        dialog = CommandEditorDialog(self.parent_window, command, settings_manager=self._settings_manager)
        dialog.connect("save-requested", self._on_save_edited_command)
        dialog.present()

    def _on_delete_requested(self, widget, command: CommandButton):
        """Show delete confirmation."""
        dialog = Adw.MessageDialog(
            transient_for=self.parent_window,
            heading=_("Delete Command?"),
            body=_("Are you sure you want to delete '{name}'?").format(name=command.name),
            default_response="cancel",
            close_response="cancel",
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_delete_confirmed, command)
        dialog.present()

    def _on_delete_confirmed(self, dialog, response, command):
        if response == "delete":
            self.command_manager.remove_command(command.id)
            self._populate_commands()

    def _on_restore_requested(self, widget, command: CommandButton):
        """Restore a builtin command to its default state."""
        dialog = Adw.MessageDialog(
            transient_for=self.parent_window,
            heading=_("Restore Default?"),
            body=_("This will restore '{name}' to its original default configuration. Your customizations will be lost.").format(name=command.name),
            default_response="cancel",
            close_response="cancel",
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("restore", _("Restore Default"))
        dialog.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_restore_confirmed, command)
        dialog.present()

    def _on_restore_confirmed(self, dialog, response, command):
        if response == "restore":
            self.command_manager.restore_builtin_default(command.id)
            self._populate_commands()

    def _on_pin_requested(self, widget, command: CommandButton):
        """Pin a command to the toolbar."""
        self.command_manager.pin_command(command.id)
        self._populate_commands()  # Refresh to update context menu
        # Notify parent window to refresh toolbar
        if hasattr(self.parent_window, "refresh_command_toolbar"):
            self.parent_window.refresh_command_toolbar()

    def _on_unpin_requested(self, widget, command: CommandButton):
        """Unpin a command from the toolbar."""
        self.command_manager.unpin_command(command.id)
        self._populate_commands()  # Refresh to update context menu
        # Notify parent window to refresh toolbar
        if hasattr(self.parent_window, "refresh_command_toolbar"):
            self.parent_window.refresh_command_toolbar()

    def _on_hide_requested(self, widget, command: CommandButton):
        """Hide a command from the interface."""
        dialog = Adw.MessageDialog(
            transient_for=self.parent_window,
            heading=_("Hide Command?"),
            body=_("Hide '{name}' from the command list? You can restore it later from settings.").format(name=command.name),
            default_response="cancel",
            close_response="cancel",
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("hide", _("Hide"))
        dialog.connect("response", self._on_hide_confirmed, command)
        dialog.present()

    def _on_hide_confirmed(self, dialog, response, command):
        if response == "hide":
            self.command_manager.hide_command(command.id)
            self._populate_commands()
            self._update_restore_hidden_visibility()

    def _update_restore_hidden_visibility(self):
        """Update visibility of restore hidden button based on hidden commands."""
        hidden_ids = self.command_manager.get_hidden_command_ids()
        self.restore_hidden_button.set_visible(len(hidden_ids) > 0)

    def _on_restore_hidden_clicked(self, button):
        """Show dialog to restore hidden commands."""
        hidden_ids = self.command_manager.get_hidden_command_ids()
        if not hidden_ids:
            return

        # Create a dialog with checkboxes for each hidden command
        dialog = Adw.Window(
            transient_for=self.parent_window,
            modal=True,
            default_width=400,
            default_height=400,
            title=_("Restore Hidden Commands"),
        )

        toolbar_view = Adw.ToolbarView()
        dialog.set_content(toolbar_view)

        header = Adw.HeaderBar()
        toolbar_view.add_top_bar(header)

        close_btn = Gtk.Button(label=_("Close"))
        close_btn.connect("clicked", lambda b: dialog.close())
        header.pack_end(close_btn)

        content_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=8,
            margin_top=16,
            margin_bottom=16,
            margin_start=16,
            margin_end=16,
        )

        info_label = Gtk.Label(
            label=_("Click on a command to restore it:"),
            xalign=0.0,
            css_classes=["dim-label"],
        )
        content_box.append(info_label)

        scrolled = Gtk.ScrolledWindow(vexpand=True)
        list_box = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE,
            css_classes=["boxed-list"],
        )

        # Get all commands to find names for hidden IDs
        all_commands = self.command_manager.get_all_commands()
        # Also get original builtins to find hidden builtin names
        from ...data.command_manager_models import get_builtin_commands
        builtin_commands = get_builtin_commands()

        all_cmd_map = {cmd.id: cmd for cmd in all_commands}
        builtin_map = {cmd.id: cmd for cmd in builtin_commands}

        for cmd_id in hidden_ids:
            # Try to get command name
            cmd = all_cmd_map.get(cmd_id) or builtin_map.get(cmd_id)
            cmd_name = cmd.name if cmd else cmd_id

            row = Adw.ActionRow(
                title=cmd_name,
                activatable=True,
            )
            row.add_suffix(Gtk.Image.new_from_icon_name("view-reveal-symbolic"))
            row.connect("activated", self._on_unhide_command, cmd_id, dialog)
            list_box.append(row)

        scrolled.set_child(list_box)
        content_box.append(scrolled)

        # Restore all button
        restore_all_btn = Gtk.Button(
            label=_("Restore All"),
            css_classes=["suggested-action"],
            halign=Gtk.Align.CENTER,
            margin_top=8,
        )
        restore_all_btn.connect("clicked", self._on_restore_all_hidden, dialog)
        content_box.append(restore_all_btn)

        toolbar_view.set_content(content_box)
        dialog.present()

    def _on_unhide_command(self, row, cmd_id, dialog):
        """Unhide a single command."""
        self.command_manager.unhide_command(cmd_id)
        self._populate_commands()
        self._update_restore_hidden_visibility()

        # Close dialog if no more hidden commands
        if not self.command_manager.get_hidden_command_ids():
            dialog.close()
        else:
            # Remove the row from list
            parent = row.get_parent()
            if parent:
                parent.remove(row)

    def _on_restore_all_hidden(self, button, dialog):
        """Restore all hidden commands."""
        for cmd_id in list(self.command_manager.get_hidden_command_ids()):
            self.command_manager.unhide_command(cmd_id)
        self._populate_commands()
        self._update_restore_hidden_visibility()
        dialog.close()

    def _on_duplicate_requested(self, widget, command: CommandButton):
        """Duplicate a command as a new custom command."""
        # Create a copy with new ID
        new_command = CommandButton(
            id=generate_id(),
            name=f"{command.name} " + _("(Copy)"),
            description=command.description,
            command_template=command.command_template,
            icon_name=command.icon_name,
            display_mode=command.display_mode,
            execution_mode=command.execution_mode,
            cursor_position=command.cursor_position,
            form_fields=list(command.form_fields),
            is_builtin=False,  # Duplicates are always custom
            category=command.category,
            sort_order=command.sort_order,
        )

        # Open editor with the copy
        dialog = CommandEditorDialog(self.parent_window, new_command, settings_manager=self._settings_manager)
        dialog.connect("save-requested", self._on_save_new_command)
        dialog.present()

    def _on_save_new_command(self, dialog, command: CommandButton):
        """Save a new command."""
        self.command_manager.add_custom_command(command)
        self._populate_commands()

    def _on_save_edited_command(self, dialog, command: CommandButton):
        """Save an edited command (custom or customized builtin)."""
        self.command_manager.update_command(command)
        self._populate_commands()

    def _on_key_pressed(self, controller, keyval, _keycode, state):
        if keyval == Gdk.KEY_Escape:
            self.close()
            return Gdk.EVENT_STOP
        return Gdk.EVENT_PROPAGATE

    def _on_active_changed(self, widget, _pspec):
        if not self._presenting and not self.is_active() and self.get_visible():
            GLib.timeout_add(200, self._delayed_close)

    def _delayed_close(self):
        if not self.is_active() and self.get_visible():
            self.close()
        return False

    def _on_close_request(self, widget):
        self.hide()
        return Gdk.EVENT_STOP

    def close(self):
        self.hide()

    def destroy(self):
        if not hasattr(self, '_allow_destroy') or not self._allow_destroy:
            self.hide()
            return
        super().destroy()

    def _on_parent_destroyed(self, parent):
        self._allow_destroy = True
        self.destroy()
