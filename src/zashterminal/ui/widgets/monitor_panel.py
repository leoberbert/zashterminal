# zashterminal/ui/widgets/monitor_panel.py
"""Live system metrics of the active terminal's host.

``MonitorView`` is the dashboard; it is shown compact in the side panel
(``MonitorPanel``) and wide in a separate window (``MonitorWindow``) that the
panel's expand button opens.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Deque, Iterable, List, Optional, Tuple

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk

from ...utils.icons import icon_image
from ...utils.translation_utils import _
from ..dialogs.base_dialog import close_on_escape

if TYPE_CHECKING:
    from ...utils.system_metrics import SystemMetrics

_CSS_FILE = Path(__file__).parent.parent.parent / "data" / "styles" / "panels.css"
_css_loaded = False
HISTORY_LEN = 60
PROCESS_ROWS = 5


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


def _short_bytes(n: float) -> str:
    """Compact size for narrow cards: 990B, 3.4K, 1.2M, 4.1G."""
    value = float(max(0.0, n))
    for unit in ("B", "K", "M", "G"):
        if value < 1024.0:
            return f"{value:.0f}{unit}" if unit == "B" or value >= 100 else f"{value:.1f}{unit}"
        value /= 1024.0
    return f"{value:.1f}T"


def _show(widget: Gtk.Widget, visible: bool) -> None:
    """Show/hide a widget, including the FlowBox wrapper that would keep its slot."""
    widget.set_visible(visible)
    parent = widget.get_parent()
    if isinstance(parent, Gtk.FlowBoxChild):
        parent.set_visible(visible)


def _flow(max_per_line: int, min_per_line: int = 1) -> Gtk.FlowBox:
    flow = Gtk.FlowBox(
        selection_mode=Gtk.SelectionMode.NONE,
        homogeneous=True,
        column_spacing=10,
        row_spacing=10,
        valign=Gtk.Align.START,
    )
    flow.set_min_children_per_line(min_per_line)
    flow.set_max_children_per_line(max_per_line)
    return flow


def _level_bar() -> Gtk.LevelBar:
    bar = Gtk.LevelBar(min_value=0, max_value=100, hexpand=True)
    bar.set_valign(Gtk.Align.CENTER)
    bar.set_mode(Gtk.LevelBarMode.CONTINUOUS)
    bar.add_css_class("zash-monitor-bar")
    # Disable stock offsets coloring so our CSS classes control the color.
    for offset in ("low", "high", "full"):
        bar.remove_offset_value(offset)
    return bar


def _apply_level(widget: Gtk.Widget, percent: Optional[float]) -> None:
    widget.remove_css_class("zash-monitor-warn")
    widget.remove_css_class("zash-monitor-crit")
    if percent is None:
        return
    if percent >= 90:
        widget.add_css_class("zash-monitor-crit")
    elif percent >= 75:
        widget.add_css_class("zash-monitor-warn")


class _Chip(Gtk.Box):
    """Small icon + text pair under a chart."""

    def __init__(self, icon_name: Optional[str], bundled: Optional[bool] = None):
        super().__init__(spacing=5)
        self.add_css_class("zash-monitor-chip")
        if icon_name:
            self.append(icon_image(icon_name, size=14, use_bundled=bundled))
        self._label = Gtk.Label(label="--", ellipsize=3)
        self.append(self._label)

    def set(self, text: str) -> None:
        self._label.set_text(text)


class _Card(Gtk.Box):
    def __init__(self, title: str, kind: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class("zash-monitor-card")
        self.add_css_class(f"zash-monitor-{kind}")
        self.title_label = Gtk.Label(label=title, xalign=0, ellipsize=3)
        self.title_label.add_css_class("zash-monitor-card-title")
        self.append(self.title_label)


class _MetricCard(_Card):
    """Card with a live chart (one or two series) and icon + value chips."""

    def __init__(
        self,
        title: str,
        kind: str,
        auto_scale: bool = False,
        second_rgb: Optional[Tuple[float, float, float]] = None,
        chart_height: int = 56,
        compact: bool = False,
    ):
        super().__init__(title, kind)
        self._samples: Deque[float] = deque(maxlen=HISTORY_LEN)
        self._samples2: Deque[float] = deque(maxlen=HISTORY_LEN)
        self._auto_scale = auto_scale
        self._second_rgb = second_rgb

        self._spark = Gtk.DrawingArea()
        self._spark.add_css_class("zash-monitor-spark")
        self._spark.set_content_height(chart_height)
        self._spark.set_hexpand(True)
        self._spark.set_draw_func(self._draw_spark)
        self.append(self._spark)

        # Narrow panel: one row of short text values (icons would not fit and
        # the card title already names the metric); wide: icon chips that wrap.
        self._compact = compact
        if compact:
            self._chips = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        else:
            self._chips = Gtk.FlowBox(
                selection_mode=Gtk.SelectionMode.NONE,
                homogeneous=False,
                halign=Gtk.Align.CENTER,
                column_spacing=12,
                row_spacing=2,
            )
            self._chips.set_max_children_per_line(3)
        self.append(self._chips)

    def add_chip(self, icon_name: Optional[str], bundled: Optional[bool] = None) -> _Chip:
        chip = _Chip(None if self._compact else icon_name, bundled)
        self._chips.append(chip)
        return chip

    def bind(self, samples: Deque[float], samples2: Deque[float]) -> None:
        """Chart the given (shared, per-terminal) sample history."""
        self._samples, self._samples2 = samples, samples2
        self._spark.queue_draw()

    def update(
        self,
        value: Optional[float],
        value2: Optional[float] = None,
        level_percent: Optional[float] = None,
        tooltip: Optional[str] = None,
        record: bool = True,
    ) -> None:
        self.set_tooltip_text(tooltip)
        _apply_level(self, level_percent)
        if record:
            if value is not None:
                self._samples.append(value)
            if value2 is not None:
                self._samples2.append(value2)
        self._spark.queue_draw()

    def _draw_spark(self, area, cr, width, height) -> None:
        if width <= 0 or height <= 0:
            return
        color = area.get_color()
        # Faint baseline so an idle (flat) chart still reads as a chart.
        cr.set_source_rgba(color.red, color.green, color.blue, 0.18)
        cr.set_line_width(1.0)
        cr.move_to(0, height - 0.5)
        cr.line_to(width, height - 0.5)
        cr.stroke()

        first, second = list(self._samples), list(self._samples2)
        peak = max(first + second, default=0.0)
        top = max(100.0, peak * 1.25) if self._auto_scale else 100.0
        rgb2 = self._second_rgb
        if rgb2 is not None:
            # Second series (network upload) follows the active theme palette.
            found, tx = area.get_style_context().lookup_color("zash_metric_tx")
            if found:
                rgb2 = (tx.red, tx.green, tx.blue)
            self._draw_series(cr, second, width, height, top, rgb2, color.alpha, fill=False)
        self._draw_series(
            cr, first, width, height, top, (color.red, color.green, color.blue), color.alpha
        )

    @staticmethod
    def _draw_series(cr, samples, width, height, top, rgb, alpha, fill=True) -> None:
        if len(samples) < 2:
            return
        step = width / (HISTORY_LEN - 1)
        x0 = width - step * (len(samples) - 1)
        pad = 3.0
        usable = height - 2 * pad

        def pt(i: int, v: float):
            return x0 + i * step, pad + usable * (1.0 - min(v, top) / top)

        cr.set_line_width(1.8)
        cr.set_line_join(1)  # round
        cr.set_line_cap(1)
        x, y = pt(0, samples[0])
        cr.move_to(x, y)
        for i, v in enumerate(samples[1:], start=1):
            x, y = pt(i, v)
            cr.line_to(x, y)
        cr.set_source_rgba(rgb[0], rgb[1], rgb[2], alpha)
        if not fill:
            cr.stroke()
            return
        cr.stroke_preserve()
        cr.line_to(x, height)
        cr.line_to(x0, height)
        cr.close_path()
        cr.set_source_rgba(rgb[0], rgb[1], rgb[2], alpha * 0.16)
        cr.fill()


class _SystemCard(_Card):
    """Host name, OS and a short key/value list."""

    def __init__(self):
        super().__init__("", "system")
        self.title_label.add_css_class("zash-monitor-host")
        self._subtitle = Gtk.Label(label="", xalign=0, ellipsize=3)
        self._subtitle.add_css_class("zash-monitor-dim")
        self.append(self._subtitle)
        self._grid = Gtk.Grid(column_spacing=12, row_spacing=3)
        self.append(self._grid)
        self._keys: List[str] = []
        self._values: List[Gtk.Label] = []

    def set_content(self, host: str, os_name: str, rows: List[Tuple[str, str]]) -> None:
        self.title_label.set_text(host)
        _show(self.title_label, bool(host))
        self._subtitle.set_text(os_name)
        _show(self._subtitle, bool(os_name))
        keys = [key for key, _value in rows]
        if keys != self._keys:
            while (child := self._grid.get_first_child()) is not None:
                self._grid.remove(child)
            self._values = []
            for row, (key, _value) in enumerate(rows):
                label = Gtk.Label(label=key, xalign=0, valign=Gtk.Align.START)
                label.add_css_class("zash-monitor-dim")
                value = Gtk.Label(
                    xalign=0, hexpand=True, wrap=True, wrap_mode=2, selectable=False
                )
                self._grid.attach(label, 0, row, 1, 1)
                self._grid.attach(value, 1, row, 1, 1)
                self._values.append(value)
            self._keys = keys
        for value_label, (_key, value) in zip(self._values, rows):
            value_label.set_text(value)


class _DisksCard(_Card):
    """One row per mounted filesystem: mount point, usage bar and percent."""

    def __init__(self):
        super().__init__(_("Disks"), "disk")
        self._rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.append(self._rows)
        self._mounts: List[str] = []
        self._widgets: List[Tuple[Gtk.Label, Gtk.LevelBar, Gtk.Label, Gtk.Box]] = []

    def update(self, disks, current_mount: str, size_text: Callable[[int], str]) -> None:
        mounts = [d.mount for d in disks]
        if mounts != self._mounts:
            while (child := self._rows.get_first_child()) is not None:
                self._rows.remove(child)
            self._widgets = []
            for _disk in disks:
                row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
                top = Gtk.Box(spacing=8)
                name = Gtk.Label(xalign=0, hexpand=True, ellipsize=2)
                value = Gtk.Label(xalign=1)
                value.add_css_class("zash-monitor-value")
                top.append(name)
                top.append(value)
                bar = _level_bar()
                row.append(top)
                row.append(bar)
                self._rows.append(row)
                self._widgets.append((name, bar, value, row))
            self._mounts = mounts
        for disk, (name, bar, value, row) in zip(disks, self._widgets):
            name.set_text(f"{disk.mount} · {size_text(disk.size)}")
            if disk.mount == current_mount:
                name.add_css_class("zash-monitor-current")
            else:
                name.remove_css_class("zash-monitor-current")
            bar.set_value(disk.percent)
            value.set_text(f"{disk.percent:.0f}%")
            _apply_level(row, disk.percent)

    def clear(self) -> None:
        self.update((), "", str)


class _ProcList(Gtk.Box):
    def __init__(self, title: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True)
        heading = Gtk.Label(label=title, xalign=0)
        heading.add_css_class("zash-monitor-dim")
        self.append(heading)
        self._rows: List[Tuple[Gtk.Label, Gtk.Label, Gtk.Box]] = []
        for _i in range(PROCESS_ROWS):
            row = Gtk.Box(spacing=8)
            name = Gtk.Label(xalign=0, hexpand=True, ellipsize=3)
            value = Gtk.Label(xalign=1)
            value.add_css_class("zash-monitor-value")
            row.append(name)
            row.append(value)
            self.append(row)
            self._rows.append((name, value, row))

    def update(self, procs, by_mem: bool) -> None:
        for index, (name, value, row) in enumerate(self._rows):
            if index < len(procs):
                proc = procs[index]
                name.set_text(proc.name)
                value.set_text(f"{(proc.mem if by_mem else proc.cpu):.1f}%")
                row.set_visible(True)
            else:
                row.set_visible(False)


class _ProcessesCard(_Card):
    """Top processes by CPU and by memory (side by side when there is room)."""

    def __init__(self, wide: bool):
        super().__init__(_("Processes"), "procs")
        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL if wide else Gtk.Orientation.VERTICAL,
            spacing=14 if wide else 10,
            homogeneous=wide,
        )
        self._cpu = _ProcList(_("Top CPU"))
        self._mem = _ProcList(_("Top memory"))
        box.append(self._cpu)
        box.append(self._mem)
        self.append(box)

    def update(self, m: "SystemMetrics") -> None:
        self._cpu.update(m.top_cpu, by_mem=False)
        self._mem.update(m.top_mem, by_mem=True)
        self.title_label.set_text(
            f"{_('Processes')} · {m.procs_total}" if m.procs_total else _("Processes")
        )


class _CoresCard(_Card):
    """Per-core CPU usage as thin bars."""

    def __init__(self):
        super().__init__(_("Cores"), "cores")
        self._flow = _flow(max_per_line=8, min_per_line=2)
        self._flow.set_homogeneous(True)
        self.append(self._flow)
        self._bars: List[Tuple[Gtk.LevelBar, Gtk.Label]] = []

    def update(self, percents: Iterable[float]) -> None:
        values = list(percents)
        if len(values) != len(self._bars):
            while (child := self._flow.get_first_child()) is not None:
                self._flow.remove(child)
            self._bars = []
            for index in range(len(values)):
                cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                label = Gtk.Label(label=str(index))
                label.add_css_class("zash-monitor-dim")
                bar = _level_bar()
                cell.append(bar)
                cell.append(label)
                self._flow.append(cell)
                self._bars.append((bar, label))
        for percent, (bar, label) in zip(values, self._bars):
            bar.set_value(percent)
            label.set_text(f"{percent:.0f}%")
            _apply_level(bar, percent)


class _History:
    """Chart samples per terminal, shared by every view of the monitor.

    Kept for as long as the terminal lives, so closing the panel, switching
    tabs or stopping the poller never wipes the charts. Each terminal holds at
    most ``HISTORY_LEN`` floats per series, so the footprint is negligible.
    """

    def __init__(self) -> None:
        self._store: dict = {}

    def pair(self, key, index: int) -> Tuple[Deque[float], Deque[float]]:
        per_terminal = self._store.setdefault(key, {})
        if index not in per_terminal:
            per_terminal[index] = (
                deque(maxlen=HISTORY_LEN),
                deque(maxlen=HISTORY_LEN),
            )
        return per_terminal[index]

    def keys(self) -> List:
        return list(self._store)

    def forget(self, key) -> None:
        self._store.pop(key, None)


class MonitorView(Gtk.Box):
    """The dashboard. ``wide`` lays cards out for a window instead of a sidebar."""

    def __init__(self, wide: bool, history: _History, record: bool = True):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        _ensure_css()
        self._wide = wide
        self._history = history
        # Only one view appends samples to the shared history.
        self._record = record
        if not wide:
            self.add_css_class("zash-monitor-compact")
        chart = 56 if wide else 40
        compact = not wide

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        content.set_margin_bottom(4)

        self._system = _SystemCard()
        content.append(self._system)

        self._cpu = _MetricCard(_("CPU"), "cpu", chart_height=chart, compact=compact)
        self._cpu_chip = self._cpu.add_chip("utilities-system-monitor-symbolic")
        self._temp_chip = self._cpu.add_chip("temperature-symbolic", bundled=True)
        self._ram = _MetricCard(_("Memory"), "mem", chart_height=chart, compact=compact)
        self._ram_chip = self._ram.add_chip("memory-symbolic", bundled=True)
        self._swap_chip = self._ram.add_chip(None)
        self._net = _MetricCard(
            _("Network"),
            "net",
            auto_scale=True,
            second_rgb=(0.96, 0.76, 0.07),
            chart_height=chart,
            compact=compact,
        )
        self._rx_chip = self._net.add_chip("go-down-symbolic", bundled=True)
        self._tx_chip = self._net.add_chip("go-up-symbolic", bundled=True)
        self._latency = _MetricCard(
            _("Latency"), "lat", auto_scale=True, chart_height=chart, compact=compact
        )
        self._lat_chip = self._latency.add_chip("network-wireless-signal-good-symbolic")
        self._gpu = _MetricCard("GPU", "gpu", chart_height=chart, compact=compact)
        self._gpu_chip = self._gpu.add_chip("video-display-symbolic")
        self._gpu_mem_chip = self._gpu.add_chip("memory-symbolic", bundled=True)
        self._gpu_temp_chip = self._gpu.add_chip("temperature-symbolic", bundled=True)

        charts = _flow(max_per_line=4 if wide else 2, min_per_line=2)
        for card in (self._cpu, self._ram, self._net, self._latency, self._gpu):
            charts.append(card)
        content.append(charts)

        self._disks = _DisksCard()
        self._procs = _ProcessesCard(wide)
        self._cores = _CoresCard()
        # Wide: cards side by side. Compact: stacked, each at its own height
        # (a homogeneous FlowBox would stretch the short cards).
        lists = (
            _flow(max_per_line=3)
            if wide
            else Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        )
        lists.append(self._disks)
        lists.append(self._procs)
        if wide:
            lists.append(self._cores)
        content.append(lists)
        _show(self._latency, False)
        _show(self._gpu, False)

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

        self._cards = (self._cpu, self._ram, self._net, self._latency, self._gpu)
        self.set_unavailable(_("Waiting for data..."))
        self.use_terminal(None)

    def use_terminal(self, key) -> None:
        """Chart the history of ``key`` (a terminal id)."""
        for index, card in enumerate(self._cards):
            card.bind(*self._history.pair(key, index))

    # -- updates ----------------------------------------------------------

    def set_metrics(self, m: "SystemMetrics") -> None:
        from ...utils.system_metrics import format_bytes, format_uptime

        rows: List[Tuple[str, str]] = []
        if m.kernel:
            rows.append((_("Kernel"), f"{m.kernel} ({m.arch})" if m.arch else m.kernel))
        if m.cpu_model:
            rows.append((_("Processor"), m.cpu_model))
        if m.cores:
            rows.append((_("Cores"), str(m.cores)))
        if m.uptime_seconds is not None:
            rows.append((_("Uptime"), format_uptime(m.uptime_seconds)))
        if m.load is not None:
            rows.append((_("Load"), " · ".join(f"{v:.2f}" for v in m.load)))
        self._system.set_content(m.hostname or "", m.os_name or "", rows)

        cpu = m.cpu_percent
        self._cpu.update(cpu, level_percent=cpu, record=self._record)
        self._cpu_chip.set(f"{cpu:.0f}%" if cpu is not None else "--")
        _show(self._temp_chip, m.temp_c is not None)
        if m.temp_c is not None:
            self._temp_chip.set(f"{m.temp_c:.0f}°C")

        mem = m.mem_percent
        has_swap = bool(m.swap_total) and m.swap_used is not None
        swap_text = (
            _("Swap: {used} / {total}").format(
                used=format_bytes(m.swap_used), total=format_bytes(m.swap_total)
            )
            if has_swap
            else ""
        )
        mem_tip = (
            f"{format_bytes(m.mem_used)} / {format_bytes(m.mem_total)}"
            if m.mem_used is not None and m.mem_total
            else ""
        )
        self._ram.update(
            mem,
            level_percent=mem,
            tooltip="\n".join(t for t in (mem_tip, swap_text) if t) or None,
            record=self._record,
        )
        compact_text = not self._wide
        if mem is not None and m.mem_used is not None:
            used = _short_bytes(m.mem_used) if compact_text else format_bytes(m.mem_used)
            self._ram_chip.set(f"{used} · {mem:.0f}%")
        else:
            self._ram_chip.set("--")
        # In the narrow panel swap lives in the tooltip to keep the card short.
        _show(self._swap_chip, has_swap and self._wide)
        if has_swap:
            self._swap_chip.set(swap_text)

        if compact_text:
            rate = lambda v: _short_bytes(v) if v is not None else "--"  # noqa: E731
            self._rx_chip.set(f"↓{rate(m.net_rx_bps)}")
            self._tx_chip.set(f"↑{rate(m.net_tx_bps)}")
        else:
            rate = lambda v: f"{format_bytes(int(v))}/s" if v is not None else "--"  # noqa: E731
            self._rx_chip.set(rate(m.net_rx_bps))
            self._tx_chip.set(rate(m.net_tx_bps))
        full = lambda v: f"{format_bytes(int(v))}/s" if v is not None else "--"  # noqa: E731
        self._net.update(
            m.net_rx_bps,
            m.net_tx_bps,
            tooltip=f"↓ {full(m.net_rx_bps)}\n↑ {full(m.net_tx_bps)}",
            record=self._record,
        )

        has_latency = m.latency_ms is not None
        _show(self._latency, has_latency)
        if has_latency:
            self._latency.update(m.latency_ms, record=self._record)
            self._lat_chip.set(f"{m.latency_ms:.0f} ms")

        gpu = m.gpu
        _show(self._gpu, gpu is not None)
        if gpu is not None:
            gpu_tip = gpu.name
            if gpu.mem_used is not None:
                gpu_tip += f"\n{format_bytes(gpu.mem_used)}"
                if gpu.mem_total:
                    gpu_tip += f" / {format_bytes(gpu.mem_total)}"
            self._gpu.update(
                gpu.util, level_percent=gpu.util, tooltip=gpu_tip, record=self._record
            )
            self._gpu_chip.set(f"{gpu.util:.0f}%" if gpu.util is not None else "--")
            _show(self._gpu_mem_chip, gpu.mem_used is not None and self._wide)
            if gpu.mem_used is not None:
                self._gpu_mem_chip.set(format_bytes(gpu.mem_used))
            _show(self._gpu_temp_chip, gpu.temp is not None)
            if gpu.temp is not None:
                self._gpu_temp_chip.set(f"{gpu.temp:.0f}°C")

        disks = m.disks
        _show(self._disks, bool(disks))
        self._disks.update(disks, m.disk_mount, format_bytes)

        has_procs = bool(m.top_cpu or m.top_mem)
        _show(self._procs, has_procs)
        if has_procs:
            self._procs.update(m)
        if self._wide:
            _show(self._cores, bool(m.core_percents))
            self._cores.update(m.core_percents)
        self._stack.set_visible_child_name("metrics")

    def set_unavailable(self, reason: str) -> None:
        self._status.set_description(reason)
        self._stack.set_visible_child_name("status")

    def clear(self) -> None:
        """Show the placeholder; chart history is kept (see ``_History``)."""
        self.set_unavailable(_("Waiting for data..."))


class MonitorWindow(Adw.Window):
    """Wide dashboard in its own window (opened from the panel's expand button)."""

    def __init__(
        self,
        parent: Optional[Gtk.Window],
        on_closed: Callable[[], None],
        history: _History,
    ):
        super().__init__(title=_("Monitor"))
        close_on_escape(self)
        self.set_default_size(980, 700)
        if parent is not None:
            self.set_transient_for(parent)
            self.set_destroy_with_parent(True)
        self.view = MonitorView(wide=True, history=history, record=False)
        self.view.set_margin_start(12)
        self.view.set_margin_end(12)
        self.view.set_margin_bottom(12)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        toolbar.set_content(self.view)
        self.set_content(toolbar)
        self.connect("close-request", lambda _w: on_closed() or False)


class MonitorPanel(Gtk.Box):
    __gtype_name__ = "ZashMonitorPanel"

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        _ensure_css()
        self.add_css_class("zash-monitor-panel")
        # Called when the expanded window opens/closes (the controller then
        # starts or stops collecting the extra "detail" metrics).
        self.on_detail_changed: Optional[Callable[[], None]] = None
        self._window: Optional[MonitorWindow] = None
        self._last_metrics: Optional["SystemMetrics"] = None
        self._last_reason: Optional[str] = _("Waiting for data...")
        self._title_text = _("Monitor")

        header = Gtk.Box(spacing=6)
        self._title = Gtk.Label(label=self._title_text, xalign=0, hexpand=True, ellipsize=3)
        self._title.add_css_class("zash-monitor-title")
        header.append(self._title)
        self._expand = Gtk.Button(icon_name="view-fullscreen-symbolic")
        self._expand.add_css_class("flat")
        self._expand.set_tooltip_text(_("Expand monitor"))
        self._expand.connect("clicked", self._on_expand_clicked)
        header.append(self._expand)
        self.append(header)

        self._history = _History()
        self._key = None
        self._view = MonitorView(wide=False, history=self._history)
        self.append(self._view)

    def is_expanded(self) -> bool:
        return self._window is not None

    def _views(self) -> List[MonitorView]:
        views = [self._view]
        if self._window is not None:
            views.append(self._window.view)
        return views

    def _on_expand_clicked(self, _button) -> None:
        if self._window is not None:
            self._window.present()
            return
        root = self.get_root()
        self._window = MonitorWindow(
            root if isinstance(root, Gtk.Window) else None,
            self._on_window_closed,
            self._history,
        )
        self._window.view.use_terminal(self._key)
        self._window.set_title(self._title_text)
        if self._last_metrics is not None:
            self._window.view.set_metrics(self._last_metrics)
        elif self._last_reason:
            self._window.view.set_unavailable(self._last_reason)
        self._window.present()
        self._notify_detail()

    def _on_window_closed(self) -> None:
        self._window = None
        self._notify_detail()

    def _notify_detail(self) -> None:
        if self.on_detail_changed is not None:
            self.on_detail_changed()

    def set_title(self, text: str) -> None:
        self._title_text = text or _("Monitor")
        self._title.set_text(self._title_text)
        if self._window is not None:
            self._window.set_title(self._title_text)

    def set_metrics(self, m: "SystemMetrics") -> None:
        self._last_metrics, self._last_reason = m, None
        for view in self._views():
            view.set_metrics(m)

    def set_unavailable(self, reason: str) -> None:
        self._last_metrics, self._last_reason = None, reason
        for view in self._views():
            view.set_unavailable(reason)

    def set_terminal(self, key) -> None:
        """Switch to the terminal ``key``: its charts come back, data is pending."""
        self._key = key
        for view in self._views():
            view.use_terminal(key)
        self.clear()

    def forget_terminals(self, is_alive: Callable[[int], bool]) -> None:
        """Drop chart history of terminals that no longer exist."""
        for key in self._history.keys():
            if key is not None and not is_alive(key):
                self._history.forget(key)

    def clear(self) -> None:
        self._last_metrics, self._last_reason = None, _("Waiting for data...")
        for view in self._views():
            view.clear()
