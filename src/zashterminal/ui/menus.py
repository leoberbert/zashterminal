# zashterminal/ui/menus.py

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from ..helpers import is_valid_url
from ..settings.config import DefaultSettings
from ..settings.manager import SettingsManager
from ..utils.icons import icon_button
from ..utils.translation_utils import _


class ThemeSelectorWidget(Gtk.Box):
    """Custom widget for selecting the GTK color scheme."""

    def __init__(self, settings_manager: SettingsManager, parent_window):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=30)
        self.settings_manager = settings_manager
        self.parent_window = parent_window
        self.style_manager = Adw.StyleManager.get_default()

        self.add_css_class("themeselector")
        self.set_halign(Gtk.Align.CENTER)

        self.system_button = Gtk.CheckButton(tooltip_text=_("Follow System Style"))
        self.system_button.add_css_class("follow")
        self.system_button.add_css_class("theme-selector")
        self.system_button.connect("toggled", self._on_theme_changed, "default")

        self.light_button = Gtk.CheckButton(
            group=self.system_button, tooltip_text=_("Light Style")
        )
        self.light_button.add_css_class("light")
        self.light_button.add_css_class("theme-selector")
        self.light_button.connect("toggled", self._on_theme_changed, "light")

        self.dark_button = Gtk.CheckButton(
            group=self.system_button, tooltip_text=_("Dark Style")
        )
        self.dark_button.add_css_class("dark")
        self.dark_button.add_css_class("theme-selector")
        self.dark_button.connect("toggled", self._on_theme_changed, "dark")

        self.terminal_button = Gtk.CheckButton(
            group=self.system_button, tooltip_text=_("Match Terminal Colors")
        )
        self.terminal_button.add_css_class("terminal")
        self.terminal_button.add_css_class("theme-selector")
        self.terminal_button.connect("toggled", self._on_theme_changed, "terminal")

        self.append(self.system_button)
        self.append(self.light_button)
        self.append(self.dark_button)
        self.append(self.terminal_button)

        self._update_button_state()

    def _on_theme_changed(self, button: Gtk.CheckButton, theme: str):
        if not button.get_active():
            return
        # Just set the setting. The SettingsManager will handle the theme application logic.
        self.settings_manager.set("gtk_theme", theme)

    def _update_button_state(self):
        current_theme = self.settings_manager.get("gtk_theme", "default")
        if current_theme == "light":
            self.light_button.set_active(True)
        elif current_theme == "dark":
            self.dark_button.set_active(True)
        elif current_theme == "terminal":
            self.terminal_button.set_active(True)
        else:
            self.system_button.set_active(True)


class FontSizerWidget(Gtk.CenterBox):
    """Custom widget for changing the base font size."""

    def __init__(self, parent_window):
        super().__init__()
        self.parent_window = parent_window
        self.settings_manager = parent_window.settings_manager

        self.add_css_class("font-sizer")

        zoom_controls_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)

        decrement_btn = icon_button("zoom-out-symbolic")
        decrement_btn.add_css_class("flat")
        decrement_btn.connect("clicked", self._on_decrement)

        font_size_button = Gtk.Button(css_classes=["flat"])
        font_size_button.connect("clicked", self._on_reset)

        self.font_size_label = Gtk.Label(label="12 pt", halign=Gtk.Align.CENTER)
        self.font_size_label.set_size_request(60, -1)
        font_size_button.set_child(self.font_size_label)

        increment_btn = icon_button("zoom-in-symbolic")
        increment_btn.add_css_class("flat")
        increment_btn.connect("clicked", self._on_increment)

        zoom_controls_box.append(decrement_btn)
        zoom_controls_box.append(font_size_button)
        zoom_controls_box.append(increment_btn)

        self.set_center_widget(zoom_controls_box)
        self.update_display()

    def _parse_font_string(self, font_string: str) -> tuple[str, int]:
        try:
            parts = font_string.rsplit(" ", 1)
            family = parts[0]
            size = int(parts[1])
            return family, size
        except (IndexError, ValueError):
            return "Monospace", 12

    def _change_font_size(self, delta: int):
        current_font = self.settings_manager.get("font")
        family, size = self._parse_font_string(current_font)
        new_size = max(6, min(72, size + delta))
        new_font_string = f"{family} {new_size}"
        self.settings_manager.set("font", new_font_string)
        self.update_display()

    def _on_decrement(self, button):
        self._change_font_size(-1)

    def _on_increment(self, button):
        self._change_font_size(1)

    def _on_reset(self, button):
        default_font = DefaultSettings.get_defaults()["font"]
        self.settings_manager.set("font", default_font)
        self.update_display()

    def update_display(self):
        font_string = self.settings_manager.get("font")
        _family, size = self._parse_font_string(font_string)
        self.font_size_label.set_text(f"{size} pt")


