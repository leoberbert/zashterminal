# zashterminal/ui/command_palette.py
"""Command palette: one search box for sessions, layouts, saved commands and actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, List

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango

from ..helpers import accelerator_to_label
from ..utils.translation_utils import _

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
    except Exception:
        # The page entries above still work without the per-row index.
        return found
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


_KIND_TITLES = {
    "session": lambda: _("Sessions"),
    "layout": lambda: _("Layouts"),
    "command": lambda: _("Saved Commands"),
    "action": lambda: _("Actions"),
    "setting": lambda: _("Settings"),
}


class CommandPalette(Adw.Dialog):
    """Searchable launcher; Enter runs the selected entry, Esc closes."""

    def __init__(self, window: "ZashTerminalWindow"):
        super().__init__(
            title=_("Command Palette"), content_width=640, content_height=480
        )
        self._window = window
        self._entries = build_entries(window)
        self._visible: List[PaletteEntry] = []

        self._search = Gtk.SearchEntry(
            placeholder_text=_("Search sessions, commands and actions"),
            hexpand=True,
        )
        self._search.connect("search-changed", lambda *_a: self._refresh())
        self._search.connect("activate", lambda *_a: self._activate_selected())
        self._search.connect("stop-search", lambda *_a: self.close())
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key_pressed)
        self._search.add_controller(keys)

        self._list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.BROWSE)
        self._list.add_css_class("navigation-sidebar")
        self._list.set_header_func(self._header_func)
        self._list.connect("row-activated", lambda _l, row: self._run(row))

        self._empty = Adw.StatusPage(
            icon_name="edit-find-symbolic", title=_("No Results"), vexpand=True
        )
        self._empty.add_css_class("compact")
        scrolled = Gtk.ScrolledWindow(child=self._list, vexpand=True)
        self._stack = Gtk.Stack()
        self._stack.add_named(scrolled, "list")
        self._stack.add_named(self._empty, "empty")

        hint = Gtk.Label(
            label=_("@ sessions only · > actions only · Enter to run · Esc to close"),
            css_classes=["dim-label", "caption"],
            margin_top=6,
            margin_bottom=8,
        )

        header = Adw.HeaderBar()
        header.set_title_widget(self._search)
        view = Adw.ToolbarView(content=self._stack)
        view.add_top_bar(header)
        view.add_bottom_bar(hint)
        self.set_child(view)
        self.set_focus(self._search)
        self._refresh()

    def _refresh(self) -> None:
        self._visible = filter_entries(self._entries, self._search.get_text())
        self._list.remove_all()
        for entry in self._visible:
            self._list.append(self._make_row(entry))
        self._stack.set_visible_child_name("list" if self._visible else "empty")
        first = self._list.get_row_at_index(0)
        if first is not None:
            self._list.select_row(first)

    @staticmethod
    def _make_row(entry: PaletteEntry) -> Gtk.ListBoxRow:
        box = Gtk.Box(spacing=12, margin_top=4, margin_bottom=4)
        box.append(Gtk.Image(icon_name=entry.icon_name))
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        texts.append(
            Gtk.Label(label=entry.title, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        )
        if entry.subtitle:
            texts.append(
                Gtk.Label(
                    label=entry.subtitle,
                    xalign=0,
                    ellipsize=Pango.EllipsizeMode.END,
                    css_classes=["dim-label", "caption"],
                )
            )
        box.append(texts)
        if entry.accel:
            box.append(Gtk.Label(label=entry.accel, css_classes=["dim-label"]))
        row = Gtk.ListBoxRow(child=box)
        row.entry = entry
        return row

    @staticmethod
    def _header_func(row: Gtk.ListBoxRow, before) -> None:
        if before is not None and before.entry.kind == row.entry.kind:
            row.set_header(None)
            return
        label = Gtk.Label(
            label=_KIND_TITLES[row.entry.kind](),
            xalign=0,
            css_classes=["heading", "dim-label"],
            margin_start=6,
            margin_top=8 if before is not None else 2,
            margin_bottom=2,
        )
        row.set_header(label)

    def _on_key_pressed(self, _controller, keyval, _keycode, _state) -> bool:
        if keyval not in (Gdk.KEY_Down, Gdk.KEY_Up):
            return False
        row = self._list.get_selected_row()
        index = row.get_index() if row else -1
        index += 1 if keyval == Gdk.KEY_Down else -1
        target = self._list.get_row_at_index(max(0, index))
        if target is not None:
            self._list.select_row(target)
            target.grab_focus()
            self._search.grab_focus()
        return True

    def _activate_selected(self) -> None:
        row = self._list.get_selected_row()
        if row is not None:
            self._run(row)

    def _run(self, row: Gtk.ListBoxRow) -> None:
        entry = row.entry
        self.close()
        # Run after the dialog is gone so focus returns to the terminal first.
        GLib.idle_add(lambda: (entry.activate(), GLib.SOURCE_REMOVE)[1])
