# zashterminal/ui/dialogs/preferences_dialog.py

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GObject, Gtk

from ...settings.manager import SettingsManager
from ...utils.logger import get_logger
from ...utils.translation_utils import _


class PreferencesDialog(Adw.PreferencesWindow):
    """Enhanced preferences dialog with comprehensive settings management."""

    __gsignals__ = {
        "transparency-changed": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
        "headerbar-transparency-changed": (
            GObject.SignalFlags.RUN_FIRST,
            None,
            (float,),
        ),
        "font-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "setting-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, object)),
    }

    def __init__(self, parent_window, settings_manager: SettingsManager):
        super().__init__(
            title=_("Preferences"),
            transient_for=parent_window,
            modal=False,
            hide_on_close=True,
            default_width=900,
            default_height=680,
            search_enabled=True,
        )
        self.add_css_class("zashterminal-dialog")
        self.logger = get_logger("zashterminal.ui.dialogs.preferences")
        self.settings_manager = settings_manager
        self._setup_appearance_page()
        self._setup_terminal_page()
        self._setup_profiles_page()
        self._setup_advanced_page()
        self.logger.info("Preferences dialog initialized")

    def _create_switch_row(
        self,
        title: str,
        subtitle: str,
        setting_key: str,
        default_value: bool = False,
    ) -> Adw.SwitchRow:
        """Create a standard switch row bound to a setting.

        Args:
            title: Row title
            subtitle: Row subtitle/description
            setting_key: Settings key to bind to
            default_value: Default value if setting not found

        Returns:
            Configured Adw.SwitchRow
        """
        row = Adw.SwitchRow(title=title, subtitle=subtitle)
        row.set_active(self.settings_manager.get(setting_key, default_value))
        row.connect(
            "notify::active",
            lambda r, _: self._on_setting_changed(setting_key, r.get_active()),
        )
        return row

    def _setup_appearance_page(self) -> None:
        page = Adw.PreferencesPage(
            title=_("Appearance"), icon_name="preferences-desktop-display-symbolic"
        )
        self.add(page)

        font_group = Adw.PreferencesGroup()
        page.add(font_group)

        font_row = Adw.ActionRow(
            title=_("Terminal Font"),
        )
        font_button = Gtk.FontButton()
        font_button.set_valign(Gtk.Align.CENTER)
        font_button.set_font(self.settings_manager.get("font", "Monospace 10"))
        # Filter to show only monospace fonts
        font_button.set_filter_func(self._font_filter_func)
        font_button.connect("font-set", self._on_font_changed)
        font_row.add_suffix(font_button)
        font_row.set_activatable_widget(font_button)
        font_group.add(font_row)

        line_spacing_row = Adw.ActionRow(
            title=_("Line Spacing"),
        )
        spacing_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.spacing_spin = Gtk.SpinButton.new_with_range(0.8, 2.0, 0.05)
        self.spacing_spin.set_valign(Gtk.Align.CENTER)
        self.spacing_spin.set_value(self.settings_manager.get("line_spacing", 1.0))
        self.spacing_spin.connect("value-changed", self._on_line_spacing_changed)
        spacing_box.append(self.spacing_spin)
        line_spacing_row.add_suffix(spacing_box)
        line_spacing_row.set_activatable_widget(self.spacing_spin)
        font_group.add(line_spacing_row)

        misc_group = Adw.PreferencesGroup()
        page.add(misc_group)

        transparency_row = Adw.ActionRow(
            title=_("Terminal Transparency"),
        )
        self.transparency_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 100, 1
        )
        self.transparency_scale.set_value(self.settings_manager.get("transparency", 0))
        self.transparency_scale.set_draw_value(True)
        self.transparency_scale.set_value_pos(Gtk.PositionType.RIGHT)
        self.transparency_scale.set_hexpand(True)
        self.transparency_scale.connect("value-changed", self._on_transparency_changed)
        transparency_row.add_suffix(self.transparency_scale)
        transparency_row.set_activatable_widget(self.transparency_scale)
        misc_group.add(transparency_row)

        headerbar_transparency_row = Adw.ActionRow(
            title=_("Headerbar Transparency"),
        )
        self.headerbar_transparency_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 100, 1
        )
        self.headerbar_transparency_scale.set_value(
            self.settings_manager.get("headerbar_transparency", 0)
        )
        self.headerbar_transparency_scale.set_draw_value(True)
        self.headerbar_transparency_scale.set_value_pos(Gtk.PositionType.RIGHT)
        self.headerbar_transparency_scale.set_hexpand(True)
        self.headerbar_transparency_scale.connect(
            "value-changed", self._on_headerbar_transparency_changed
        )
        headerbar_transparency_row.add_suffix(self.headerbar_transparency_scale)
        headerbar_transparency_row.set_activatable_widget(
            self.headerbar_transparency_scale
        )
        misc_group.add(headerbar_transparency_row)

        bold_bright_row = self._create_switch_row(
            _("Use Bright Colors for Bold Text"),
            "",
            "bold_is_bright",
            default_value=True,
        )
        misc_group.add(bold_bright_row)

        auto_hide_sidebar_row = Adw.SwitchRow(
            title=_("Auto-Hide Sessions Panel"),
            subtitle=_("Hide panel when activating sessions"),
        )
        auto_hide_sidebar_row.set_active(
            self.settings_manager.get("auto_hide_sidebar", False)
        )
        auto_hide_sidebar_row.connect(
            "notify::active",
            lambda r, _: self._on_auto_hide_sidebar_changed(r.get_active()),
        )
        misc_group.add(auto_hide_sidebar_row)

        text_blink_row = Adw.ComboRow(
            title=_("Blinking Text"),
        )
        text_blink_row.set_model(Gtk.StringList.new([_("When focused"), _("Always")]))
        text_blink_row.set_selected(self.settings_manager.get("text_blink_mode", 0))
        text_blink_row.connect("notify::selected", self._on_text_blink_mode_changed)
        misc_group.add(text_blink_row)

        tab_alignment_row = Adw.ComboRow(
            title=_("Tab Alignment"),
        )
        tab_alignment_row.set_model(Gtk.StringList.new([_("Left"), _("Center")]))
        current_alignment = self.settings_manager.get("tab_alignment", "center")
        selected_index = 0 if current_alignment == "left" else 1
        tab_alignment_row.set_selected(selected_index)
        tab_alignment_row.connect("notify::selected", self._on_tab_alignment_changed)
        misc_group.add(tab_alignment_row)

        # Icon Theme Strategy - Performance optimization for GTK4 startup
        icon_theme_row = Adw.ComboRow(
            title=_("Icon Theme"),
        )
        icon_theme_row.set_model(
            Gtk.StringList.new([_("Zashterminal Icons (Bundled)"), _("System Icons")])
        )
        current_strategy = self.settings_manager.get("icon_theme_strategy", "zashterminal")
        icon_strategy_index = 0 if current_strategy == "zashterminal" else 1
        icon_theme_row.set_selected(icon_strategy_index)
        icon_theme_row.connect("notify::selected", self._on_icon_theme_changed)
        misc_group.add(icon_theme_row)

        # Headerbar buttons behavior when maximized
        # (for KDE Plasma Active Window Control / Borderless Maximized Windows)
        headerbar_buttons_row = Adw.ComboRow(
            title=_("Window Buttons When Maximized"),
            subtitle=_("For KDE Plasma panel integration"),
        )
        headerbar_buttons_row.set_model(
            Gtk.StringList.new([
                _("Auto-detect"),
                _("Always hide"),
                _("Never hide"),
            ])
        )
        current_btn_setting = self.settings_manager.get(
            "hide_headerbar_buttons_when_maximized", "auto"
        )
        btn_setting_map = {"auto": 0, "always": 1, "never": 2}
        headerbar_buttons_row.set_selected(btn_setting_map.get(current_btn_setting, 0))
        headerbar_buttons_row.connect(
            "notify::selected", self._on_headerbar_buttons_mode_changed
        )
        misc_group.add(headerbar_buttons_row)

    def _setup_terminal_page(self) -> None:
        page = Adw.PreferencesPage(
            title=_("Terminal"), icon_name="utilities-terminal-symbolic"
        )
        self.add(page)

        # Behavior group at the top of Terminal page
        behavior_group = Adw.PreferencesGroup()
        page.add(behavior_group)

        # New instance behavior setting
        instance_behavior_row = Adw.ComboRow(
            title=_("When Already Running"),
            subtitle=_("Action when the application is launched while already open"),
        )
        behavior_map = ["new_tab", "new_window", "focus_existing"]
        behavior_strings = [
            _("Open a new tab"),
            _("Open a new window"),
            _("Focus existing window"),
        ]
        instance_behavior_row.set_model(Gtk.StringList.new(behavior_strings))
        current_behavior = self.settings_manager.get("new_instance_behavior", "new_tab")
        try:
            behavior_index = behavior_map.index(current_behavior)
        except ValueError:
            behavior_index = 0
        instance_behavior_row.set_selected(behavior_index)
        instance_behavior_row.connect(
            "notify::selected", self._on_instance_behavior_changed, behavior_map
        )
        behavior_group.add(instance_behavior_row)

        cursor_group = Adw.PreferencesGroup()
        page.add(cursor_group)

        cursor_shape_row = Adw.ComboRow(
            title=_("Cursor Shape"),
        )
        cursor_shape_row.set_model(
            Gtk.StringList.new([_("Block"), _("I-Beam"), _("Underline")])
        )
        cursor_shape_row.set_selected(self.settings_manager.get("cursor_shape", 0))
        cursor_shape_row.connect("notify::selected", self._on_cursor_shape_changed)
        cursor_group.add(cursor_shape_row)

        cursor_blink_row = Adw.ComboRow(
            title=_("Cursor Blinking"),
        )
        cursor_blink_row.set_model(
            Gtk.StringList.new([_("Follow System"), _("On"), _("Off")])
        )
        cursor_blink_row.set_selected(self.settings_manager.get("cursor_blink", 0))
        cursor_blink_row.connect("notify::selected", self._on_cursor_blink_changed)
        cursor_group.add(cursor_blink_row)

        scrolling_group = Adw.PreferencesGroup()
        page.add(scrolling_group)

        scrollback_row = Adw.ActionRow(
            title=_("Scrollback Lines"),
            subtitle=_("0 for unlimited"),
        )
        scrollback_spin = Gtk.SpinButton.new_with_range(0, 1000000, 1000)
        scrollback_spin.set_valign(Gtk.Align.CENTER)
        scrollback_spin.set_value(self.settings_manager.get("scrollback_lines", 10000))
        scrollback_spin.connect("value-changed", self._on_scrollback_changed)
        scrollback_row.add_suffix(scrollback_spin)
        scrollback_row.set_activatable_widget(scrollback_spin)
        scrolling_group.add(scrollback_row)

        mouse_scroll_row = Adw.ActionRow(
            title=_("Mouse Scroll Sensitivity"),
            subtitle=_("Lower is slower"),
        )
        mouse_scroll_spin = Gtk.SpinButton.new_with_range(1, 500, 1)
        mouse_scroll_spin.set_valign(Gtk.Align.CENTER)
        mouse_scroll_spin.set_value(
            self.settings_manager.get("mouse_scroll_sensitivity", 30.0)
        )
        mouse_scroll_spin.connect(
            "value-changed", self._on_mouse_scroll_sensitivity_changed
        )
        mouse_scroll_row.add_suffix(mouse_scroll_spin)
        mouse_scroll_row.set_activatable_widget(mouse_scroll_spin)
        scrolling_group.add(mouse_scroll_row)

        touchpad_scroll_row = Adw.ActionRow(
            title=_("Touchpad Scroll Sensitivity"),
            subtitle=_("Lower is slower"),
        )
        touchpad_scroll_spin = Gtk.SpinButton.new_with_range(1, 500, 1)
        touchpad_scroll_spin.set_valign(Gtk.Align.CENTER)
        touchpad_scroll_spin.set_value(
            self.settings_manager.get("touchpad_scroll_sensitivity", 30.0)
        )
        touchpad_scroll_spin.connect(
            "value-changed", self._on_touchpad_scroll_sensitivity_changed
        )
        touchpad_scroll_row.add_suffix(touchpad_scroll_spin)
        touchpad_scroll_row.set_activatable_widget(touchpad_scroll_spin)
        scrolling_group.add(touchpad_scroll_row)

        scroll_on_insert_row = self._create_switch_row(
            _("Scroll on Paste"),
            "",
            "scroll_on_insert",
            default_value=True,
        )
        scrolling_group.add(scroll_on_insert_row)

        shell_group = Adw.PreferencesGroup()
        page.add(shell_group)

        login_shell_row = self._create_switch_row(
            _("Run Command as a Login Shell"),
            "",
            "use_login_shell",
            default_value=False,
        )
        shell_group.add(login_shell_row)

        bell_row = self._create_switch_row(
            _("Audible Bell"),
            "",
            "bell_sound",
            default_value=False,
        )
        shell_group.add(bell_row)

    def _setup_profiles_page(self) -> None:
        page = Adw.PreferencesPage(
            title=_("Profiles & Data"), icon_name="folder-saved-search-symbolic"
        )
        self.add(page)

        startup_group = Adw.PreferencesGroup()
        page.add(startup_group)

        restore_policy_row = Adw.ComboRow(
            title=_("On Startup"),
        )
        policy_map = ["always", "ask", "never"]
        policy_strings = [
            _("Always restore previous session"),
            _("Ask to restore previous session"),
            _("Never restore previous session"),
        ]
        restore_policy_row.set_model(Gtk.StringList.new(policy_strings))
        current_policy = self.settings_manager.get("session_restore_policy", "never")
        try:
            selected_index = policy_map.index(current_policy)
        except ValueError:
            selected_index = 2
        restore_policy_row.set_selected(selected_index)
        restore_policy_row.connect(
            "notify::selected", self._on_restore_policy_changed, policy_map
        )
        startup_group.add(restore_policy_row)

        backup_group = Adw.PreferencesGroup()
        page.add(backup_group)

        backup_now_row = Adw.ActionRow(
            title=_("Create Backup"),
            subtitle=_("Encrypted backup of sessions and settings"),
        )
        backup_now_button = Gtk.Button(label=_("Create Backup..."))
        backup_now_button.set_valign(Gtk.Align.CENTER)
        backup_now_button.connect("clicked", self._on_backup_now_clicked)
        backup_now_row.add_suffix(backup_now_button)
        backup_now_row.set_activatable_widget(backup_now_button)
        backup_group.add(backup_now_row)

        restore_row = Adw.ActionRow(
            title=_("Restore from Backup"),
        )
        restore_button = Gtk.Button(label=_("Restore..."))
        restore_button.set_valign(Gtk.Align.CENTER)
        restore_button.connect("clicked", self._on_restore_backup_clicked)
        restore_row.add_suffix(restore_button)
        restore_row.set_activatable_widget(restore_button)
        backup_group.add(restore_row)

        remote_edit_group = Adw.PreferencesGroup()
        page.add(remote_edit_group)

        use_tmp_dir_row = self._create_switch_row(
            _("Use System Temporary Directory"),
            _("Use /tmp for remote editing files"),
            "use_system_tmp_for_edit",
            default_value=False,
        )
        remote_edit_group.add(use_tmp_dir_row)

        clear_on_exit_row = self._create_switch_row(
            _("Clear Remote Edit Files on Exit"),
            "",
            "clear_remote_edit_files_on_exit",
            default_value=False,
        )
        remote_edit_group.add(clear_on_exit_row)

        ssh_group = Adw.PreferencesGroup()
        page.add(ssh_group)

        persist_row = Adw.ActionRow(
            title=_("SSH Connection Persistence"),
            subtitle=_("Seconds to keep connections alive (0 to disable)"),
        )
        persist_spin = Gtk.SpinButton.new_with_range(0, 3600, 60)
        persist_spin.set_valign(Gtk.Align.CENTER)
        persist_spin.set_value(
            self.settings_manager.get("ssh_control_persist_duration", 600)
        )
        persist_spin.connect("value-changed", self._on_ssh_persist_changed)
        persist_row.add_suffix(persist_spin)
        persist_row.set_activatable_widget(persist_spin)
        ssh_group.add(persist_row)

    def _setup_advanced_page(self) -> None:
        advanced_page = Adw.PreferencesPage(
            title=_("Advanced"), icon_name="preferences-other-symbolic"
        )
        self.add(advanced_page)

        features_group = Adw.PreferencesGroup()
        advanced_page.add(features_group)

        bidi_row = self._create_switch_row(
            _("Bidirectional Text Support"),
            _("For Arabic and Hebrew (affects performance)"),
            "bidi_enabled",
            default_value=False,
        )
        features_group.add(bidi_row)

        shaping_row = self._create_switch_row(
            _("Enable Arabic Text Shaping"),
            _("Render ligatures for Arabic script"),
            "enable_shaping",
            default_value=False,
        )
        features_group.add(shaping_row)

        sixel_row = self._create_switch_row(
            _("SIXEL Graphics Support"),
            _("Display SIXEL images (experimental)"),
            "sixel_enabled",
            default_value=True,
        )
        features_group.add(sixel_row)

        compatibility_group = Adw.PreferencesGroup()
        advanced_page.add(compatibility_group)

        backspace_row = Adw.ComboRow(
            title=_("Backspace Key"),
        )
        backspace_row.set_model(
            Gtk.StringList.new([
                _("Automatic"),
                _("ASCII BACKSPACE (^H)"),
                _("ASCII DELETE"),
                _("Escape Sequence"),
            ])
        )
        backspace_row.set_selected(self.settings_manager.get("backspace_binding", 0))
        backspace_row.connect("notify::selected", self._on_backspace_binding_changed)
        compatibility_group.add(backspace_row)

        delete_row = Adw.ComboRow(
            title=_("Delete Key"),
        )
        delete_row.set_model(
            Gtk.StringList.new([
                _("Automatic"),
                _("ASCII DELETE"),
                _("Escape Sequence"),
            ])
        )
        delete_row.set_selected(self.settings_manager.get("delete_binding", 0))
        delete_row.connect("notify::selected", self._on_delete_binding_changed)
        compatibility_group.add(delete_row)

        cjk_width_row = Adw.ComboRow(
            title=_("Ambiguous-width Characters"),
        )
        cjk_width_row.set_model(
            Gtk.StringList.new([_("Narrow (single-cell)"), _("Wide (double-cell)")])
        )
        cjk_width_row.set_selected(
            self.settings_manager.get("cjk_ambiguous_width", 1) - 1
        )
        cjk_width_row.connect("notify::selected", self._on_cjk_width_changed)
        compatibility_group.add(cjk_width_row)

        selection_group = Adw.PreferencesGroup()
        advanced_page.add(selection_group)

        word_chars_row = Adw.EntryRow(
            title=_("Word Characters"),
        )
        word_chars_row.set_text(
            self.settings_manager.get("word_char_exceptions", "-_.:/~")
        )
        word_chars_row.connect("changed", self._on_word_chars_changed)
        selection_group.add(word_chars_row)

        log_group = Adw.PreferencesGroup()
        advanced_page.add(log_group)

        log_to_file_row = self._create_switch_row(
            _("Save Logs to File"),
            "",
            "log_to_file",
            default_value=False,
        )
        log_group.add(log_to_file_row)

        log_level_row = Adw.ComboRow(
            title=_("Console Log Level"),
        )
        log_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        log_level_row.set_model(Gtk.StringList.new(log_levels))
        current_level = self.settings_manager.get("console_log_level", "ERROR")
        try:
            selected_index = log_levels.index(current_level.upper())
        except ValueError:
            selected_index = 3
        log_level_row.set_selected(selected_index)
        log_level_row.connect("notify::selected", self._on_log_level_changed)
        log_group.add(log_level_row)

        reset_group = Adw.PreferencesGroup()
        advanced_page.add(reset_group)
        reset_row = Adw.ActionRow(
            title=_("Reset All Settings"),
        )
        reset_button = Gtk.Button(label=_("Reset"), css_classes=["destructive-action"])
        reset_button.set_valign(Gtk.Align.CENTER)
        reset_button.connect("clicked", self._on_reset_settings_clicked)
        reset_row.add_suffix(reset_button)
        reset_row.set_activatable_widget(reset_button)
        reset_group.add(reset_row)

    def _on_font_changed(self, font_button) -> None:
        font = font_button.get_font()
        self.settings_manager.set("font", font)
        self.emit("font-changed", font)

    @staticmethod
    def _font_filter_func(family, _face):
        """Filter function to show only monospace fonts."""
        return family.is_monospace()

    def _on_line_spacing_changed(self, spin_button) -> None:
        value = spin_button.get_value()
        self._on_setting_changed("line_spacing", value)

    def _on_transparency_changed(self, scale) -> None:
        value = scale.get_value()
        self.settings_manager.set("transparency", value)
        self.emit("transparency-changed", value)

    def _on_headerbar_transparency_changed(self, scale) -> None:
        value = scale.get_value()
        self.settings_manager.set("headerbar_transparency", value)
        self.emit("headerbar-transparency-changed", value)

    def _on_restore_policy_changed(self, combo_row, _param, policy_map):
        index = combo_row.get_selected()
        if 0 <= index < len(policy_map):
            policy = policy_map[index]
            self._on_setting_changed("session_restore_policy", policy)

    def _on_instance_behavior_changed(self, combo_row, _param, behavior_map):
        index = combo_row.get_selected()
        if 0 <= index < len(behavior_map):
            behavior = behavior_map[index]
            self._on_setting_changed("new_instance_behavior", behavior)

    def _on_backup_now_clicked(self, button):
        app = self.get_transient_for().get_application()
        if app:
            app.activate_action("backup-now", None)

    def _on_restore_backup_clicked(self, button):
        app = self.get_transient_for().get_application()
        if app:
            app.activate_action("restore-backup", None)

    def _on_log_level_changed(self, combo_row, _param):
        selected_item = combo_row.get_selected_item()
        if selected_item:
            level_str = selected_item.get_string()
            self._on_setting_changed("console_log_level", level_str)

    def _on_scrollback_changed(self, spin_button) -> None:
        value = int(spin_button.get_value())
        self._on_setting_changed("scrollback_lines", value)

    def _on_mouse_scroll_sensitivity_changed(self, spin_button) -> None:
        value = spin_button.get_value()
        self._on_setting_changed("mouse_scroll_sensitivity", value)

    def _on_touchpad_scroll_sensitivity_changed(self, spin_button) -> None:
        value = spin_button.get_value()
        self._on_setting_changed("touchpad_scroll_sensitivity", value)

    def _on_cursor_shape_changed(self, combo_row, _param) -> None:
        index = combo_row.get_selected()
        self._on_setting_changed("cursor_shape", index)

    def _on_cursor_blink_changed(self, combo_row, _param) -> None:
        index = combo_row.get_selected()
        self._on_setting_changed("cursor_blink", index)

    def _on_text_blink_mode_changed(self, combo_row, _param) -> None:
        index = combo_row.get_selected()
        self._on_setting_changed("text_blink_mode", index)

    def _on_tab_alignment_changed(self, combo_row, _param) -> None:
        index = combo_row.get_selected()
        alignment = "left" if index == 0 else "center"
        self._on_setting_changed("tab_alignment", alignment)

    def _on_icon_theme_changed(self, combo_row, _param) -> None:
        """Handle icon theme strategy change.

        Note: This change requires application restart to take full effect
        since icon paths are configured at startup for performance.
        """
        index = combo_row.get_selected()
        strategy = "zashterminal" if index == 0 else "system"
        self._on_setting_changed("icon_theme_strategy", strategy)
        # Show restart notice
        self._show_restart_required_dialog(
            _("Icon Theme Changed"),
            _(
                "The icon theme change will take effect after restarting the application."
            ),
        )

    def _on_headerbar_buttons_mode_changed(self, combo_row, _param) -> None:
        """Handle window buttons visibility mode change for maximized windows."""
        index = combo_row.get_selected()
        mode_map = {0: "auto", 1: "always", 2: "never"}
        mode = mode_map.get(index, "auto")
        self._on_setting_changed("hide_headerbar_buttons_when_maximized", mode)

    def _show_restart_required_dialog(self, title: str, message: str) -> None:
        """Show a dialog informing the user that a restart is required."""
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=title,
            body=message,
        )
        dialog.add_response("ok", _("OK"))
        dialog.set_default_response("ok")
        dialog.present()

    def _on_word_chars_changed(self, entry_row):
        text = entry_row.get_text()
        self._on_setting_changed("word_char_exceptions", text)

    def _on_cjk_width_changed(self, combo_row, _param) -> None:
        value = combo_row.get_selected() + 1  # 0 -> 1 (Narrow), 1 -> 2 (Wide)
        self._on_setting_changed("cjk_ambiguous_width", value)

    def _on_backspace_binding_changed(self, combo_row, _param) -> None:
        index = combo_row.get_selected()
        self._on_setting_changed("backspace_binding", index)

    def _on_delete_binding_changed(self, combo_row, _param) -> None:
        index = combo_row.get_selected()
        self._on_setting_changed("delete_binding", index)

    def _on_ssh_persist_changed(self, spin_button) -> None:
        value = int(spin_button.get_value())
        self._on_setting_changed("ssh_control_persist_duration", value)

    def _on_setting_changed(self, key: str, value) -> None:
        self.settings_manager.set(key, value)
        self.emit("setting-changed", key, value)

    def _on_auto_hide_sidebar_changed(self, new_value: bool) -> None:
        """Handle auto-hide sidebar setting change with informational dialog."""
        current_value = self.settings_manager.get("auto_hide_sidebar", True)

        # If user is disabling auto-hide sidebar, show informational dialog
        if current_value and not new_value:
            self._show_sidebar_info_dialog()

        # Apply the setting
        self._on_setting_changed("auto_hide_sidebar", new_value)

    def _show_sidebar_info_dialog(self) -> None:
        """Show informational dialog about sidebar visibility changes."""
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Sessions Panel Visibility"),
            body=_(
                "The sessions panel visibility change will take effect when you close and reopen the application. "
                "You can also toggle the sessions panel manually using Ctrl+Shift+H."
            ),
        )
        dialog.add_response("ok", _("OK"))
        dialog.set_default_response("ok")
        dialog.present()

    def _on_reset_settings_clicked(self, button) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self,
            title=_("Reset All Settings"),
            body=_(
                "Are you sure you want to reset all settings to their default values? This action cannot be undone."
            ),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("reset", _("Reset All Settings"))
        dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(dlg, response_id):
            if response_id == "reset":
                try:
                    self.settings_manager.reset_to_defaults()
                    success_dialog = Adw.MessageDialog(
                        transient_for=self,
                        title=_("Settings Reset"),
                        body=_(
                            "All settings have been reset to their default values. Please restart the application for all changes to take effect."
                        ),
                    )
                    success_dialog.add_response("ok", _("OK"))
                    success_dialog.present()
                    self.logger.info("All settings reset to defaults")
                except Exception as e:
                    self.logger.error(f"Failed to reset settings: {e}")
                    error_dialog = Adw.MessageDialog(
                        transient_for=self,
                        title=_("Reset Failed"),
                        body=_("Failed to reset settings: {}").format(e),
                    )
                    error_dialog.add_response("ok", _("OK"))
                    error_dialog.present()
            dlg.close()

        dialog.connect("response", on_response)
        dialog.present()