class MainApplicationMenu:
    """Factory for creating the main application popover menu.

    Built as a native ``Gtk.PopoverMenu`` so sections, the import submenu,
    keyboard navigation and accelerator labels come from GTK.
    """

    @staticmethod
    def create_main_popover(parent_window) -> tuple[Gtk.PopoverMenu, FontSizerWidget]:
        menu = Gio.Menu()

        quick = Gio.Menu()
        for custom_id in ("theme-selector", "font-sizer"):
            item = Gio.MenuItem.new(None, None)
            item.set_attribute_value("custom", GLib.Variant.new_string(custom_id))
            quick.append_item(item)
        menu.append_section(None, quick)

        window_section = Gio.Menu()
        window_section.append(_("New Window"), "win.new-window")
        menu.append_section(None, window_section)

        customize = Gio.Menu()
        customize.append(_("Preferences"), "win.preferences")
        customize.append(_("Colors & Highlighting"), "win.highlight-settings")
        customize.append(_("AI Assistant"), "win.configure-ai")
        customize.append(_("Keyboard Shortcuts"), "win.shortcuts")
        menu.append_section(_("Customize"), customize)

        imports = Gio.Menu()
        # Product names: identical in every language.
        imports.append("SecureCRT…", "win.import-securecrt-sessions")
        imports.append("PuTTY…", "win.import-putty-sessions")
        imports.append("Multi PuTTY…", "win.import-multiputty-sessions")
        imports.append("mRemoteNG…", "win.import-mremoteng-sessions")
        sessions = Gio.Menu()
        sessions.append_submenu(_("Import Sessions"), imports)
        menu.append_section(_("Sessions"), sessions)

        tools = Gio.Menu()
        tools.append_item(MainApplicationMenu._tftp_menu_item(parent_window))
        menu.append_section(_("Tools"), tools)

        app_section = Gio.Menu()
        app_section.append(_("About"), "app.about")
        app_section.append(_("Quit"), "app.quit")
        menu.append_section(None, app_section)

        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.add_css_class("zashterminal-popover")
        popover.tftp_section = tools

        font_sizer_widget = FontSizerWidget(parent_window)
        popover.add_child(
            ThemeSelectorWidget(parent_window.settings_manager, parent_window),
            "theme-selector",
        )
        popover.add_child(font_sizer_widget, "font-sizer")
        return popover, font_sizer_widget

    @staticmethod
    def _tftp_menu_item(parent_window) -> Gio.MenuItem:
        is_running = bool(
            getattr(getattr(parent_window, "tftp_server", None), "is_running", False)
        )
        label = _("Stop TFTP Server") if is_running else _("Start TFTP Server")
        return Gio.MenuItem.new(label, "win.toggle-tftp-server")

    @staticmethod
    def update_tftp_server_label(parent_window, popover: Gtk.Popover) -> None:
        section = getattr(popover, "tftp_section", None)
        if section is None:
            return
        section.remove(0)
        section.insert_item(0, MainApplicationMenu._tftp_menu_item(parent_window))


def create_session_menu(
    session_item,
    session_store,
    position,
    folder_store=None,
    clipboard_has_content=False,
) -> Gio.Menu:
    """Factory function to create a session context menu model."""
    menu = Gio.Menu()
    if session_item.is_ssh():
        sftp_item = Gio.MenuItem.new(_("Connect with SFTP"), "win.connect-sftp")
        sftp_item.set_icon(Gio.ThemedIcon.new("folder-remote-symbolic"))
        menu.append_item(sftp_item)
        menu.append_section(None, Gio.Menu())
    menu.append(_("Edit"), "win.edit-session")
    menu.append(_("Duplicate"), "win.duplicate-session")
    menu.append(_("Rename"), "win.rename-session")
    menu.append_section(None, Gio.Menu())
    if folder_store and folder_store.get_n_items() > 0:
        menu.append(_("Move to Folder..."), "win.move-session-to-folder")
    menu.append_section(None, Gio.Menu())
    menu.append(_("Delete"), "win.delete-session")
    return menu


