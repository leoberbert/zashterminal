# zashterminal/ui/command_palette.py
"""Command palette entries and matching (the dialog widget is in ui/dialogs)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, List

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib

from ..helpers import accelerator_to_label
from ..utils.translation_utils import _
from ..utils.logger import get_logger

if TYPE_CHECKING:
    from ..window import ZashTerminalWindow

MAX_RESULTS = 60

# Prefixes that restrict the search to one kind of entry.
SESSION_PREFIX = "@"
ACTION_PREFIX = ">"


@dataclass
class PaletteEntry:
    kind: str  # "session" | "layout" | "command" | "action"
    title: str
    subtitle: str
    icon_name: str
    activate: Callable[[], None]
    accel: str = ""

    @property
    def haystack(self) -> str:
        return f"{self.title} {self.subtitle}".casefold()


def _action_entries(window: "ZashTerminalWindow") -> List[PaletteEntry]:
    """Window actions with a human label (every entry here exists in actions.py)."""
    from .dialogs.shortcuts_dialog import SHORTCUT_DATA

    labels = {
        name: title for group in SHORTCUT_DATA for name, title in group["shortcuts"]
    }
    labels.update(
        {
            "highlight-settings": _("Colors & Highlighting"),
            "configure-ai": _("AI Assistant"),
            "shortcuts": _("Keyboard Shortcuts"),
            "add-session-root": _("Add Session"),
            "add-folder-root": _("Add Folder"),
            "save-layout": _("Save Current Layout"),
            "import-securecrt-sessions": _("Import Sessions") + ": SecureCRT",
            "import-putty-sessions": _("Import Sessions") + ": PuTTY",
            "import-multiputty-sessions": _("Import Sessions") + ": Multi PuTTY",
            "import-mremoteng-sessions": _("Import Sessions") + ": mRemoteNG",
            "toggle-tftp-server": _("Start TFTP Server")
            if not getattr(getattr(window, "tftp_server", None), "is_running", False)
            else _("Stop TFTP Server"),
        }
    )
    labels.pop("command-palette", None)
    app = window.get_application()
    entries = []
    for name, title in labels.items():
        if not window.lookup_action(name):
            continue
        accels = app.get_accels_for_action(f"win.{name}") if app else []
        entries.append(
            PaletteEntry(
                kind="action",
                title=title,
                subtitle="",
                icon_name="system-run-symbolic",
                activate=lambda n=name: window.activate_action(f"win.{n}", None),
                accel=accelerator_to_label(accels[0]) if accels else "",
            )
        )
    return entries


def _preferences_entries(window: "ZashTerminalWindow") -> List[PaletteEntry]:
    """One entry per Preferences page (page names are set in preferences_dialog)."""
    pages = (
        ("appearance", _("Appearance"), "preferences-desktop-display-symbolic"),
        ("terminal", _("Terminal"), "utilities-terminal-symbolic"),
        ("profiles", _("Profiles & Data"), "folder-saved-search-symbolic"),
        ("advanced", _("Advanced"), "preferences-other-symbolic"),
    )

    def open_page(name: str) -> None:
        window.action_handler.preferences(page=name)

    entries = [
        PaletteEntry(
            kind="setting",
            title=_("Preferences") + ": " + title,
            subtitle="",
            icon_name=icon,
            activate=lambda n=name: open_page(n),
        )
        for name, title, icon in pages
    ]
    entries.extend(_individual_preference_entries(window, open_page))
    return entries


def _individual_preference_entries(window, open_page) -> List[PaletteEntry]:
    """One entry per preference row, found by walking a throwaway dialog.

    Searching "blur" or "scrollback" then jumps to the page that holds it.
    """
    from .dialogs import PreferencesDialog

    found: List[PaletteEntry] = []
    try:
        dialog = PreferencesDialog(window, window.settings_manager)
        page_titles = {}

        def walk(widget, page):
            if isinstance(widget, Adw.PreferencesPage):
                page = widget
                page_titles[page.get_name()] = page.get_title()
            elif isinstance(widget, Adw.PreferencesRow) and page is not None:
                title = widget.get_title()
                if title:
                    found.append(
                        PaletteEntry(
                            kind="setting",
                            title=title,
                            subtitle=_("Preferences") + " › " + page.get_title(),
                            icon_name="preferences-system-symbolic",
                            activate=lambda n=page.get_name(): open_page(n),
                        )
                    )
            child = widget.get_first_child()
            while child is not None:
                walk(child, page)
                child = child.get_next_sibling()

        walk(dialog, None)
        # Never presented, so close-request does not run: unregister by hand.
        window.settings_manager.remove_change_listener(dialog._on_external_setting)
        dialog.destroy()
    except Exception as exc:
        # The page entries above still work without the per-row index.
        get_logger("zashterminal.ui.command_palette").debug(
            f"Preference row index unavailable: {exc}"
        )
    return found


def build_entries(window: "ZashTerminalWindow") -> List[PaletteEntry]:
    entries: List[PaletteEntry] = []

    sessions = [
        window.session_store.get_item(i)
        for i in range(window.session_store.get_n_items())
    ]
    sessions.sort(key=lambda s: not s.favorite)  # stable: favorites first
    for session in sessions:
        folder = (session.folder_path or "").strip("/")
        details = [session.get_connection_string()]
        if folder:
            details.append(folder)
        entries.append(
            PaletteEntry(
                kind="session",
                title=("★ " if session.favorite else "") + session.name,
                subtitle=" · ".join(details),
                icon_name="computer-symbolic"
                if session.is_local()
                else "network-server-symbolic",
                activate=lambda s=session: window._on_session_activated(s),
            )
        )
        if session.is_ssh():
            entries.append(
                PaletteEntry(
                    kind="session",
                    title=_("SFTP: {name}").format(name=session.name),
                    subtitle=session.get_connection_string(),
                    icon_name="folder-remote-symbolic",
                    activate=lambda s=session: window.tab_manager.create_sftp_tab(s),
                )
            )

    for layout in getattr(window, "layouts", []) or []:
        entries.append(
            PaletteEntry(
                kind="layout",
                title=layout.name,
                subtitle=_("Saved layout"),
                icon_name="view-restore-symbolic",
                activate=lambda n=layout.name: window.activate_action(
                    "win.restore_layout", GLib.Variant.new_string(n)
                ),
            )
        )

    try:
        from ..data.command_manager_models import get_command_button_manager

        manager = get_command_button_manager()
        for command in manager.get_all_commands():
            if manager.is_command_hidden(command.id):
                continue
            entries.append(
                PaletteEntry(
                    kind="command",
                    title=command.name,
                    subtitle=command.command_template,
                    icon_name=command.icon_name or "utilities-terminal-symbolic",
                    activate=lambda c=command: window.execute_toolbar_command(c),
                )
            )
    except Exception:
        # Saved commands are optional; the palette still works without them.
        pass

    entries.extend(_action_entries(window))
    entries.extend(_preferences_entries(window))
    return entries


_KIND_ORDER = {"session": 0, "layout": 1, "command": 2, "action": 3, "setting": 4}


def filter_entries(entries: List[PaletteEntry], query: str) -> List[PaletteEntry]:
    """Every word of the query must match; grouped by kind, title prefixes first."""
    query = query.strip()
    kinds = None
    if query.startswith(SESSION_PREFIX):
        kinds, query = {"session", "layout"}, query[1:]
    elif query.startswith(ACTION_PREFIX):
        kinds, query = {"action", "setting"}, query[1:]
    terms = query.casefold().split()

    matched = []
    for index, entry in enumerate(entries):
        if kinds and entry.kind not in kinds:
            continue
        if all(term in entry.haystack for term in terms):
            prefix = bool(terms) and entry.title.casefold().startswith(terms[0])
            matched.append((_KIND_ORDER[entry.kind], not prefix, index, entry))
    matched.sort(key=lambda item: item[:3])
    return [item[3] for item in matched[:MAX_RESULTS]]
