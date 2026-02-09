# zashterminal/ui/menus.py

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk

from ..helpers import accelerator_to_label, is_valid_url, safe_popover_popdown
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
    """Factory for creating the main application popover menu."""

    @staticmethod
    def create_main_popover(parent_window) -> tuple[Gtk.Popover, FontSizerWidget]:
        popover = Gtk.Popover()
        popover.add_css_class("zashterminal-popover")
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        main_box.add_css_class("main-menu-popover")
        popover.set_child(main_box)

        theme_selector = ThemeSelectorWidget(
            parent_window.settings_manager, parent_window
        )
        font_sizer_widget = FontSizerWidget(parent_window)

        main_box.append(theme_selector)
        main_box.append(font_sizer_widget)
        main_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        menu_items = [
            {"label": _("New Window"), "action": "win.new-window"},
            {"label": _("Preferences"), "action": "win.preferences"},
            {
                "label": _("Colors"),
                "action": "win.highlight-settings",
                "icon": "format-text-highlight-symbolic",
            },
            {"label": _("AI Assistant"), "action": "win.configure-ai"},
            {"label": _("Keyboard Shortcuts"), "action": "win.shortcuts"},
            {"label": _("About"), "action": "app.about"},
            "---",
            {"label": _("Quit"), "action": "app.quit"},
        ]
        actions_that_close_menu = {
            "win.new-window",
            "win.preferences",
            "win.highlight-settings",
            "win.configure-ai",
            "win.shortcuts",
            "app.about",
        }
        app = parent_window.get_application()

        for item in menu_items:
            if item == "---":
                main_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
            else:
                button = Gtk.Button(
                    action_name=item["action"],
                    css_classes=["flat", "body"],
                    halign=Gtk.Align.FILL,
                )
                box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)
                button.set_child(box)
                action_label = Gtk.Label(label=item["label"], xalign=0.0, hexpand=True)
                box.append(action_label)
                accels = app.get_accels_for_action(item["action"])
                if accels:
                    shortcut_label = Gtk.Label(xalign=1.0, css_classes=["dim-label"])
                    shortcut_label.set_text(accelerator_to_label(accels[0]))
                    box.append(shortcut_label)
                if item["action"] in actions_that_close_menu:
                    button.connect("clicked", lambda b, p=popover: safe_popover_popdown(p))
                main_box.append(button)

        return popover, font_sizer_widget


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
