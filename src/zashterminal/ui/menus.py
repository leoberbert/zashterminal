# zashterminal/ui/menus.py

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gio, Gtk

from ..helpers import is_valid_url
from ..utils.translation_utils import _


class MainApplicationMenu:
    """Factory for creating the main application popover menu.

    Built as a native ``Gtk.PopoverMenu`` so sections, the import submenu,
    keyboard navigation and accelerator labels come from GTK.
    """

    @staticmethod
    def build_menu_model(parent_window) -> tuple[Gio.Menu, Gio.Menu]:
        """Return the menu model and its "Tools" section (holds the TFTP item)."""
        menu = Gio.Menu()

        # Theme, fonts, colors, AI and shortcuts live in Preferences.
        window_section = Gio.Menu()
        window_section.append(_("New Window"), "win.new-window")
        window_section.append(_("Command Palette"), "win.command-palette")
        window_section.append(_("Preferences"), "win.preferences")
        menu.append_section(None, window_section)

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
        return menu, tools

    @staticmethod
    def create_main_popover(parent_window) -> Gtk.PopoverMenu:
        menu, tools = MainApplicationMenu.build_menu_model(parent_window)
        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.add_css_class("zashterminal-popover")
        popover.tftp_section = tools
        return popover

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
