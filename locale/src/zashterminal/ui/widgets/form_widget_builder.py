# zashterminal/ui/widgets/form_widget_builder.py
"""
Form Widget Builder - Factory for creating Adwaita form widgets.

This module provides a centralized builder for creating form field widgets
used in command dialogs. It eliminates code duplication between
CommandFormDialog and CommandEditorDialog.
"""

from typing import Any, Callable, Dict, List, Optional, Tuple
from dataclasses import dataclass

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk

from ...utils.translation_utils import _
from ...utils.tooltip_helper import get_tooltip_helper


@dataclass(slots=True)
class FieldConfig:
    """Configuration for a form field widget."""
    field_type: str
    label: str
    field_id: str = ""
    tooltip: str = ""
    placeholder: str = ""
    default_value: Any = None
    options: List[Tuple[str, str]] = None  # List of (value, label) tuples
    extra_config: Dict[str, Any] = None

    def __post_init__(self):
        if self.options is None:
            self.options = []
        if self.extra_config is None:
            self.extra_config = {}


class FormWidgetBuilder:
    """
    Factory class for creating Adwaita form field widgets.
    
    Supports creating widgets for various field types:
    - text: Single-line text entry
    - password: Password entry with peek icon
    - text_area: Multi-line text area
    - number: Spin button for numeric input
    - slider: Scale widget for range selection
    - switch: Toggle switch
    - dropdown: Combo row with dropdown options
    - radio: Radio button group
    - multi_select: Checkbox group
    - file_path: File chooser entry
    - directory_path: Directory chooser entry
    - date_time: Date/time entry
    - color: Color picker button
    - command_text: Static command text display
    """

    @staticmethod
    def create_field_widget(
        config: FieldConfig,
        on_change: Optional[Callable] = None,
        interactive: bool = True,
    ) -> Tuple[Gtk.Widget, Gtk.Widget]:
        """
        Create a form field widget based on the configuration.
        
        Args:
            config: Field configuration
            on_change: Callback to invoke when the field value changes
            interactive: Whether the widget should be interactive (False for preview)
            
        Returns:
            Tuple of (row_widget, value_widget) where row_widget is the container
            and value_widget is the widget that holds/provides the value
        """
        builder = FormWidgetBuilder()

        creators = {
            "text": builder._create_text_field,
            "password": builder._create_password_field,
            "text_area": builder._create_text_area_field,
            "number": builder._create_number_field,
            "slider": builder._create_slider_field,
            "switch": builder._create_switch_field,
            "dropdown": builder._create_dropdown_field,
            "radio": builder._create_radio_field,
            "multi_select": builder._create_multi_select_field,
            "file_path": builder._create_file_path_field,
            "directory_path": builder._create_directory_path_field,
            "date_time": builder._create_date_time_field,
            "color": builder._create_color_field,
            "command_text": builder._create_command_text_field,
        }

        creator = creators.get(config.field_type, builder._create_text_field)
        return creator(config, on_change, interactive)

    def _create_text_field(
        self, config: FieldConfig, on_change: Optional[Callable], interactive: bool
    ) -> Tuple[Gtk.Widget, Gtk.Widget]:
        """Create a single-line text entry field.
        
        Uses Adw.ActionRow with Gtk.Entry when placeholder is needed (for proper
        placeholder visibility), otherwise uses Adw.EntryRow for cleaner look.
        """
        # If placeholder is specified, use ActionRow + Entry for native placeholder support
        if config.placeholder:
            row = Adw.ActionRow(title=config.label)

            if config.tooltip:
                row.set_subtitle(config.tooltip)

            entry = Gtk.Entry(
                placeholder_text=config.placeholder,
                hexpand=True,
                valign=Gtk.Align.CENTER,
            )
            entry.set_editable(interactive)

            if config.default_value:
                entry.set_text(str(config.default_value))

            if on_change and interactive:
                entry.connect("changed", lambda *_: on_change())

            row.add_suffix(entry)
            row.set_activatable_widget(entry)
            return row, entry

        # No placeholder - use standard Adw.EntryRow
        row = Adw.EntryRow(title=config.label)

        if config.tooltip:
            get_tooltip_helper().add_tooltip(row, config.tooltip)

        if config.default_value:
            row.set_text(str(config.default_value))

        if on_change and interactive:
            row.connect("changed", lambda *_: on_change())

        row.set_editable(interactive)
        return row, row

    def _create_password_field(
        self, config: FieldConfig, on_change: Optional[Callable], interactive: bool
    ) -> Tuple[Gtk.Widget, Gtk.Widget]:
        """Create a password entry field."""
        row = Adw.ActionRow(title=config.label)

        if config.tooltip:
            row.set_subtitle(config.tooltip)

        pwd_entry = Gtk.PasswordEntry(
            placeholder_text=config.placeholder or _("Enter password..."),
            show_peek_icon=True,
            hexpand=True,
            valign=Gtk.Align.CENTER,
        )

        if config.default_value:
            pwd_entry.set_text(str(config.default_value))

        if on_change and interactive:
            pwd_entry.connect("changed", lambda *_: on_change())

        pwd_entry.set_editable(interactive)
        row.add_suffix(pwd_entry)
        return row, pwd_entry

    def _create_text_area_field(
        self, config: FieldConfig, on_change: Optional[Callable], interactive: bool
    ) -> Tuple[Gtk.Widget, Gtk.Widget]:
        """Create a multi-line text area field."""
        row = Adw.ActionRow(title=config.label)

        if config.tooltip:
            row.set_subtitle(config.tooltip)
        elif config.placeholder:
            row.set_subtitle(config.placeholder)

        num_rows = config.extra_config.get("rows", 4)

        text_view = Gtk.TextView(
            wrap_mode=Gtk.WrapMode.WORD,
            editable=interactive,
            margin_top=4,
            margin_bottom=4,
            margin_start=4,
            margin_end=4,
            monospace=True,
        )
        text_view.add_css_class("card")

        if config.default_value:
            text_view.get_buffer().set_text(str(config.default_value))

        if on_change and interactive:
            text_view.get_buffer().connect("changed", lambda *_: on_change())

        scrolled = Gtk.ScrolledWindow(
            hexpand=True,
            height_request=num_rows * 20,
        )
        scrolled.set_child(text_view)
        row.add_suffix(scrolled)

        return row, text_view

    def _create_number_field(
        self, config: FieldConfig, on_change: Optional[Callable], interactive: bool
    ) -> Tuple[Gtk.Widget, Gtk.Widget]:
        """Create a number spin button field."""
        min_val = config.extra_config.get("min_value", 0)
        max_val = config.extra_config.get("max_value", 9999)
        step = config.extra_config.get("step", 1)

        row = Adw.SpinRow.new_with_range(min_val, max_val, step)
        row.set_title(config.label)

        if config.tooltip:
            row.set_subtitle(config.tooltip)

        if config.default_value is not None:
            try:
                row.set_value(float(config.default_value))
            except (ValueError, TypeError):
                pass

        if on_change and interactive:
            row.connect("notify::value", lambda *_: on_change())

        row.set_editable(interactive)
        return row, row

    def _create_slider_field(
        self, config: FieldConfig, on_change: Optional[Callable], interactive: bool
    ) -> Tuple[Gtk.Widget, Gtk.Widget]:
        """Create a slider/scale field."""
        min_val = config.extra_config.get("min_value", 0)
        max_val = config.extra_config.get("max_value", 100)
        step = config.extra_config.get("step", 1)

        row = Adw.ActionRow(title=config.label)

        if config.tooltip:
            row.set_subtitle(config.tooltip)

        scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL,
            min_val, max_val, step
        )
        scale.set_hexpand(True)
        scale.set_draw_value(True)
        scale.set_value_pos(Gtk.PositionType.RIGHT)
        scale.set_sensitive(interactive)

        default_val = (min_val + max_val) / 2
        if config.default_value is not None:
            try:
                default_val = float(config.default_value)
            except (ValueError, TypeError):
                pass
        scale.set_value(default_val)

        if on_change and interactive:
            scale.connect("value-changed", lambda *_: on_change())

        row.add_suffix(scale)
        return row, scale

    def _create_switch_field(
        self, config: FieldConfig, on_change: Optional[Callable], interactive: bool
    ) -> Tuple[Gtk.Widget, Gtk.Widget]:
        """Create a switch/toggle field."""
        row = Adw.SwitchRow(
            title=config.label,
            subtitle=config.tooltip or None,
        )

        if config.default_value:
            row.set_active(bool(config.default_value))

        if on_change and interactive:
            row.connect("notify::active", lambda *_: on_change())

        row.set_activatable(interactive)
        return row, row

    def _create_dropdown_field(
        self, config: FieldConfig, on_change: Optional[Callable], interactive: bool
    ) -> Tuple[Gtk.Widget, Gtk.Widget]:
        """Create a dropdown/combo field."""
        row = Adw.ComboRow(
            title=config.label,
            subtitle=config.tooltip or None,
        )

        if config.options:
            model = Gtk.StringList()
            for opt in config.options:
                if isinstance(opt, (tuple, list)) and len(opt) >= 2:
                    model.append(str(opt[1]))  # Use label
                elif isinstance(opt, (tuple, list)) and len(opt) == 1:
                    model.append(str(opt[0]))
                else:
                    model.append(str(opt))
            row.set_model(model)

            # Find default index
            default_idx = 0
            for i, opt in enumerate(config.options):
                value = opt[0] if isinstance(opt, (tuple, list)) else opt
                if value == config.default_value:
                    default_idx = i
                    break
            row.set_selected(default_idx)

            # Store options for later retrieval
            row._options = config.options
        else:
            row.set_subtitle(_("No options defined"))

        if on_change and interactive:
            row.connect("notify::selected", lambda *_: on_change())

        row.set_sensitive(interactive)
        return row, row

    def _create_radio_field(
        self, config: FieldConfig, on_change: Optional[Callable], interactive: bool
    ) -> Tuple[Gtk.Widget, Gtk.Widget]:
        """Create a radio button group field."""
        group = Adw.PreferencesGroup(title=config.label)

        if config.tooltip:
            group.set_description(config.tooltip)

        radio_group = None
        selected_value = None

        def on_radio_toggled(btn, value, grp):
            """Update selected value and call on_change when radio toggled."""
            if btn.get_active():
                grp._selected_value = value
                if on_change:
                    on_change()

        if config.options:
            for i, opt in enumerate(config.options):
                if isinstance(opt, (tuple, list)) and len(opt) >= 2:
                    value, label = opt[0], opt[1]
                else:
                    value = label = str(opt)

                opt_row = Adw.ActionRow(title=label)
                check = Gtk.CheckButton()
                check.set_sensitive(interactive)

                if radio_group is None:
                    radio_group = check
                else:
                    check.set_group(radio_group)

                if value == config.default_value or (i == 0 and not config.default_value):
                    check.set_active(True)
                    selected_value = value

                check._value = value
                if interactive:
                    check.connect("toggled", on_radio_toggled, value, group)

                opt_row.add_prefix(check)
                opt_row.set_activatable_widget(check)
                group.add(opt_row)
        else:
            empty_row = Adw.ActionRow(title=_("No options defined"))
            group.add(empty_row)

        group._selected_value = selected_value
        return group, group

    def _create_multi_select_field(
        self, config: FieldConfig, on_change: Optional[Callable], interactive: bool
    ) -> Tuple[Gtk.Widget, Gtk.Widget]:
        """Create a multi-select checkbox group field."""
        group = Adw.PreferencesGroup(title=config.label)

        if config.tooltip:
            group.set_description(config.tooltip)

        checkboxes = []

        if config.options:
            for opt in config.options:
                if isinstance(opt, (tuple, list)) and len(opt) >= 2:
                    value, label = opt[0], opt[1]
                else:
                    value = label = str(opt)

                opt_row = Adw.ActionRow(title=label)
                check = Gtk.CheckButton()
                check._value = value
                check.set_sensitive(interactive)

                if on_change and interactive:
                    check.connect("toggled", lambda *_: on_change())

                opt_row.add_prefix(check)
                opt_row.set_activatable_widget(check)
                group.add(opt_row)
                checkboxes.append(check)
        else:
            empty_row = Adw.ActionRow(title=_("No options defined"))
            group.add(empty_row)

        group._checkboxes = checkboxes
        return group, group

    def _create_file_path_field(
        self, config: FieldConfig, on_change: Optional[Callable], interactive: bool
    ) -> Tuple[Gtk.Widget, Gtk.Widget]:
        """Create a file path chooser field."""
        row = Adw.ActionRow(title=config.label)

        if config.tooltip:
            row.set_subtitle(config.tooltip)

        entry = Gtk.Entry(
            placeholder_text=config.placeholder or _("Select file..."),
            hexpand=True,
            valign=Gtk.Align.CENTER,
        )
        entry.set_editable(interactive)

        if config.default_value:
            entry.set_text(str(config.default_value))

        if on_change and interactive:
            entry.connect("changed", lambda *_: on_change())

        btn = Gtk.Button(
            icon_name="document-open-symbolic",
            valign=Gtk.Align.CENTER,
            css_classes=["flat"],
        )
        btn.set_sensitive(interactive)

        row.add_suffix(entry)
        row.add_suffix(btn)

        # Store button reference for file dialog binding
        entry._browse_button = btn
        return row, entry

    def _create_directory_path_field(
        self, config: FieldConfig, on_change: Optional[Callable], interactive: bool
    ) -> Tuple[Gtk.Widget, Gtk.Widget]:
        """Create a directory path chooser field."""
        row = Adw.ActionRow(title=config.label)

        if config.tooltip:
            row.set_subtitle(config.tooltip)

        entry = Gtk.Entry(
            placeholder_text=config.placeholder or _("Select folder..."),
            hexpand=True,
            valign=Gtk.Align.CENTER,
        )
        entry.set_editable(interactive)

        if config.default_value:
            entry.set_text(str(config.default_value))

        if on_change and interactive:
            entry.connect("changed", lambda *_: on_change())

        btn = Gtk.Button(
            icon_name="folder-open-symbolic",
            valign=Gtk.Align.CENTER,
            css_classes=["flat"],
        )
        btn.set_sensitive(interactive)

        row.add_suffix(entry)
        row.add_suffix(btn)

        entry._browse_button = btn
        return row, entry

    def _create_date_time_field(
        self, config: FieldConfig, on_change: Optional[Callable], interactive: bool
    ) -> Tuple[Gtk.Widget, Gtk.Widget]:
        """Create a date/time entry field."""
        row = Adw.ActionRow(title=config.label)

        date_format = config.extra_config.get("format", "%Y-%m-%d %H:%M")

        if config.tooltip:
            row.set_subtitle(config.tooltip)
        else:
            row.set_subtitle(_("Format: {}").format(date_format))

        from datetime import datetime
        try:
            sample = datetime.now().strftime(date_format)
        except Exception:
            sample = "2025-01-01 12:00"

        entry = Gtk.Entry(
            text=str(config.default_value) if config.default_value else sample,
            hexpand=True,
            valign=Gtk.Align.CENTER,
        )
        entry.set_editable(interactive)
        entry._format = date_format

        if on_change and interactive:
            entry.connect("changed", lambda *_: on_change())

        btn = Gtk.Button(
            icon_name="x-office-calendar-symbolic",
            valign=Gtk.Align.CENTER,
            css_classes=["flat"],
        )
        btn.set_sensitive(interactive)
        get_tooltip_helper().add_tooltip(btn, _("Format: {}").format(date_format))

        row.add_suffix(entry)
        row.add_suffix(btn)

        return row, entry

    def _create_color_field(
        self, config: FieldConfig, on_change: Optional[Callable], interactive: bool
    ) -> Tuple[Gtk.Widget, Gtk.Widget]:
        """Create a color picker field."""
        row = Adw.ActionRow(title=config.label)

        if config.tooltip:
            row.set_subtitle(config.tooltip)

        color_btn = Gtk.ColorButton()
        color_btn.set_valign(Gtk.Align.CENTER)
        color_btn.set_sensitive(interactive)

        default_color = str(config.default_value) if config.default_value else "#000000"
        try:
            rgba = Gdk.RGBA()
            rgba.parse(default_color)
            color_btn.set_rgba(rgba)
        except Exception:
            pass

        # Store format from extra_config
        color_btn._color_format = config.extra_config.get("color_format", "hex")

        if on_change and interactive:
            color_btn.connect("color-set", lambda *_: on_change())

        row.add_suffix(color_btn)
        return row, color_btn

    def _create_command_text_field(
        self, config: FieldConfig, on_change: Optional[Callable], interactive: bool
    ) -> Tuple[Gtk.Widget, Gtk.Widget]:
        """Create a static command text display field (non-editable)."""
        row = Adw.ActionRow(title=config.label)

        if config.tooltip:
            row.set_subtitle(config.tooltip)

        label = Gtk.Label(
            label=str(config.default_value) if config.default_value else "",
            selectable=True,
            wrap=True,
            css_classes=["monospace"],
        )

        row.add_suffix(label)
        return row, label


