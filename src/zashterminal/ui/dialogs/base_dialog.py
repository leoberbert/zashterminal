# zashterminal/ui/dialogs/base_dialog.py

from typing import Callable, List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk

from ...settings.config import get_config_paths
from ...utils.logger import get_logger
from ...utils.translation_utils import _


class BaseDialog(Adw.Window):
    """Base dialog class with enhanced functionality and error handling.

    Provides optional auto-creation of ToolbarView, HeaderBar, and Cancel button
    to reduce boilerplate in subclasses.

    Args:
        parent_window: The parent window for the dialog
        dialog_title: The title to display in the headerbar
        auto_setup_toolbar: If True, automatically creates ToolbarView, HeaderBar,
                           and Cancel button. Defaults to False for backward compatibility.
        **kwargs: Additional properties for Adw.Window
    """

    def __init__(
        self,
        parent_window,
        dialog_title: str,
        auto_setup_toolbar: bool = False,
        **kwargs,
    ):
        default_props = {
            "title": dialog_title,
            "modal": True,
            "transient_for": parent_window,
            "hide_on_close": True,
        }
        default_props.update(kwargs)
        super().__init__(**default_props)

        # Add CSS class for theming
        self.add_css_class("zashterminal-dialog")

        self.logger = get_logger(
            f"zashterminal.ui.dialogs.{self.__class__.__name__.lower()}"
        )
        self.parent_window = parent_window
        self.config_paths = get_config_paths()
        self._validation_errors: List[str] = []
        self._has_changes = False

        # Toolbar components (created only if auto_setup_toolbar is True)
        self._toolbar_view: Optional[Adw.ToolbarView] = None
        self._header_bar: Optional[Adw.HeaderBar] = None
        self._cancel_button: Optional[Gtk.Button] = None
        self._scrolled_window: Optional[Gtk.ScrolledWindow] = None

        if auto_setup_toolbar:
            self._setup_toolbar()

        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_controller)

    def _setup_toolbar(self) -> None:
        """Set up the ToolbarView, HeaderBar, and Cancel button."""
        self._toolbar_view = Adw.ToolbarView()

        # Create HeaderBar
        self._header_bar = Adw.HeaderBar()

        # Create Cancel button
        self._cancel_button = Gtk.Button(label=_("Cancel"))
        self._cancel_button.connect("clicked", self._on_cancel_clicked)
        self._header_bar.pack_start(self._cancel_button)

        self._toolbar_view.add_top_bar(self._header_bar)

        # Create scrolled window for content
        self._scrolled_window = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vexpand=True,
        )
        self._toolbar_view.set_content(self._scrolled_window)

        self.set_content(self._toolbar_view)

    @property
    def header_bar(self) -> Optional[Adw.HeaderBar]:
        """Get the dialog's header bar (only available if auto_setup_toolbar=True)."""
        return self._header_bar

    @property
    def toolbar_view(self) -> Optional[Adw.ToolbarView]:
        """Get the dialog's toolbar view (only available if auto_setup_toolbar=True)."""
        return self._toolbar_view

    def set_body_content(self, widget: Gtk.Widget) -> None:
        """Set the main content widget inside the scrolled area.

        Args:
            widget: The widget to place inside the dialog's content area.

        Raises:
            RuntimeError: If auto_setup_toolbar was not enabled.
        """
        if self._scrolled_window is None:
            raise RuntimeError(
                "set_body_content requires auto_setup_toolbar=True in __init__"
            )
        self._scrolled_window.set_child(widget)

    def add_header_button(self, widget: Gtk.Widget, pack_start: bool = False) -> None:
        """Add a button or widget to the header bar.

        Args:
            widget: The widget to add (typically a Gtk.Button)
            pack_start: If True, pack at the start (left). If False, pack at the end (right).

        Raises:
            RuntimeError: If auto_setup_toolbar was not enabled.
        """
        if self._header_bar is None:
            raise RuntimeError(
                "add_header_button requires auto_setup_toolbar=True in __init__"
            )
        if pack_start:
            self._header_bar.pack_start(widget)
        else:
            self._header_bar.pack_end(widget)

    def _on_key_pressed(self, controller, keyval, _keycode, state):
        if keyval == Gdk.KEY_Escape:
            self._on_cancel_clicked(None)
            return Gdk.EVENT_STOP
        return Gdk.EVENT_PROPAGATE

    def _on_cancel_clicked(self, button):
        self.close()

    def _mark_changed(self):
        self._has_changes = True

    def _show_error_dialog(
        self, title: str, message: str, details: Optional[str] = None
    ) -> None:
        try:
            dialog = Adw.MessageDialog(transient_for=self, title=title, body=message)
            if details:
                dialog.set_body_use_markup(True)
                full_body = (
                    f"{message}\n\n<small>{GLib.markup_escape_text(details)}</small>"
                )
                dialog.set_body(full_body)
            dialog.add_response("ok", _("OK"))
            dialog.present()
            self.logger.warning(f"Error dialog shown: {title} - {message}")
        except Exception as e:
            self.logger.error(f"Failed to show error dialog: {e}")

    def _show_warning_dialog(
        self, title: str, message: str, on_confirm: Optional[Callable] = None
    ) -> None:
        try:
            dialog = Adw.MessageDialog(transient_for=self, title=title, body=message)
            dialog.add_response("cancel", _("Cancel"))
            dialog.add_response("confirm", _("Continue"))
            dialog.set_response_appearance(
                "confirm", Adw.ResponseAppearance.DESTRUCTIVE
            )

            def on_response(dlg, response_id):
                if response_id == "confirm" and on_confirm:
                    on_confirm()
                dlg.close()

            dialog.connect("response", on_response)
            dialog.present()
        except Exception as e:
            self.logger.error(f"Failed to show warning dialog: {e}")

    def _validate_required_field(self, entry, field_name: str) -> bool:
        """Validate a required field. Works with both Gtk.Entry and Adw.EntryRow."""
        value = entry.get_text().strip()
        if not value:
            entry.add_css_class("error")
            self._validation_errors.append(_("{} is required").format(field_name))
            return False
        else:
            entry.remove_css_class("error")
            return True

    def _clear_validation_errors(self):
        self._validation_errors.clear()

    # =========================================================================
    # Form Field Creation Helpers
    # =========================================================================

    def _create_entry_row(
        self,
        title: str,
        text: str = "",
        subtitle: Optional[str] = None,
        on_changed: Optional[Callable] = None,
        css_classes: Optional[List[str]] = None,
    ) -> Adw.EntryRow:
        """Create an Adw.EntryRow with common configuration.
        
        Args:
            title: The row title/label.
            text: Initial text value.
            subtitle: Optional tooltip text (EntryRow doesn't support subtitle).
            on_changed: Optional callback for "changed" signal.
            css_classes: Optional list of CSS classes to add.
            
        Returns:
            Configured Adw.EntryRow instance.
        """
        row = Adw.EntryRow(title=title)
        # Note: EntryRow doesn't have set_subtitle, subtitle param is ignored
        # or can be used as tooltip in caller code
        if text:
            row.set_text(text)
        if on_changed:
            row.connect("changed", on_changed)
        if css_classes:
            for cls in css_classes:
                row.add_css_class(cls)
        return row

    def _create_password_row(
        self,
        title: str,
        text: str = "",
        subtitle: Optional[str] = None,
        on_changed: Optional[Callable] = None,
    ) -> Adw.PasswordEntryRow:
        """Create an Adw.PasswordEntryRow with common configuration.
        
        Args:
            title: The row title/label.
            text: Initial text value.
            subtitle: Optional tooltip text (PasswordEntryRow doesn't support subtitle).
            on_changed: Optional callback for "changed" signal.
            
        Returns:
            Configured Adw.PasswordEntryRow instance.
        """
        row = Adw.PasswordEntryRow(title=title)
        # Note: PasswordEntryRow doesn't have set_subtitle
        if text:
            row.set_text(text)
        if on_changed:
            row.connect("changed", on_changed)
        return row

    def _create_switch_row(
        self,
        title: str,
        subtitle: str = "",
        active: bool = False,
        on_changed: Optional[Callable[[bool], None]] = None,
    ) -> Adw.SwitchRow:
        """Create an Adw.SwitchRow with common configuration.
        
        Args:
            title: The row title/label.
            subtitle: Optional subtitle text.
            active: Initial switch state.
            on_changed: Optional callback receiving the new boolean state.
            
        Returns:
            Configured Adw.SwitchRow instance.
        """
        row = Adw.SwitchRow(title=title)
        if subtitle:
            row.set_subtitle(subtitle)
        row.set_active(active)
        if on_changed:
            row.connect("notify::active", lambda r, _: on_changed(r.get_active()))
        return row

    def _create_spin_row(
        self,
        title: str,
        value: float,
        min_val: float,
        max_val: float,
        step: float = 1.0,
        subtitle: Optional[str] = None,
        on_changed: Optional[Callable[[float], None]] = None,
    ) -> Adw.SpinRow:
        """Create an Adw.SpinRow with common configuration.
        
        Args:
            title: The row title/label.
            value: Initial value.
            min_val: Minimum value.
            max_val: Maximum value.
            step: Step increment.
            subtitle: Optional subtitle text.
            on_changed: Optional callback receiving the new value.
            
        Returns:
            Configured Adw.SpinRow instance.
        """
        row = Adw.SpinRow.new_with_range(min_val, max_val, step)
        row.set_title(title)
        if subtitle:
            row.set_subtitle(subtitle)
        row.set_value(value)
        if on_changed:
            row.connect("notify::value", lambda r, _: on_changed(r.get_value()))
        return row

    def _create_combo_row(
        self,
        title: str,
        items: List[str],
        selected_index: int = 0,
        subtitle: Optional[str] = None,
        on_changed: Optional[Callable[[int], None]] = None,
    ) -> Adw.ComboRow:
        """Create an Adw.ComboRow with common configuration.
        
        Args:
            title: The row title/label.
            items: List of string items for the dropdown.
            selected_index: Initially selected index.
            subtitle: Optional subtitle text.
            on_changed: Optional callback receiving the new selected index.
            
        Returns:
            Configured Adw.ComboRow instance.
        """
        row = Adw.ComboRow(title=title)
        if subtitle:
            row.set_subtitle(subtitle)
        row.set_model(Gtk.StringList.new(items))
        row.set_selected(selected_index)
        if on_changed:
            row.connect("notify::selected", lambda r, _: on_changed(r.get_selected()))
        return row

    def _create_preferences_group(
        self,
        title: str = "",
        description: str = "",
    ) -> Adw.PreferencesGroup:
        """Create an Adw.PreferencesGroup with common configuration.
        
        Args:
            title: The group title.
            description: Optional group description.
            
        Returns:
            Configured Adw.PreferencesGroup instance.
        """
        group = Adw.PreferencesGroup()
        if title:
            group.set_title(title)
        if description:
            group.set_description(description)
        return group

