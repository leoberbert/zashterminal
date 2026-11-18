# zashterminal/ui/dialogs/command_palette_dialog.py
"""Command palette dialog (GTK widget). Matching logic lives in ui/command_palette.py."""

from __future__ import annotations

from typing import TYPE_CHECKING, List

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango

from ...utils.translation_utils import _
from ..command_palette import PaletteEntry, build_entries, filter_entries

if TYPE_CHECKING:
    from ...window import ZashTerminalWindow

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
        self._list.add_css_class("command-palette")
        self._search.add_css_class("command-palette-search")
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
