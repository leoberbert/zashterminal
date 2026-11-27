# zashterminal/ui/dialogs/preferences_preview.py
"""Live preview widgets for the preferences window.

``TerminalPreviewCard`` shows a real Vte.Terminal configured through the same
settings helpers the application terminals use, so font, color scheme,
transparency and cursor changes are visible immediately.
``SchemeSwatchesCard`` shows a handful of the application color schemes as
selectable swatches.
"""

import math
from typing import Callable, Dict, List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import Gdk, Gtk, Pango, Vte

from ...settings.manager import SettingsManager
from ...utils.icons import icon_image
from ...utils.translation_utils import _

_ESC = "\x1b"
_SAMPLE_TEXT = (
    f"{_ESC}[1;32mubuntu@zeus{_ESC}[0m:{_ESC}[1;34m~{_ESC}[0m$ ls -la\r\n"
    "total 40\r\n"
    f"drwxr-xr-x  6 ubuntu ubuntu 4096 Nov 26 13:20 {_ESC}[1;34m.{_ESC}[0m\r\n"
    f"drwxr-xr-x  3 root   root   4096 Nov 25 19:07 {_ESC}[1;34m..{_ESC}[0m\r\n"
    f"drwxr-xr-x  4 ubuntu ubuntu 4096 Nov 26 10:11 {_ESC}[1;34mprojects/{_ESC}[0m\r\n"
    f"drwxr-xr-x  3 ubuntu ubuntu 4096 Nov 25 21:30 {_ESC}[1;34minfra/{_ESC}[0m\r\n"
    f"-rw-r--r--  1 ubuntu ubuntu 1820 Nov 26 13:18 {_ESC}[1;32mdeploy.sh{_ESC}[0m\r\n"
    "\r\n"
    f"{_ESC}[1;32mubuntu@zeus{_ESC}[0m:{_ESC}[1;34m~{_ESC}[0m$ "
)


def _parse_rgba(value: str, fallback: str = "#000000") -> Gdk.RGBA:
    rgba = Gdk.RGBA()
    if not rgba.parse(value or fallback):
        rgba.parse(fallback)
    return rgba


class TerminalPreviewCard(Gtk.Box):
    """Card holding a read-only terminal that mirrors the current settings."""

    def __init__(self, settings_manager: SettingsManager):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.settings_manager = settings_manager
        self.add_css_class("prefs-card")

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.append(icon_image("utilities-terminal-symbolic", 16))
        title = Gtk.Label(label=_("Terminal Preview"), xalign=0, hexpand=True)
        title.add_css_class("prefs-card-title")
        header.append(title)
        self.append(header)

        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        frame.add_css_class("prefs-preview-frame")
        frame.set_overflow(Gtk.Overflow.HIDDEN)
        frame.append(self._build_chrome())

        self.terminal = Vte.Terminal()
        self.terminal.set_input_enabled(False)
        self.terminal.set_can_focus(False)
        self.terminal.set_can_target(False)
        self.terminal.set_scrollback_lines(0)
        self.terminal.set_size(58, 11)
        self.terminal.set_hexpand(True)
        self.terminal.add_css_class("prefs-preview-terminal")
        frame.append(self.terminal)
        self.append(frame)

        self.refresh()
        self.terminal.feed(_SAMPLE_TEXT.encode("utf-8"))

    @staticmethod
    def _build_chrome() -> Gtk.Box:
        chrome = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        chrome.add_css_class("prefs-preview-chrome")
        for css_class in ("prefs-dot-red", "prefs-dot-yellow", "prefs-dot-green"):
            dot = Gtk.Box()
            dot.add_css_class("prefs-dot")
            dot.add_css_class(css_class)
            dot.set_valign(Gtk.Align.CENTER)
            chrome.append(dot)
        tab = Gtk.Label(label="ubuntu@zeus ~")
        tab.add_css_class("prefs-preview-tab")
        tab.set_margin_start(8)
        chrome.append(tab)
        return chrome

    def refresh(self) -> None:
        """Re-apply the current settings to the preview terminal."""
        try:
            self.settings_manager.apply_terminal_colors(self.terminal)
            self.settings_manager._apply_terminal_behavior(self.terminal)
            # Stay read-only and compact whatever the global behavior says.
            self.terminal.set_scrollback_lines(0)
            self.terminal.set_audible_bell(False)
        except Exception:
            self.terminal.set_font(Pango.FontDescription.from_string("Monospace 10"))


