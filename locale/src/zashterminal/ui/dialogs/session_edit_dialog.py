# zashterminal/ui/dialogs/session_edit_dialog.py

import copy
import threading
from pathlib import Path
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from ...sessions.models import SessionItem
from ...utils.exceptions import HostnameValidationError, SSHKeyError
from ...utils.platform import get_ssh_directory
from ...utils.security import (
    HostnameValidator,
    validate_ssh_hostname,
    validate_ssh_key_file,
)
from ...utils.tooltip_helper import get_tooltip_helper
from ...utils.translation_utils import _
from ..widgets.bash_text_view import BashTextView
from .base_dialog import BaseDialog


class SessionEditDialog(BaseDialog):
    def __init__(
        self,
        parent_window,
        session_item: SessionItem,
        session_store,
        position: int,
        folder_store=None,
        settings_manager=None,
    ):
        self.is_new_item = position == -1
        title = _("Add Session") if self.is_new_item else _("Edit Session")
        super().__init__(parent_window, title, default_width=700, default_height=720)

        self.session_store = session_store
        self.folder_store = folder_store
        self.position = position
        self._settings_manager = settings_manager
        # The editing_session is a data holder, not the live store object
        self.editing_session = (
            SessionItem.from_dict(session_item.to_dict())
            if not self.is_new_item
            else session_item
        )
        self.original_session = session_item if not self.is_new_item else None
        self.folder_paths_map: dict[str, str] = {}
        self.post_login_expander: Optional[Adw.ExpanderRow] = None
        self.post_login_switch = None  # Alias to expander for compatibility
        self.post_login_entry: Optional[BashTextView] = None
        self.post_login_text_view: Optional[BashTextView] = None
        self.sftp_group: Optional[Adw.PreferencesGroup] = None
        self.sftp_switch: Optional[Adw.SwitchRow] = None
        self.sftp_local_entry: Optional[Adw.EntryRow] = None
        self.sftp_remote_entry: Optional[Adw.EntryRow] = None
        self.port_forward_group: Optional[Adw.PreferencesGroup] = None
        self.port_forward_list: Optional[Gtk.ListBox] = None
        self.port_forward_add_button: Optional[Gtk.Button] = None
        self.port_forwardings: list[dict] = [
            dict(item) for item in self.editing_session.port_forwardings
        ]
        self.x11_switch: Optional[Adw.SwitchRow] = None
        # Local terminal options
        self.local_terminal_group: Optional[Adw.PreferencesGroup] = None
        self.startup_commands_group: Optional[Adw.PreferencesGroup] = None
        self._updating_highlighting_ui = False
        self._setup_ui()
        self.connect("map", self._on_map)
        self.logger.info(
            f"Session edit dialog opened: {self.editing_session.name} ({'new' if self.is_new_item else 'edit'})"
        )

    def _on_map(self, widget):
        if self.name_row:
            self.name_row.grab_focus()

    def _setup_ui(self) -> None:
        try:
            # Apply custom CSS for modern styling
            self._apply_custom_css()

            # Use Adw.ToolbarView for proper header bar integration
            toolbar_view = Adw.ToolbarView()
            self.set_content(toolbar_view)

            # Header bar with title
            header = Adw.HeaderBar()
            header.set_show_end_title_buttons(True)
            header.set_show_start_title_buttons(False)

            # Cancel button on left
            cancel_button = Gtk.Button(label=_("Cancel"))
            cancel_button.connect("clicked", self._on_cancel_clicked)
            header.pack_start(cancel_button)

            # Save button on right (first pack_end so it's rightmost)
            save_button = Gtk.Button(label=_("Save"), css_classes=["suggested-action"])
            save_button.connect("clicked", self._on_save_clicked)
            header.pack_end(save_button)
            self.set_default_widget(save_button)

            # Test connection button (to the left of Save, only for SSH)
            self.test_button = Gtk.Button(label=_("Test Connection"))
            get_tooltip_helper().add_tooltip(self.test_button, _("Test SSH connection"))
            self.test_button.connect("clicked", self._on_test_connection_clicked)
            header.pack_end(self.test_button)

            toolbar_view.add_top_bar(header)

            # Scrolled window with preferences page
            scrolled = Gtk.ScrolledWindow()
            scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            toolbar_view.set_content(scrolled)

            # Preferences page for proper styling
            prefs_page = Adw.PreferencesPage()
            scrolled.set_child(prefs_page)

            # Create all sections
            self._create_name_section(prefs_page)
            if self.folder_store:
                self._create_folder_section(prefs_page)
            self._create_highlighting_section(prefs_page)
            self._create_local_terminal_section(prefs_page)
            self._create_ssh_section(prefs_page)

            self._update_ssh_visibility()
            self._update_local_visibility()
            self._update_auth_visibility()
        except Exception as e:
            self.logger.error(f"Failed to setup UI: {e}")
            self._show_error_dialog(
                _("UI Error"), _("Failed to initialize dialog interface")
            )
            self.close()

    def _apply_custom_css(self) -> None:
        """Custom CSS for startup commands section is now loaded globally.

        The styles are defined in data/styles/components.css:
        - .startup-commands-container: Modern rounded container
        - .startup-commands-text: Monospace text view styling

        These styles are loaded at app startup by window_ui.py.
        """
        pass  # CSS is loaded globally from components.css

    def _apply_bash_colors(self, text_view: BashTextView) -> None:
        """Apply terminal color scheme to BashTextView if settings_manager is available."""
        if not self._settings_manager:
            return

        gtk_theme = self._settings_manager.get("gtk_theme", "system")
        if gtk_theme == "terminal":
            scheme_data = self._settings_manager.get_color_scheme_data()
            if scheme_data:
                palette = scheme_data.get("palette", [])
                foreground = scheme_data.get("foreground", "#ffffff")
                if palette:
                    text_view.update_colors_from_scheme(palette, foreground)

    # NOTE: _create_entry_row and _create_spin_row are inherited from BaseDialog

    def _create_name_section(self, parent: Adw.PreferencesPage) -> None:
        """Create the session information section with proper Adw widgets."""
        name_group = Adw.PreferencesGroup()

        # Session Name - using helper
        self.name_row = self._create_entry_row(
            title=_("Session Name"),
            text=self.editing_session.name,
            on_changed=self._on_name_changed,
        )
        name_group.add(self.name_row)

        # Session Type - using Adw.ComboRow properly
        self.type_combo = Adw.ComboRow(
            title=_("Session Type"),
            subtitle=_("Choose between local terminal or SSH connection"),
        )
        self.type_combo.set_model(
            Gtk.StringList.new([_("Local Terminal"), _("SSH Connection")])
        )
        self.type_combo.set_selected(0 if self.editing_session.is_local() else 1)
        self.type_combo.connect("notify::selected", self._on_type_changed)
        name_group.add(self.type_combo)

        # Tab Color - using Adw.ActionRow with color button
        color_row = Adw.ActionRow(
            title=_("Tab Color"),
            subtitle=_("Choose a color to identify this session's tab"),
        )

        color_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        color_box.set_valign(Gtk.Align.CENTER)

        self.color_button = Gtk.ColorButton(valign=Gtk.Align.CENTER, show_editor=True)
        self.color_button.connect("color-set", self._on_color_changed)

        if self.editing_session.tab_color:
            rgba = Gdk.RGBA()
            if rgba.parse(self.editing_session.tab_color):
                self.color_button.set_rgba(rgba)
            else:
                self.color_button.set_rgba(Gdk.RGBA())
        else:
            self.color_button.set_rgba(Gdk.RGBA())

        clear_button = Gtk.Button(
            icon_name="edit-clear-symbolic",
            valign=Gtk.Align.CENTER,
            tooltip_text=_("Clear Color"),
            css_classes=["flat"],
        )
        clear_button.connect("clicked", self._on_clear_color_clicked)

        color_box.append(self.color_button)
        color_box.append(clear_button)
        color_row.add_suffix(color_box)
        name_group.add(color_row)

        parent.add(name_group)

    def _create_folder_section(self, parent: Adw.PreferencesPage) -> None:
        """Create the folder organization section."""
        folder_group = Adw.PreferencesGroup(
            title=_("Organization"),
            description=_("Choose where to store this session"),
        )

        folder_row = Adw.ComboRow(
            title=_("Folder"),
            subtitle=_("Select a folder to organize this session"),
        )

        folder_model = Gtk.StringList()
        folder_model.append(_("Root"))
        self.folder_paths_map = {_("Root"): ""}

        folders = sorted(
            [
                self.folder_store.get_item(i)
                for i in range(self.folder_store.get_n_items())
            ],
            key=lambda f: f.path,
        )
        for folder in folders:
            display_name = f"{'  ' * folder.path.count('/')}{folder.name}"
            folder_model.append(display_name)
            self.folder_paths_map[display_name] = folder.path

        folder_row.set_model(folder_model)

        selected_index = 0
        for i, (display, path_val) in enumerate(self.folder_paths_map.items()):
            if path_val == self.editing_session.folder_path:
                selected_index = i
                break
        folder_row.set_selected(selected_index)
        folder_row.connect("notify::selected", self._on_folder_changed)
        self.folder_combo = folder_row

        folder_group.add(folder_row)
        parent.add(folder_group)

    # ---------------------------------------------------------------------
    # Highlighting overrides
    # ---------------------------------------------------------------------

    @staticmethod
    def _tri_state_to_selected(value: Optional[bool]) -> int:
        """Map tri-state (None/True/False) to ComboRow selection index."""
        if value is None:
            return 0
        return 1 if value else 2

    @staticmethod
    def _selected_to_tri_state(selected: int) -> Optional[bool]:
        """Map ComboRow selection index to tri-state (None/True/False)."""
        if selected == 0:
            return None
        if selected == 1:
            return True
        return False

    def _create_tristate_combo_row(
        self,
        *,
        title: str,
        subtitle: str,
        initial_value: Optional[bool],
        on_changed,
    ) -> Adw.ComboRow:
        row = Adw.ComboRow(title=title, subtitle=subtitle)
        row.set_model(Gtk.StringList.new([_("Automatic"), _("Enabled"), _("Disabled")]))
        row.set_selected(self._tri_state_to_selected(initial_value))
        row.connect("notify::selected", on_changed)
        return row

    def _on_highlighting_override_changed(self, *_args) -> None:
        if self._updating_highlighting_ui:
            return

        self._mark_changed()

        # Keep the in-memory editing_session synced so other parts of the dialog
        # (and future logic) can rely on the values.
        try:
            # If the user isn't customizing, treat everything as Automatic.
            if not self.highlighting_customize_switch.get_active():
                self.editing_session.output_highlighting = None
                self.editing_session.command_specific_highlighting = None
                self.editing_session.cat_colorization = None
                self.editing_session.shell_input_highlighting = None
                return

            self.editing_session.output_highlighting = self._selected_to_tri_state(
                self.output_highlighting_row.get_selected()
            )
            self.editing_session.command_specific_highlighting = (
                self._selected_to_tri_state(
                    self.command_specific_highlighting_row.get_selected()
                )
            )
            self.editing_session.cat_colorization = self._selected_to_tri_state(
                self.cat_colorization_row.get_selected()
            )
            self.editing_session.shell_input_highlighting = self._selected_to_tri_state(
                self.shell_input_highlighting_row.get_selected()
            )
        except Exception:
            # Keep the dialog responsive even if validation raises.
            pass

    def _set_highlighting_overrides_visible(self, visible: bool) -> None:
        for row in (
            self.output_highlighting_row,
            self.command_specific_highlighting_row,
            self.cat_colorization_row,
            self.shell_input_highlighting_row,
        ):
            row.set_visible(visible)
            row.set_sensitive(visible)

    def _on_highlighting_customize_switch_changed(self, *_args) -> None:
        if self._updating_highlighting_ui:
            return

        self._mark_changed()
        active = self.highlighting_customize_switch.get_active()

        self._updating_highlighting_ui = True
        try:
            if not active:
                # Equivalent to "Automatic" for all overrides.
                self.output_highlighting_row.set_selected(0)
                self.command_specific_highlighting_row.set_selected(0)
                self.cat_colorization_row.set_selected(0)
                self.shell_input_highlighting_row.set_selected(0)

            self._set_highlighting_overrides_visible(active)
        finally:
            self._updating_highlighting_ui = False

        # Sync editing_session state.
        self._on_highlighting_override_changed()

    def _create_highlighting_section(self, parent: Adw.PreferencesPage) -> None:
        group = Adw.PreferencesGroup(
            title=_("Highlighting"),
            description=_(
                "Highlighting adds colors to make terminal text easier to read. You can override global preferences per session."
            ),
        )

        warning_row = Adw.ActionRow(
            title=_("Experimental Feature"),
            subtitle=_(
                "Per-session highlighting overrides are experimental and may change."
            ),
        )
        warning_row.add_prefix(Gtk.Image.new_from_icon_name("dialog-warning-symbolic"))
        group.add(warning_row)

        has_custom_overrides = any(
            getattr(self.editing_session, key, None) is not None
            for key in (
                "output_highlighting",
                "command_specific_highlighting",
                "cat_colorization",
                "shell_input_highlighting",
            )
        )

        self.highlighting_customize_switch = Adw.SwitchRow(
            title=_("Customize highlighting for this session"),
            subtitle=_("When off, this session uses the global highlighting settings"),
        )
        self.highlighting_customize_switch.set_active(has_custom_overrides)
        self.highlighting_customize_switch.connect(
            "notify::active", self._on_highlighting_customize_switch_changed
        )
        group.add(self.highlighting_customize_switch)

        self.output_highlighting_row = self._create_tristate_combo_row(
            title=_("Output Highlighting"),
            subtitle=_("Enable/disable output highlighting for this session"),
            initial_value=getattr(self.editing_session, "output_highlighting", None),
            on_changed=self._on_highlighting_override_changed,
        )
        group.add(self.output_highlighting_row)

        self.command_specific_highlighting_row = self._create_tristate_combo_row(
            title=_("Command-Specific Highlighting"),
            subtitle=_("Use context-aware rules for specific commands"),
            initial_value=getattr(
                self.editing_session, "command_specific_highlighting", None
            ),
            on_changed=self._on_highlighting_override_changed,
        )
        group.add(self.command_specific_highlighting_row)

        # NOTE: 'cat' is a terminal command, so we don't translate it
        # Use format string to keep 'cat' untranslated while translating the rest
        self.cat_colorization_row = self._create_tristate_combo_row(
            title=_("{} Command Colorization").format("cat"),
            subtitle=_("Colorize file content output using syntax highlighting"),
            initial_value=getattr(self.editing_session, "cat_colorization", None),
            on_changed=self._on_highlighting_override_changed,
        )
        group.add(self.cat_colorization_row)

        self.shell_input_highlighting_row = self._create_tristate_combo_row(
            title=_("Shell Input Highlighting"),
            subtitle=_("Highlight commands as you type at the shell prompt"),
            initial_value=getattr(
                self.editing_session, "shell_input_highlighting", None
            ),
            on_changed=self._on_highlighting_override_changed,
        )
        group.add(self.shell_input_highlighting_row)

        # Only show the per-setting overrides when customization is enabled.
        self._set_highlighting_overrides_visible(has_custom_overrides)

        parent.add(group)

    def _create_local_terminal_section(self, parent: Adw.PreferencesPage) -> None:
        """Create the Local Terminal configuration section."""
        local_group = Adw.PreferencesGroup(
            title=_("Local Terminal Options"),
        )

        # Working Directory - using Adw.ActionRow with entry and browse button
        working_dir_row = Adw.ActionRow(
            title=_("Working Directory"),
            subtitle=_("Start the terminal in this folder"),
        )

        working_dir_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        working_dir_box.set_valign(Gtk.Align.CENTER)
        working_dir_box.set_hexpand(True)

        self.local_working_dir_entry = Gtk.Entry(
            text=self.editing_session.local_working_directory or "",
            placeholder_text=_("Default (Home directory)"),
            hexpand=True,
            width_chars=25,
        )
        self.local_working_dir_entry.connect(
            "changed", self._on_local_working_dir_changed
        )

        browse_working_dir_button = Gtk.Button(
            icon_name="folder-open-symbolic",
            tooltip_text=_("Browse for folder"),
            css_classes=["flat"],
        )
        browse_working_dir_button.set_valign(Gtk.Align.CENTER)
        browse_working_dir_button.connect(
            "clicked", self._on_browse_working_dir_clicked
        )

        working_dir_box.append(self.local_working_dir_entry)
        working_dir_box.append(browse_working_dir_button)
        working_dir_row.add_suffix(working_dir_box)
        local_group.add(working_dir_row)

        parent.add(local_group)

        # Startup Commands - separate group for better visual organization
        startup_commands_group = Adw.PreferencesGroup(
            title=_("Startup Commands"),
            description=_(
                "Commands executed when the terminal starts (one per line). "
                "You can write multiple commands like a small script."
            ),
        )

        # Create a scrolled window with BashTextView for syntax-highlighted multi-line input
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_min_content_height(130)
        scrolled.set_max_content_height(220)
        scrolled.set_margin_start(12)
        scrolled.set_margin_end(12)
        scrolled.set_margin_top(6)
        scrolled.set_margin_bottom(12)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.add_css_class("startup-commands-container")

        # Use BashTextView for syntax highlighting (no auto-resize since we have scrolled window)
        self.local_startup_command_view = BashTextView(
            auto_resize=False, min_lines=3, max_lines=10
        )
        # Use default BashTextView spacing - no need to override pixels_above/below_lines
        self.local_startup_command_view.set_top_margin(10)
        self.local_startup_command_view.set_bottom_margin(10)
        self.local_startup_command_view.set_left_margin(12)
        self.local_startup_command_view.set_right_margin(12)
        self.local_startup_command_view.add_css_class("startup-commands-text")

        # Set initial text
        self.local_startup_command_view.set_text(
            self.editing_session.local_startup_command or ""
        )
        self.local_startup_command_view.get_buffer().connect(
            "changed", self._on_local_startup_command_changed
        )

        # Apply color scheme if available
        self._apply_bash_colors(self.local_startup_command_view)

        scrolled.set_child(self.local_startup_command_view)
        startup_commands_group.add(scrolled)

        # Store reference to the startup commands group for visibility control
        self.startup_commands_group = startup_commands_group
        parent.add(startup_commands_group)

        # Store reference to the local terminal group for visibility control
        self.local_terminal_group = local_group

    def _create_ssh_section(self, parent: Adw.PreferencesPage) -> None:
        """Create the SSH configuration section with proper Adw widgets."""
        ssh_group = Adw.PreferencesGroup(
            title=_("SSH Configuration"),
        )

        # Host - using helper
        self.host_row = self._create_entry_row(
            title=_("Host"),
            text=self.editing_session.host or "",
            on_changed=self._on_host_changed,
        )
        ssh_group.add(self.host_row)
        # Keep reference with old name for compatibility
        self.host_entry = self.host_row

        # Username - using helper
        self.user_row = self._create_entry_row(
            title=_("Username"),
            text=self.editing_session.user or "",
            on_changed=self._on_user_changed,
        )
        ssh_group.add(self.user_row)
        # Keep reference with old name for compatibility
        self.user_entry = self.user_row

        # Port - using helper
        self.port_row = self._create_spin_row(
            title=_("Port"),
            value=self.editing_session.port or 22,
            min_val=1,
            max_val=65535,
            on_changed=self._on_port_changed,
        )
        ssh_group.add(self.port_row)
        # Keep reference with old name for compatibility
        self.port_entry = self.port_row

        # Authentication Method - using Adw.ComboRow
        self.auth_combo = Adw.ComboRow(
            title=_("Authentication"),
            subtitle=_("Choose how to authenticate with the server"),
        )
        self.auth_combo.set_model(Gtk.StringList.new([_("SSH Key"), _("Password")]))
        self.auth_combo.set_selected(0 if self.editing_session.uses_key_auth() else 1)
        self.auth_combo.connect("notify::selected", self._on_auth_changed)
        ssh_group.add(self.auth_combo)

        # SSH Key Path - using Adw.ActionRow with entry and browse button
        key_value = (
            self.editing_session.auth_value
            if self.editing_session.uses_key_auth()
            else ""
        )
        self.key_row = Adw.ActionRow(
            title=_("SSH Key Path"),
            subtitle=_("Path to your private key file"),
        )

        key_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        key_box.set_valign(Gtk.Align.CENTER)
        key_box.set_hexpand(True)

        self.key_path_entry = Gtk.Entry(
            text=key_value,
            placeholder_text=f"{get_ssh_directory()}/id_rsa",
            hexpand=True,
            width_chars=30,
        )
        self.key_path_entry.connect("changed", self._on_key_path_changed)

        self.browse_button = Gtk.Button(
            icon_name="folder-open-symbolic",
            tooltip_text=_("Browse for SSH key file"),
            css_classes=["flat"],
        )
        self.browse_button.set_valign(Gtk.Align.CENTER)
        self.browse_button.connect("clicked", self._on_browse_key_clicked)

        key_box.append(self.key_path_entry)
        key_box.append(self.browse_button)
        self.key_row.add_suffix(key_box)
        ssh_group.add(self.key_row)
        self.key_box = self.key_row  # Keep reference for visibility control

        # Password - using Adw.PasswordEntryRow
        password_value = (
            self.editing_session.auth_value
            if self.editing_session.uses_password_auth()
            else ""
        )
        self.password_row = Adw.PasswordEntryRow(
            title=_("Password"),
        )
        self.password_row.set_text(password_value)
        self.password_row.connect("changed", self._on_password_changed)
        ssh_group.add(self.password_row)
        # Keep reference with old name for compatibility
        self.password_entry = self.password_row
        self.password_box = self.password_row  # Keep reference for visibility control

        # Add keyring info
        from ...utils.crypto import is_encryption_available

        if not is_encryption_available():
            keyring_row = Adw.ActionRow(
                title=_("Password Storage"),
                subtitle=_(
                    "System keyring not available - password will be stored in plain text"
                ),
            )
            keyring_row.add_prefix(
                Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
            )
            ssh_group.add(keyring_row)

        self.ssh_box = ssh_group
        parent.add(ssh_group)

        # Create additional SSH options in a separate group
        self._create_ssh_options_group(parent)

    def _create_ssh_options_group(self, parent: Adw.PreferencesPage) -> None:
        """Create additional SSH options like post-login command, X11, SFTP."""
        # Post-login command section - dedicated group with switch and command input
        post_login_group = Adw.PreferencesGroup(
            title=_("SSH Options"),
        )

        # Post-login command toggle using SwitchRow
        self.post_login_switch = Adw.SwitchRow(
            title=_("Run Command After Login"),
            subtitle=_("Execute commands automatically after SSH connects"),
        )
        is_post_login_enabled = self.editing_session.post_login_command_enabled
        self.post_login_switch.set_active(is_post_login_enabled)
        self.post_login_switch.connect("notify::active", self._on_post_login_toggle)
        post_login_group.add(self.post_login_switch)

        # Command input container - shown/hidden based on switch
        self.post_login_command_container = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=0,
        )
        self.post_login_command_container.set_visible(is_post_login_enabled)

        # Create a scrolled window with BashTextView for syntax-highlighted input
        post_login_scrolled = Gtk.ScrolledWindow()
        post_login_scrolled.set_min_content_height(100)
        post_login_scrolled.set_max_content_height(160)
        post_login_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        post_login_scrolled.set_margin_start(12)
        post_login_scrolled.set_margin_end(12)
        post_login_scrolled.set_margin_top(4)
        post_login_scrolled.set_margin_bottom(8)
        post_login_scrolled.add_css_class("startup-commands-container")

        # Use BashTextView for syntax highlighting
        self.post_login_text_view = BashTextView(
            auto_resize=False, min_lines=2, max_lines=6
        )
        self.post_login_text_view.set_top_margin(10)
        self.post_login_text_view.set_bottom_margin(10)
        self.post_login_text_view.set_left_margin(12)
        self.post_login_text_view.set_right_margin(12)
        self.post_login_text_view.add_css_class("startup-commands-text")
        self.post_login_text_view.set_text(
            self.editing_session.post_login_command or ""
        )
        self.post_login_text_view.get_buffer().connect(
            "changed", self._on_post_login_command_changed
        )

        # Apply color scheme if available
        self._apply_bash_colors(self.post_login_text_view)

        post_login_scrolled.set_child(self.post_login_text_view)
        self.post_login_command_container.append(post_login_scrolled)
        post_login_group.add(self.post_login_command_container)

        # Keep legacy references
        self.post_login_entry = self.post_login_text_view
        self.post_login_expander = None
        self.post_login_command_row = self.post_login_switch
        self.post_login_command_group = post_login_group

        parent.add(post_login_group)

        # Other SSH options in a separate group
        options_group = Adw.PreferencesGroup()

        # X11 Forwarding toggle
        self.x11_switch = Adw.SwitchRow(
            title=_("Enable X11 Forwarding"),
            subtitle=_("Allow graphical applications from remote server"),
        )
        self.x11_switch.set_active(self.editing_session.x11_forwarding)
        self.x11_switch.connect("notify::active", self._on_x11_toggled)
        options_group.add(self.x11_switch)

        # SFTP toggle
        self.sftp_switch = Adw.SwitchRow(
            title=_("Enable SFTP Session"),
            subtitle=_("Use default directories when opening SFTP"),
        )
        self.sftp_switch.set_active(self.editing_session.sftp_session_enabled)
        self.sftp_switch.connect("notify::active", self._on_sftp_toggle)
        options_group.add(self.sftp_switch)

        # SFTP Local Directory - using helper
        self.sftp_local_entry = self._create_entry_row(
            title=_("SFTP Local Directory"),
            text=self.editing_session.sftp_local_directory or "",
            on_changed=self._on_sftp_local_changed,
        )
        options_group.add(self.sftp_local_entry)
        self.sftp_local_row = self.sftp_local_entry

        # SFTP Remote Directory - using helper
        self.sftp_remote_entry = self._create_entry_row(
            title=_("SFTP Remote Directory"),
            text=self.editing_session.sftp_remote_directory or "",
            on_changed=self._on_sftp_remote_changed,
        )
        options_group.add(self.sftp_remote_entry)
        self.sftp_remote_row = self.sftp_remote_entry

        # Port Forwarding section (inside SSH Options)
        self._create_port_forward_widgets(options_group)

        self.ssh_options_group = options_group
        parent.add(options_group)

        # Update initial visibility
        self._update_post_login_command_state()
        self._update_sftp_state()

    def _create_port_forward_widgets(self, parent_group: Adw.PreferencesGroup) -> None:
        """Create port forwarding widgets inside the SSH Options group."""
        # Port forwarding header row (just a separator with title)
        port_forward_header = Adw.ActionRow(
            title=_("Port Forwarding"),
            subtitle=_("Create SSH tunnels to forward ports"),
        )
        port_forward_header.set_activatable(False)
        parent_group.add(port_forward_header)

        # Add button row
        add_row = Adw.ActionRow(
            title=_("Add Port Forward"),
            subtitle=_("Create a new SSH tunnel"),
        )
        add_row.set_activatable(True)
        add_button = Gtk.Button(icon_name="list-add-symbolic")
        add_button.set_valign(Gtk.Align.CENTER)
        add_button.add_css_class("flat")
        add_row.add_suffix(add_button)
        add_row.set_activatable_widget(add_button)
        add_button.connect("clicked", self._on_add_port_forward_clicked)
        parent_group.add(add_row)
        self.port_forward_add_button = add_button
        self.port_forward_add_row = add_row

        # List container for port forwards
        self.port_forward_list = Gtk.ListBox()
        self.port_forward_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.port_forward_list.add_css_class("boxed-list")

        list_row = Adw.ActionRow()
        list_row.set_child(self.port_forward_list)
        parent_group.add(list_row)
        self.port_forward_list_row = list_row

        # Keep reference for visibility control (use parent_group as proxy)
        self.port_forward_group = parent_group

        self._refresh_port_forward_list()

    def _create_port_forward_section(self, parent: Adw.PreferencesPage) -> None:
        """Legacy method - port forwarding is now inside SSH Options group."""
        pass  # No longer used

    def _refresh_port_forward_list(self) -> None:
        if not self.port_forward_list:
            return
        # Gtk4 ListBox does not expose get_children; iterate manually.
        child = self.port_forward_list.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self.port_forward_list.remove(child)
            child = next_child

        if not self.port_forwardings:
            placeholder_row = Gtk.ListBoxRow()
            placeholder_row.set_selectable(False)
            placeholder_row.set_activatable(False)
            label = Gtk.Label(
                label=_("No port forwards configured."),
                xalign=0,
                margin_top=6,
                margin_bottom=6,
                margin_start=12,
                margin_end=12,
            )
            label.add_css_class("dim-label")
            placeholder_row.set_child(label)
            self.port_forward_list.append(placeholder_row)
            return

        for index, tunnel in enumerate(self.port_forwardings):
            row = Gtk.ListBoxRow()
            row.set_selectable(False)
            row.set_activatable(False)
            row_box = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=12,
                margin_top=6,
                margin_bottom=6,
                margin_start=12,
                margin_end=12,
            )

            labels_box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=2,
                hexpand=True,
            )
            title = Gtk.Label(
                label=tunnel.get("name", _("Tunnel")),
                xalign=0,
            )
            remote_host_display = tunnel.get("remote_host") or _("SSH Host")
            subtitle_text = _("{local_host}:{local_port} → {remote_host}:{remote_port}").format(
                local_host=tunnel.get("local_host", "localhost"),
                local_port=tunnel.get("local_port", 0),
                remote_host=remote_host_display,
                remote_port=tunnel.get("remote_port", 0),
            )
            subtitle = Gtk.Label(label=subtitle_text, xalign=0)
            subtitle.add_css_class("dim-label")
            labels_box.append(title)
            labels_box.append(subtitle)
            row_box.append(labels_box)

            button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            edit_button = Gtk.Button(
                icon_name="document-edit-symbolic", css_classes=["flat"]
            )
            edit_button.connect("clicked", self._on_edit_port_forward_clicked, index)
            delete_button = Gtk.Button(
                icon_name="user-trash-symbolic", css_classes=["flat"]
            )
            delete_button.connect("clicked", self._on_delete_port_forward_clicked, index)
            button_box.append(edit_button)
            button_box.append(delete_button)
            row_box.append(button_box)

            row.set_child(row_box)
            self.port_forward_list.append(row)

    def _on_add_port_forward_clicked(self, _button) -> None:
        new_entry = self._show_port_forward_dialog()
        if new_entry:
            self.port_forwardings.append(new_entry)
            self._refresh_port_forward_list()
            self._mark_changed()

    def _on_edit_port_forward_clicked(self, _button, index: int) -> None:
        if 0 <= index < len(self.port_forwardings):
            existing = copy.deepcopy(self.port_forwardings[index])
            updated = self._show_port_forward_dialog(existing)
            if updated:
                self.port_forwardings[index] = updated
                self._refresh_port_forward_list()
                self._mark_changed()

    def _on_delete_port_forward_clicked(self, _button, index: int) -> None:
        if 0 <= index < len(self.port_forwardings):
            del self.port_forwardings[index]
            self._refresh_port_forward_list()
            self._mark_changed()

    def _show_port_forward_dialog(self, existing: Optional[dict] = None) -> Optional[dict]:
        is_edit = existing is not None

        # Use Adw.Window with ToolbarView for consistency
        dialog = Adw.Window(
            transient_for=self,
            modal=True,
            default_width=600,
            default_height=600,
        )
        dialog.set_title(_("Edit Port Forward") if is_edit else _("Add Port Forward"))

        # Use Adw.ToolbarView for proper layout
        toolbar_view = Adw.ToolbarView()
        dialog.set_content(toolbar_view)

        # Header bar with buttons
        header_bar = Adw.HeaderBar()
        header_bar.set_show_end_title_buttons(True)
        header_bar.set_show_start_title_buttons(False)

        cancel_button = Gtk.Button(label=_("Cancel"))
        cancel_button.connect("clicked", lambda b: dialog.close())
        header_bar.pack_start(cancel_button)

        save_button = Gtk.Button(label=_("Save"), css_classes=["suggested-action"])
        header_bar.pack_end(save_button)

        toolbar_view.add_top_bar(header_bar)

        # Scrolled content with preferences page
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        toolbar_view.set_content(scrolled)

        prefs_page = Adw.PreferencesPage()
        scrolled.set_child(prefs_page)

        # Tunnel settings group
        settings_group = Adw.PreferencesGroup()
        prefs_page.add(settings_group)

        # Name field - using Adw.EntryRow
        name_row = Adw.EntryRow(title=_("Tunnel Name"))
        name_row.set_text(existing.get("name", "") if existing else "")
        settings_group.add(name_row)

        # Local Settings group
        local_group = Adw.PreferencesGroup(
            title=_("Local Settings"),
        )
        prefs_page.add(local_group)

        # Local Host - using Adw.EntryRow
        local_host_row = Adw.EntryRow(title=_("Local Host"))
        local_host_row.set_text(
            existing.get("local_host", "localhost") if existing else "localhost"
        )
        local_group.add(local_host_row)

        # Local Port - using Adw.SpinRow
        local_port_row = Adw.SpinRow.new_with_range(1, 65535, 1)
        local_port_row.set_title(_("Local Port"))
        local_port_row.set_subtitle(_("Port on your machine (1025-65535 recommended)"))
        local_port_row.set_value(existing.get("local_port", 8080) if existing else 8080)
        local_group.add(local_port_row)

        # Remote Settings group
        remote_group = Adw.PreferencesGroup(
            title=_("Remote Settings"),
        )
        prefs_page.add(remote_group)

        # Remote host toggle
        use_custom_remote = bool(existing and existing.get("remote_host"))
        remote_toggle_row = Adw.SwitchRow(
            title=_("Use Custom Remote Host"),
            subtitle=_("Leave off to use the SSH server as target"),
        )
        remote_toggle_row.set_active(use_custom_remote)
        remote_group.add(remote_toggle_row)

        # Remote Host - using Adw.EntryRow
        remote_host_row = Adw.EntryRow(title=_("Remote Host"))
        remote_host_row.set_text(existing.get("remote_host", "") if existing else "")
        remote_host_row.set_visible(use_custom_remote)
        remote_group.add(remote_host_row)

        def on_remote_toggle(switch_row, _param):
            remote_host_row.set_visible(switch_row.get_active())

        remote_toggle_row.connect("notify::active", on_remote_toggle)

        # Remote Port - using Adw.SpinRow
        remote_port_row = Adw.SpinRow.new_with_range(1, 65535, 1)
        remote_port_row.set_title(_("Remote Port"))
        remote_port_row.set_subtitle(_("Port on the remote host (1-65535)"))
        remote_port_row.set_value(existing.get("remote_port", 80) if existing else 80)
        remote_group.add(remote_port_row)

        result: Optional[dict] = None

        def on_save(_button):
            nonlocal result
            name = name_row.get_text().strip() or _("Tunnel")
            local_host = local_host_row.get_text().strip() or "localhost"
            local_port = int(local_port_row.get_value())
            remote_port = int(remote_port_row.get_value())
            remote_host = (
                remote_host_row.get_text().strip()
                if remote_toggle_row.get_active()
                else ""
            )

            errors = []
            if not (1024 < local_port <= 65535):
                errors.append(
                    _(
                        "Local port must be between 1025 and 65535 (ports below 1024 require administrator privileges)."
                    )
                )
            if not (1 <= remote_port <= 65535):
                errors.append(_("Remote port must be between 1 and 65535."))
            if not local_host:
                errors.append(_("Local host cannot be empty."))

            if errors:
                self._show_error_dialog(
                    _("Invalid Port Forward"),
                    "\n".join(errors),
                )
                return

            result = {
                "name": name,
                "local_host": local_host,
                "local_port": local_port,
                "remote_host": remote_host,
                "remote_port": remote_port,
            }
            dialog.close()

        save_button.connect("clicked", on_save)

        # Run dialog blocking
        response_holder = {"finished": False}
        loop = GLib.MainLoop()

        def on_close(_dialog):
            response_holder["finished"] = True
            loop.quit()

        dialog.connect("close-request", on_close)
        dialog.present()
        loop.run()

        return result

    def _on_name_changed(self, entry) -> None:
        entry.remove_css_class("error")
        self._mark_changed()

    def _on_color_changed(self, button: Gtk.ColorButton) -> None:
        self._mark_changed()

    def _on_clear_color_clicked(self, button: Gtk.Button) -> None:
        self.color_button.set_rgba(Gdk.RGBA())  # Set to no color
        self._mark_changed()

    def _on_folder_changed(self, combo_row, param) -> None:
        self._mark_changed()

    def _on_type_changed(self, combo_row, param) -> None:
        if (
            combo_row.get_selected() == 1
            and self.is_new_item
            and self.auth_combo.get_selected() == 0
            and not self.key_path_entry.get_text().strip()
        ):
            self.key_path_entry.set_text(f"{get_ssh_directory()}/id_rsa")
        self._update_ssh_visibility()
        self._update_local_visibility()
        self._mark_changed()

    def _on_host_changed(self, entry: Gtk.Entry) -> None:
        entry.remove_css_class("error")
        self._mark_changed()
        hostname = entry.get_text().strip()
        if hostname and not HostnameValidator.is_valid_hostname(hostname):
            entry.add_css_class("error")

    def _on_user_changed(self, entry: Gtk.Entry) -> None:
        self._mark_changed()

    def _on_port_changed(self, spin_row, _param) -> None:
        self._mark_changed()
        port = int(spin_row.get_value())
        spin_row.remove_css_class(
            "error"
        ) if 1 <= port <= 65535 else spin_row.add_css_class("error")

    def _on_auth_changed(self, combo_row, param) -> None:
        if combo_row.get_selected() == 0 and not self.key_path_entry.get_text().strip():
            self.key_path_entry.set_text(f"{get_ssh_directory()}/id_rsa")
        self._update_auth_visibility()
        self._mark_changed()

    def _on_key_path_changed(self, entry: Gtk.Entry) -> None:
        entry.remove_css_class("error")
        self._mark_changed()
        key_path = entry.get_text().strip()
        if key_path:
            try:
                validate_ssh_key_file(key_path)
            except Exception:
                entry.add_css_class("error")

    def _on_password_changed(self, entry: Gtk.PasswordEntry) -> None:
        self._mark_changed()

    def _on_post_login_toggle(self, switch_row: Adw.SwitchRow, _param) -> None:
        self._mark_changed()
        # Show/hide the command container based on switch state
        is_enabled = switch_row.get_active()
        if (
            hasattr(self, "post_login_command_container")
            and self.post_login_command_container
        ):
            self.post_login_command_container.set_visible(is_enabled)
        self._update_post_login_command_state()
        if self.post_login_entry and not is_enabled:
            self.post_login_entry.remove_css_class("error")

    def _on_post_login_command_changed(self, buffer: Gtk.TextBuffer) -> None:
        if self.post_login_entry:
            self.post_login_entry.remove_css_class("error")
        self._mark_changed()

    def _on_x11_toggled(self, switch_row: Adw.SwitchRow, _param) -> None:
        if switch_row.get_active() and hasattr(self.parent_window, "toast_overlay"):
            toast = Adw.Toast(
                title=_("Remote server must have X11Forwarding yes in sshd_config.")
            )
            self.parent_window.toast_overlay.add_toast(toast)
        self._mark_changed()

    def _on_sftp_toggle(self, switch_row: Adw.SwitchRow, _param) -> None:
        self._mark_changed()
        self._update_sftp_state()
        if self.sftp_local_entry and not switch_row.get_active():
            self.sftp_local_entry.remove_css_class("error")

    def _on_sftp_local_changed(self, entry: Gtk.Entry) -> None:
        entry.remove_css_class("error")
        self._mark_changed()

    def _on_sftp_remote_changed(self, entry: Gtk.Entry) -> None:
        self._mark_changed()

    def _update_ssh_visibility(self) -> None:
        if self.ssh_box and self.type_combo:
            is_ssh = self.type_combo.get_selected() == 1
            self.ssh_box.set_visible(is_ssh)
            if hasattr(self, "ssh_options_group") and self.ssh_options_group:
                self.ssh_options_group.set_visible(is_ssh)
            if (
                hasattr(self, "post_login_command_group")
                and self.post_login_command_group
            ):
                self.post_login_command_group.set_visible(is_ssh)
            if hasattr(self, "test_button"):
                self.test_button.set_visible(is_ssh)
            if self.x11_switch:
                self.x11_switch.set_sensitive(is_ssh)
                if not is_ssh:
                    self.x11_switch.set_active(False)
            if self.sftp_switch:
                self.sftp_switch.set_sensitive(is_ssh)
                if not is_ssh:
                    self.sftp_switch.set_active(False)
        self._update_port_forward_state()
        self._update_post_login_command_state()
        self._update_sftp_state()

    def _update_local_visibility(self) -> None:
        """Update visibility of local terminal options based on session type."""
        is_local = self.type_combo.get_selected() == 0 if self.type_combo else False

        if hasattr(self, "local_terminal_group") and self.local_terminal_group:
            self.local_terminal_group.set_visible(is_local)

        # Also update the startup commands group visibility
        if hasattr(self, "startup_commands_group") and self.startup_commands_group:
            self.startup_commands_group.set_visible(is_local)

    def _update_auth_visibility(self) -> None:
        if self.key_box and self.password_box and self.auth_combo:
            is_key = self.auth_combo.get_selected() == 0
            self.key_box.set_visible(is_key)
            self.password_box.set_visible(not is_key)
        self._update_port_forward_state()
        self._update_post_login_command_state()
        self._update_sftp_state()

    def _update_port_forward_state(self) -> None:
        is_ssh_session = (
            self.type_combo.get_selected() == 1 if self.type_combo else False
        )
        # Port forward widgets are now inside SSH Options, control visibility
        if hasattr(self, "port_forward_add_row") and self.port_forward_add_row:
            self.port_forward_add_row.set_visible(is_ssh_session)
        if hasattr(self, "port_forward_list_row") and self.port_forward_list_row:
            self.port_forward_list_row.set_visible(is_ssh_session)
        if self.port_forward_list:
            self.port_forward_list.set_sensitive(is_ssh_session)
        if self.port_forward_add_button:
            self.port_forward_add_button.set_sensitive(is_ssh_session)

    def _update_post_login_command_state(self) -> None:
        if not self.post_login_switch or not self.post_login_entry:
            return
        is_ssh_session = (
            self.type_combo.get_selected() == 1 if self.type_combo else False
        )
        # Control visibility of switch and container based on session type
        self.post_login_switch.set_sensitive(is_ssh_session)
        if (
            hasattr(self, "post_login_command_container")
            and self.post_login_command_container
        ):
            # Show container only if SSH session AND switch is active
            is_enabled = self.post_login_switch.get_active() and is_ssh_session
            self.post_login_command_container.set_visible(is_enabled)
        if not is_ssh_session:
            self.post_login_entry.remove_css_class("error")

    def _update_sftp_state(self) -> None:
        if (
            not self.sftp_switch
            or not self.sftp_local_entry
            or not self.sftp_remote_entry
        ):
            return
        if not hasattr(self, "sftp_local_row") or not hasattr(self, "sftp_remote_row"):
            return
        is_ssh_session = (
            self.type_combo.get_selected() == 1 if self.type_combo else False
        )
        self.sftp_switch.set_sensitive(is_ssh_session)
        is_enabled = self.sftp_switch.get_active() and is_ssh_session
        # Control visibility instead of just sensitivity
        self.sftp_local_row.set_visible(is_enabled)
        self.sftp_remote_row.set_visible(is_enabled)
        if not is_enabled:
            self.sftp_local_entry.remove_css_class("error")

    def _on_local_working_dir_changed(self, entry: Gtk.Entry) -> None:
        """Handle changes to the local working directory."""
        self._mark_changed()
        path_text = entry.get_text().strip()
        if path_text:
            try:
                path = Path(path_text).expanduser()
                if not path.exists() or not path.is_dir():
                    entry.add_css_class("error")
                else:
                    entry.remove_css_class("error")
            except Exception:
                entry.add_css_class("error")
        else:
            entry.remove_css_class("error")

    def _on_local_startup_command_changed(self, buffer: Gtk.TextBuffer) -> None:
        """Handle changes to the local startup commands."""
        self._mark_changed()

    def _on_browse_working_dir_clicked(self, button) -> None:
        """Browse for a working directory folder."""
        try:
            file_dialog = Gtk.FileDialog(
                title=_("Select Working Directory"), modal=True
            )
            home_dir = Path.home()
            current_path = self.local_working_dir_entry.get_text().strip()
            if current_path:
                try:
                    path = Path(current_path).expanduser()
                    if path.exists() and path.is_dir():
                        file_dialog.set_initial_folder(Gio.File.new_for_path(str(path)))
                    else:
                        file_dialog.set_initial_folder(
                            Gio.File.new_for_path(str(home_dir))
                        )
                except Exception:
                    file_dialog.set_initial_folder(Gio.File.new_for_path(str(home_dir)))
            else:
                file_dialog.set_initial_folder(Gio.File.new_for_path(str(home_dir)))
            file_dialog.select_folder(self, None, self._on_working_dir_dialog_response)
        except Exception as e:
            self.logger.error(f"Browse working dir dialog failed: {e}")
            self._show_error_dialog(
                _("File Dialog Error"), _("Failed to open folder browser")
            )

    def _on_working_dir_dialog_response(self, dialog, result) -> None:
        """Handle the working directory folder selection response."""
        try:
            folder = dialog.select_folder_finish(result)
            if folder and (path := folder.get_path()):
                self.local_working_dir_entry.set_text(path)
                self.local_working_dir_entry.remove_css_class("error")
        except GLib.Error as e:
            if not e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                self.logger.error(f"Folder dialog error: {e.message}")
                self._show_error_dialog(
                    _("Folder Selection Error"),
                    _("Failed to select folder: {}").format(e.message),
                )
        except Exception as e:
            self.logger.error(f"Folder dialog response handling failed: {e}")

    def _on_browse_key_clicked(self, button) -> None:
        try:
            file_dialog = Gtk.FileDialog(title=_("Select SSH Key"), modal=True)
            ssh_dir = get_ssh_directory()
            if ssh_dir.exists():
                file_dialog.set_initial_folder(Gio.File.new_for_path(str(ssh_dir)))
            file_dialog.open(self, None, self._on_file_dialog_response)
        except Exception as e:
            self.logger.error(f"Browse key dialog failed: {e}")
            self._show_error_dialog(
                _("File Dialog Error"), _("Failed to open file browser")
            )

    def _on_file_dialog_response(self, dialog, result) -> None:
        try:
            file = dialog.open_finish(result)
            if file and (path := file.get_path()):
                try:
                    validate_ssh_key_file(path)
                    self.key_path_entry.set_text(path)
                    self.key_path_entry.remove_css_class("error")
                except SSHKeyError as e:
                    self._show_error_dialog(_("Invalid SSH Key"), e.user_message)
        except GLib.Error as e:
            if not e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                self.logger.error(f"File dialog error: {e.message}")
                self._show_error_dialog(
                    _("File Selection Error"),
                    _("Failed to select file: {}").format(e.message),
                )
        except Exception as e:
            self.logger.error(f"File dialog response handling failed: {e}")

    def _on_test_connection_clicked(self, button) -> None:
        try:
            test_session = self._create_session_from_fields()
            if not test_session:
                self._show_error_dialog(
                    _("Validation Error"),
                    _("Please fill in all required SSH fields first."),
                )
                return
            self.testing_dialog = Adw.MessageDialog(
                transient_for=self,
                title=_("Testing Connection..."),
                body=_("Attempting to connect to {host}...").format(
                    host=test_session.host
                ),
            )
            spinner = Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER, margin_top=12)
            self.testing_dialog.set_extra_child(spinner)
            self.testing_dialog.present()
            thread = threading.Thread(
                target=self._run_test_in_thread, args=(test_session,)
            )
            thread.start()
        except Exception as e:
            self.logger.error(f"Test connection setup failed: {e}")
            if hasattr(self, "testing_dialog"):
                self.testing_dialog.close()
            self._show_error_dialog(
                _("Test Connection Error"),
                _("Failed to start connection test: {}").format(e),
            )

    def _create_session_from_fields(self) -> Optional[SessionItem]:
        if (
            not self.host_entry.get_text().strip()
            or not self.user_entry.get_text().strip()
        ):
            return None
        return SessionItem(
            name="Test Connection",
            session_type="ssh",
            host=self.host_entry.get_text().strip(),
            user=self.user_entry.get_text().strip(),
            port=int(self.port_entry.get_value()),
            auth_type="key" if self.auth_combo.get_selected() == 0 else "password",
            auth_value=self._get_auth_value(),
        )

    def _run_test_in_thread(self, test_session: SessionItem):
        # Lazy import to defer loading until actually testing connection
        from ...terminal.spawner import get_spawner
        spawner = get_spawner()
        success, message = spawner.test_ssh_connection(test_session)
        GLib.idle_add(self._on_test_finished, success, message)

    def _on_test_finished(self, success: bool, message: str):
        if hasattr(self, "testing_dialog"):
            self.testing_dialog.close()
        if success:
            result_dialog = Adw.MessageDialog(
                transient_for=self,
                title=_("Connection Successful"),
                body=_("Successfully connected to the SSH server."),
            )
            result_dialog.add_response("ok", _("OK"))
            result_dialog.present()
        else:
            self._show_error_dialog(
                _("Connection Failed"),
                _("Could not connect to the SSH server."),
                details=message,
            )
        return False

    def _on_cancel_clicked(self, button) -> None:
        try:
            if self._has_changes:
                self._show_warning_dialog(
                    _("Unsaved Changes"),
                    _("You have unsaved changes. Are you sure you want to cancel?"),
                    lambda: self.close(),
                )
            else:
                self.close()
        except Exception as e:
            self.logger.error(f"Cancel handling failed: {e}")
            self.close()

    def _on_save_clicked(self, button) -> None:
        """Handles the save button click by delegating to the SessionOperations layer."""
        try:
            updated_session = self._build_updated_session()
            if not updated_session:
                return  # Validation failed and showed a dialog

            operations = self.parent_window.session_operations
            if self.is_new_item:
                result = operations.add_session(updated_session)
            else:
                result = operations.update_session(self.position, updated_session)

            if result and result.success:
                self.logger.info(
                    f"Session operation successful: {updated_session.name}"
                )
                # Tree refresh is handled automatically via AppSignals
                self.close()
            elif result:
                self._show_error_dialog(_("Save Error"), result.message)
        except Exception as e:
            self.logger.error(f"Save handling failed: {e}")
            self._show_error_dialog(
                _("Save Error"), _("An unexpected error occurred while saving.")
            )

    def _build_updated_session(self) -> Optional[SessionItem]:
        """Builds a SessionItem from dialog fields and performs validation."""
        self._clear_validation_errors()
        if not self._validate_basic_fields():
            return None
        is_local = self.type_combo.get_selected() == 0
        if is_local and not self._validate_local_fields():
            return None
        if not is_local and not self._validate_ssh_fields():
            return None

        # Create a new SessionItem instance with the data from the form
        session_data = self.editing_session.to_dict()
        session_data.update({
            "name": self.name_row.get_text().strip(),
            "session_type": "local" if self.type_combo.get_selected() == 0 else "ssh",
        })

        # Per-session highlighting overrides (tri-state)
        if hasattr(self, "highlighting_customize_switch") and not self.highlighting_customize_switch.get_active():
            # Customization is off => equivalent to "Automatic" for all.
            session_data["output_highlighting"] = None
            session_data["command_specific_highlighting"] = None
            session_data["cat_colorization"] = None
            session_data["shell_input_highlighting"] = None
        else:
            if hasattr(self, "output_highlighting_row"):
                session_data["output_highlighting"] = self._selected_to_tri_state(
                    self.output_highlighting_row.get_selected()
                )
            if hasattr(self, "command_specific_highlighting_row"):
                session_data["command_specific_highlighting"] = self._selected_to_tri_state(
                    self.command_specific_highlighting_row.get_selected()
                )
            if hasattr(self, "cat_colorization_row"):
                session_data["cat_colorization"] = self._selected_to_tri_state(
                    self.cat_colorization_row.get_selected()
                )
            if hasattr(self, "shell_input_highlighting_row"):
                session_data["shell_input_highlighting"] = self._selected_to_tri_state(
                    self.shell_input_highlighting_row.get_selected()
                )

        rgba = self.color_button.get_rgba()
        if rgba.alpha > 0:  # Check if a color is set
            session_data["tab_color"] = rgba.to_string()
        else:
            session_data["tab_color"] = None

        if (
            hasattr(self, "folder_combo")
            and self.folder_combo
            and (selected_item := self.folder_combo.get_selected_item())
        ):
            session_data["folder_path"] = self.folder_paths_map.get(
                selected_item.get_string(), ""
            )

        post_login_enabled = (
            self.post_login_switch.get_active()
            if self.post_login_switch and self.type_combo.get_selected() == 1
            else False
        )
        post_login_command = (
            self.post_login_entry.get_text().strip()
            if self.post_login_entry
            else ""
        )
        session_data["post_login_command_enabled"] = post_login_enabled
        session_data["post_login_command"] = (
            post_login_command if post_login_enabled else ""
        )

        sftp_enabled = (
            self.sftp_switch.get_active()
            if self.sftp_switch and self.type_combo.get_selected() == 1
            else False
        )
        local_dir = (
            self.sftp_local_entry.get_text().strip()
            if self.sftp_local_entry
            else ""
        )
        remote_dir = (
            self.sftp_remote_entry.get_text().strip()
            if self.sftp_remote_entry
            else ""
        )
        session_data["sftp_session_enabled"] = sftp_enabled
        session_data["sftp_local_directory"] = local_dir
        session_data["sftp_remote_directory"] = remote_dir
        session_data["port_forwardings"] = (
            copy.deepcopy(self.port_forwardings)
            if session_data["session_type"] == "ssh"
            else []
        )
        session_data["x11_forwarding"] = (
            self.x11_switch.get_active()
            if self.x11_switch and session_data["session_type"] == "ssh"
            else False
        )

        raw_password = ""
        if session_data["session_type"] == "ssh":
            session_data.update({
                "host": self.host_entry.get_text().strip(),
                "user": self.user_entry.get_text().strip(),
                "port": int(self.port_entry.get_value()),
                "auth_type": "key"
                if self.auth_combo.get_selected() == 0
                else "password",
            })
            if session_data["auth_type"] == "key":
                session_data["auth_value"] = self.key_path_entry.get_text().strip()
            else:
                raw_password = self.password_entry.get_text()
                session_data["auth_value"] = ""  # Will be stored in keyring
            # Clear local terminal fields for SSH sessions
            session_data["local_working_directory"] = ""
            session_data["local_startup_command"] = ""
        else:
            session_data.update({
                "host": "",
                "user": "",
                "auth_type": "",
                "auth_value": "",
            })
            session_data["sftp_session_enabled"] = False
            session_data["port_forwardings"] = []
            session_data["x11_forwarding"] = False
            # Set local terminal fields
            session_data["local_working_directory"] = (
                self.local_working_dir_entry.get_text().strip()
                if hasattr(self, "local_working_dir_entry")
                else ""
            )
            # Get startup commands from BashTextView
            startup_commands = ""
            if hasattr(self, "local_startup_command_view"):
                startup_commands = self.local_startup_command_view.get_text().strip()
            session_data["local_startup_command"] = startup_commands

        updated_session = SessionItem.from_dict(session_data)
        if updated_session.uses_password_auth() and raw_password:
            updated_session.auth_value = raw_password

        if not updated_session.validate():
            errors = updated_session.get_validation_errors()
            self._show_error_dialog(
                _("Validation Error"),
                _("Session validation failed:\n{}").format("\n".join(errors)),
            )
            return None

        return updated_session

    def _validate_basic_fields(self) -> bool:
        return self._validate_required_field(self.name_row, _("Session name"))

    def _validate_local_fields(self) -> bool:
        """Validate local terminal specific fields."""
        valid = True
        if hasattr(self, "local_working_dir_entry"):
            path_text = self.local_working_dir_entry.get_text().strip()
            if path_text:
                try:
                    path = Path(path_text).expanduser()
                    if not path.exists() or not path.is_dir():
                        self.local_working_dir_entry.add_css_class("error")
                        self._validation_errors.append(
                            _("Working directory must exist and be a folder.")
                        )
                        valid = False
                    else:
                        self.local_working_dir_entry.remove_css_class("error")
                except Exception:
                    self.local_working_dir_entry.add_css_class("error")
                    self._validation_errors.append(_("Invalid working directory path."))
                    valid = False
        if not valid and self._validation_errors:
            self._show_error_dialog(
                _("Validation Error"),
                "\n".join(self._validation_errors),
            )
        return valid

    def _validate_ssh_fields(self) -> bool:
        valid = True
        if not self._validate_required_field(self.host_entry, _("Host")):
            valid = False
        else:
            hostname = self.host_entry.get_text().strip()
            try:
                validate_ssh_hostname(hostname)
                self.host_entry.remove_css_class("error")
            except HostnameValidationError as e:
                self.host_entry.add_css_class("error")
                self._validation_errors.append(e.user_message)
                valid = False
        if self.auth_combo.get_selected() == 0:
            key_path = self.key_path_entry.get_text().strip()
            if key_path:
                try:
                    validate_ssh_key_file(key_path)
                    self.key_path_entry.remove_css_class("error")
                except SSHKeyError as e:
                    self.key_path_entry.add_css_class("error")
                    self._validation_errors.append(e.user_message)
                    valid = False
        if self.post_login_switch and self.post_login_entry:
            if (
                self.post_login_switch.get_active()
                and not self.post_login_entry.get_text().strip()
            ):
                self.post_login_entry.add_css_class("error")
                self._validation_errors.append(
                    _("Post-login command cannot be empty when enabled.")
                )
                valid = False
            else:
                self.post_login_entry.remove_css_class("error")
        if self.sftp_switch and self.sftp_switch.get_active():
            if self.sftp_local_entry:
                local_dir = self.sftp_local_entry.get_text().strip()
                if local_dir:
                    try:
                        local_path = Path(local_dir).expanduser()
                        if not local_path.exists() or not local_path.is_dir():
                            self.sftp_local_entry.add_css_class("error")
                            self._validation_errors.append(
                                _("SFTP local directory must exist and be a directory.")
                            )
                            valid = False
                        else:
                            self.sftp_local_entry.remove_css_class("error")
                    except Exception:
                        self.sftp_local_entry.add_css_class("error")
                        self._validation_errors.append(
                            _("SFTP local directory must exist and be a directory.")
                        )
                        valid = False
                else:
                    self.sftp_local_entry.remove_css_class("error")
        if not valid and self._validation_errors:
            self._show_error_dialog(
                _("SSH Validation Error"),
                _("SSH configuration errors:\n{}").format(
                    "\n".join(self._validation_errors)
                ),
            )
        return valid

    def _get_auth_value(self) -> str:
        if self.type_combo.get_selected() == 0:
            return ""
        elif self.auth_combo.get_selected() == 0:
            return self.key_path_entry.get_text().strip()
        else:
            return self.password_entry.get_text()
