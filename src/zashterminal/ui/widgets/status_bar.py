# zashterminal/ui/widgets/status_bar.py
"""Bottom status bar and connection banner of the main window."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango

from ...utils.icons import icon_image
from ...utils.translation_utils import _

if TYPE_CHECKING:
    from ...utils.system_metrics import SystemMetrics


def _item(icon_name: Optional[str], css: str = "") -> tuple[Gtk.Box, Gtk.Label]:
    box = Gtk.Box(spacing=5, css_classes=["zash-status-item"])
    if css:
        box.add_css_class(css)
    if icon_name:
        box.append(icon_image(icon_name, size=14))
    label = Gtk.Label()
    box.append(label)
    return box, label


class StatusBar(Gtk.Box):
    """Connection state, host OS, CPU/RAM/disk and terminal encoding/shell/font."""

    # Connection states understood by set_connection()
    NONE = "none"
    LOCAL = "local"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"

    def __init__(self) -> None:
        super().__init__(spacing=14, css_classes=["zash-statusbar"])

        self._state_box = Gtk.Box(spacing=6, css_classes=["zash-status-item"])
        self._dot = Gtk.Box(css_classes=["zash-status-dot"])
        self._dot.set_valign(Gtk.Align.CENTER)
        self._state_label = Gtk.Label()
        self._state_box.append(self._dot)
        self._state_box.append(self._state_label)
        self.append(self._state_box)

        self._os_box, self._os_label = _item("computer-symbolic")
        self._cpu_box, self._cpu_label = _item("utilities-system-monitor-symbolic")
        self._ram_box, self._ram_label = _item("view-list-bullet-symbolic")
        self._disk_box, self._disk_label = _item("drive-harddisk-symbolic")
        self._latency_box, self._latency_label = _item("network-wireless-signal-good-symbolic")
        for box in (
            self._os_box,
            self._cpu_box,
            self._ram_box,
            self._disk_box,
            self._latency_box,
        ):
            box.set_visible(False)
            self.append(box)

        self.append(Gtk.Box(hexpand=True))

        self._encoding_box, self._encoding_label = _item(None)
        self._shell_box, self._shell_label = _item(None)
        self._font_box, self._font_label = _item(None)
        for box in (self._encoding_box, self._shell_box, self._font_box):
            box.set_visible(False)
            self.append(box)

        self.set_connection(self.NONE)

    def set_connection(self, state: str, label: str = "") -> None:
        for css in ("connected", "disconnected", "local", "none"):
            self._dot.remove_css_class(css)
        self._dot.add_css_class(state)
        if state == self.CONNECTED:
            text = label or _("SSH connected")
        elif state == self.DISCONNECTED:
            text = label or _("Disconnected")
        elif state == self.LOCAL:
            text = label or _("Local terminal")
        else:
            text = label or _("No terminal")
        self._state_label.set_text(text)

    def set_metrics(self, metrics: Optional["SystemMetrics"]) -> None:
        """Show the host metrics; ``None`` hides every metric item."""
        def put(box: Gtk.Box, label: Gtk.Label, text: Optional[str]) -> None:
            box.set_visible(bool(text))
            if text:
                label.set_text(text)

        if metrics is None:
            for box, label in (
                (self._os_box, self._os_label),
                (self._cpu_box, self._cpu_label),
                (self._ram_box, self._ram_label),
                (self._disk_box, self._disk_label),
                (self._latency_box, self._latency_label),
            ):
                put(box, label, None)
            return

        put(self._os_box, self._os_label, metrics.os_name or None)
        put(
            self._cpu_box,
            self._cpu_label,
            _("CPU: {value}%").format(value=f"{metrics.cpu_percent:.0f}")
            if metrics.cpu_percent is not None
            else None,
        )
        put(
            self._ram_box,
            self._ram_label,
            _("RAM: {value}%").format(value=f"{metrics.mem_percent:.0f}")
            if metrics.mem_percent is not None
            else None,
        )
        put(
            self._disk_box,
            self._disk_label,
            _("Disk: {value}%").format(value=f"{metrics.disk_percent:.0f}")
            if metrics.disk_percent is not None
            else None,
        )
        put(
            self._latency_box,
            self._latency_label,
            _("{value} ms").format(value=f"{metrics.latency_ms:.0f}")
            if metrics.latency_ms is not None
            else None,
        )

    def set_terminal_info(
        self,
        encoding: Optional[str],
        shell: Optional[str],
        font_size: Optional[float],
    ) -> None:
        for box, label, text in (
            (self._encoding_box, self._encoding_label, encoding),
            (self._shell_box, self._shell_label, shell),
            (
                self._font_box,
                self._font_label,
                f"{font_size:g} pt" if font_size else None,
            ),
        ):
            box.set_visible(bool(text))
            if text:
                label.set_text(text)


class ConnectionBanner(Gtk.Revealer):
    """Slim info line above the terminal: "Connected to <host> (<OS>)"."""

    def __init__(self, on_dismiss: Callable[[], None]) -> None:
        super().__init__(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
            reveal_child=False,
        )
        row = Gtk.Box(spacing=8, css_classes=["zash-banner"])
        row.append(icon_image("emblem-ok-symbolic", size=16))
        self._label = Gtk.Label(
            xalign=0.0, hexpand=True, ellipsize=Pango.EllipsizeMode.END, use_markup=True
        )
        row.append(self._label)
        close = Gtk.Button(
            icon_name="window-close-symbolic",
            css_classes=["flat", "circular", "zash-banner-close"],
            valign=Gtk.Align.CENTER,
            tooltip_text=_("Dismiss"),
        )
        close.connect("clicked", lambda _b: on_dismiss())
        row.append(close)
        self.set_child(row)

    def show_connected(self, name: str, os_name: str = "") -> None:
        text = GLib.markup_escape_text(name)
        if os_name:
            markup = _("Connected to <b>{name}</b> ({os})").format(
                name=text, os=GLib.markup_escape_text(os_name)
            )
        else:
            markup = _("Connected to <b>{name}</b>").format(name=text)
        self._label.set_markup(markup)
        self.set_reveal_child(True)

    def hide_banner(self) -> None:
        self.set_reveal_child(False)
