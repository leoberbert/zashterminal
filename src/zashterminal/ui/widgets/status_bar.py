# zashterminal/ui/widgets/status_bar.py
"""Bottom status bar and connection banner of the main window."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from ...utils.icons import icon_image
from ...utils.translation_utils import _

if TYPE_CHECKING:
    from ...utils.system_metrics import SystemMetrics


def _item(
    icon_name: Optional[str], css: str = "", bundled: Optional[bool] = None
) -> tuple[Gtk.Box, Gtk.Label]:
    box = Gtk.Box(spacing=5, css_classes=["zash-status-item"])
    if css:
        box.add_css_class(css)
    if icon_name:
        box.append(icon_image(icon_name, size=14, use_bundled=bundled))
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
        self._ram_box, self._ram_label = _item("memory-symbolic", bundled=True)
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

        # Clicking a metric opens the Monitor panel (set by the window).
        self.on_metrics_activated: Optional[Callable[[], None]] = None
        for box in (self._cpu_box, self._ram_box, self._disk_box, self._latency_box):
            click = Gtk.GestureClick.new()
            click.connect("released", self._on_metric_clicked)
            box.add_controller(click)
            box.set_cursor_from_name("pointer")

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
            _("Memory: {value}%").format(value=f"{metrics.mem_percent:.0f}")
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
        self._disk_box.set_tooltip_text(metrics.disk_mount or None)
        put(
            self._latency_box,
            self._latency_label,
            _("{value} ms").format(value=f"{metrics.latency_ms:.0f}")
            if metrics.latency_ms is not None
            else None,
        )

    def _on_metric_clicked(self, *_args) -> None:
        if self.on_metrics_activated is not None:
            self.on_metrics_activated()

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
