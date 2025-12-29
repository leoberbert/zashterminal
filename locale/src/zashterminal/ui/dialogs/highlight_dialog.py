# zashterminal/ui/dialogs/highlight_dialog.py
"""
Highlight Colors Dialog for managing syntax highlighting rules.

This dialog provides a GNOME HIG-compliant interface for configuring
regex-based coloring rules that are applied to terminal output text.

Supports:
- Multi-group regex coloring (colors list for capture groups)
- Theme-aware logical color names
- Context-aware highlighting with command-specific rule sets
"""

import re

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject, Gtk

from ...settings.highlights import (
    HighlightContext,
    HighlightRule,
    get_highlight_manager,
)
from ...settings.manager import get_settings_manager
from ...utils.icons import icon_image
from ...utils.logger import get_logger
from ...utils.tooltip_helper import get_tooltip_helper
from ...utils.translation_utils import _
from ..colors import (
    get_background_color_options,
    get_foreground_color_options,
    get_text_effect_options,
)
from ..widgets.regex_text_view import RegexTextView

# Get color options from centralized module
LOGICAL_COLOR_OPTIONS = get_foreground_color_options()
TEXT_EFFECT_OPTIONS = get_text_effect_options()
BACKGROUND_COLOR_OPTIONS = get_background_color_options()


