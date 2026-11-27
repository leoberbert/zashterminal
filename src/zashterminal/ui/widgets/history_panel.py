# zashterminal/ui/widgets/history_panel.py
"""Side panel listing recently used sessions."""

import time
from pathlib import Path
from typing import List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GObject, Gtk

from ...data.connection_history import ConnectionHistory, HistoryEntry
from ...utils.icons import icon_button, icon_image
from ...utils.translation_utils import _

_CSS_FILE = Path(__file__).parent.parent.parent / "data" / "styles" / "panels.css"
_css_loaded = False

_KIND_ICONS = {
    "ssh": "network-server-symbolic",
    "local": "utilities-terminal-symbolic",
    "sftp": "folder-remote-symbolic",
}


def _ensure_css() -> None:
    global _css_loaded
    if _css_loaded:
        return
    display = Gdk.Display.get_default()
    if display is None or not _CSS_FILE.exists():
        return
    provider = Gtk.CssProvider()
    provider.load_from_path(str(_CSS_FILE))
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 10
    )
    _css_loaded = True


def format_relative_time(timestamp: float, now: Optional[float] = None) -> str:
    """Return a short localized relative time such as '5 min ago'."""
    delta = max(0, int((now if now is not None else time.time()) - timestamp))
    if delta < 60:
        return _("just now")
    if delta < 3600:
        return _("{n} min ago").format(n=delta // 60)
    if delta < 86400:
        return _("{n} h ago").format(n=delta // 3600)
    return _("{n} d ago").format(n=delta // 86400)


class HistoryPanel(Gtk.Box):
    __gtype_name__ = "ZashHistoryPanel"
    __gsignals__ = {
        "session-activated": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, history: ConnectionHistory):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        _ensure_css()
        self._history = history
        self._entries: List[HistoryEntry] = []
        self.add_css_class("zash-history-panel")

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        title = Gtk.Label(label=_("History"), xalign=0, hexpand=True)
        title.add_css_class("zash-history-title")
        header.append(title)
        self._clear_button = icon_button(
            "user-trash-symbolic", tooltip=_("Clear history"), css_classes=["flat"]
        )
        self._clear_button.connect("clicked", self._on_clear_clicked)
        header.append(self._clear_button)
        self.append(header)

        self._search = Gtk.SearchEntry(placeholder_text=_("Search history..."))
        self._search.connect("search-changed", lambda *_a: self._listbox.invalidate_filter())
        self.append(self._search)

        self._listbox = Gtk.ListBox()
        self._listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self._listbox.set_activate_on_single_click(True)
        self._listbox.add_css_class("zash-history-list")
        self._listbox.set_filter_func(self._filter_row)
        self._listbox.connect("row-activated", self._on_row_activated)

        self._scrolled = Gtk.ScrolledWindow(vexpand=True)
        self._scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scrolled.set_child(self._listbox)

        self._empty = Adw.StatusPage(
            title=_("No history yet"), icon_name="document-open-recent-symbolic"
        )
        self._empty.set_vexpand(True)

        self._stack = Gtk.Stack()
        self._stack.add_named(self._scrolled, "list")
        self._stack.add_named(self._empty, "empty")
        self._stack.set_vexpand(True)
        self.append(self._stack)

        self.refresh()

    def refresh(self) -> None:
        """Rebuild the list from the history store."""
        while (row := self._listbox.get_row_at_index(0)) is not None:
            self._listbox.remove(row)
        self._entries = self._history.recent()
        for entry in self._entries:
            self._listbox.append(self._build_row(entry))
        self._stack.set_visible_child_name("list" if self._entries else "empty")
        self._clear_button.set_sensitive(bool(self._entries))

    def _build_row(self, entry: HistoryEntry) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.add_css_class("zash-history-row")
        row.entry = entry

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(10)
        box.set_margin_end(6)

        icon = icon_image(_KIND_ICONS.get(entry.kind, _KIND_ICONS["local"]), size=18)
        icon.add_css_class("zash-history-icon")
        box.append(icon)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        name = Gtk.Label(label=entry.session_name, xalign=0)
        name.set_ellipsize(3)  # Pango.EllipsizeMode.END
        name.add_css_class("zash-history-name")
        text.append(name)
        if entry.host:
            sub = Gtk.Label(label=entry.host, xalign=0)
            sub.set_ellipsize(3)
            sub.add_css_class("zash-history-sub")
            text.append(sub)
        box.append(text)

        when = Gtk.Label(label=format_relative_time(entry.last_used), xalign=1)
        when.add_css_class("zash-history-time")
        box.append(when)

        remove = icon_button(
            "window-close-symbolic",
            size=12,
            tooltip=_("Remove from history"),
            css_classes=["flat", "circular", "zash-history-remove"],
        )
        remove.set_valign(Gtk.Align.CENTER)
        remove.connect("clicked", lambda _b, n=entry.session_name: self._remove(n))
        box.append(remove)

        row.set_child(box)

        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        click.connect("pressed", lambda *_a, n=entry.session_name: self._remove(n))
        row.add_controller(click)
        return row

    def _filter_row(self, row: Gtk.ListBoxRow) -> bool:
        query = self._search.get_text().strip().lower()
        if not query:
            return True
        entry = getattr(row, "entry", None)
        if entry is None:
            return True
        return query in entry.session_name.lower() or query in entry.host.lower()

    def _remove(self, session_name: str) -> None:
        self._history.remove(session_name)
        self.refresh()

    def _on_clear_clicked(self, _button) -> None:
        self._history.clear()
        self.refresh()

    def _on_row_activated(self, _listbox, row: Gtk.ListBoxRow) -> None:
        entry = getattr(row, "entry", None)
        if entry is not None:
            self.emit("session-activated", entry.session_name)