# Type mapping from FieldType enum to string keys
FIELD_TYPE_MAPPING = {
    "text": "text",
    "password": "password",
    "text_area": "text_area",
    "number": "number",
    "slider": "slider",
    "switch": "switch",
    "dropdown": "dropdown",
    "radio": "radio",
    "multi_select": "multi_select",
    "file_path": "file_path",
    "directory_path": "directory_path",
    "date_time": "date_time",
    "color": "color",
    "command_text": "command_text",
}


def create_field_from_form_field(form_field, on_change: Optional[Callable] = None) -> Tuple[Gtk.Widget, Gtk.Widget]:
    """
    Create a field widget from a CommandFormField object.
    
    Args:
        form_field: A CommandFormField instance from command_manager_models
        on_change: Callback to invoke when value changes
        
    Returns:
        Tuple of (row_widget, value_widget)
    """
    config = FieldConfig(
        field_type=form_field.field_type.value if hasattr(form_field.field_type, 'value') else str(form_field.field_type),
        label=form_field.label,
        field_id=form_field.id,
        tooltip=form_field.tooltip or "",
        placeholder=form_field.placeholder or "",
        default_value=form_field.default_value,
        options=form_field.options or [],
        extra_config=form_field.extra_config or {},
    )

    return FormWidgetBuilder.create_field_widget(config, on_change, interactive=True)


def create_field_from_dict(field_data: Dict, on_change: Optional[Callable] = None, interactive: bool = True) -> Tuple[Gtk.Widget, Gtk.Widget]:
    """
    Create a field widget from a dictionary configuration.
    
    Args:
        field_data: Dictionary with field configuration
        on_change: Callback to invoke when value changes
        interactive: Whether widget should be interactive
        
    Returns:
        Tuple of (row_widget, value_widget)
    """
    config = FieldConfig(
        field_type=field_data.get("type", "text"),
        label=field_data.get("label", "") or field_data.get("id", "Field"),
        field_id=field_data.get("id", ""),
        tooltip=field_data.get("tooltip", ""),
        placeholder=field_data.get("placeholder", ""),
        default_value=field_data.get("default", ""),
        options=field_data.get("options", []),
        extra_config={
            "rows": field_data.get("rows", 4),
            "min_value": field_data.get("min_value", 0),
            "max_value": field_data.get("max_value", 100),
            "step": field_data.get("step", 1),
            "format": field_data.get("format", "%Y-%m-%d %H:%M"),
            "color_format": field_data.get("color_format", "hex"),
        },
    )

    return FormWidgetBuilder.create_field_widget(config, on_change, interactive)