class ColorEntryRow(Adw.ActionRow):
    """
    A row for editing a single color in the colors list.

    Provides:
    - Dropdown to select base foreground color
    - Toggle buttons for text effects (bold, italic, underline, etc.)
    - Dropdown to select background color (optional)
    - Delete button to remove the row

    The color string is composed from base color + active effects + background.
    Example: "bold italic red on_blue"
    """

    __gsignals__ = {
        "color-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "remove-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, group_index: int, color_name: str = "white"):
        """
        Initialize the color entry row.

        Args:
            group_index: The capture group index (1-based for display)
            color_name: Initial logical color name (may include modifiers and bg color)
        """
        super().__init__()
        self._group_index = group_index

        # Parse initial color string into components
        self._fg_color, self._bg_color, self._effects = self._parse_color_string(
            color_name or "white"
        )

        self.set_title(_("Group {}").format(group_index))
        # Subtitle removed to save horizontal space

        self._effect_toggles: dict[str, Gtk.ToggleButton] = {}
        self._setup_ui()
        self._load_color()

    def _parse_color_string(self, color_string: str) -> tuple:
        """
        Parse a color string into foreground color, background color, and effects.

        Args:
            color_string: e.g., "bold italic red on_blue", "green", "underline white"

        Returns:
            Tuple of (foreground_color, background_color, set of effects)
        """
        parts = color_string.lower().split()
        fg_color = "white"
        bg_color = ""
        effects = set()

        # Known effects from TEXT_EFFECT_OPTIONS
        known_effects = {opt[0] for opt in TEXT_EFFECT_OPTIONS}

        for part in parts:
            if part.startswith("on_"):
                bg_color = part
            elif part in known_effects:
                effects.add(part)
            else:
                # It's the base color (last non-effect, non-bg part wins)
                fg_color = part

        return fg_color, bg_color, effects

    def _setup_ui(self) -> None:
        """Setup the row UI components with horizontal toolbar layout."""
        # Main horizontal container for all controls
        main_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        main_box.set_valign(Gtk.Align.CENTER)

        # === Color Preview Box (prefix) ===
        self._color_box = Gtk.Box()
        self._color_box.set_size_request(28, 28)
        self._color_box.set_valign(Gtk.Align.CENTER)
        self._color_box.add_css_class("circular")
        self.add_prefix(self._color_box)

        # === Foreground Color Dropdown ===
        fg_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        fg_label = Gtk.Label(label=_("Color:"))
        fg_label.add_css_class("dim-label")
        fg_box.append(fg_label)

        self._fg_dropdown = Gtk.DropDown()
        self._fg_model = Gtk.StringList()
        for color_id, color_label in LOGICAL_COLOR_OPTIONS:
            self._fg_model.append(color_label)
        self._fg_dropdown.set_model(self._fg_model)
        self._fg_dropdown.connect("notify::selected", self._on_fg_color_selected)
        fg_box.append(self._fg_dropdown)
        main_box.append(fg_box)

        # === Vertical Separator ===
        sep1 = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep1.set_margin_start(4)
        sep1.set_margin_end(4)
        main_box.append(sep1)

        # === Effect Toggle Buttons (linked box) ===
        effects_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        effects_box.add_css_class("linked")

        for effect_id, effect_label, icon_name in TEXT_EFFECT_OPTIONS:
            toggle = Gtk.ToggleButton()
            toggle.set_icon_name(icon_name)
            get_tooltip_helper().add_tooltip(toggle, effect_label)
            toggle.set_valign(Gtk.Align.CENTER)
            toggle.connect("toggled", self._on_effect_toggled, effect_id)
            effects_box.append(toggle)
            self._effect_toggles[effect_id] = toggle

        main_box.append(effects_box)

        # === Vertical Separator ===
        sep2 = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep2.set_margin_start(4)
        sep2.set_margin_end(4)
        main_box.append(sep2)

        # === Background Color Dropdown ===
        bg_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        bg_label = Gtk.Label(label=_("Bg:"))
        bg_label.add_css_class("dim-label")
        bg_box.append(bg_label)

        self._bg_dropdown = Gtk.DropDown()
        self._bg_model = Gtk.StringList()
        for color_id, color_label in BACKGROUND_COLOR_OPTIONS:
            self._bg_model.append(color_label)
        self._bg_dropdown.set_model(self._bg_model)
        self._bg_dropdown.connect("notify::selected", self._on_bg_color_selected)
        bg_box.append(self._bg_dropdown)
        main_box.append(bg_box)

        self.add_suffix(main_box)

        # === Remove Button ===
        remove_btn = Gtk.Button(icon_name="user-trash-symbolic")
        remove_btn.set_valign(Gtk.Align.CENTER)
        remove_btn.add_css_class("flat")
        get_tooltip_helper().add_tooltip(remove_btn, _("Remove"))
        remove_btn.connect("clicked", lambda b: self.emit("remove-requested"))
        self.add_suffix(remove_btn)

    def _load_color(self) -> None:
        """Load the initial colors and effects into the UI controls."""
        # Find and select foreground color
        fg_lower = self._fg_color.lower()
        for idx, (color_id, color_label) in enumerate(LOGICAL_COLOR_OPTIONS):
            if color_id == fg_lower:
                self._fg_dropdown.set_selected(idx)
                break
        else:
            # Default to white if not found
            for idx, (color_id, _label) in enumerate(LOGICAL_COLOR_OPTIONS):
                if color_id == "white":
                    self._fg_dropdown.set_selected(idx)
                    break

        # Find and select background color
        bg_lower = self._bg_color.lower()
        for idx, (color_id, color_label) in enumerate(BACKGROUND_COLOR_OPTIONS):
            if color_id == bg_lower:
                self._bg_dropdown.set_selected(idx)
                break

        # Set effect toggles
        for effect_id, toggle in self._effect_toggles.items():
            toggle.set_active(effect_id in self._effects)

        self._update_color_preview()

    def _on_fg_color_selected(self, dropdown: Gtk.DropDown, _pspec) -> None:
        """Handle foreground color selection change."""
        idx = dropdown.get_selected()
        if idx != Gtk.INVALID_LIST_POSITION and idx < len(LOGICAL_COLOR_OPTIONS):
            self._fg_color = LOGICAL_COLOR_OPTIONS[idx][0]
            self._update_color_preview()
            self.emit("color-changed")

    def _on_bg_color_selected(self, dropdown: Gtk.DropDown, _pspec) -> None:
        """Handle background color selection change."""
        idx = dropdown.get_selected()
        if idx != Gtk.INVALID_LIST_POSITION and idx < len(BACKGROUND_COLOR_OPTIONS):
            self._bg_color = BACKGROUND_COLOR_OPTIONS[idx][0]
            self._update_color_preview()
            self.emit("color-changed")

    def _on_effect_toggled(self, toggle: Gtk.ToggleButton, effect_id: str) -> None:
        """Handle text effect toggle."""
        if toggle.get_active():
            self._effects.add(effect_id)
        else:
            self._effects.discard(effect_id)
        self._update_color_preview()
        self.emit("color-changed")

    def _update_color_preview(self) -> None:
        """Update the color preview box showing foreground, background, and effects.

        The preview shows:
        - Foreground color as the circle fill (center)
        - Background color as the circle border (around)
        """
        manager = get_highlight_manager()

        # Get foreground hex color for display (shown as fill)
        fg_hex = manager.resolve_color(self._fg_color)

        # Get background hex color if set (shown as border)
        bg_hex = None
        if self._bg_color:
            # Strip "on_" prefix to resolve color
            bg_color_name = (
                self._bg_color[3:]
                if self._bg_color.startswith("on_")
                else self._bg_color
            )
            bg_hex = manager.resolve_color(bg_color_name)

        # Build CSS for preview:
        # - Fill (center) = foreground color
        # - Border (around) = background color (or transparent outline if no bg)
        fill_style = f"background-color: {fg_hex};"

        if bg_hex:
            border_style_value = f"4px solid {bg_hex}"
        else:
            # Subtle border when no background is set
            border_style_value = "2px solid alpha(currentColor, 0.3)"

        # Adjust border based on text effects for visual feedback
        border_width = 5 if "bold" in self._effects else 4 if bg_hex else 2
        line_style = "dashed" if "italic" in self._effects else "solid"

        if bg_hex:
            border_style_value = f"{border_width}px {line_style} {bg_hex}"
        else:
            border_style_value = f"2px {line_style} alpha(currentColor, 0.3)"

        css_provider = Gtk.CssProvider()
        css = f"""
        .color-preview {{
            {fill_style}
            border-radius: 50%;
            border: {border_style_value};
        }}
        """
        css_provider.load_from_data(css.encode("utf-8"))

        context = self._color_box.get_style_context()
        if hasattr(self, "_css_provider"):
            context.remove_provider(self._css_provider)

        context.add_provider(css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._color_box.add_css_class("color-preview")
        self._css_provider = css_provider

    @property
    def color_name(self) -> str:
        """
        Get the combined color name (effects + foreground + optional background).

        Returns a string like "bold italic red on_blue" that can be passed
        to resolve_color_to_ansi() for rendering.
        """
        parts = []

        # Add active effects first (in consistent order)
        for effect_id, _label, _icon in TEXT_EFFECT_OPTIONS:
            if effect_id in self._effects:
                parts.append(effect_id)

        # Add foreground color
        parts.append(self._fg_color)

        # Add background color if set
        if self._bg_color:
            parts.append(self._bg_color)

        return " ".join(parts)

    @property
    def group_index(self) -> int:
        """Get the group index."""
        return self._group_index


class RuleEditDialog(Adw.Window):
    """
    Dialog for creating or editing a highlight rule.

    Provides form fields for rule name, regex pattern, and multi-group
    color selection with theme-aware logical color names.

    Uses Adw.Window for full resize/maximize support with size persistence.
    """

    __gsignals__ = {
        "rule-saved": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    # Settings keys for window size persistence
    _SIZE_KEY_WIDTH = "rule_edit_dialog_width"
    _SIZE_KEY_HEIGHT = "rule_edit_dialog_height"
    _DEFAULT_WIDTH = 850
    _DEFAULT_HEIGHT = 600

    def __init__(
        self,
        parent: Gtk.Widget,
        rule: HighlightRule = None,
        is_new: bool = True,
    ):
        """
        Initialize the rule edit dialog.

        Args:
            parent: Parent widget for the dialog.
            rule: Existing rule to edit, or None to create new.
            is_new: Whether this is a new rule or editing existing.
        """
        # Load saved dimensions
        settings = get_settings_manager()
        saved_width = settings.get(self._SIZE_KEY_WIDTH, self._DEFAULT_WIDTH)
        saved_height = settings.get(self._SIZE_KEY_HEIGHT, self._DEFAULT_HEIGHT)

        super().__init__()
        self.add_css_class("zashterminal-dialog")
        self.logger = get_logger("zashterminal.ui.dialogs.rule_edit")
        self._parent = parent
        self._rule = rule or HighlightRule(name="", pattern="", colors=["white"])
        self._is_new = is_new
        self._manager = get_highlight_manager()

        # Color entry rows
        self._color_rows: list[ColorEntryRow] = []

        # Window configuration
        self.set_title(_("New Rule") if is_new else _("Edit Rule"))
        self.set_default_size(saved_width, saved_height)
        self.set_modal(True)

        # Get the actual parent window
        if isinstance(parent, Gtk.Window):
            self.set_transient_for(parent)
        elif hasattr(parent, "get_root"):
            root = parent.get_root()
            if isinstance(root, Gtk.Window):
                self.set_transient_for(root)

        # Connect close event for size persistence
        self.connect("close-request", self._on_close_request)

        self._setup_ui()
        self._load_rule_data()

    def _on_close_request(self, window) -> bool:
        """Save window size when closing."""
        settings = get_settings_manager()
        width = self.get_width()
        height = self.get_height()

        if (
            settings.get(self._SIZE_KEY_WIDTH, 0) != width
            or settings.get(self._SIZE_KEY_HEIGHT, 0) != height
        ):
            settings.set(self._SIZE_KEY_WIDTH, width)
            settings.set(self._SIZE_KEY_HEIGHT, height)

        return False  # Allow default close behavior

    def present(self, parent=None):
        """Present the dialog, optionally setting a parent window."""
        if parent is not None:
            if isinstance(parent, Gtk.Window):
                self.set_transient_for(parent)
            elif hasattr(parent, "get_root"):
                root = parent.get_root()
                if isinstance(root, Gtk.Window):
                    self.set_transient_for(root)
        super().present()

    def _setup_ui(self) -> None:
        """Setup the dialog UI components."""
        # Main container with toolbar view
        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        # Header bar with window controls (minimize, maximize, close)
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(True)  # Show close/maximize buttons
        header.set_show_start_title_buttons(False)

        # Cancel button
        cancel_btn = Gtk.Button(label=_("Cancel"))
        cancel_btn.connect("clicked", lambda b: self.close())
        header.pack_start(cancel_btn)

        # Save button
        self._save_btn = Gtk.Button(label=_("Save"))
        self._save_btn.add_css_class("suggested-action")
        self._save_btn.connect("clicked", self._on_save_clicked)
        header.pack_end(self._save_btn)

        toolbar_view.add_top_bar(header)

        # Scrolled content
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        toolbar_view.set_content(scrolled)

        # Content
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        content_box.set_margin_start(24)
        content_box.set_margin_end(24)
        content_box.set_margin_top(24)
        content_box.set_margin_bottom(24)
        scrolled.set_child(content_box)

        # Name entry
        name_group = Adw.PreferencesGroup()
        self._name_row = Adw.EntryRow(title=_("Rule Name"))
        self._name_row.connect("changed", self._on_input_changed)
        name_group.add(self._name_row)
        content_box.append(name_group)

        # Pattern entry with regex syntax highlighting
        pattern_group = Adw.PreferencesGroup(
            title=_("Pattern"),
            description=_(
                "Python regex syntax. Capture groups () can have individual colors."
            ),
        )

        # Create a custom row for the regex pattern with syntax highlighting
        pattern_action_row = Adw.ActionRow(title=_("Regex Pattern"))
        pattern_action_row.set_subtitle(_("Syntax highlighted"))

        # Create container for the regex text view
        pattern_container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        pattern_container.set_valign(Gtk.Align.CENTER)
        pattern_container.set_hexpand(True)

        # Create the syntax-highlighted regex text view
        self._pattern_text_view = RegexTextView(single_line=True)
        self._pattern_text_view.set_hexpand(True)
        self._pattern_text_view.set_size_request(300, 32)
        self._pattern_text_view.add_css_class("card")
        self._pattern_text_view.connect_changed(self._on_pattern_changed)

        # Frame for the text view to give it a proper border
        pattern_frame = Gtk.Frame()
        pattern_frame.set_child(self._pattern_text_view)
        pattern_frame.set_hexpand(True)
        pattern_frame.add_css_class("view")
        pattern_container.append(pattern_frame)

        # Regex help button
        help_btn = Gtk.Button(icon_name="help-about-symbolic")
        help_btn.add_css_class("flat")
        help_btn.set_valign(Gtk.Align.CENTER)
        get_tooltip_helper().add_tooltip(help_btn, _("Regex reference"))
        help_btn.connect("clicked", self._on_regex_help_clicked)
        pattern_container.append(help_btn)

        pattern_action_row.add_suffix(pattern_container)
        pattern_group.add(pattern_action_row)
        content_box.append(pattern_group)

        # Validation status
        self._validation_label = Gtk.Label()
        self._validation_label.set_xalign(0)
        self._validation_label.add_css_class("dim-label")
        self._validation_label.set_wrap(True)
        content_box.append(self._validation_label)

        # Colors group
        self._colors_group = Adw.PreferencesGroup(
            title=_("Colors & Effects"),
            description=_("First color applies to entire match if no groups."),
        )
        content_box.append(self._colors_group)

        # Add color button
        add_color_row = Adw.ActionRow(title=_("Add Color"))
        add_color_row.set_activatable(True)
        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.set_valign(Gtk.Align.CENTER)
        add_btn.add_css_class("flat")
        add_color_row.add_suffix(add_btn)
        add_color_row.set_activatable_widget(add_btn)
        add_btn.connect("clicked", self._on_add_color_clicked)
        self._colors_group.add(add_color_row)
        self._add_color_row = add_color_row

        # Description entry
        desc_group = Adw.PreferencesGroup()
        self._desc_row = Adw.EntryRow(title=_("Description (optional)"))
        desc_group.add(self._desc_row)
        content_box.append(desc_group)

        # Apply CSS for regex text view styling
        self._apply_regex_textview_css()

    def _apply_regex_textview_css(self) -> None:
        """Regex textview CSS is now loaded globally from components.css.

        The .regex-textview class styles are defined in:
        data/styles/components.css (loaded by window_ui.py at startup)
        """
        pass  # CSS is loaded globally

    def _load_rule_data(self) -> None:
        """Load existing rule data into form fields."""
        self._name_row.set_text(self._rule.name)
        self._pattern_text_view.set_text(self._rule.pattern)
        self._desc_row.set_text(self._rule.description)

        # Load colors
        colors = self._rule.colors or ["white"]
        for idx, color_name in enumerate(colors):
            self._add_color_row_widget(idx + 1, color_name or "white")

        self._validate_input()

    def _add_color_row_widget(self, group_index: int, color_name: str) -> None:
        """Add a color entry row to the colors group."""
        row = ColorEntryRow(group_index, color_name)
        row.connect("color-changed", self._on_color_changed)
        row.connect("remove-requested", self._on_remove_color, row)

        # Insert before the add button
        self._colors_group.remove(self._add_color_row)
        self._colors_group.add(row)
        self._colors_group.add(self._add_color_row)

        self._color_rows.append(row)

    def _on_add_color_clicked(self, button: Gtk.Button) -> None:
        """Handle add color button click."""
        group_index = len(self._color_rows) + 1
        self._add_color_row_widget(group_index, "white")

    def _on_remove_color(self, row: ColorEntryRow, target_row: ColorEntryRow) -> None:
        """Handle remove color button click."""
        if len(self._color_rows) <= 1:
            # Must have at least one color
            return

        self._colors_group.remove(target_row)
        self._color_rows.remove(target_row)

        # Renumber remaining rows
        for idx, row in enumerate(self._color_rows):
            row.set_title(_("Group {}").format(idx + 1))
            row._group_index = idx + 1

    def _on_color_changed(self, row: ColorEntryRow) -> None:
        """Handle color selection change."""
        pass  # Colors are read on save

    def _on_input_changed(self, widget) -> None:
        """Handle input changes to validate form."""
        self._validate_input()

    def _on_pattern_changed(self, widget) -> None:
        """Handle pattern changes - update colors count suggestion."""
        self._validate_input()

        # Suggest number of color rows based on capture groups
        pattern = self._pattern_text_view.get_text().strip()
        if pattern:
            is_valid, _ = self._manager.validate_pattern(pattern)
            if is_valid:
                try:
                    compiled = re.compile(pattern)
                    num_groups = compiled.groups

                    # Update description with group info
                    if num_groups > 0:
                        self._colors_group.set_description(
                            _(
                                "Pattern has {} capture group(s). Add colors for each group."
                            ).format(num_groups)
                        )
                    else:
                        self._colors_group.set_description(
                            _(
                                "Pattern has no capture groups. First color applies to entire match."
                            )
                        )
                except Exception:
                    pass

    def _validate_input(self) -> None:
        """Validate the current input and update UI accordingly."""
        name = self._name_row.get_text().strip()
        pattern = self._pattern_text_view.get_text().strip()

        # Check for required fields
        if not name:
            self._validation_label.set_text(_("Rule name is required"))
            self._validation_label.add_css_class("error")
            self._save_btn.set_sensitive(False)
            return

        if not pattern:
            self._validation_label.set_text(_("Pattern is required"))
            self._validation_label.add_css_class("error")
            self._save_btn.set_sensitive(False)
            return

        # Validate regex pattern
        is_valid, error_msg = self._manager.validate_pattern(pattern)

        if not is_valid:
            self._validation_label.set_text(_("Invalid regex: {}").format(error_msg))
            self._validation_label.add_css_class("error")
            self._validation_label.remove_css_class("success")
            self._save_btn.set_sensitive(False)
        else:
            self._validation_label.set_text(_("✓ Valid pattern"))
            self._validation_label.remove_css_class("error")
            self._validation_label.add_css_class("success")
            self._save_btn.set_sensitive(True)

    def _on_regex_help_clicked(self, button: Gtk.Button) -> None:
        """Show regex reference dialog."""
        dialog = Adw.Dialog()
        dialog.set_title(_("Regex Reference"))
        dialog.set_content_width(600)
        dialog.set_content_height(600)

        toolbar_view = Adw.ToolbarView()
        dialog.set_child(toolbar_view)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(True)
        toolbar_view.add_top_bar(header)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        toolbar_view.set_content(scrolled)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_start(24)
        content.set_margin_end(24)
        content.set_margin_top(24)
        content.set_margin_bottom(24)
        scrolled.set_child(content)

        # Basic patterns
        basic_group = Adw.PreferencesGroup(title=_("Basic Patterns"))
        basic_items = [
            (".", _("Any character except newline")),
            ("\\d", _("Any digit [0-9]")),
            ("\\w", _("Word character [a-zA-Z0-9_]")),
            ("\\s", _("Whitespace character")),
            ("\\D, \\W, \\S", _("Negations of above")),
            ("^", _("Start of line")),
            ("$", _("End of line")),
            ("\\b", _("Word boundary")),
        ]
        for pattern, desc in basic_items:
            row = Adw.ActionRow(
                title=f"<tt>{GLib.markup_escape_text(pattern)}</tt>", subtitle=desc
            )
            row.set_title_lines(1)
            basic_group.add(row)
        content.append(basic_group)

        # Quantifiers
        quant_group = Adw.PreferencesGroup(title=_("Quantifiers"))
        quant_items = [
            ("*", _("0 or more times")),
            ("+", _("1 or more times")),
            ("?", _("0 or 1 time")),
            ("{n}", _("Exactly n times")),
            ("{n,m}", _("Between n and m times")),
            ("*?, +?, ??", _("Non-greedy versions")),
        ]
        for pattern, desc in quant_items:
            row = Adw.ActionRow(
                title=f"<tt>{GLib.markup_escape_text(pattern)}</tt>", subtitle=desc
            )
            row.set_title_lines(1)
            quant_group.add(row)
        content.append(quant_group)

        # Groups and alternatives
        groups_group = Adw.PreferencesGroup(title=_("Groups & Alternatives"))
        groups_items = [
            ("(abc)", _("Capture group (gets a color)")),
            ("(?:abc)", _("Non-capturing group")),
            ("a|b", _("Alternation (a or b)")),
            ("[abc]", _("Character class (a, b, or c)")),
            ("[^abc]", _("Negated class (not a, b, c)")),
            ("[a-z]", _("Character range")),
        ]
        for pattern, desc in groups_items:
            row = Adw.ActionRow(
                title=f"<tt>{GLib.markup_escape_text(pattern)}</tt>", subtitle=desc
            )
            row.set_title_lines(1)
            groups_group.add(row)
        content.append(groups_group)

        # Examples
        examples_group = Adw.PreferencesGroup(title=_("Examples"))
        examples_items = [
            ("\\b\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\b", _("IPv4 address")),
            ("error|fail|fatal", _("Match error keywords")),
            ("(\\w+)=(\\w+)", _("Key=value pairs (2 groups)")),
            ("^\\s*#.*$", _("Comment lines")),
        ]
        for pattern, desc in examples_items:
            row = Adw.ActionRow(
                title=f"<tt>{GLib.markup_escape_text(pattern)}</tt>", subtitle=desc
            )
            row.set_title_lines(1)
            row.set_subtitle_lines(1)
            examples_group.add(row)
        content.append(examples_group)

        dialog.present(self)

    def _on_save_clicked(self, button: Gtk.Button) -> None:
        """Handle save button click."""
        name = self._name_row.get_text().strip()
        pattern = self._pattern_text_view.get_text().strip()
        description = self._desc_row.get_text().strip()

        # Collect colors from rows
        colors = [row.color_name for row in self._color_rows]
        if not colors:
            colors = ["white"]

        rule = HighlightRule(
            name=name,
            pattern=pattern,
            colors=colors,
            enabled=self._rule.enabled if not self._is_new else True,
            description=description,
        )

        self.emit("rule-saved", rule)
        self.close()


class ContextRulesDialog(Adw.Window):
    """
    Dialog for editing rules of a specific command context.

    Opens when user clicks on a context row, providing a focused
    interface for managing context-specific highlighting rules.

    Uses Adw.Window for full resize/maximize support with size persistence.
    """

    __gsignals__ = {
        "context-updated": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    # Settings keys for window size persistence
    _SIZE_KEY_WIDTH = "context_rules_dialog_width"
    _SIZE_KEY_HEIGHT = "context_rules_dialog_height"
    _DEFAULT_WIDTH = 850
    _DEFAULT_HEIGHT = 600

    def __init__(self, parent: Gtk.Widget, context_name: str):
        """
        Initialize the context rules dialog.

        Args:
            parent: Parent widget for the dialog.
            context_name: Name of the context to edit.
        """
        # Load saved dimensions
        settings = get_settings_manager()
        saved_width = settings.get(self._SIZE_KEY_WIDTH, self._DEFAULT_WIDTH)
        saved_height = settings.get(self._SIZE_KEY_HEIGHT, self._DEFAULT_HEIGHT)

        super().__init__()
        self.add_css_class("zashterminal-dialog")
        self.logger = get_logger("zashterminal.ui.dialogs.context_rules")
        self._parent = parent
        self._context_name = context_name
        self._manager = get_highlight_manager()
        self._context_rule_rows: list[Adw.ActionRow] = []

        # Window configuration
        self.set_title(_("Command: {}").format(context_name))
        self.set_default_size(saved_width, saved_height)
        self.set_modal(True)

        # Get the actual parent window
        if isinstance(parent, Gtk.Window):
            self.set_transient_for(parent)
        elif hasattr(parent, "get_root"):
            root = parent.get_root()
            if isinstance(root, Gtk.Window):
                self.set_transient_for(root)

        # Connect close event for size persistence
        self.connect("close-request", self._on_close_request)

        self._setup_ui()
        self._load_context_data()

    def _on_close_request(self, window) -> bool:
        """Save window size when closing."""
        settings = get_settings_manager()
        width = self.get_width()
        height = self.get_height()

        if (
            settings.get(self._SIZE_KEY_WIDTH, 0) != width
            or settings.get(self._SIZE_KEY_HEIGHT, 0) != height
        ):
            settings.set(self._SIZE_KEY_WIDTH, width)
            settings.set(self._SIZE_KEY_HEIGHT, height)

        return False  # Allow default close behavior

    def present(self, parent=None):
        """Present the dialog, optionally setting a parent window."""
        if parent is not None:
            if isinstance(parent, Gtk.Window):
                self.set_transient_for(parent)
            elif hasattr(parent, "get_root"):
                root = parent.get_root()
                if isinstance(root, Gtk.Window):
                    self.set_transient_for(root)
        super().present()

    def _setup_ui(self) -> None:
        """Setup the dialog UI components."""
        # Main container with toolbar view
        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        # Header bar with window controls (close, maximize)
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(True)
        header.set_show_start_title_buttons(False)

        toolbar_view.add_top_bar(header)

        # Scrolled content
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        toolbar_view.set_content(scrolled)

        # Main content box
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        scrolled.set_child(content_box)

        # Preferences page for consistent styling
        self._prefs_page = Adw.PreferencesPage()
        content_box.append(self._prefs_page)

        # Context settings group
        settings_group = Adw.PreferencesGroup()
        self._prefs_page.add(settings_group)

        # Enable toggle
        self._enable_row = Adw.SwitchRow(
            title=_("Enable Command Rules"),
        )
        self._enable_row.connect("notify::active", self._on_enable_toggled)
        settings_group.add(self._enable_row)

        # Use global rules toggle
        self._use_global_row = Adw.SwitchRow(
            title=_("Include Global Rules"),
        )
        self._use_global_row.connect("notify::active", self._on_use_global_toggled)
        settings_group.add(self._use_global_row)

        # Reset button
        reset_row = Adw.ActionRow(
            title=_("Reset to System Default"),
        )
        reset_row.set_activatable(True)
        reset_btn = Gtk.Button(icon_name="edit-undo-symbolic")
        reset_btn.set_valign(Gtk.Align.CENTER)
        reset_btn.add_css_class("flat")
        reset_row.add_suffix(reset_btn)
        reset_row.set_activatable_widget(reset_btn)
        reset_btn.connect("clicked", self._on_reset_clicked)
        settings_group.add(reset_row)

        # Triggers group
        self._triggers_group = Adw.PreferencesGroup(
            title=_("Triggers"),
            description=_(
                "Command names or patterns that activate this rule set. "
                "When a command starting with a trigger is detected, these highlighting rules are applied."
            ),
        )
        self._prefs_page.add(self._triggers_group)

        # Add trigger button
        add_trigger_row = Adw.ActionRow(
            title=_("➕ Add Trigger"),
        )
        add_trigger_row.set_activatable(True)
        add_trigger_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_trigger_btn.set_valign(Gtk.Align.CENTER)
        add_trigger_btn.add_css_class("suggested-action")
        add_trigger_row.add_suffix(add_trigger_btn)
        add_trigger_row.set_activatable_widget(add_trigger_btn)
        add_trigger_btn.connect("clicked", self._on_add_trigger_clicked)
        self._triggers_group.add(add_trigger_row)
        self._add_trigger_row = add_trigger_row

        # Container for trigger rows
        self._trigger_rows: list[Adw.ActionRow] = []

        # Rules group
        self._rules_group = Adw.PreferencesGroup(
            title=_("Highlight Rules"),
        )
        self._prefs_page.add(self._rules_group)

        # Add rule button - first and prominent
        add_row = Adw.ActionRow(
            title=_("➕ Add New Rule"),
        )
        add_row.set_activatable(True)
        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.set_valign(Gtk.Align.CENTER)
        add_btn.add_css_class("suggested-action")
        add_row.add_suffix(add_btn)
        add_row.set_activatable_widget(add_btn)
        add_btn.connect("clicked", self._on_add_rule_clicked)
        self._rules_group.add(add_row)

    def _load_context_data(self) -> None:
        """Load context data into the form."""
        context = self._manager.get_context(self._context_name)
        if not context:
            return

        # Block signal handlers during load
        self._enable_row.handler_block_by_func(self._on_enable_toggled)
        self._enable_row.set_active(context.enabled)
        self._enable_row.handler_unblock_by_func(self._on_enable_toggled)

        self._use_global_row.handler_block_by_func(self._on_use_global_toggled)
        self._use_global_row.set_active(context.use_global_rules)
        self._use_global_row.handler_unblock_by_func(self._on_use_global_toggled)

        # Populate triggers
        self._populate_triggers()

        # Populate rules
        self._populate_rules()

    def _populate_triggers(self) -> None:
        """Populate the triggers list."""
        # Clear existing trigger rows
        for row in self._trigger_rows:
            self._triggers_group.remove(row)
        self._trigger_rows.clear()

        context = self._manager.get_context(self._context_name)
        if not context:
            return

        # Add trigger rows
        for trigger in context.triggers:
            row = self._create_trigger_row(trigger)
            self._triggers_group.add(row)
            self._trigger_rows.append(row)

    def _create_trigger_row(self, trigger: str) -> Adw.ActionRow:
        """Create an action row for a trigger with edit and delete buttons."""
        row = Adw.ActionRow(title=trigger)

        # Terminal icon prefix (uses bundled icon)
        icon = icon_image("utilities-terminal-symbolic")
        icon.set_opacity(0.6)
        row.add_prefix(icon)

        # Edit button - always visible
        edit_btn = Gtk.Button(icon_name="document-edit-symbolic")
        edit_btn.add_css_class("flat")
        edit_btn.set_valign(Gtk.Align.CENTER)
        get_tooltip_helper().add_tooltip(edit_btn, _("Edit trigger"))
        edit_btn.connect("clicked", self._on_edit_trigger_clicked, trigger)
        row.add_suffix(edit_btn)

        # Delete button - always visible but disabled if only one trigger
        delete_btn = Gtk.Button(icon_name="user-trash-symbolic")
        delete_btn.add_css_class("flat")
        delete_btn.set_valign(Gtk.Align.CENTER)
        get_tooltip_helper().add_tooltip(delete_btn, _("Remove trigger"))
        delete_btn.connect("clicked", self._on_delete_trigger_clicked, trigger)

        context = self._manager.get_context(self._context_name)
        if context and len(context.triggers) <= 1:
            delete_btn.set_sensitive(False)
            get_tooltip_helper().add_tooltip(
                delete_btn, _("Cannot remove the last trigger")
            )

        row.add_suffix(delete_btn)

        return row

    def _on_add_trigger_clicked(self, button: Gtk.Button) -> None:
        """Handle add trigger button click."""
        dialog = AddTriggerDialog(self, self._context_name)
        dialog.connect("trigger-added", self._on_trigger_added)
        dialog.present(self)

    def _on_trigger_added(self, dialog, trigger: str) -> None:
        """Handle new trigger added."""
        context = self._manager.get_context(self._context_name)
        if context and trigger not in context.triggers:
            context.triggers.append(trigger)
            self._manager.save_context_to_user(context)
            self._populate_triggers()
            self.emit("context-updated")

    def _on_edit_trigger_clicked(self, button: Gtk.Button, old_trigger: str) -> None:
        """Handle edit trigger button click."""
        dialog = AddTriggerDialog(self, self._context_name, old_trigger)
        dialog.connect("trigger-added", self._on_trigger_edited, old_trigger)
        dialog.present(self)

    def _on_trigger_edited(self, dialog, new_trigger: str, old_trigger: str) -> None:
        """Handle trigger edit."""
        context = self._manager.get_context(self._context_name)
        if context:
            # Replace old trigger with new
            if old_trigger in context.triggers:
                idx = context.triggers.index(old_trigger)
                context.triggers[idx] = new_trigger
            self._manager.save_context_to_user(context)
            self._populate_triggers()
            self.emit("context-updated")

    def _on_delete_trigger_clicked(self, button: Gtk.Button, trigger: str) -> None:
        """Handle delete trigger button click."""
        context = self._manager.get_context(self._context_name)
        if not context or len(context.triggers) <= 1:
            return

        dialog = Adw.AlertDialog(
            heading=_("Remove Trigger?"),
            body=_('Remove "{}" from the triggers list?').format(trigger),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("remove", _("Remove"))
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_delete_trigger_confirmed, trigger)
        dialog.present(self)

    def _on_delete_trigger_confirmed(
        self, dialog: Adw.AlertDialog, response: str, trigger: str
    ) -> None:
        """Handle delete trigger confirmation."""
        if response == "remove":
            context = self._manager.get_context(self._context_name)
            if context and trigger in context.triggers:
                context.triggers.remove(trigger)
                self._manager.save_context_to_user(context)
                self._populate_triggers()
                self.emit("context-updated")

    def _populate_rules(self) -> None:
        """Populate the rules list."""
        # Clear existing rule rows
        for row in self._context_rule_rows:
            self._rules_group.remove(row)
        self._context_rule_rows.clear()

        context = self._manager.get_context(self._context_name)
        if not context:
            return

        # Add rule rows
        for index, rule in enumerate(context.rules):
            row = self._create_rule_row(rule, index, len(context.rules))
            self._rules_group.add(row)
            self._context_rule_rows.append(row)

    def _create_rule_row(
        self, rule: HighlightRule, index: int, total: int
    ) -> Adw.ActionRow:
        """Create an action row for a rule with inline reorder, edit, delete, switch."""
        escaped_name = GLib.markup_escape_text(rule.name)
        subtitle_text = (
            rule.description
            if rule.description
            else (rule.pattern[:40] + "..." if len(rule.pattern) > 40 else rule.pattern)
        )
        escaped_subtitle = GLib.markup_escape_text(subtitle_text)

        row = Adw.ActionRow()
        row.set_title(f"#{index + 1} {escaped_name}")
        row.set_subtitle(escaped_subtitle)

        # Reorder buttons prefix
        reorder_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        reorder_box.set_valign(Gtk.Align.CENTER)
        reorder_box.set_margin_end(4)

        up_btn = Gtk.Button(icon_name="go-up-symbolic")
        up_btn.add_css_class("flat")
        up_btn.add_css_class("circular")
        up_btn.set_size_request(24, 24)
        up_btn.set_sensitive(index > 0)
        up_btn.connect("clicked", self._on_move_rule_up, index)
        get_tooltip_helper().add_tooltip(up_btn, _("Move up"))
        reorder_box.append(up_btn)

        down_btn = Gtk.Button(icon_name="go-down-symbolic")
        down_btn.add_css_class("flat")
        down_btn.add_css_class("circular")
        down_btn.set_size_request(24, 24)
        down_btn.set_sensitive(index < total - 1)
        down_btn.connect("clicked", self._on_move_rule_down, index)
        get_tooltip_helper().add_tooltip(down_btn, _("Move down"))
        reorder_box.append(down_btn)

        row.add_prefix(reorder_box)

        # Color indicator
        color_box = Gtk.Box()
        color_box.set_size_request(16, 16)
        color_box.add_css_class("circular")
        if rule.colors and rule.colors[0]:
            hex_color = self._manager.resolve_color(rule.colors[0])
            self._apply_color_to_box(color_box, hex_color)
        color_box.set_margin_end(8)
        color_box.set_valign(Gtk.Align.CENTER)
        row.add_prefix(color_box)

        # Edit button (icon)
        edit_btn = Gtk.Button(icon_name="document-edit-symbolic")
        edit_btn.add_css_class("flat")
        edit_btn.set_valign(Gtk.Align.CENTER)
        get_tooltip_helper().add_tooltip(edit_btn, _("Edit rule"))
        edit_btn.connect("clicked", self._on_edit_rule, index)
        row.add_suffix(edit_btn)

        # Delete button (icon)
        delete_btn = Gtk.Button(icon_name="user-trash-symbolic")
        delete_btn.add_css_class("flat")
        delete_btn.set_valign(Gtk.Align.CENTER)
        get_tooltip_helper().add_tooltip(delete_btn, _("Delete rule"))
        delete_btn.connect("clicked", self._on_delete_rule, index)
        row.add_suffix(delete_btn)

        # Enable switch (rightmost)
        switch = Gtk.Switch()
        switch.set_active(rule.enabled)
        switch.set_valign(Gtk.Align.CENTER)
        switch.connect("notify::active", self._on_rule_toggle, index)
        row.add_suffix(switch)

        return row

    def _apply_color_to_box(self, box: Gtk.Box, hex_color: str) -> None:
        """Apply a color as background to a box widget."""
        css_provider = Gtk.CssProvider()
        css = f"""
        .rule-color-indicator {{
            background-color: {hex_color};
            border-radius: 50%;
            border: 1px solid alpha(currentColor, 0.3);
        }}
        """
        css_provider.load_from_data(css.encode("utf-8"))

        context = box.get_style_context()
        if hasattr(box, "_css_provider"):
            context.remove_provider(box._css_provider)

        context.add_provider(css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        box.add_css_class("rule-color-indicator")
        box._css_provider = css_provider

    def _on_enable_toggled(self, switch: Adw.SwitchRow, _pspec) -> None:
        """Handle enable toggle."""
        self._manager.set_context_enabled(self._context_name, switch.get_active())
        self._manager.save_config()
        self.emit("context-updated")

    def _on_use_global_toggled(self, switch: Adw.SwitchRow, _pspec) -> None:
        """Handle use global rules toggle."""
        self._manager.set_context_use_global_rules(
            self._context_name, switch.get_active()
        )
        self._manager.save_config()
        self.emit("context-updated")

    def _on_reset_clicked(self, button: Gtk.Button) -> None:
        """Handle reset button click."""
        dialog = Adw.AlertDialog(
            heading=_("Reset to System Default?"),
            body=_(
                'This will remove your customizations for "{}" and revert to system rules.'
            ).format(self._context_name),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("reset", _("Reset"))
        dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_reset_confirmed)
        dialog.present(self)

    def _on_reset_confirmed(self, dialog: Adw.AlertDialog, response: str) -> None:
        """Handle reset confirmation."""
        if response == "reset":
            if self._manager.delete_user_context(self._context_name):
                self._load_context_data()
                self.emit("context-updated")

    def _on_add_rule_clicked(self, button: Gtk.Button) -> None:
        """Handle add rule button click."""
        dialog = RuleEditDialog(self, is_new=True)
        dialog.connect("rule-saved", self._on_rule_saved)
        dialog.present(self)

    def _on_rule_saved(self, dialog: RuleEditDialog, rule: HighlightRule) -> None:
        """Handle new rule saved."""
        self._manager.add_rule_to_context(self._context_name, rule)
        context = self._manager.get_context(self._context_name)
        if context:
            self._manager.save_context_to_user(context)
        self._populate_rules()
        self.emit("context-updated")

    def _on_rule_toggle(self, switch: Gtk.Switch, _pspec, index: int) -> None:
        """Handle rule enable/disable toggle."""
        self._manager.set_context_rule_enabled(
            self._context_name, index, switch.get_active()
        )
        # Save context to user directory to persist rule enabled state
        context = self._manager.get_context(self._context_name)
        if context:
            self._manager.save_context_to_user(context)
        self.emit("context-updated")

    def _on_move_rule_up(self, button: Gtk.Button, index: int) -> None:
        """Move a rule up."""
        if index <= 0:
            return
        self._manager.move_context_rule(self._context_name, index, index - 1)
        # Save context to user directory to persist rule order
        context = self._manager.get_context(self._context_name)
        if context:
            self._manager.save_context_to_user(context)
        self._populate_rules()
        self.emit("context-updated")

    def _on_move_rule_down(self, button: Gtk.Button, index: int) -> None:
        """Move a rule down."""
        ctx = self._manager.get_context(self._context_name)
        if not ctx or index >= len(ctx.rules) - 1:
            return
        self._manager.move_context_rule(self._context_name, index, index + 1)
        # Save context to user directory to persist rule order
        context = self._manager.get_context(self._context_name)
        if context:
            self._manager.save_context_to_user(context)
        self._populate_rules()
        self.emit("context-updated")

    def _on_edit_rule(self, button: Gtk.Button, index: int) -> None:
        """Handle edit rule button click."""
        context = self._manager.get_context(self._context_name)
        if context and 0 <= index < len(context.rules):
            rule = context.rules[index]
            dialog = RuleEditDialog(self, rule=rule, is_new=False)
            dialog.connect("rule-saved", self._on_rule_edited, index)
            dialog.present(self)

    def _on_rule_edited(
        self, dialog: RuleEditDialog, rule: HighlightRule, index: int
    ) -> None:
        """Handle rule edit saved."""
        self._manager.update_context_rule(self._context_name, index, rule)
        context = self._manager.get_context(self._context_name)
        if context:
            self._manager.save_context_to_user(context)
        self._populate_rules()
        self.emit("context-updated")

    def _on_delete_rule(self, button: Gtk.Button, index: int) -> None:
        """Handle delete rule button click."""
        context = self._manager.get_context(self._context_name)
        if not context or index >= len(context.rules):
            return

        rule = context.rules[index]
        dialog = Adw.AlertDialog(
            heading=_("Delete Rule?"),
            body=_('Are you sure you want to delete "{}"?').format(rule.name),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_delete_confirmed, index, rule.name)
        dialog.present(self)

    def _on_delete_confirmed(
        self, dialog: Adw.AlertDialog, response: str, index: int, rule_name: str
    ) -> None:
        """Handle delete confirmation."""
        if response == "delete":
            self._manager.remove_context_rule(self._context_name, index)
            context = self._manager.get_context(self._context_name)
            if context:
                self._manager.save_context_to_user(context)
            self._populate_rules()
            self.emit("context-updated")


class HighlightDialog(Adw.PreferencesWindow):
    """
    Main dialog for managing syntax highlighting settings.

    Provides controls for global activation settings, a list of
    customizable highlight rules, and context-aware highlighting
    with command-specific rule sets.

    Window size is persisted between sessions.
    """

    __gsignals__ = {
        "settings-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    # Settings keys for window size persistence
    _SIZE_KEY_WIDTH = "highlight_dialog_width"
    _SIZE_KEY_HEIGHT = "highlight_dialog_height"
    _DEFAULT_WIDTH = 900
    _DEFAULT_HEIGHT = 700

    def __init__(self, parent_window: Gtk.Window):
        """
        Initialize the highlight dialog.

        Args:
            parent_window: Parent window for the dialog.
        """
        # Load saved dimensions
        settings = get_settings_manager()
        saved_width = settings.get(self._SIZE_KEY_WIDTH, self._DEFAULT_WIDTH)
        saved_height = settings.get(self._SIZE_KEY_HEIGHT, self._DEFAULT_HEIGHT)

        super().__init__(
            title=_("Highlight Colors"),
            transient_for=parent_window,
            modal=False,
            hide_on_close=True,
            default_width=saved_width,
            default_height=saved_height,
            search_enabled=True,
        )
        self.add_css_class("zashterminal-dialog")
        self.logger = get_logger("zashterminal.ui.dialogs.highlight")
        self._parent_window = parent_window
        self._manager = get_highlight_manager()
        self._rule_rows: list[Adw.ExpanderRow] = []
        self._context_rule_rows: list[Adw.ExpanderRow] = []
        self._selected_context: str = ""

        # Connect to window state events for size persistence
        self.connect("close-request", self._on_close_request)

        self._setup_ui()
        self._load_settings()

        self.logger.info("HighlightDialog initialized")

    def _on_close_request(self, window) -> bool:
        """Save window size when closing."""
        self._save_window_size()
        return False  # Allow default close behavior

    def _save_window_size(self) -> None:
        """Save the current window size to settings."""
        settings = get_settings_manager()
        width = self.get_width()
        height = self.get_height()

        # Only save if different from current saved values
        if (
            settings.get(self._SIZE_KEY_WIDTH, 0) != width
            or settings.get(self._SIZE_KEY_HEIGHT, 0) != height
        ):
            settings.set(self._SIZE_KEY_WIDTH, width)
            settings.set(self._SIZE_KEY_HEIGHT, height)
            self.logger.debug(f"Saved highlight dialog size: {width}x{height}")

    def _setup_ui(self) -> None:
        """Setup the dialog UI components."""
        # Create first page for Terminal Colors (primary, fundamental settings)
        self._terminal_colors_page = Adw.PreferencesPage(
            title=_("Terminal Colors"),
            icon_name="preferences-color-symbolic",
        )
        self.add(self._terminal_colors_page)

        # Color Scheme group - the most important terminal color setting
        self._setup_color_scheme_page(self._terminal_colors_page)

        # Create second page for Output Highlighting (Global Rules)
        self._global_page = Adw.PreferencesPage(
            title=_("Output Highlighting"),
            icon_name="view-list-symbolic",
        )
        self.add(self._global_page)

        # Welcome/explanation text
        self._setup_welcome_banner(self._global_page)

        # Activation group with performance warning
        self._setup_activation_group(self._global_page)

        # Cat colorization group (Pygments-based syntax highlighting)
        self._setup_cat_colorization_group(self._global_page)

        # Shell input highlighting group (experimental)
        self._setup_shell_input_highlighting_group(self._global_page)

        # Ignored commands group (collapsible - placed before Global Rules as it's compact)
        self._setup_ignored_commands_group(self._global_page)

        # Global rules group (last, as it can be a longer list)
        self._setup_rules_group(self._global_page)

        # Apply initial sensitivity state for dependent groups
        self._update_dependent_groups_sensitivity()

        # Create third page for Command-Specific Rules
        self._context_page = Adw.PreferencesPage(
            title=_("Command-Specific"),
            icon_name="utilities-terminal-symbolic",
        )
        self.add(self._context_page)

        # Context settings group
        self._setup_context_settings_group(self._context_page)

        # Context selector group (clicking a context opens a dialog)
        self._setup_context_selector_group(self._context_page)

    def _setup_color_scheme_page(self, page: Adw.PreferencesPage) -> None:
        """Setup the Terminal Colors page with integrated Color Scheme selector."""
        # Color Scheme group
        scheme_group = Adw.PreferencesGroup(
            title=_("Color Scheme"),
        )
        page.add(scheme_group)

        # Create the scheme list
        self._scheme_listbox = Gtk.ListBox()
        self._scheme_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._scheme_listbox.add_css_class("boxed-list")
        self._scheme_listbox.connect("row-selected", self._on_scheme_row_selected)

        # Populate schemes
        self._scheme_rows: dict = {}
        self._populate_color_schemes()

        scheme_group.add(self._scheme_listbox)

        # Actions group (New, Edit, Delete)
        actions_group = Adw.PreferencesGroup()
        page.add(actions_group)

        # New scheme button
        new_row = Adw.ActionRow(
            title=_("Create New Scheme"),
            subtitle=_("Create a custom color scheme based on existing"),
        )
        new_row.set_activatable(True)
        new_btn = Gtk.Button(icon_name="list-add-symbolic")
        new_btn.set_valign(Gtk.Align.CENTER)
        new_btn.add_css_class("flat")
        new_btn.connect("clicked", self._on_new_scheme_clicked)
        new_row.add_suffix(new_btn)
        new_row.set_activatable_widget(new_btn)
        actions_group.add(new_row)

    def _populate_color_schemes(self) -> None:
        """Populate the color scheme list."""
        settings = get_settings_manager()
        all_schemes = settings.get_all_schemes()
        scheme_order = settings.get_scheme_order()
        current_scheme = settings.get_color_scheme_name()

        # Clear existing rows
        while True:
            row = self._scheme_listbox.get_first_child()
            if row is None:
                break
            self._scheme_listbox.remove(row)
        self._scheme_rows.clear()

        for scheme_key in scheme_order:
            if scheme_key not in all_schemes:
                continue

            scheme_data = all_schemes[scheme_key]
            is_custom = scheme_key.startswith("custom_")

            row = self._create_scheme_row(scheme_key, scheme_data, is_custom)
            self._scheme_listbox.append(row)
            self._scheme_rows[scheme_key] = row

            # Select current scheme
            if scheme_key == current_scheme:
                self._scheme_listbox.select_row(row)

    def _create_scheme_row(
        self, scheme_key: str, scheme_data: dict, is_custom: bool
    ) -> Adw.ActionRow:
        """Create a row for a color scheme with preview."""
        row = Adw.ActionRow(
            title=scheme_data.get("name", scheme_key),
        )
        row.scheme_key = scheme_key
        row.scheme_data = scheme_data
        row.is_custom = is_custom

        # Color preview using DrawingArea for better visual representation
        preview = Gtk.DrawingArea()
        preview.set_size_request(120, 32)
        preview.set_valign(Gtk.Align.CENTER)
        preview.set_margin_end(12)

        def draw_preview(area, cr, width, height):
            # Draw background
            bg_color = scheme_data.get("background", "#000000")
            self._set_color_from_hex(cr, bg_color)
            cr.rectangle(0, 0, width * 0.3, height)
            cr.fill()

            # Draw foreground
            fg_color = scheme_data.get("foreground", "#ffffff")
            self._set_color_from_hex(cr, fg_color)
            cr.rectangle(width * 0.3, 0, width * 0.15, height)
            cr.fill()

            # Draw palette colors
            palette = scheme_data.get("palette", [])
            num_colors = min(len(palette), 8)
            if num_colors > 0:
                color_width = (width * 0.55) / num_colors
                x_offset = width * 0.45
                for i, color in enumerate(palette[:num_colors]):
                    self._set_color_from_hex(cr, color)
                    cr.rectangle(x_offset + i * color_width, 0, color_width, height)
                    cr.fill()

            # Draw subtle border
            cr.set_source_rgba(0.5, 0.5, 0.5, 0.3)
            cr.set_line_width(1)
            cr.rectangle(0.5, 0.5, width - 1, height - 1)
            cr.stroke()

        preview.set_draw_func(draw_preview)
        row.add_prefix(preview)

        # Edit button - available for ALL schemes (built-in creates a copy)
        edit_btn = Gtk.Button(icon_name="document-edit-symbolic")
        edit_btn.set_valign(Gtk.Align.CENTER)
        edit_btn.add_css_class("flat")
        if is_custom:
            edit_btn.set_tooltip_text(_("Edit scheme"))
        else:
            edit_btn.set_tooltip_text(_("Customize (creates a copy)"))
        edit_btn.connect("clicked", lambda b, r=row: self._on_edit_scheme_clicked(r))
        row.add_suffix(edit_btn)

        # Delete button only for custom schemes
        if is_custom:
            delete_btn = Gtk.Button(icon_name="user-trash-symbolic")
            delete_btn.set_valign(Gtk.Align.CENTER)
            delete_btn.add_css_class("flat")
            delete_btn.set_tooltip_text(_("Delete scheme"))
            delete_btn.connect(
                "clicked", lambda b, r=row: self._on_delete_scheme_clicked(r)
            )
            row.add_suffix(delete_btn)

        # Checkmark for selected scheme
        check_icon = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
        check_icon.set_visible(False)
        row.check_icon = check_icon
        row.add_suffix(check_icon)

        return row

    def _set_color_from_hex(self, cr, hex_color: str) -> None:
        """Set cairo source color from hex string."""
        try:
            hex_val = hex_color.lstrip("#")
            r = int(hex_val[0:2], 16) / 255.0
            g = int(hex_val[2:4], 16) / 255.0
            b = int(hex_val[4:6], 16) / 255.0
            cr.set_source_rgb(r, g, b)
        except (ValueError, IndexError):
            cr.set_source_rgb(0.5, 0.5, 0.5)

    def _on_scheme_row_selected(self, listbox, row) -> None:
        """Handle color scheme selection."""
        if row is None:
            return

        # Update visual selection (checkmarks)
        for scheme_row in self._scheme_rows.values():
            scheme_row.check_icon.set_visible(scheme_row == row)

        # Apply the scheme
        settings = get_settings_manager()
        scheme_order = settings.get_scheme_order()
        selected_index = scheme_order.index(row.scheme_key)
        settings.set("color_scheme", selected_index)

        self.logger.info(f"Color scheme changed to: {row.scheme_key}")

        # Apply to terminals
        if self._parent_window and hasattr(self._parent_window, "terminal_manager"):
            self._parent_window.terminal_manager.apply_settings_to_all_terminals()

        # Apply to GTK theme if using terminal theme
        if settings.get("gtk_theme") == "terminal" and self._parent_window:
            settings.apply_gtk_terminal_theme(self._parent_window)

        # Refresh shell input highlighter
        try:
            from ...terminal.highlighter import get_shell_input_highlighter

            highlighter = get_shell_input_highlighter()
            highlighter.refresh_settings()
        except Exception:
            pass

    def _on_new_scheme_clicked(self, button) -> None:
        """Create a new color scheme based on selected."""
        from ..color_scheme_dialog import _SchemeEditorDialog

        settings = get_settings_manager()
        selected_row = self._scheme_listbox.get_selected_row()
        template_scheme = (
            selected_row.scheme_data
            if selected_row
            else settings.get_all_schemes()["dark"]
        )

        all_names = [s["name"] for s in settings.get_all_schemes().values()]

        def generate_unique_name(base_name: str, existing: set) -> str:
            if base_name not in existing:
                return base_name
            counter = 1
            while f"{base_name} ({counter})" in existing:
                counter += 1
            return f"{base_name} ({counter})"

        new_name = generate_unique_name(
            f"Copy of {template_scheme['name']}", set(all_names)
        )

        new_scheme_data = template_scheme.copy()
        new_scheme_data["name"] = new_name

        editor = _SchemeEditorDialog(
            self, settings, new_name, new_scheme_data, is_new=True
        )
        editor.connect("save-requested", self._on_editor_save)
        editor.present()

    def _on_edit_scheme_clicked(self, row) -> None:
        """Edit a color scheme. Built-in schemes create a copy when saved."""
        from ..color_scheme_dialog import _SchemeEditorDialog

        settings = get_settings_manager()

        # For built-in schemes, we'll create a new scheme (is_new=True)
        # For custom schemes, we edit in place (is_new=False)
        is_builtin = not row.is_custom

        if is_builtin:
            # Generate unique name for the copy
            all_names = [s["name"] for s in settings.get_all_schemes().values()]

            def generate_unique_name(base_name: str, existing: set) -> str:
                if base_name not in existing:
                    return base_name
                counter = 1
                while f"{base_name} ({counter})" in existing:
                    counter += 1
                return f"{base_name} ({counter})"

            new_name = generate_unique_name(
                f"{row.scheme_data.get('name', row.scheme_key)} (Custom)",
                set(all_names),
            )
            scheme_data = row.scheme_data.copy()
            scheme_data["name"] = new_name

            editor = _SchemeEditorDialog(self, settings, None, scheme_data, is_new=True)
        else:
            editor = _SchemeEditorDialog(
                self, settings, row.scheme_key, row.scheme_data.copy(), is_new=False
            )

        editor.connect("save-requested", self._on_editor_save)
        editor.present()

    def _on_delete_scheme_clicked(self, row) -> None:
        """Delete a custom color scheme."""
        dialog = Adw.AlertDialog(
            heading=_("Delete Scheme?"),
            body=_("Are you sure you want to delete '{}'?").format(
                row.scheme_data.get("name", row.scheme_key)
            ),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.connect(
            "response",
            lambda d, r: self._handle_delete_scheme_response(r, row.scheme_key),
        )
        dialog.present(self)

    def _handle_delete_scheme_response(self, response: str, scheme_key: str) -> None:
        """Handle delete scheme confirmation."""
        if response != "delete":
            return

        settings = get_settings_manager()
        if scheme_key in settings.custom_schemes:
            del settings.custom_schemes[scheme_key]
            settings.save_custom_schemes()

            # If deleted scheme was selected, switch to first scheme
            if settings.get_color_scheme_name() == scheme_key:
                settings.set("color_scheme", 0)
                if self._parent_window and hasattr(
                    self._parent_window, "terminal_manager"
                ):
                    self._parent_window.terminal_manager.apply_settings_to_all_terminals()

            self._populate_color_schemes()
            self.add_toast(Adw.Toast(title=_("Scheme deleted")))

    def _on_editor_save(
        self, editor, original_key: str, new_key: str, scheme_data: dict
    ) -> None:
        """Handle save from scheme editor."""
        settings = get_settings_manager()

        # Determine if this is a new scheme or edit of existing
        is_new = (
            original_key is None
            or original_key == ""
            or not original_key.startswith("custom_")
        )

        if is_new:
            # Generate unique key for new scheme
            import time

            unique_key = f"custom_{int(time.time() * 1000)}"
            settings.custom_schemes[unique_key] = scheme_data
        else:
            # Update existing custom scheme
            if original_key in settings.custom_schemes:
                del settings.custom_schemes[original_key]
            settings.custom_schemes[new_key if new_key else original_key] = scheme_data

        settings.save_custom_schemes()
        self._populate_color_schemes()

        # If this is a new scheme, automatically select it
        if is_new:
            scheme_order = settings.get_scheme_order()
            try:
                new_scheme_index = scheme_order.index(unique_key)
                settings.set("color_scheme", new_scheme_index)
                # Update visual selection
                for scheme_row in self._scheme_rows.values():
                    if scheme_row.scheme_key == unique_key:
                        self._scheme_listbox.select_row(scheme_row)
                        # Update checkmarks
                        for other_row in self._scheme_rows.values():
                            other_row.check_icon.set_visible(other_row == scheme_row)
                        break
            except ValueError:
                pass  # Should not happen, but just in case

        # Reapply settings to terminals
        if self._parent_window and hasattr(self._parent_window, "terminal_manager"):
            self._parent_window.terminal_manager.apply_settings_to_all_terminals()

        # Apply to GTK theme if using terminal theme
        if settings.get("gtk_theme") == "terminal" and self._parent_window:
            settings.apply_gtk_terminal_theme(self._parent_window)

        # Refresh shell input highlighter
        try:
            from ...terminal.highlighter import get_shell_input_highlighter

            highlighter = get_shell_input_highlighter()
            highlighter.refresh_settings()
        except Exception:
            pass

        self.add_toast(Adw.Toast(title=_("Scheme saved")))

    def _setup_welcome_banner(self, page: Adw.PreferencesPage) -> None:
        """Setup the welcome/explanation text at the top."""
        welcome_group = Adw.PreferencesGroup(
            description=_(
                "Colorizes terminal output patterns like errors, warnings, and IPs. "
                "Many rules can slow down large outputs."
            ),
        )
        page.add(welcome_group)

    def _setup_activation_group(self, page: Adw.PreferencesPage) -> None:
        """Setup the activation settings group with performance warning."""
        activation_group = Adw.PreferencesGroup(
            title=_("Activation"),
            description=_(
                "⚠️ On slower computers, enabling output highlighting may slightly "
                "reduce terminal responsiveness, as all displayed content is processed for color patterns."
            ),
        )
        page.add(activation_group)

        # Enable for local terminals toggle
        self._local_toggle = Adw.SwitchRow(
            title=_("Local Terminals"),
            subtitle=_("Apply output highlighting to local terminal sessions"),
        )
        self._local_toggle.set_active(self._manager.enabled_for_local)
        self._local_toggle.connect("notify::active", self._on_local_toggled)
        activation_group.add(self._local_toggle)

        # Enable for SSH terminals toggle
        self._ssh_toggle = Adw.SwitchRow(
            title=_("SSH Sessions"),
            subtitle=_("Apply output highlighting to SSH connections"),
        )
        self._ssh_toggle.set_active(self._manager.enabled_for_ssh)
        self._ssh_toggle.connect("notify::active", self._on_ssh_toggled)
        activation_group.add(self._ssh_toggle)

    def _setup_cat_colorization_group(self, page: Adw.PreferencesPage) -> None:
        """Setup the cat colorization settings group using Pygments."""
        # Check if Pygments is available
        import importlib.util

        pygments_available = importlib.util.find_spec("pygments") is not None

        # NOTE: 'cat' is a terminal command, so we don't translate it
        self._cat_group = Adw.PreferencesGroup(
            title=_("{} Command Colorization").format("cat"),
            description=_(
                "Syntax highlighting for '{}' command output (using Pygments)"
            ).format("cat")
            if pygments_available
            else _("Pygments is not installed - '{}' output will not be colorized").format("cat"),
        )
        page.add(self._cat_group)

        settings = get_settings_manager()

        # Experimental notice (keep at the top of the group)
        if pygments_available:
            note_row = Adw.ActionRow(
                title=_("⚠️ Experimental Feature"),
                subtitle=_(
                    "This feature colorizes output for the '{}' command. "
                    "It may not work perfectly with every shell/prompt or when output is fragmented."
                ).format("cat"),
            )
            note_row.add_css_class("dim-label")
            self._cat_group.add(note_row)

        # Enable cat colorization toggle (first item)
        self._cat_colorization_toggle = Adw.SwitchRow(
            title=_("Enable '{}' Colorization").format("cat"),
            subtitle=_("Apply syntax highlighting to '{}' command output").format("cat"),
        )
        current_enabled = settings.get("cat_colorization_enabled", True)
        self._cat_colorization_toggle.set_active(current_enabled)
        self._cat_colorization_toggle.connect(
            "notify::active", self._on_cat_colorization_toggled
        )
        self._cat_group.add(self._cat_colorization_toggle)

        # Color theme dropdown (only if Pygments is available)
        if pygments_available:
            # Get available Pygments styles
            from pygments.styles import get_all_styles

            all_themes = sorted(list(get_all_styles()))

            # Theme mode selector (Auto/Manual)
            self._cat_theme_mode_row = Adw.ComboRow(
                title=_("Theme Mode"),
                subtitle=_("Auto: adapts to background color. Manual: single theme."),
            )
            mode_model = Gtk.StringList()
            mode_model.append(_("Auto"))
            mode_model.append(_("Manual"))
            self._cat_theme_mode_row.set_model(mode_model)
            current_mode = settings.get("cat_theme_mode", "auto")
            self._cat_theme_mode_row.set_selected(0 if current_mode == "auto" else 1)
            self._cat_theme_mode_row.connect(
                "notify::selected", self._on_cat_theme_mode_changed
            )
            self._cat_group.add(self._cat_theme_mode_row)

            # Dark themes (background luminance <= 0.5) - based on actual Pygments style analysis
            dark_only_themes = [
                "a11y-dark",
                "a11y-high-contrast-dark",
                "blinds-dark",
                "coffee",
                "dracula",
                "fruity",
                "github-dark",
                "github-dark-colorblind",
                "github-dark-high-contrast",
                "gotthard-dark",
                "greative",
                "gruvbox-dark",
                "inkpot",
                "lightbulb",
                "material",
                "monokai",
                "native",
                "nord",
                "nord-darker",
                "one-dark",
                "paraiso-dark",
                "pitaya-smoothie",
                "rrt",
                "solarized-dark",
                "stata-dark",
                "vim",
                "zenburn",
            ]

            # Light themes (background luminance > 0.5) - based on actual Pygments style analysis
            light_only_themes = [
                "a11y-high-contrast-light",
                "a11y-light",
                "abap",
                "algol",
                "algol_nu",
                "arduino",
                "autumn",
                "blinds-light",
                "borland",
                "bw",
                "colorful",
                "default",
                "emacs",
                "friendly",
                "friendly_grayscale",
                "github-light",
                "github-light-colorblind",
                "github-light-high-contrast",
                "gotthard-light",
                "gruvbox-light",
                "igor",
                "lilypond",
                "lovelace",
                "manni",
                "murphy",
                "paraiso-light",
                "pastie",
                "perldoc",
                "rainbow_dash",
                "sas",
                "solarized-light",
                "staroffice",
                "stata-light",
                "tango",
                "trac",
                "vs",
                "xcode",
            ]

            # Dark theme selector - show only dark themes
            self._cat_dark_theme_row = Adw.ComboRow(
                title=_("Dark Background Theme"),
                subtitle=_("Theme used when background is dark"),
            )
            dark_themes_model = Gtk.StringList()
            dark_themes = []
            for theme in dark_only_themes:
                if theme in all_themes:
                    dark_themes.append(theme)
            for theme in all_themes:
                if theme not in dark_themes and theme not in light_only_themes:
                    dark_themes.append(theme)
            for theme in dark_themes:
                dark_themes_model.append(theme)
            self._cat_dark_theme_row.set_model(dark_themes_model)
            self._cat_dark_theme_names = dark_themes

            current_dark = settings.get("cat_dark_theme", "monokai")
            try:
                dark_idx = dark_themes.index(current_dark)
                self._cat_dark_theme_row.set_selected(dark_idx)
            except ValueError:
                self._cat_dark_theme_row.set_selected(0)
            self._cat_dark_theme_row.connect(
                "notify::selected", self._on_cat_dark_theme_changed
            )
            self._cat_group.add(self._cat_dark_theme_row)

            # Light theme selector - show only light themes
            self._cat_light_theme_row = Adw.ComboRow(
                title=_("Light Background Theme"),
                subtitle=_("Theme used when background is light"),
            )
            light_themes_model = Gtk.StringList()
            light_themes = []
            for theme in light_only_themes:
                if theme in all_themes:
                    light_themes.append(theme)
            for theme in all_themes:
                if theme not in light_themes and theme not in dark_only_themes:
                    light_themes.append(theme)
            for theme in light_themes:
                light_themes_model.append(theme)
            self._cat_light_theme_row.set_model(light_themes_model)
            self._cat_light_theme_names = light_themes

            current_light = settings.get("cat_light_theme", "solarized-light")
            try:
                light_idx = light_themes.index(current_light)
                self._cat_light_theme_row.set_selected(light_idx)
            except ValueError:
                self._cat_light_theme_row.set_selected(0)
            self._cat_light_theme_row.connect(
                "notify::selected", self._on_cat_light_theme_changed
            )
            self._cat_group.add(self._cat_light_theme_row)

            # Manual theme selector (legacy, shown when mode is Manual)
            self._cat_theme_row = Adw.ComboRow(
                title=_("Manual Theme"),
                subtitle=_("Single theme to use in manual mode"),
            )
            manual_themes_model = Gtk.StringList()
            for theme in all_themes:
                manual_themes_model.append(theme)
            self._cat_theme_row.set_model(manual_themes_model)
            self._cat_theme_names = all_themes

            current_theme = settings.get("pygments_theme", "monokai").lower()
            try:
                theme_index = all_themes.index(current_theme)
                self._cat_theme_row.set_selected(theme_index)
            except ValueError:
                self._cat_theme_row.set_selected(0)

            self._cat_theme_row.connect("notify::selected", self._on_cat_theme_changed)
            self._cat_group.add(self._cat_theme_row)

            # Update visibility based on current mode and enabled state
            is_auto_mode = current_mode == "auto"
            self._cat_theme_mode_row.set_visible(current_enabled)
            self._cat_dark_theme_row.set_visible(current_enabled and is_auto_mode)
            self._cat_light_theme_row.set_visible(current_enabled and is_auto_mode)
            self._cat_theme_row.set_visible(current_enabled and not is_auto_mode)
        else:
            self._cat_theme_row = None
            self._cat_theme_names = []
            self._cat_theme_mode_row = None
            self._cat_dark_theme_row = None
            self._cat_dark_theme_names = []
            self._cat_light_theme_row = None
            self._cat_light_theme_names = []

            # Show install hint
            install_row = Adw.ActionRow(
                title=_("Install Pygments"),
                subtitle=_("pip install pygments"),
            )
            install_row.add_css_class("dim-label")
            self._cat_group.add(install_row)

    def _setup_shell_input_highlighting_group(self, page: Adw.PreferencesPage) -> None:
        """Setup the shell input highlighting settings group (experimental)."""
        # Check if Pygments is available
        import importlib.util

        pygments_available = importlib.util.find_spec("pygments") is not None

        self._shell_input_group = Adw.PreferencesGroup(
            title=_("Shell Input Highlighting"),
            description=_(
                "Live syntax highlighting as you type commands (experimental)"
            )
            if pygments_available
            else _("Pygments is not installed - shell input highlighting unavailable"),
        )
        page.add(self._shell_input_group)

        settings = get_settings_manager()

        # Experimental notice (keep at the top of the group)
        if pygments_available:
            note_row = Adw.ActionRow(
                title=_("⚠️ Experimental Feature"),
                subtitle=_(
                    "This feature applies highlighting to echoed shell input. "
                    "It may not work perfectly with all prompts or shells."
                ),
            )
            note_row.add_css_class("dim-label")
            self._shell_input_group.add(note_row)

        # Enable shell input highlighting toggle
        self._shell_input_toggle = Adw.SwitchRow(
            title=_("Enable Shell Input Highlighting"),
            subtitle=_(
                "Color shell commands as you type them. Useful for SSH sessions and "
                "Docker containers where shell configuration cannot be changed."
            ),
        )
        current_enabled = settings.get("shell_input_highlighting_enabled", False)
        self._shell_input_toggle.set_active(current_enabled)
        self._shell_input_toggle.set_sensitive(pygments_available)
        self._shell_input_toggle.connect(
            "notify::active", self._on_shell_input_highlighting_toggled
        )
        self._shell_input_group.add(self._shell_input_toggle)

        # Color theme dropdown (only if Pygments is available)
        if pygments_available:
            # Theme Mode selector (auto/manual)
            self._theme_mode_row = Adw.ComboRow(
                title=_("Theme Mode"),
                subtitle=_("Auto detects background, Manual uses selected theme"),
            )
            mode_model = Gtk.StringList()
            mode_model.append(_("Auto"))
            mode_model.append(_("Manual"))
            self._theme_mode_row.set_model(mode_model)

            current_mode = settings.get("shell_input_theme_mode", "auto")
            self._theme_mode_row.set_selected(0 if current_mode == "auto" else 1)
            self._theme_mode_row.connect(
                "notify::selected", self._on_shell_input_mode_changed
            )
            self._shell_input_group.add(self._theme_mode_row)

            # Get available Pygments styles
            from pygments.styles import get_all_styles

            all_themes = sorted(list(get_all_styles()))

            # Dark themes (background luminance <= 0.5) - based on actual Pygments style analysis
            dark_only_themes = [
                "a11y-dark",
                "a11y-high-contrast-dark",
                "blinds-dark",
                "coffee",
                "dracula",
                "fruity",
                "github-dark",
                "github-dark-colorblind",
                "github-dark-high-contrast",
                "gotthard-dark",
                "greative",
                "gruvbox-dark",
                "inkpot",
                "lightbulb",
                "material",
                "monokai",
                "native",
                "nord",
                "nord-darker",
                "one-dark",
                "paraiso-dark",
                "pitaya-smoothie",
                "rrt",
                "solarized-dark",
                "stata-dark",
                "vim",
                "zenburn",
            ]

            # Light themes (background luminance > 0.5) - based on actual Pygments style analysis
            light_only_themes = [
                "a11y-high-contrast-light",
                "a11y-light",
                "abap",
                "algol",
                "algol_nu",
                "arduino",
                "autumn",
                "blinds-light",
                "borland",
                "bw",
                "colorful",
                "default",
                "emacs",
                "friendly",
                "friendly_grayscale",
                "github-light",
                "github-light-colorblind",
                "github-light-high-contrast",
                "gotthard-light",
                "gruvbox-light",
                "igor",
                "lilypond",
                "lovelace",
                "manni",
                "murphy",
                "paraiso-light",
                "pastie",
                "perldoc",
                "rainbow_dash",
                "sas",
                "solarized-light",
                "staroffice",
                "stata-light",
                "tango",
                "trac",
                "vs",
                "xcode",
            ]

            # Dark theme selector - show only dark themes
            self._dark_theme_row = Adw.ComboRow(
                title=_("Dark Background Theme"),
                subtitle=_("Theme used when background is dark"),
            )
            dark_themes_model = Gtk.StringList()
            dark_themes = []
            # First add known dark themes
            for theme in dark_only_themes:
                if theme in all_themes:
                    dark_themes.append(theme)
            # Then add any remaining themes not in either list
            for theme in all_themes:
                if theme not in dark_themes and theme not in light_only_themes:
                    dark_themes.append(theme)
            for theme in dark_themes:
                dark_themes_model.append(theme)
            self._dark_theme_row.set_model(dark_themes_model)
            self._dark_theme_names = dark_themes

            current_dark = settings.get("shell_input_dark_theme", "monokai")
            try:
                dark_idx = dark_themes.index(current_dark)
                self._dark_theme_row.set_selected(dark_idx)
            except ValueError:
                self._dark_theme_row.set_selected(0)
            self._dark_theme_row.connect(
                "notify::selected", self._on_dark_theme_changed
            )
            self._shell_input_group.add(self._dark_theme_row)

            # Light theme selector - show only light themes
            self._light_theme_row = Adw.ComboRow(
                title=_("Light Background Theme"),
                subtitle=_("Theme used when background is light"),
            )
            light_themes_model = Gtk.StringList()
            light_themes = []
            # First add known light themes
            for theme in light_only_themes:
                if theme in all_themes:
                    light_themes.append(theme)
            # Then add any remaining themes not in either list
            for theme in all_themes:
                if theme not in light_themes and theme not in dark_only_themes:
                    light_themes.append(theme)
            for theme in light_themes:
                light_themes_model.append(theme)
            self._light_theme_row.set_model(light_themes_model)
            self._light_theme_names = light_themes

            current_light = settings.get("shell_input_light_theme", "solarized-light")
            try:
                light_idx = light_themes.index(current_light)
                self._light_theme_row.set_selected(light_idx)
            except ValueError:
                self._light_theme_row.set_selected(0)
            self._light_theme_row.connect(
                "notify::selected", self._on_light_theme_changed
            )
            self._shell_input_group.add(self._light_theme_row)

            # Manual theme selector (legacy, shown when mode is Manual)
            self._shell_input_theme_row = Adw.ComboRow(
                title=_("Manual Theme"),
                subtitle=_("Single theme to use in manual mode"),
            )
            manual_themes_model = Gtk.StringList()
            for theme in all_themes:
                manual_themes_model.append(theme)
            self._shell_input_theme_row.set_model(manual_themes_model)
            self._shell_input_theme_names = all_themes

            current_theme = settings.get(
                "shell_input_pygments_theme", "monokai"
            ).lower()
            try:
                theme_index = all_themes.index(current_theme)
                self._shell_input_theme_row.set_selected(theme_index)
            except ValueError:
                self._shell_input_theme_row.set_selected(0)

            self._shell_input_theme_row.connect(
                "notify::selected", self._on_shell_input_theme_changed
            )
            self._shell_input_group.add(self._shell_input_theme_row)

            # Update visibility based on current settings
            is_auto = current_mode == "auto"
            self._dark_theme_row.set_visible(current_enabled and is_auto)
            self._light_theme_row.set_visible(current_enabled and is_auto)
            self._shell_input_theme_row.set_visible(current_enabled and not is_auto)
            self._theme_mode_row.set_visible(current_enabled)
        else:
            self._shell_input_theme_row = None
            self._shell_input_theme_names = []
            self._theme_mode_row = None
            self._dark_theme_row = None
            self._light_theme_row = None

        # (Experimental notice is intentionally placed at the top of the group.)

    def _on_shell_input_highlighting_toggled(
        self, switch: Adw.SwitchRow, _pspec
    ) -> None:
        """Handle shell input highlighting toggle changes."""
        enabled = switch.get_active()
        settings = get_settings_manager()
        settings.set("shell_input_highlighting_enabled", enabled)

        # Update all related row visibility
        is_auto = settings.get("shell_input_theme_mode", "auto") == "auto"
        if self._theme_mode_row:
            self._theme_mode_row.set_visible(enabled)
        if self._dark_theme_row:
            self._dark_theme_row.set_visible(enabled and is_auto)
        if self._light_theme_row:
            self._light_theme_row.set_visible(enabled and is_auto)
        if self._shell_input_theme_row:
            self._shell_input_theme_row.set_visible(enabled and not is_auto)

        # Refresh the shell input highlighter
        try:
            from ...terminal.highlighter import get_shell_input_highlighter

            highlighter = get_shell_input_highlighter()
            highlighter.refresh_settings()
        except Exception as e:
            self.logger.warning(f"Failed to refresh shell input highlighter: {e}")

        self.logger.debug(
            f"Shell input highlighting {'enabled' if enabled else 'disabled'}"
        )

    def _on_shell_input_mode_changed(self, combo_row: Adw.ComboRow, _pspec) -> None:
        """Handle theme mode changes (auto/manual)."""
        idx = combo_row.get_selected()
        is_auto = idx == 0
        mode = "auto" if is_auto else "manual"

        settings = get_settings_manager()
        settings.set("shell_input_theme_mode", mode)

        # Update row visibility
        if self._dark_theme_row:
            self._dark_theme_row.set_visible(is_auto)
        if self._light_theme_row:
            self._light_theme_row.set_visible(is_auto)
        if self._shell_input_theme_row:
            self._shell_input_theme_row.set_visible(not is_auto)

        # Refresh highlighter
        try:
            from ...terminal.highlighter import get_shell_input_highlighter

            highlighter = get_shell_input_highlighter()
            highlighter.refresh_settings()
        except Exception as e:
            self.logger.warning(f"Failed to refresh shell input highlighter: {e}")

        self.logger.debug(f"Shell input theme mode changed to: {mode}")

    def _on_dark_theme_changed(self, combo_row: Adw.ComboRow, _pspec) -> None:
        """Handle dark theme selection changes."""
        idx = combo_row.get_selected()
        if idx != Gtk.INVALID_LIST_POSITION and hasattr(self, "_dark_theme_names"):
            if idx < len(self._dark_theme_names):
                theme = self._dark_theme_names[idx]
                settings = get_settings_manager()
                settings.set("shell_input_dark_theme", theme)

                # Refresh highlighter
                try:
                    from ...terminal.highlighter import get_shell_input_highlighter

                    highlighter = get_shell_input_highlighter()
                    highlighter.refresh_settings()
                except Exception as e:
                    self.logger.warning(
                        f"Failed to refresh shell input highlighter: {e}"
                    )

                self.logger.debug(f"Shell input dark theme changed to: {theme}")

    def _on_light_theme_changed(self, combo_row: Adw.ComboRow, _pspec) -> None:
        """Handle light theme selection changes."""
        idx = combo_row.get_selected()
        if idx != Gtk.INVALID_LIST_POSITION and hasattr(self, "_light_theme_names"):
            if idx < len(self._light_theme_names):
                theme = self._light_theme_names[idx]
                settings = get_settings_manager()
                settings.set("shell_input_light_theme", theme)

                # Refresh highlighter
                try:
                    from ...terminal.highlighter import get_shell_input_highlighter

                    highlighter = get_shell_input_highlighter()
                    highlighter.refresh_settings()
                except Exception as e:
                    self.logger.warning(
                        f"Failed to refresh shell input highlighter: {e}"
                    )

                self.logger.debug(f"Shell input light theme changed to: {theme}")

    def _on_shell_input_theme_changed(self, combo_row: Adw.ComboRow, _pspec) -> None:
        """Handle shell input color theme changes (manual mode)."""
        idx = combo_row.get_selected()
        if idx != Gtk.INVALID_LIST_POSITION and hasattr(
            self, "_shell_input_theme_names"
        ):
            theme_names = self._shell_input_theme_names
            if idx < len(theme_names):
                theme = theme_names[idx]
                settings = get_settings_manager()
                settings.set("shell_input_pygments_theme", theme)

                # Refresh the shell input highlighter with new theme
                try:
                    from ...terminal.highlighter import get_shell_input_highlighter

                    highlighter = get_shell_input_highlighter()
                    highlighter.refresh_settings()
                except Exception as e:
                    self.logger.warning(
                        f"Failed to refresh shell input highlighter: {e}"
                    )

                self.logger.debug(f"Shell input theme changed to: {theme}")

    def _setup_ignored_commands_group(self, page: Adw.PreferencesPage) -> None:
        """Setup the ignored commands group as a collapsible section."""
        self._ignored_commands_group = Adw.PreferencesGroup()
        page.add(self._ignored_commands_group)

        # Main expander row that contains all ignored commands
        self._ignored_expander = Adw.ExpanderRow(
            title=_("Ignored Commands"),
        )
        self._ignored_expander.set_enable_expansion(True)
        self._ignored_expander.set_expanded(False)  # Collapsed by default

        # Add icon prefix
        icon = Gtk.Image.new_from_icon_name("dialog-information-symbolic")
        icon.set_opacity(0.6)
        self._ignored_expander.add_prefix(icon)

        self._ignored_commands_group.add(self._ignored_expander)

        # Restore defaults row (inside expander, at the top)
        restore_row = Adw.ActionRow(
            title=_("Restore Defaults"),
        )
        restore_row.set_activatable(True)
        restore_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        restore_btn.set_valign(Gtk.Align.CENTER)
        restore_btn.add_css_class("flat")
        restore_row.add_suffix(restore_btn)
        restore_row.set_activatable_widget(restore_btn)
        restore_btn.connect("clicked", self._on_restore_ignored_defaults_clicked)
        self._ignored_expander.add_row(restore_row)

        # Add command button (inside expander) - prominent style
        add_cmd_row = Adw.ActionRow(
            title=_("➕ Add Ignored Command"),
        )
        add_cmd_row.set_activatable(True)
        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.set_valign(Gtk.Align.CENTER)
        add_btn.add_css_class("suggested-action")
        add_cmd_row.add_suffix(add_btn)
        add_cmd_row.set_activatable_widget(add_btn)
        add_btn.connect("clicked", self._on_add_ignored_command_clicked)
        self._add_ignored_cmd_row = add_cmd_row
        self._ignored_expander.add_row(add_cmd_row)

        # Container for command rows inside the expander
        self._ignored_command_rows: dict[str, Adw.ActionRow] = {}

        # Populate initial list (after add button)
        self._populate_ignored_commands()

    def _populate_ignored_commands(self) -> None:
        """Populate the ignored commands list from settings."""
        # Clear existing rows from expander (but keep the add button)
        for row in list(self._ignored_command_rows.values()):
            self._ignored_expander.remove(row)
        self._ignored_command_rows.clear()

        # Get current ignored commands
        settings = get_settings_manager()
        ignored_commands = settings.get("ignored_highlight_commands", [])

        # Sort and add rows to expander (after the add button)
        for cmd in sorted(ignored_commands):
            row = self._create_ignored_command_row(cmd)
            self._ignored_command_rows[cmd] = row
            self._ignored_expander.add_row(row)

        # Update expander subtitle with count
        count = len(ignored_commands)
        self._ignored_expander.set_subtitle(
            _("{count} command(s) • Click to expand/collapse").format(count=count)
        )

    def _create_ignored_command_row(self, cmd: str) -> Adw.ActionRow:
        """Create a row for an ignored command with remove button."""
        row = Adw.ActionRow(title=cmd)

        # Remove button
        remove_btn = Gtk.Button(icon_name="user-trash-symbolic")
        remove_btn.set_valign(Gtk.Align.CENTER)
        remove_btn.add_css_class("flat")
        get_tooltip_helper().add_tooltip(remove_btn, _("Remove from ignored list"))
        remove_btn.connect("clicked", self._on_remove_ignored_command, cmd)
        row.add_suffix(remove_btn)

        return row

    def _on_add_ignored_command_clicked(self, button: Gtk.Button) -> None:
        """Handle add ignored command button click."""
        dialog = AddIgnoredCommandDialog(self)
        dialog.connect("command-added", self._on_ignored_command_added)
        dialog.present(self)

    def _on_ignored_command_added(self, dialog, command: str) -> None:
        """Handle new ignored command added."""
        settings = get_settings_manager()
        ignored_commands = settings.get("ignored_highlight_commands", [])

        if command not in ignored_commands:
            ignored_commands.append(command)
            ignored_commands.sort()
            settings.set("ignored_highlight_commands", ignored_commands)

            # Refresh highlighter's ignored commands cache
            from ...terminal.highlighter import get_output_highlighter

            get_output_highlighter().refresh_ignored_commands()

            self._populate_ignored_commands()
            self.emit("settings-changed")
            self.add_toast(Adw.Toast(title=_("Command added: {}").format(command)))

    def _on_remove_ignored_command(self, button: Gtk.Button, command: str) -> None:
        """Handle remove ignored command button click - show confirmation."""
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Remove Ignored Command?"),
            body=_(
                'Remove "{}" from the ignored commands list? Highlighting will be applied to this command\'s output.'
            ).format(command),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("remove", _("Remove"))
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_remove_ignored_confirmed, command)
        dialog.present()

    def _on_remove_ignored_confirmed(
        self, dialog: Adw.MessageDialog, response: str, command: str
    ) -> None:
        """Handle remove ignored command confirmation."""
        dialog.close()
        if response == "remove":
            settings = get_settings_manager()
            ignored_commands = settings.get("ignored_highlight_commands", [])

            if command in ignored_commands:
                ignored_commands.remove(command)
                settings.set("ignored_highlight_commands", ignored_commands)

                # Refresh highlighter's ignored commands cache
                from ...terminal.highlighter import get_output_highlighter

                get_output_highlighter().refresh_ignored_commands()

                self._populate_ignored_commands()
                self.emit("settings-changed")
                self.add_toast(Adw.Toast(title=_("Command removed: {}").format(command)))

    def _on_restore_ignored_defaults_clicked(self, button: Gtk.Button) -> None:
        """Handle restore defaults button click for ignored commands."""
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Restore Default Ignored Commands?"),
            body=_(
                "This will replace your current ignored commands list with the system defaults. Custom additions will be lost."
            ),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("restore", _("Restore Defaults"))
        dialog.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_restore_ignored_defaults_confirmed)
        dialog.present()

    def _on_restore_ignored_defaults_confirmed(self, dialog: Adw.MessageDialog, response: str) -> None:
        """Handle restore defaults confirmation."""
        dialog.close()
        if response == "restore":
            from ...settings.config import DefaultSettings
            default_ignored = DefaultSettings.get_defaults().get("ignored_highlight_commands", [])

            settings = get_settings_manager()
            settings.set("ignored_highlight_commands", list(default_ignored))

            # Refresh highlighter's ignored commands cache
            from ...terminal.highlighter import get_output_highlighter
            get_output_highlighter().refresh_ignored_commands()

            self._populate_ignored_commands()
            self.emit("settings-changed")
            self.add_toast(Adw.Toast(title=_("Restored default ignored commands")))

    def _setup_context_settings_group(self, page: Adw.PreferencesPage) -> None:
        """Setup the context-aware settings group."""
        context_settings_group = Adw.PreferencesGroup(
            title=_("Per-Command Highlighting"),
        )
        page.add(context_settings_group)

        # Enable context-aware highlighting toggle
        self._context_aware_toggle = Adw.SwitchRow(
            title=_("Enable Command Detection"),
        )
        self._context_aware_toggle.set_active(self._manager.context_aware_enabled)
        self._context_aware_toggle.connect(
            "notify::active", self._on_context_aware_toggled
        )
        context_settings_group.add(self._context_aware_toggle)

    def _setup_context_selector_group(self, page: Adw.PreferencesPage) -> None:
        """Setup the context selector group with bulk actions."""
        self._context_selector_group = Adw.PreferencesGroup(
            title=_("Commands"),
        )
        page.add(self._context_selector_group)

        # ADD NEW CONTEXT - prominent at the top
        add_context_row = Adw.ActionRow(
            title=_("➕ Add Command"),
        )
        add_context_row.set_activatable(True)
        add_context_row.add_css_class("suggested-action")
        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.set_valign(Gtk.Align.CENTER)
        add_btn.add_css_class("suggested-action")
        add_context_row.add_suffix(add_btn)
        add_context_row.set_activatable_widget(add_btn)
        add_btn.connect("clicked", self._on_add_context_clicked)
        self._add_context_row = add_context_row
        self._context_selector_group.add(add_context_row)

        # Bulk action buttons
        bulk_actions_row = Adw.ActionRow(
            title=_("Bulk Actions"),
        )

        enable_all_btn = Gtk.Button(label=_("Enable All"))
        enable_all_btn.set_valign(Gtk.Align.CENTER)
        enable_all_btn.add_css_class("suggested-action")
        enable_all_btn.connect("clicked", self._on_enable_all_contexts)
        bulk_actions_row.add_suffix(enable_all_btn)

        disable_all_btn = Gtk.Button(label=_("Disable All"))
        disable_all_btn.set_valign(Gtk.Align.CENTER)
        disable_all_btn.add_css_class("destructive-action")
        disable_all_btn.connect("clicked", self._on_disable_all_contexts)
        bulk_actions_row.add_suffix(disable_all_btn)

        self._context_selector_group.add(bulk_actions_row)

        # Reset all contexts button
        reset_contexts_row = Adw.ActionRow(
            title=_("Reset All Commands"),
        )
        reset_contexts_row.set_activatable(True)
        reset_contexts_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        reset_contexts_btn.set_valign(Gtk.Align.CENTER)
        reset_contexts_btn.add_css_class("flat")
        reset_contexts_row.add_suffix(reset_contexts_btn)
        reset_contexts_row.set_activatable_widget(reset_contexts_btn)
        reset_contexts_btn.connect("clicked", self._on_reset_all_contexts_clicked)
        self._context_selector_group.add(reset_contexts_row)

        # Scrolled container for context list
        self._context_list_group = Adw.PreferencesGroup(
            title=_("Available Commands"),
        )
        page.add(self._context_list_group)

        # Context rows will be added dynamically
        self._context_rows: dict[str, Adw.ActionRow] = {}

    def _setup_context_rules_group(self, page: Adw.PreferencesPage) -> None:
        """Setup the context rules list group with reorder support."""
        self._context_rules_group = Adw.PreferencesGroup(
            title=_("Command Rules"),
            description=_("Rules specific to the selected command. Order matters!"),
        )
        page.add(self._context_rules_group)

        # Context header with enable/settings
        self._context_header_row = Adw.ActionRow(
            title=_("No command selected"),
            subtitle=_("Select a command from the list above"),
        )
        self._context_rules_group.add(self._context_header_row)

        # Context enable toggle
        self._context_enable_row = Adw.SwitchRow(
            title=_("Enable Command Rules"),
            subtitle=_("Apply rules when this command is detected"),
        )
        self._context_enable_row.connect(
            "notify::active", self._on_context_enable_toggled
        )
        self._context_rules_group.add(self._context_enable_row)

        # Use global rules toggle
        self._use_global_rules_row = Adw.SwitchRow(
            title=_("Include Global Rules"),
            subtitle=_("Also apply global rules alongside command-specific rules"),
        )
        self._use_global_rules_row.connect("notify::active", self._on_use_global_rules_toggled)
        self._context_rules_group.add(self._use_global_rules_row)

        # Reset to default button
        self._reset_context_row = Adw.ActionRow(
            title=_("Reset to System Default"),
            subtitle=_("Remove user customization and revert to system rules"),
        )
        self._reset_context_row.set_activatable(True)
        reset_btn = Gtk.Button(icon_name="edit-undo-symbolic")
        reset_btn.set_valign(Gtk.Align.CENTER)
        reset_btn.add_css_class("flat")
        self._reset_context_row.add_suffix(reset_btn)
        self._reset_context_row.set_activatable_widget(reset_btn)
        reset_btn.connect("clicked", self._on_reset_context_clicked)
        self._reset_context_row.set_sensitive(False)
        self._context_rules_group.add(self._reset_context_row)

        # Rules list group (separate for better organization)
        self._context_rules_list_group = Adw.PreferencesGroup(
            title=_("Rules (in execution order)"),
            description=_("Use arrows to reorder rules. Rules are matched from top to bottom."),
        )
        page.add(self._context_rules_list_group)

        # Add rule to context button - make it prominent
        add_rule_row = Adw.ActionRow(
            title=_("➕ Add Rule to This Command"),
            subtitle=_("Create a new highlighting pattern for the selected command"),
        )
        add_rule_row.set_activatable(True)
        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.set_valign(Gtk.Align.CENTER)
        add_btn.add_css_class("suggested-action")
        add_rule_row.add_suffix(add_btn)
        add_rule_row.set_activatable_widget(add_btn)
        add_btn.connect("clicked", self._on_add_context_rule_clicked)
        self._add_context_rule_row = add_rule_row
        self._context_rules_list_group.add(add_rule_row)

    def _setup_rules_group(self, page: Adw.PreferencesPage) -> None:
        """Setup the global rules list group."""
        self._rules_group = Adw.PreferencesGroup(
            title=_("Global Highlight Rules"),
        )
        page.add(self._rules_group)

        # Add rule button - make it more prominent
        add_row = Adw.ActionRow(
            title=_("➕ Add New Global Rule"),
        )
        add_row.set_activatable(True)
        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.set_valign(Gtk.Align.CENTER)
        add_btn.add_css_class("suggested-action")
        add_row.add_suffix(add_btn)
        add_row.set_activatable_widget(add_btn)
        add_btn.connect("clicked", self._on_add_rule_clicked)
        self._rules_group.add(add_row)

        # Reset global rules button
        reset_row = Adw.ActionRow(
            title=_("Reset Global Rules"),
        )
        reset_row.set_activatable(True)
        reset_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        reset_btn.set_valign(Gtk.Align.CENTER)
        reset_btn.add_css_class("flat")
        reset_row.add_suffix(reset_btn)
        reset_row.set_activatable_widget(reset_btn)
        reset_btn.connect("clicked", self._on_reset_global_rules_clicked)
        self._rules_group.add(reset_row)

    def _load_settings(self) -> None:
        """Load current settings from the manager."""
        self._local_toggle.set_active(self._manager.enabled_for_local)
        self._ssh_toggle.set_active(self._manager.enabled_for_ssh)
        self._context_aware_toggle.set_active(self._manager.context_aware_enabled)

        # Load cat colorization settings
        settings = get_settings_manager()

        # Load cat colorization enabled state
        cat_enabled = settings.get("cat_colorization_enabled", True)
        self._cat_colorization_toggle.set_active(cat_enabled)

        # Load cat theme mode
        if self._cat_theme_mode_row is not None:
            current_mode = settings.get("cat_theme_mode", "auto")
            self._cat_theme_mode_row.set_selected(0 if current_mode == "auto" else 1)
            is_auto_mode = current_mode == "auto"

            # Update dark theme selection
            if self._cat_dark_theme_row is not None:
                current_dark = settings.get("cat_dark_theme", "monokai")
                try:
                    dark_idx = self._cat_dark_theme_names.index(current_dark)
                    self._cat_dark_theme_row.set_selected(dark_idx)
                except ValueError:
                    self._cat_dark_theme_row.set_selected(0)
                self._cat_dark_theme_row.set_visible(cat_enabled and is_auto_mode)

            # Update light theme selection
            if self._cat_light_theme_row is not None:
                current_light = settings.get("cat_light_theme", "solarized-light")
                try:
                    light_idx = self._cat_light_theme_names.index(current_light)
                    self._cat_light_theme_row.set_selected(light_idx)
                except ValueError:
                    self._cat_light_theme_row.set_selected(0)
                self._cat_light_theme_row.set_visible(cat_enabled and is_auto_mode)

            # Update manual theme selection
            if self._cat_theme_row is not None:
                current_theme = settings.get("pygments_theme", "monokai").lower()
                try:
                    theme_index = self._cat_theme_names.index(current_theme)
                    self._cat_theme_row.set_selected(theme_index)
                except ValueError:
                    self._cat_theme_row.set_selected(0)
                self._cat_theme_row.set_visible(cat_enabled and not is_auto_mode)

            self._cat_theme_mode_row.set_visible(cat_enabled)
        elif self._cat_theme_row is not None:
            current_theme = settings.get("pygments_theme", "monokai").lower()
            try:
                theme_index = self._cat_theme_names.index(current_theme)
                self._cat_theme_row.set_selected(theme_index)
            except ValueError:
                self._cat_theme_row.set_selected(0)
            # Update sensitivity based on toggle state
            self._cat_theme_row.set_sensitive(cat_enabled)

        self._populate_rules()
        self._populate_contexts()

    def _populate_contexts(self) -> None:
        """Populate the context list with toggle rows for each context."""
        # Clear existing context rows
        for row in list(self._context_rows.values()):
            self._context_list_group.remove(row)
        self._context_rows.clear()

        # Get all contexts sorted by name
        context_names = sorted(self._manager.get_context_names())

        # Count enabled contexts
        enabled_count = sum(
            1
            for name in context_names
            if self._manager.get_context(name)
            and self._manager.get_context(name).enabled
        )

        # Update selector group description
        self._context_selector_group.set_description(
            _("{total} command(s), {enabled} enabled").format(
                total=len(context_names), enabled=enabled_count
            )
        )

        # Create a row for each context
        for name in context_names:
            ctx = self._manager.get_context(name)
            if not ctx:
                continue

            row = self._create_context_list_row(name, ctx)
            self._context_rows[name] = row
            self._context_list_group.add(row)

    def _create_context_list_row(self, name: str, ctx) -> Adw.ActionRow:
        """Create a row for a context in the list with inline edit, delete, switch buttons."""
        rule_count = len(ctx.rules)

        trigger_info = ", ".join(ctx.triggers)
        row = Adw.ActionRow(
            title=trigger_info,
            subtitle=_("{count} rules").format(count=rule_count),
        )
        row.set_activatable(False)  # Not clickable as full row

        # Terminal icon prefix (uses bundled icon)
        icon = icon_image("utilities-terminal-symbolic")
        icon.set_opacity(0.6)
        row.add_prefix(icon)

        # Edit button (icon)
        edit_btn = Gtk.Button(icon_name="document-edit-symbolic")
        edit_btn.add_css_class("flat")
        edit_btn.set_valign(Gtk.Align.CENTER)
        get_tooltip_helper().add_tooltip(edit_btn, _("Edit command rules"))
        edit_btn.connect("clicked", self._on_edit_context_clicked, name)
        row.add_suffix(edit_btn)

        # Delete button (icon) - only for user-modified contexts
        if self._manager.has_user_context_override(name):
            delete_btn = Gtk.Button(icon_name="user-trash-symbolic")
            delete_btn.add_css_class("flat")
            delete_btn.set_valign(Gtk.Align.CENTER)
            get_tooltip_helper().add_tooltip(delete_btn, _("Delete command"))
            delete_btn.connect("clicked", self._on_delete_context_clicked, name)
            row.add_suffix(delete_btn)

        # Enable/disable switch (rightmost)
        switch = Gtk.Switch()
        switch.set_valign(Gtk.Align.CENTER)
        switch.set_active(ctx.enabled)
        switch.connect("state-set", self._on_context_toggle, name)
        row.add_suffix(switch)

        # Store switch reference for later updates
        row._context_switch = switch

        return row

    def _on_edit_context_clicked(self, button: Gtk.Button, context_name: str) -> None:
        """Handle edit context button click."""
        self._open_context_dialog(context_name)

    def _on_delete_context_clicked(self, button: Gtk.Button, context_name: str) -> None:
        """Handle delete context button click."""
        ctx = self._manager.get_context(context_name)
        if not ctx or not self._manager.has_user_context_override(context_name):
            return

        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Delete Command?"),
            body=_(
                'Are you sure you want to delete "{}"? This will remove all custom rules for this command.'
            ).format(context_name),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_delete_context_confirmed, context_name)
        dialog.present()

    def _on_delete_context_confirmed(self, dialog: Adw.MessageDialog, response: str, context_name: str) -> None:
        """Handle delete context confirmation."""
        dialog.close()
        if response == "delete":
            if self._manager.delete_user_context(context_name):
                self._populate_contexts()
                self.emit("settings-changed")
                self.add_toast(
                    Adw.Toast(title=_("Command deleted: {}").format(context_name))
                )

    def _on_context_toggle(
        self, switch: Gtk.Switch, state: bool, context_name: str
    ) -> bool:
        """Handle context toggle from the list."""
        self._manager.set_context_enabled(context_name, state)
        self._manager.save_config()

        # Update the description count
        context_names = self._manager.get_context_names()
        enabled_count = sum(
            1 for name in context_names
            if self._manager.get_context(name) and self._manager.get_context(name).enabled
        )
        self._context_selector_group.set_description(
            _("{total} command(s), {enabled} enabled").format(
                total=len(context_names), enabled=enabled_count
            )
        )

        # If this is the selected context, update its detail view
        if context_name == self._selected_context:
            self._context_enable_row.handler_block_by_func(
                self._on_context_enable_toggled
            )
            self._context_enable_row.set_active(state)
            self._context_enable_row.handler_unblock_by_func(
                self._on_context_enable_toggled
            )

        self.emit("settings-changed")
        return False  # Don't block the default handler

    def _on_context_row_activated(self, row: Adw.ActionRow, context_name: str) -> None:
        """Handle context row activation - open context rules dialog."""
        self._open_context_dialog(context_name)

    def _open_context_dialog(self, context_name: str) -> None:
        """Open the context rules dialog for a specific context."""
        dialog = ContextRulesDialog(self, context_name)
        dialog.connect("context-updated", self._on_context_dialog_updated)
        dialog.present(self)

    def _on_context_dialog_updated(self, dialog) -> None:
        """Handle updates from the context rules dialog."""
        self._populate_contexts()
        self.emit("settings-changed")

    def _select_context(self, context_name: str) -> None:
        """Select a context for editing."""
        self._selected_context = context_name

        # Update visual selection (highlight the selected row)
        for name, row in self._context_rows.items():
            if name == context_name:
                row.add_css_class("accent")
            else:
                row.remove_css_class("accent")

        # Enable reset button
        self._reset_context_row.set_sensitive(bool(self._selected_context))

        # Update the context rules section
        self._populate_context_rules()

    def _on_enable_all_contexts(self, button: Gtk.Button) -> None:
        """Enable all contexts."""
        for name in self._manager.get_context_names():
            self._manager.set_context_enabled(name, True)
        self._manager.save_config()
        self._populate_contexts()
        self.emit("settings-changed")

    def _on_disable_all_contexts(self, button: Gtk.Button) -> None:
        """Disable all contexts."""
        for name in self._manager.get_context_names():
            self._manager.set_context_enabled(name, False)
        self._manager.save_config()
        self._populate_contexts()
        self.emit("settings-changed")

    def _populate_context_rules(self) -> None:
        """Populate rules for the selected context."""
        # Clear existing context rule rows
        for row in self._context_rule_rows:
            self._context_rules_list_group.remove(row)
        self._context_rule_rows.clear()

        if not self._selected_context:
            self._context_enable_row.set_sensitive(False)
            self._use_global_rules_row.set_sensitive(False)
            self._add_context_rule_row.set_sensitive(False)
            self._reset_context_row.set_sensitive(False)
            # Update header
            self._context_header_row.set_title(_("No command selected"))
            self._context_header_row.set_subtitle(
                _("Select a command from the list above")
            )
            self._context_rules_list_group.set_description(
                _("Select a command to view its rules")
            )
            return

        self._context_enable_row.set_sensitive(True)
        self._use_global_rules_row.set_sensitive(True)
        self._add_context_rule_row.set_sensitive(True)
        self._reset_context_row.set_sensitive(True)

        # Get context
        context = self._manager.get_context(self._selected_context)
        if not context:
            self._context_header_row.set_title(self._selected_context)
            self._context_header_row.set_subtitle(_("Command not found"))
            return

        # Update header with context info
        trigger_info = ", ".join(context.triggers[:3])
        if len(context.triggers) > 3:
            trigger_info += "..."
        self._context_header_row.set_title(self._selected_context)
        self._context_header_row.set_subtitle(
            _("Triggers: {triggers}").format(triggers=trigger_info)
        )

        # Update rules group description
        status = _("Enabled") if context.enabled else _("Disabled")
        rule_count = len(context.rules)
        self._context_rules_list_group.set_description(
            _("{count} rule(s) • {status} • Use arrows to reorder").format(
                count=rule_count, status=status
            )
        )

        # Block signal handler during programmatic update
        self._context_enable_row.handler_block_by_func(self._on_context_enable_toggled)
        self._context_enable_row.set_active(context.enabled)
        self._context_enable_row.handler_unblock_by_func(
            self._on_context_enable_toggled
        )

        # Update use global rules toggle
        self._use_global_rules_row.handler_block_by_func(
            self._on_use_global_rules_toggled
        )
        self._use_global_rules_row.set_active(context.use_global_rules)
        self._use_global_rules_row.handler_unblock_by_func(
            self._on_use_global_rules_toggled
        )

        # Add rule rows for this context with reorder buttons
        for index, rule in enumerate(context.rules):
            row = self._create_context_rule_row(rule, index, len(context.rules))
            self._context_rules_list_group.add(row)
            self._context_rule_rows.append(row)

    def _get_rule_color_display(self, rule: HighlightRule) -> str:
        """Get the first color from a rule for display."""
        if rule.colors and rule.colors[0]:
            return self._manager.resolve_color(rule.colors[0])
        return "#ffffff"

    def _create_context_rule_row(self, rule: HighlightRule, index: int, total_rules: int = 0) -> Adw.ExpanderRow:
        """Create an expander row for a context-specific rule with reorder buttons."""
        # Escape markup characters to prevent GTK parsing errors
        escaped_name = GLib.markup_escape_text(rule.name)
        subtitle_text = rule.description if rule.description else (
            rule.pattern[:40] + "..." if len(rule.pattern) > 40 else rule.pattern
        )
        escaped_subtitle = GLib.markup_escape_text(subtitle_text)

        row = Adw.ExpanderRow()
        row.set_title(f"#{index + 1} {escaped_name}")
        row.set_subtitle(escaped_subtitle)

        # Reorder buttons prefix (box with up/down arrows)
        reorder_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        reorder_box.set_valign(Gtk.Align.CENTER)
        reorder_box.set_margin_end(4)

        up_btn = Gtk.Button(icon_name="go-up-symbolic")
        up_btn.add_css_class("flat")
        up_btn.add_css_class("circular")
        up_btn.set_size_request(24, 24)
        up_btn.set_sensitive(index > 0)
        up_btn.connect("clicked", self._on_move_rule_up, index)
        get_tooltip_helper().add_tooltip(up_btn, _("Move up"))
        reorder_box.append(up_btn)

        down_btn = Gtk.Button(icon_name="go-down-symbolic")
        down_btn.add_css_class("flat")
        down_btn.add_css_class("circular")
        down_btn.set_size_request(24, 24)
        down_btn.set_sensitive(index < total_rules - 1)
        down_btn.connect("clicked", self._on_move_rule_down, index)
        get_tooltip_helper().add_tooltip(down_btn, _("Move down"))
        reorder_box.append(down_btn)

        row.add_prefix(reorder_box)

        # Color indicator (shows first color)
        color_box = Gtk.Box()
        color_box.set_size_request(16, 16)
        color_box.add_css_class("circular")
        self._apply_color_to_box(color_box, self._get_rule_color_display(rule))
        color_box.set_margin_end(8)
        color_box.set_valign(Gtk.Align.CENTER)
        row.add_prefix(color_box)

        # Colors count badge
        if rule.colors and len(rule.colors) > 1:
            colors_badge = Gtk.Label(label=f"{len(rule.colors)}")
            colors_badge.add_css_class("dim-label")
            colors_badge.set_valign(Gtk.Align.CENTER)
            get_tooltip_helper().add_tooltip(
                colors_badge, _("{} colors").format(len(rule.colors))
            )
            row.add_suffix(colors_badge)

        # Enable/disable switch suffix
        switch = Gtk.Switch()
        switch.set_active(rule.enabled)
        switch.set_valign(Gtk.Align.CENTER)
        switch.connect("notify::active", self._on_context_rule_switch_toggled, index)
        row.add_suffix(switch)

        # Expanded content - action buttons
        actions_row = Adw.ActionRow(title=_("Actions"))

        edit_btn = Gtk.Button(label=_("Edit"))
        edit_btn.set_valign(Gtk.Align.CENTER)
        edit_btn.connect("clicked", self._on_edit_context_rule_clicked, index)
        actions_row.add_suffix(edit_btn)

        delete_btn = Gtk.Button(label=_("Delete"))
        delete_btn.set_valign(Gtk.Align.CENTER)
        delete_btn.add_css_class("destructive-action")
        delete_btn.connect("clicked", self._on_delete_context_rule_clicked, index)
        actions_row.add_suffix(delete_btn)

        row.add_row(actions_row)

        return row

    def _on_move_rule_up(self, button: Gtk.Button, index: int) -> None:
        """Move a rule up in the order."""
        if not self._selected_context or index <= 0:
            return
        self._manager.move_context_rule(self._selected_context, index, index - 1)
        self._manager.save_config()
        self._populate_context_rules()
        self.emit("settings-changed")

    def _on_move_rule_down(self, button: Gtk.Button, index: int) -> None:
        """Move a rule down in the order."""
        if not self._selected_context:
            return
        ctx = self._manager.get_context(self._selected_context)
        if not ctx or index >= len(ctx.rules) - 1:
            return
        self._manager.move_context_rule(self._selected_context, index, index + 1)
        self._manager.save_config()
        self._populate_context_rules()
        self.emit("settings-changed")

    def _on_context_aware_toggled(self, switch: Adw.SwitchRow, _pspec) -> None:
        """Handle context-aware toggle."""
        self._manager.context_aware_enabled = switch.get_active()
        self._manager.save_config()
        self.emit("settings-changed")

    def _on_context_enable_toggled(self, switch: Adw.SwitchRow, _pspec) -> None:
        """Handle context enable toggle from detail view."""
        if self._selected_context:
            state = switch.get_active()
            self._manager.set_context_enabled(self._selected_context, state)
            self._manager.save_config()

            # Update the list row switch
            if self._selected_context in self._context_rows:
                row = self._context_rows[self._selected_context]
                # The switch is the first child of prefix
                for child in row:
                    if isinstance(child, Gtk.Switch):
                        child.handler_block_by_func(self._on_context_toggle)
                        child.set_active(state)
                        child.handler_unblock_by_func(self._on_context_toggle)
                        break

            # Update the description count
            context_names = self._manager.get_context_names()
            enabled_count = sum(
                1
                for name in context_names
                if self._manager.get_context(name)
                and self._manager.get_context(name).enabled
            )
            self._context_selector_group.set_description(
                _(
                    "{total} command(s), {enabled} enabled. Click to toggle, select to edit."
                ).format(total=len(context_names), enabled=enabled_count)
            )

            self.emit("settings-changed")

    def _on_use_global_rules_toggled(self, switch: Adw.SwitchRow, _pspec) -> None:
        """Handle use global rules toggle."""
        if self._selected_context:
            self._manager.set_context_use_global_rules(
                self._selected_context, switch.get_active()
            )
            self._manager.save_config()
            self.emit("settings-changed")

    def _on_context_rule_switch_toggled(self, switch: Gtk.Switch, _pspec, index: int) -> None:
        """Handle context rule enable/disable toggle."""
        if self._selected_context:
            self._manager.set_context_rule_enabled(
                self._selected_context, index, switch.get_active()
            )
            self._manager.save_config()
            self.emit("settings-changed")

    def _on_add_context_clicked(self, button: Gtk.Button) -> None:
        """Handle add context button click."""
        dialog = ContextNameDialog(self)
        dialog.connect("context-created", self._on_context_created)
        dialog.present(self)

    def _on_context_created(self, dialog, context_name: str) -> None:
        """Handle new context creation."""
        context = HighlightContext(
            command_name=context_name,
            triggers=[context_name],
            rules=[],
            enabled=True,
            description=f"Custom rules for {context_name}",
        )
        self._manager.add_context(context)
        self._manager.save_context_to_user(context)
        self._populate_contexts()

        self.emit("settings-changed")
        self.add_toast(Adw.Toast(title=_("Command created: {}").format(context_name)))

        # Open the dialog for the new context so user can add rules
        self._open_context_dialog(context_name)

    def _on_reset_context_clicked(self, button: Gtk.Button) -> None:
        """Handle reset context to system default button click."""
        if not self._selected_context:
            return

        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Reset to System Default?"),
            body=_('This will remove your customizations for "{}" and revert to system rules.').format(
                self._selected_context
            ),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("reset", _("Reset"))
        dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_reset_context_confirmed)
        dialog.present()

    def _on_reset_context_confirmed(
        self, dialog: Adw.MessageDialog, response: str
    ) -> None:
        """Handle reset context confirmation."""
        dialog.close()
        if response == "reset" and self._selected_context:
            name = self._selected_context
            if self._manager.delete_user_context(name):
                self._populate_contexts()
                self.emit("settings-changed")
                self.add_toast(Adw.Toast(title=_("Command reset: {}").format(name)))
            else:
                self.add_toast(Adw.Toast(title=_("No user customization to reset")))

    def _on_add_context_rule_clicked(self, button: Gtk.Button) -> None:
        """Handle add rule to context button click."""
        if not self._selected_context:
            return

        dialog = RuleEditDialog(self, is_new=True)
        dialog.connect("rule-saved", self._on_context_rule_saved)
        dialog.present(self)

    def _on_context_rule_saved(self, dialog: RuleEditDialog, rule: HighlightRule) -> None:
        """Handle saving a new context rule."""
        if self._selected_context:
            self._manager.add_rule_to_context(self._selected_context, rule)
            # Save to user directory to create override
            context = self._manager.get_context(self._selected_context)
            if context:
                self._manager.save_context_to_user(context)
            self._populate_context_rules()
            self.emit("settings-changed")
            self.add_toast(Adw.Toast(title=_("Rule added: {}").format(rule.name)))

    def _on_edit_context_rule_clicked(self, button: Gtk.Button, index: int) -> None:
        """Handle edit context rule button click."""
        if not self._selected_context:
            return

        context = self._manager.get_context(self._selected_context)
        if context and 0 <= index < len(context.rules):
            rule = context.rules[index]
            dialog = RuleEditDialog(self, rule=rule, is_new=False)
            dialog.connect("rule-saved", self._on_context_rule_edited, index)
            dialog.present(self)

    def _on_context_rule_edited(
        self, dialog: RuleEditDialog, rule: HighlightRule, index: int
    ) -> None:
        """Handle saving an edited context rule."""
        if self._selected_context:
            self._manager.update_context_rule(self._selected_context, index, rule)
            # Save to user directory to create override
            context = self._manager.get_context(self._selected_context)
            if context:
                self._manager.save_context_to_user(context)
            self._populate_context_rules()
            self.emit("settings-changed")
            self.add_toast(Adw.Toast(title=_("Rule updated: {}").format(rule.name)))

    def _on_delete_context_rule_clicked(self, button: Gtk.Button, index: int) -> None:
        """Handle delete context rule button click."""
        if not self._selected_context:
            return

        context = self._manager.get_context(self._selected_context)
        if not context or index >= len(context.rules):
            return

        rule = context.rules[index]
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Delete Rule?"),
            body=_('Are you sure you want to delete "{}"?').format(rule.name),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_delete_context_rule_confirmed, index, rule.name)
        dialog.present()

    def _on_delete_context_rule_confirmed(
        self,
        dialog: Adw.MessageDialog,
        response: str,
        index: int,
        rule_name: str,
    ) -> None:
        """Handle delete context rule confirmation."""
        dialog.close()
        if response == "delete" and self._selected_context:
            self._manager.remove_context_rule(self._selected_context, index)
            # Save to user directory to create override
            context = self._manager.get_context(self._selected_context)
            if context:
                self._manager.save_context_to_user(context)
            self._populate_context_rules()
            self.emit("settings-changed")
            self.add_toast(Adw.Toast(title=_("Rule deleted: {}").format(rule_name)))

    def _update_dependent_groups_sensitivity(self) -> None:
        """Update sensitivity of highlighting groups based on activation state.
        
        When BOTH local and SSH highlighting are disabled, all output-related
        highlighting features should be disabled as well since there's no output to process.
        This includes:
        - Cat colorization group
        - Shell input highlighting group  
        - Ignored commands group
        - Global highlight rules group
        - Command-Specific page (entire page)
        """
        any_output_enabled = self._manager.enabled_for_local or self._manager.enabled_for_ssh

        # Update cat group sensitivity
        if hasattr(self, "_cat_group") and self._cat_group is not None:
            self._cat_group.set_sensitive(any_output_enabled)

        # Update shell input group sensitivity
        if hasattr(self, "_shell_input_group") and self._shell_input_group is not None:
            self._shell_input_group.set_sensitive(any_output_enabled)

        # Update ignored commands group sensitivity
        if hasattr(self, "_ignored_commands_group") and self._ignored_commands_group is not None:
            self._ignored_commands_group.set_sensitive(any_output_enabled)

        # Update global rules group sensitivity
        if hasattr(self, "_rules_group") and self._rules_group is not None:
            self._rules_group.set_sensitive(any_output_enabled)

        # Update Command-Specific page sensitivity (entire page)
        if hasattr(self, "_context_page") and self._context_page is not None:
            self._context_page.set_sensitive(any_output_enabled)

    def _on_local_toggled(self, switch: Adw.SwitchRow, _pspec) -> None:
        """Handle local terminals toggle."""
        is_active = switch.get_active()
        self._manager.enabled_for_local = is_active
        self._manager.save_config()
        self.emit("settings-changed")
        self._update_dependent_groups_sensitivity()

    def _on_ssh_toggled(self, switch: Adw.SwitchRow, _pspec) -> None:
        """Handle SSH terminals toggle."""
        is_active = switch.get_active()
        self._manager.enabled_for_ssh = is_active
        self._manager.save_config()
        self.emit("settings-changed")
        self._update_dependent_groups_sensitivity()

    def _on_cat_theme_changed(self, combo: Adw.ComboRow, _pspec) -> None:
        """Handle Pygments theme selection change."""
        selected_index = combo.get_selected()
        if selected_index >= 0 and selected_index < len(self._cat_theme_names):
            theme = self._cat_theme_names[selected_index]
            settings = get_settings_manager()
            settings.set("pygments_theme", theme)
            self.emit("settings-changed")
            self.add_toast(Adw.Toast(title=_("Theme changed to: {}").format(theme)))

    def _on_cat_colorization_toggled(self, switch: Adw.SwitchRow, _pspec) -> None:
        """Handle cat colorization toggle."""
        is_active = switch.get_active()
        settings = get_settings_manager()
        settings.set("cat_colorization_enabled", is_active)

        # Update visibility based on mode and enabled state
        if self._cat_theme_mode_row is not None:
            self._cat_theme_mode_row.set_visible(is_active)
            is_auto_mode = self._cat_theme_mode_row.get_selected() == 0
            if self._cat_dark_theme_row is not None:
                self._cat_dark_theme_row.set_visible(is_active and is_auto_mode)
            if self._cat_light_theme_row is not None:
                self._cat_light_theme_row.set_visible(is_active and is_auto_mode)
            if self._cat_theme_row is not None:
                self._cat_theme_row.set_visible(is_active and not is_auto_mode)
        elif self._cat_theme_row is not None:
            self._cat_theme_row.set_visible(is_active)

        self.emit("settings-changed")

        status = _("enabled") if is_active else _("disabled")
        self.add_toast(Adw.Toast(title=_("'{}' colorization {}").format("cat", status)))

    def _on_cat_theme_mode_changed(self, combo: Adw.ComboRow, _pspec) -> None:
        """Handle cat theme mode change (auto/manual)."""
        selected_index = combo.get_selected()
        is_auto_mode = selected_index == 0
        mode = "auto" if is_auto_mode else "manual"

        settings = get_settings_manager()
        settings.set("cat_theme_mode", mode)

        # Update visibility of theme dropdowns
        if self._cat_dark_theme_row is not None:
            self._cat_dark_theme_row.set_visible(is_auto_mode)
        if self._cat_light_theme_row is not None:
            self._cat_light_theme_row.set_visible(is_auto_mode)
        if self._cat_theme_row is not None:
            self._cat_theme_row.set_visible(not is_auto_mode)

        self.emit("settings-changed")
        mode_name = _("Auto") if is_auto_mode else _("Manual")
        self.add_toast(Adw.Toast(title=_("Cat theme mode: {}").format(mode_name)))

    def _on_cat_dark_theme_changed(self, combo: Adw.ComboRow, _pspec) -> None:
        """Handle cat dark theme selection change."""
        selected_index = combo.get_selected()
        if selected_index >= 0 and selected_index < len(self._cat_dark_theme_names):
            theme = self._cat_dark_theme_names[selected_index]
            settings = get_settings_manager()
            settings.set("cat_dark_theme", theme)
            self.emit("settings-changed")
            self.add_toast(Adw.Toast(title=_("Dark theme: {}").format(theme)))

    def _on_cat_light_theme_changed(self, combo: Adw.ComboRow, _pspec) -> None:
        """Handle cat light theme selection change."""
        selected_index = combo.get_selected()
        if selected_index >= 0 and selected_index < len(self._cat_light_theme_names):
            theme = self._cat_light_theme_names[selected_index]
            settings = get_settings_manager()
            settings.set("cat_light_theme", theme)
            self.emit("settings-changed")
            self.add_toast(Adw.Toast(title=_("Light theme: {}").format(theme)))

    def _show_restart_required_dialog(self) -> None:
        """Show a dialog informing user that restart is required for changes to take effect."""
        dialog = Adw.AlertDialog(
            heading=_("Restart Required"),
            body=_("Restart the program for the colors to be applied to the terminal."),
        )
        dialog.add_response("ok", _("OK"))
        dialog.set_default_response("ok")
        dialog.present(self)

    def _populate_rules(self) -> None:
        """Populate the global rules list from the manager."""
        # Clear existing rule rows
        for row in self._rule_rows:
            self._rules_group.remove(row)
        self._rule_rows.clear()

        # Add rules
        for index, rule in enumerate(self._manager.rules):
            row = self._create_rule_row(rule, index)
            self._rules_group.add(row)
            self._rule_rows.append(row)

    def _create_rule_row(self, rule: HighlightRule, index: int) -> Adw.ActionRow:
        """Create an action row for a highlight rule with inline edit/delete icons."""
        # Escape markup characters to prevent GTK parsing errors
        escaped_name = GLib.markup_escape_text(rule.name)
        subtitle_text = rule.description if rule.description else (
            rule.pattern[:40] + "..." if len(rule.pattern) > 40 else rule.pattern
        )
        escaped_subtitle = GLib.markup_escape_text(subtitle_text)

        row = Adw.ActionRow()
        row.set_title(escaped_name)
        row.set_subtitle(escaped_subtitle)

        # Color indicator prefix (shows first color)
        color_box = Gtk.Box()
        color_box.set_size_request(16, 16)
        color_box.add_css_class("circular")
        self._apply_color_to_box(color_box, self._get_rule_color_display(rule))
        color_box.set_margin_end(8)
        color_box.set_valign(Gtk.Align.CENTER)
        row.add_prefix(color_box)

        # Colors count badge
        if rule.colors and len(rule.colors) > 1:
            colors_badge = Gtk.Label(label=f"{len(rule.colors)}")
            colors_badge.add_css_class("dim-label")
            colors_badge.set_valign(Gtk.Align.CENTER)
            get_tooltip_helper().add_tooltip(
                colors_badge, _("{} colors").format(len(rule.colors))
            )
            row.add_suffix(colors_badge)

        # Edit button (icon)
        edit_btn = Gtk.Button(icon_name="document-edit-symbolic")
        edit_btn.add_css_class("flat")
        edit_btn.set_valign(Gtk.Align.CENTER)
        get_tooltip_helper().add_tooltip(edit_btn, _("Edit rule"))
        edit_btn.connect("clicked", self._on_edit_rule_clicked, index)
        row.add_suffix(edit_btn)

        # Delete button (icon)
        delete_btn = Gtk.Button(icon_name="user-trash-symbolic")
        delete_btn.add_css_class("flat")
        delete_btn.set_valign(Gtk.Align.CENTER)
        get_tooltip_helper().add_tooltip(delete_btn, _("Delete rule"))
        delete_btn.connect("clicked", self._on_delete_rule_clicked, index)
        row.add_suffix(delete_btn)

        # Enable/disable switch suffix (rightmost)
        switch = Gtk.Switch()
        switch.set_active(rule.enabled)
        switch.set_valign(Gtk.Align.CENTER)
        switch.connect("notify::active", self._on_rule_switch_toggled, index)
        row.add_suffix(switch)

        # Store index for reference
        row._rule_index = index
        row._rule_switch = switch
        row._color_box = color_box

        return row

    def _apply_color_to_box(self, box: Gtk.Box, hex_color: str) -> None:
        """Apply a color as background to a box widget."""
        css_provider = Gtk.CssProvider()
        css = f"""
        .rule-color-indicator {{
            background-color: {hex_color};
            border-radius: 50%;
            border: 1px solid alpha(currentColor, 0.3);
        }}
        """
        css_provider.load_from_data(css.encode("utf-8"))

        context = box.get_style_context()
        # Store and remove old provider
        if hasattr(box, "_css_provider"):
            context.remove_provider(box._css_provider)

        context.add_provider(css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        box.add_css_class("rule-color-indicator")
        box._css_provider = css_provider

    def _on_rule_switch_toggled(self, switch: Gtk.Switch, _pspec, index: int) -> None:
        """Handle rule enable/disable toggle."""
        self._manager.set_rule_enabled(index, switch.get_active())
        self._manager.save_global_rules_to_user()  # Save full rules to user file
        self._manager.save_config()
        self.emit("settings-changed")

    def _on_add_rule_clicked(self, button: Gtk.Button) -> None:
        """Handle add rule button click."""
        dialog = RuleEditDialog(self, is_new=True)
        dialog.connect("rule-saved", self._on_new_rule_saved)
        dialog.present(self)

    def _on_new_rule_saved(self, dialog: RuleEditDialog, rule: HighlightRule) -> None:
        """Handle saving a new rule."""
        self._manager.add_rule(rule)
        self._manager.save_global_rules_to_user()  # Save full rules to user file
        self._manager.save_config()
        self._populate_rules()
        self.emit("settings-changed")

        self.add_toast(Adw.Toast(title=_("Rule added: {}").format(rule.name)))

    def _on_edit_rule_clicked(self, button: Gtk.Button, index: int) -> None:
        """Handle edit rule button click."""
        rule = self._manager.get_rule(index)
        if rule:
            dialog = RuleEditDialog(self, rule=rule, is_new=False)
            dialog.connect("rule-saved", self._on_rule_edited, index)
            dialog.present(self)

    def _on_rule_edited(
        self, dialog: RuleEditDialog, rule: HighlightRule, index: int
    ) -> None:
        """Handle saving an edited rule."""
        self._manager.update_rule(index, rule)
        self._manager.save_global_rules_to_user()  # Save full rules to user file
        self._manager.save_config()
        self._populate_rules()
        self.emit("settings-changed")

        self.add_toast(Adw.Toast(title=_("Rule updated: {}").format(rule.name)))

    def _on_delete_rule_clicked(self, button: Gtk.Button, index: int) -> None:
        """Handle delete rule button click."""
        rule = self._manager.get_rule(index)
        if not rule:
            return

        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Delete Rule?"),
            body=_('Are you sure you want to delete "{}"?').format(rule.name),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_delete_confirmed, index, rule.name)
        dialog.present()

    def _on_delete_confirmed(
        self,
        dialog: Adw.MessageDialog,
        response: str,
        index: int,
        rule_name: str,
    ) -> None:
        """Handle delete confirmation response."""
        dialog.close()
        if response == "delete":
            self._manager.remove_rule(index)
            self._manager.save_global_rules_to_user()  # Save full rules to user file
            self._manager.save_config()
            self._populate_rules()
            self.emit("settings-changed")

            self.add_toast(Adw.Toast(title=_("Rule deleted: {}").format(rule_name)))

    def _on_reset_global_rules_clicked(self, button: Gtk.Button) -> None:
        """Handle reset global rules button click."""
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Reset Global Rules?"),
            body=_(
                "This will restore global rules to system defaults. Context customizations will be preserved."
            ),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("reset", _("Reset"))
        dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_reset_global_rules_confirmed)
        dialog.present()

    def _on_reset_global_rules_confirmed(
        self, dialog: Adw.MessageDialog, response: str
    ) -> None:
        """Handle reset global rules confirmation response."""
        dialog.close()
        if response == "reset":
            self._manager.reset_global_rules()
            self._manager.save_config()
            self._populate_rules()
            self.emit("settings-changed")

            self.add_toast(Adw.Toast(title=_("Global rules reset to defaults")))

    def _on_reset_all_contexts_clicked(self, button: Gtk.Button) -> None:
        """Handle reset all contexts button click."""
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Reset All Commands?"),
            body=_(
                "This will restore all commands to system defaults. Global rules will be preserved."
            ),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("reset", _("Reset"))
        dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_reset_all_contexts_confirmed)
        dialog.present()

    def _on_reset_all_contexts_confirmed(
        self, dialog: Adw.MessageDialog, response: str
    ) -> None:
        """Handle reset all contexts confirmation response."""
        dialog.close()
        if response == "reset":
            self._manager.reset_all_contexts()
            self._manager.save_config()
            self._populate_contexts()
            self.emit("settings-changed")

            self.add_toast(Adw.Toast(title=_("All commands reset to defaults")))