class SchemeSwatchesCard(Gtk.Box):
    """Selectable swatches for a few of the application color schemes."""

    _PREFERRED = ("tokyonight", "catppuccin", "dracula", "nord", "monokai")
    _SWATCH_COUNT = 5

    def __init__(
        self,
        settings_manager: SettingsManager,
        on_select: Callable[[str], None],
        on_customize: Callable[[], None],
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.settings_manager = settings_manager
        self._on_select = on_select
        self._syncing = False
        self._buttons: Dict[str, Gtk.ToggleButton] = {}
        self.add_css_class("prefs-card")

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.append(icon_image("preferences-desktop-display-symbolic", 16))
        title = Gtk.Label(label=_("Theme & Palette"), xalign=0, hexpand=True)
        title.add_css_class("prefs-card-title")
        header.append(title)
        manage = Gtk.Button(label=_("Manage Themes"))
        manage.add_css_class("flat")
        manage.add_css_class("prefs-link-button")
        manage.connect("clicked", lambda _b: on_customize())
        header.append(manage)
        self.append(header)

        self._swatch_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8, homogeneous=True
        )
        self.append(self._swatch_row)

        customize = Gtk.Button()
        customize.add_css_class("flat")
        customize.add_css_class("prefs-card-action")
        customize_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        customize_box.append(
            Gtk.Label(label=_("Colors & Highlighting"), xalign=0, hexpand=True)
        )
        customize_box.append(icon_image("go-next-symbolic", 16))
        customize.set_child(customize_box)
        customize.connect("clicked", lambda _b: on_customize())
        self.append(customize)

        self._populate()

    def _current_name(self) -> Optional[str]:
        if self.settings_manager.get("wallpaper_theme") is not None:
            return None
        return self.settings_manager.get_color_scheme_name()

    def _swatch_names(self, current: Optional[str]) -> List[str]:
        order = self.settings_manager.get_scheme_order()
        names = [name for name in self._PREFERRED if name in order]
        for name in order:
            if len(names) >= self._SWATCH_COUNT:
                break
            if name not in names:
                names.append(name)
        names = names[: self._SWATCH_COUNT]
        if current and current not in names and names:
            names[-1] = current
        return names

    def _populate(self) -> None:
        child = self._swatch_row.get_first_child()
        while child is not None:
            self._swatch_row.remove(child)
            child = self._swatch_row.get_first_child()
        self._buttons.clear()

        current = self._current_name()
        schemes = self.settings_manager.get_all_schemes()
        group_leader: Optional[Gtk.ToggleButton] = None
        self._syncing = True
        for name in self._swatch_names(current):
            data = schemes.get(name)
            if not data:
                continue
            button = Gtk.ToggleButton()
            button.add_css_class("prefs-swatch")
            if group_leader is None:
                group_leader = button
            else:
                button.set_group(group_leader)
            area = Gtk.DrawingArea()
            area.set_content_width(92)
            area.set_content_height(58)
            area.set_draw_func(self._draw_swatch, (data, button))
            button.set_child(area)
            button.connect("notify::active", lambda b, _p, a=area: a.queue_draw())
            button.connect("toggled", self._on_toggled, name)
            button.set_active(name == current)

            label = Gtk.Label(label=data.get("name", name))
            label.add_css_class("prefs-swatch-label")
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_max_width_chars(11)

            cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            cell.append(button)
            cell.append(label)
            self._swatch_row.append(cell)
            self._buttons[name] = button
        self._syncing = False

    def _on_toggled(self, button: Gtk.ToggleButton, name: str) -> None:
        if self._syncing or not button.get_active():
            return
        self._on_select(name)

    def sync(self) -> None:
        """Follow external scheme changes."""
        current = self._current_name()
        if current is not None and current not in self._buttons:
            self._populate()
            return
        self._syncing = True
        try:
            # With a wallpaper palette active (current is None) nothing is selected.
            for name, button in self._buttons.items():
                button.set_active(name == current)
        finally:
            self._syncing = False

    @staticmethod
    def _draw_swatch(_area, cr, width, height, user_data) -> None:
        data, button = user_data
        palette = data.get("palette", [])

        def color(index: int, fallback: str) -> Gdk.RGBA:
            value = palette[index] if len(palette) > index else fallback
            return _parse_rgba(value, fallback)

        def set_source(rgba: Gdk.RGBA, alpha: float = 1.0) -> None:
            cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, alpha)

        radius = 8
        cr.new_sub_path()
        cr.arc(width - radius, radius, radius, -math.pi / 2, 0)
        cr.arc(width - radius, height - radius, radius, 0, math.pi / 2)
        cr.arc(radius, height - radius, radius, math.pi / 2, math.pi)
        cr.arc(radius, radius, radius, math.pi, 3 * math.pi / 2)
        cr.close_path()
        set_source(_parse_rgba(data.get("background", "#000000")))
        cr.fill()

        for i, index in enumerate((1, 3, 2)):
            set_source(color(index, "#888888"))
            cr.arc(12 + i * 9, 11, 2.6, 0, 2 * math.pi)
            cr.fill()

        foreground = _parse_rgba(data.get("foreground", "#ffffff"), "#ffffff")
        bars = (
            (color(4, "#3465a4"), 1.0, 0.55),
            (foreground, 0.55, 0.72),
            (color(2, "#4e9a06"), 1.0, 0.36),
        )
        for i, (rgba, alpha, fraction) in enumerate(bars):
            set_source(rgba, alpha)
            y = 24 + i * 10
            cr.rectangle(10, y, (width - 20) * fraction, 3.5)
            cr.fill()

        if button.get_active():
            cx, cy = width - 13, 13
            cr.arc(cx, cy, 8, 0, 2 * math.pi)
            cr.set_source_rgba(0.18, 0.43, 0.96, 1.0)
            cr.fill()
            cr.set_source_rgba(1, 1, 1, 1)
            cr.set_line_width(1.8)
            cr.move_to(cx - 3.6, cy + 0.2)
            cr.line_to(cx - 1, cy + 2.8)
            cr.line_to(cx + 3.8, cy - 2.6)
            cr.stroke()
