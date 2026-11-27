# zashterminal/ui/widgets/monitor_panel.py
"""Side panel showing live system metrics of the active terminal's host."""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Deque, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk

from ...utils.translation_utils import _

if TYPE_CHECKING:
    from ...utils.system_metrics import SystemMetrics

_CSS_FILE = Path(__file__).parent.parent.parent / "data" / "styles" / "panels.css"
_css_loaded = False
HISTORY_LEN = 60


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


class _MetricRow(Gtk.Box):
    """Label + percentage, level bar, detail text and optional sparkline."""

    def __init__(self, title: str, sparkline: bool):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.add_css_class("zash-monitor-card")
        self._samples: Deque[float] = deque(maxlen=HISTORY_LEN)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        name = Gtk.Label(label=title, xalign=0, hexpand=True)
        name.add_css_class("zash-monitor-dim")
        top.append(name)
        self._value = Gtk.Label(label="--", xalign=1)
        self._value.add_css_class("zash-monitor-value")
        top.append(self._value)
        self.append(top)

        self._bar = Gtk.LevelBar(min_value=0, max_value=100)
        self._bar.set_mode(Gtk.LevelBarMode.CONTINUOUS)
        self._bar.add_css_class("zash-monitor-bar")
        # Disable stock offsets coloring so our CSS classes control the color.
        for offset in ("low", "high", "full"):
            self._bar.remove_offset_value(offset)
        self.append(self._bar)

        self._detail = Gtk.Label(label="", xalign=0)
        self._detail.add_css_class("zash-monitor-dim")
        self.append(self._detail)

        self._spark: Optional[Gtk.DrawingArea] = None
        if sparkline:
            self._spark = Gtk.DrawingArea()
            self._spark.add_css_class("zash-monitor-spark")
            self._spark.set_content_height(32)
            self._spark.set_hexpand(True)
            self._spark.set_draw_func(self._draw_spark)
            self.append(self._spark)

    def update(self, percent: Optional[float], detail: str = "") -> None:
        if percent is None:
            self._value.set_text("--")
            self._bar.set_value(0)
        else:
            percent = max(0.0, min(100.0, percent))
            self._value.set_text(f"{percent:.0f}%")
            self._bar.set_value(percent)
            self._samples.append(percent)
            self._bar.remove_css_class("zash-monitor-warn")
            self._bar.remove_css_class("zash-monitor-crit")
            if percent >= 90:
                self._bar.add_css_class("zash-monitor-crit")
            elif percent >= 75:
                self._bar.add_css_class("zash-monitor-warn")
        self._detail.set_text(detail)
        if self._spark is not None:
            self._spark.queue_draw()

    def reset(self) -> None:
        self._samples.clear()
        self.update(None, "")

    def _draw_spark(self, area, cr, width, height) -> None:
        samples = list(self._samples)
        if len(samples) < 2 or width <= 0 or height <= 0:
            return
        color = area.get_color()
        step = width / (HISTORY_LEN - 1)
        x0 = width - step * (len(samples) - 1)
        pad = 2.0
        usable = height - 2 * pad

        def pt(i: int, v: float):
            return x0 + i * step, pad + usable * (1.0 - v / 100.0)

        cr.set_line_width(1.5)
        cr.set_line_join(1)  # round
        x, y = pt(0, samples[0])
        cr.move_to(x, y)
        for i, v in enumerate(samples[1:], start=1):
            x, y = pt(i, v)
            cr.line_to(x, y)
        cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        cr.stroke_preserve()
        cr.line_to(x, height)
        cr.line_to(x0, height)
        cr.close_path()
        cr.set_source_rgba(color.red, color.green, color.blue, color.alpha * 0.18)
        cr.fill()


class MonitorPanel(Gtk.Box):
    __gtype_name__ = "ZashMonitorPanel"

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        _ensure_css()
        self.add_css_class("zash-monitor-panel")

        self._title = Gtk.Label(label=_("Monitor"), xalign=0)
        self._title.add_css_class("zash-monitor-title")
        self.append(self._title)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        info.add_css_class("zash-monitor-card")
        self._host = Gtk.Label(label="", xalign=0)
        self._host.add_css_class("zash-monitor-host")
        self._host.set_ellipsize(3)
        self._os = Gtk.Label(label="", xalign=0)
        self._os.add_css_class("zash-monitor-dim")
        self._os.set_ellipsize(3)
        self._kernel = Gtk.Label(label="", xalign=0)
        self._kernel.add_css_class("zash-monitor-dim")
        self._kernel.set_ellipsize(3)
        self._uptime = Gtk.Label(label="", xalign=0)
        self._uptime.add_css_class("zash-monitor-dim")
        self._latency = Gtk.Label(label="", xalign=0)
        self._latency.add_css_class("zash-monitor-dim")
        for w in (self._host, self._os, self._kernel, self._uptime, self._latency):
            info.append(w)
        content.append(info)

        self._cpu = _MetricRow(_("CPU"), sparkline=True)
        self._ram = _MetricRow(_("RAM"), sparkline=True)
        self._disk = _MetricRow(_("Disk"), sparkline=False)
        for row in (self._cpu, self._ram, self._disk):
            content.append(row)

        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(content)

        self._status = Adw.StatusPage(
            title=_("Monitor unavailable"), icon_name="utilities-system-monitor-symbolic"
        )
        self._status.set_vexpand(True)

        self._stack = Gtk.Stack()
        self._stack.add_named(scrolled, "metrics")
        self._stack.add_named(self._status, "status")
        self._stack.set_vexpand(True)
        self.append(self._stack)

        self.set_unavailable(_("Waiting for data..."))

    def set_title(self, text: str) -> None:
        self._title.set_text(text)

    def set_metrics(self, m: SystemMetrics) -> None:
        from ...utils.system_metrics import format_bytes, format_uptime

        self._host.set_text(m.hostname or "")
        self._host.set_visible(bool(m.hostname))
        self._os.set_text(m.os_name or "")
        self._os.set_visible(bool(m.os_name))
        self._kernel.set_text(_("Kernel: {v}").format(v=m.kernel) if m.kernel else "")
        self._kernel.set_visible(bool(m.kernel))
        if m.uptime_seconds is not None:
            self._uptime.set_text(
                _("Uptime: {v}").format(v=format_uptime(m.uptime_seconds))
            )
        self._uptime.set_visible(m.uptime_seconds is not None)
        if m.latency_ms is not None:
            self._latency.set_text(_("Latency: {v} ms").format(v=f"{m.latency_ms:.0f}"))
        self._latency.set_visible(m.latency_ms is not None)

        self._cpu.update(m.cpu_percent)
        if m.mem_used is not None and m.mem_total:
            detail = f"{format_bytes(m.mem_used)} / {format_bytes(m.mem_total)}"
        else:
            detail = ""
        self._ram.update(m.mem_percent, detail)
        self._disk.update(m.disk_percent, "/" if m.disk_percent is not None else "")
        self._stack.set_visible_child_name("metrics")

    def set_unavailable(self, reason: str) -> None:
        self._status.set_description(reason)
        self._stack.set_visible_child_name("status")

    def clear(self) -> None:
        for row in (self._cpu, self._ram, self._disk):
            row.reset()
        for lbl in (self._host, self._os, self._kernel, self._uptime, self._latency):
            lbl.set_text("")
        self.set_unavailable(_("Waiting for data..."))