class ContextNameDialog(Adw.Dialog):
    """
    Dialog for creating a new command context.

    Provides a simple form to enter the command name for a new context.
    """

    __gsignals__ = {
        "context-created": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, parent: Gtk.Widget):
        """
        Initialize the context name dialog.

        Args:
            parent: Parent widget for the dialog.
        """
        super().__init__()
        self.add_css_class("zashterminal-dialog")
        self.logger = get_logger("zashterminal.ui.dialogs.context_name")
        self._parent = parent
        self._manager = get_highlight_manager()

        self.set_title(_("New Command"))
        self.set_content_width(350)
        self.set_content_height(200)

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the dialog UI components."""
        # Main container with toolbar view
        toolbar_view = Adw.ToolbarView()
        self.set_child(toolbar_view)

        # Header bar
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)

        # Cancel button
        cancel_btn = Gtk.Button(label=_("Cancel"))
        cancel_btn.connect("clicked", lambda b: self.close())
        header.pack_start(cancel_btn)

        # Create button
        self._create_btn = Gtk.Button(label=_("Create"))
        self._create_btn.add_css_class("suggested-action")
        self._create_btn.set_sensitive(False)
        self._create_btn.connect("clicked", self._on_create_clicked)
        header.pack_end(self._create_btn)

        toolbar_view.add_top_bar(header)

        # Content
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        content_box.set_margin_start(24)
        content_box.set_margin_end(24)
        content_box.set_margin_top(24)
        content_box.set_margin_bottom(24)
        toolbar_view.set_content(content_box)

        # Command name entry
        name_group = Adw.PreferencesGroup(
            description=_("Enter the command name (e.g., ping, docker, git)")
        )
        self._name_row = Adw.EntryRow(title=_("Command Name"))
        self._name_row.connect("changed", self._on_name_changed)
        name_group.add(self._name_row)
        content_box.append(name_group)

        # Validation label
        self._validation_label = Gtk.Label()
        self._validation_label.set_xalign(0)
        self._validation_label.add_css_class("dim-label")
        content_box.append(self._validation_label)

    def _on_name_changed(self, entry: Adw.EntryRow) -> None:
        """Handle name entry change."""
        name = entry.get_text().strip().lower()

        if not name:
            self._validation_label.set_text(_("Enter a command name"))
            self._validation_label.remove_css_class("error")
            self._create_btn.set_sensitive(False)
            return

        # Check if context already exists
        if name in self._manager.get_context_names():
            self._validation_label.set_text(_("Command already exists"))
            self._validation_label.add_css_class("error")
            self._create_btn.set_sensitive(False)
            return

        # Validate name (alphanumeric + underscore)
        if not name.replace("_", "").replace("-", "").isalnum():
            self._validation_label.set_text(_("Use only letters, numbers, - and _"))
            self._validation_label.add_css_class("error")
            self._create_btn.set_sensitive(False)
            return

        self._validation_label.set_text(_("✓ Valid name"))
        self._validation_label.remove_css_class("error")
        self._validation_label.add_css_class("success")
        self._create_btn.set_sensitive(True)

    def _on_create_clicked(self, button: Gtk.Button) -> None:
        """Handle create button click."""
        name = self._name_row.get_text().strip().lower()
        self.emit("context-created", name)
        self.close()


class AddTriggerDialog(Adw.Dialog):
    """
    Dialog for adding or editing a trigger command for a context.

    Triggers are command names that activate the highlighting context.
    """

    __gsignals__ = {
        "trigger-added": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, parent: Gtk.Widget, context_name: str, existing_trigger: str = None):
        """
        Initialize the add/edit trigger dialog.

        Args:
            parent: Parent widget for the dialog.
            context_name: Name of the context this trigger belongs to.
            existing_trigger: If editing, the current trigger name.
        """
        super().__init__()
        self.add_css_class("zashterminal-dialog")
        self.logger = get_logger("zashterminal.ui.dialogs.add_trigger")
        self._parent = parent
        self._context_name = context_name
        self._existing_trigger = existing_trigger
        self._manager = get_highlight_manager()

        title = _("Edit Trigger") if existing_trigger else _("Add Trigger")
        self.set_title(title)
        self.set_content_width(350)
        self.set_content_height(200)

        self._setup_ui()

        if existing_trigger:
            self._name_row.set_text(existing_trigger)

    def _setup_ui(self) -> None:
        """Setup the dialog UI components."""
        toolbar_view = Adw.ToolbarView()
        self.set_child(toolbar_view)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)

        cancel_btn = Gtk.Button(label=_("Cancel"))
        cancel_btn.connect("clicked", lambda b: self.close())
        header.pack_start(cancel_btn)

        btn_label = _("Save") if self._existing_trigger else _("Add")
        self._save_btn = Gtk.Button(label=btn_label)
        self._save_btn.add_css_class("suggested-action")
        self._save_btn.set_sensitive(False)
        self._save_btn.connect("clicked", self._on_save_clicked)
        header.pack_end(self._save_btn)

        toolbar_view.add_top_bar(header)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        content_box.set_margin_start(24)
        content_box.set_margin_end(24)
        content_box.set_margin_top(24)
        content_box.set_margin_bottom(24)
        toolbar_view.set_content(content_box)

        name_group = Adw.PreferencesGroup(
            description=_(
                "Enter a command name that should activate the '{}' command rules."
            ).format(self._context_name)
        )
        self._name_row = Adw.EntryRow(title=_("Trigger Command"))
        self._name_row.connect("changed", self._on_name_changed)
        name_group.add(self._name_row)
        content_box.append(name_group)

        self._validation_label = Gtk.Label()
        self._validation_label.set_xalign(0)
        self._validation_label.add_css_class("dim-label")
        content_box.append(self._validation_label)

    def _on_name_changed(self, entry: Adw.EntryRow) -> None:
        """Handle name entry change."""
        name = entry.get_text().strip().lower()

        if not name:
            self._validation_label.set_text(_("Enter a command name"))
            self._validation_label.remove_css_class("error")
            self._save_btn.set_sensitive(False)
            return

        # Check if already in triggers (unless editing the same one)
        context = self._manager.get_context(self._context_name)
        if context and name in context.triggers and name != self._existing_trigger:
            self._validation_label.set_text(_("Trigger already exists"))
            self._validation_label.add_css_class("error")
            self._save_btn.set_sensitive(False)
            return

        self._validation_label.set_text(_("✓ Valid trigger"))
        self._validation_label.remove_css_class("error")
        self._validation_label.add_css_class("success")
        self._save_btn.set_sensitive(True)

    def _on_save_clicked(self, button: Gtk.Button) -> None:
        """Handle save button click."""
        name = self._name_row.get_text().strip().lower()
        self.emit("trigger-added", name)
        self.close()


class AddIgnoredCommandDialog(Adw.Dialog):
    """
    Dialog for adding a command to the ignored list.

    Commands in the ignored list will have highlighting disabled
    to preserve their native ANSI coloring.
    """

    __gsignals__ = {
        "command-added": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, parent: Gtk.Widget):
        """
        Initialize the add ignored command dialog.

        Args:
            parent: Parent widget for the dialog.
        """
        super().__init__()
        self.add_css_class("zashterminal-dialog")
        self.logger = get_logger("zashterminal.ui.dialogs.add_ignored_cmd")
        self._parent = parent

        self.set_title(_("Add Ignored Command"))
        self.set_content_width(350)
        self.set_content_height(200)

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the dialog UI components."""
        # Main container with toolbar view
        toolbar_view = Adw.ToolbarView()
        self.set_child(toolbar_view)

        # Header bar
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)

        # Cancel button
        cancel_btn = Gtk.Button(label=_("Cancel"))
        cancel_btn.connect("clicked", lambda b: self.close())
        header.pack_start(cancel_btn)

        # Add button
        self._add_btn = Gtk.Button(label=_("Add"))
        self._add_btn.add_css_class("suggested-action")
        self._add_btn.set_sensitive(False)
        self._add_btn.connect("clicked", self._on_add_clicked)
        header.pack_end(self._add_btn)

        toolbar_view.add_top_bar(header)

        # Content
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        content_box.set_margin_start(24)
        content_box.set_margin_end(24)
        content_box.set_margin_top(24)
        content_box.set_margin_bottom(24)
        toolbar_view.set_content(content_box)

        # Command name entry
        name_group = Adw.PreferencesGroup(
            description=_("Commands with native coloring (grep, ls, git, etc.) should be added here.")
        )
        self._name_row = Adw.EntryRow(title=_("Command Name"))
        self._name_row.connect("changed", self._on_name_changed)
        name_group.add(self._name_row)
        content_box.append(name_group)

        # Validation label
        self._validation_label = Gtk.Label()
        self._validation_label.set_xalign(0)
        self._validation_label.add_css_class("dim-label")
        content_box.append(self._validation_label)

    def _on_name_changed(self, entry: Adw.EntryRow) -> None:
        """Handle name entry change."""
        name = entry.get_text().strip().lower()

        if not name:
            self._validation_label.set_text(_("Enter a command name"))
            self._validation_label.remove_css_class("error")
            self._add_btn.set_sensitive(False)
            return

        # Check if already in list
        settings = get_settings_manager()
        ignored_commands = settings.get("ignored_highlight_commands", [])
        if name in ignored_commands:
            self._validation_label.set_text(_("Command already in list"))
            self._validation_label.add_css_class("error")
            self._add_btn.set_sensitive(False)
            return

        # Validate name (alphanumeric + underscore + hyphen)
        if not name.replace("_", "").replace("-", "").isalnum():
            self._validation_label.set_text(_("Use only letters, numbers, - and _"))
            self._validation_label.add_css_class("error")
            self._add_btn.set_sensitive(False)
            return

        self._validation_label.set_text(_("✓ Valid command name"))
        self._validation_label.remove_css_class("error")
        self._validation_label.add_css_class("success")
        self._add_btn.set_sensitive(True)

    def _on_add_clicked(self, button: Gtk.Button) -> None:
        """Handle add button click."""
        name = self._name_row.get_text().strip().lower()
        self.emit("command-added", name)
        self.close()