def create_folder_menu(
    folder_item,
    folder_store,
    position,
    session_store=None,
    clipboard_has_content=False,
) -> Gio.Menu:
    """Factory function to create a folder context menu model."""
    menu = Gio.Menu()
    menu.append(_("Edit"), "win.edit-folder")
    menu.append(_("Add Session Here"), "win.add-session-to-folder")
    menu.append(_("Rename"), "win.rename-folder")
    menu.append_section(None, Gio.Menu())
    if clipboard_has_content:
        menu.append(_("Paste"), "win.paste-item")
    menu.append_section(None, Gio.Menu())
    menu.append(_("Delete"), "win.delete-folder")
    return menu


def create_root_menu(clipboard_has_content=False) -> Gio.Menu:
    """Factory function to create a root tree view context menu model."""
    menu = Gio.Menu()
    menu.append(_("Add Session"), "win.add-session-root")
    menu.append(_("Add Folder"), "win.add-folder-root")
    if clipboard_has_content:
        menu.append_section(None, Gio.Menu())
        menu.append(_("Paste to Root"), "win.paste-item-root")
    return menu


def create_terminal_menu(
    terminal, click_x=None, click_y=None, settings_manager=None
) -> Gio.Menu:
    """Factory function to create a terminal context menu model."""
    menu = Gio.Menu()
    url_at_click = None
    if click_x is not None and click_y is not None and hasattr(terminal, "match_check"):
        try:
            char_width = terminal.get_char_width()
            char_height = terminal.get_char_height()
            if char_width > 0 and char_height > 0:
                col = int(click_x / char_width)
                row = int(click_y / char_height)
                match_result = terminal.match_check(col, row)
                if match_result and len(match_result) >= 2:
                    matched_text = match_result[0]
                    if matched_text and is_valid_url(matched_text):
                        url_at_click = matched_text
        except Exception:
            pass

    if url_at_click:
        url_section = Gio.Menu()
        terminal._context_menu_url = url_at_click
        url_section.append(_("Open Link"), "win.open-url")
        url_section.append(_("Copy Link"), "win.copy-url")
        menu.append_section(None, url_section)

    standard_section = Gio.Menu()
    standard_section.append(_("Copy"), "win.copy")
    standard_section.append(_("Paste"), "win.paste")
    standard_section.append(_("Select All"), "win.select-all")
    standard_section.append(_("Clear Session"), "win.clear-session")
    menu.append_section(None, standard_section)

    # AI Assistant section - only show if enabled and text is selected
    if settings_manager and settings_manager.get("ai_assistant_enabled", False):
        # Check if there's selected text
        has_selection = (
            terminal.get_has_selection()
            if hasattr(terminal, "get_has_selection")
            else False
        )
        if has_selection:
            ai_section = Gio.Menu()
            ai_item = Gio.MenuItem.new(_("Ask AI"), "win.ask-ai-selection")
            ai_item.set_icon(Gio.ThemedIcon.new("avatar-default-symbolic"))
            ai_section.append_item(ai_item)
            menu.append_section(None, ai_section)

    split_section = Gio.Menu()
    split_h_item = Gio.MenuItem.new(_("Split Left/Right"), "win.split-horizontal")
    split_h_item.set_icon(Gio.ThemedIcon.new("view-split-horizontal-symbolic"))
    split_section.append_item(split_h_item)
    split_v_item = Gio.MenuItem.new(_("Split Top/Bottom"), "win.split-vertical")
    split_v_item.set_icon(Gio.ThemedIcon.new("view-split-vertical-symbolic"))
    split_section.append_item(split_v_item)
    split_section.append(_("Close Pane"), "win.close-pane")
    menu.append_section(None, split_section)

    return menu
