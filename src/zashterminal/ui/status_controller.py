# zashterminal/ui/status_controller.py
"""Keeps the status bar and monitor panel in sync with the
active terminal, polling host metrics in the background."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

from gi.repository import GLib, Pango

from ..sessions.models import SessionItem
from ..terminal.registry import LIVE_TERMINAL_STATUSES
from ..utils.logger import get_logger
from ..utils.translation_utils import _

if TYPE_CHECKING:
    from ..utils.system_metrics import SystemMetrics
    from ..window import ZashTerminalWindow
    from .widgets.monitor_panel import MonitorPanel
    from .widgets.status_bar import StatusBar

POLL_INTERVAL_SECONDS = 3.0
# Terminals turn "live" asynchronously (process spawn / SSH login) with no
# event, so the tick is what notices it; keep it short.
STATE_TICK_MS = 250

# (terminal id, terminal type, live)
_Signature = Tuple[Optional[int], str, bool]


class StatusController:
    def __init__(
        self,
        window: "ZashTerminalWindow",
        status_bar: "StatusBar",
        monitor_panel: "MonitorPanel",
    ) -> None:
        self.window = window
        self.status_bar = status_bar
        self.monitor_panel = monitor_panel
        monitor_panel.on_detail_changed = self._on_detail_changed
        self.logger = get_logger("zashterminal.ui.status")

        self._poller: Any = None
        self._collector: Any = None
        self._session: Optional[SessionItem] = None
        self._disk_path = "/"
        self._signature: Optional[_Signature] = None
        self._monitor_active = False
        # Last sample per terminal: shown instantly when switching back to a tab
        # while the fresh sample is still being collected.
        self._metrics_cache: Dict[int, "SystemMetrics"] = {}
        self._tick_id = GLib.timeout_add(STATE_TICK_MS, self._tick)

    # -- public API -------------------------------------------------------

    def refresh(self) -> None:
        """Re-evaluate the active terminal (tab switch, focus, zoom, connect)."""
        self._evaluate()

    def set_monitor_active(self, active: bool) -> None:
        """The monitor panel needs data even when the status bar is hidden."""
        if self._monitor_active != active:
            self._monitor_active = active
            self._sync_poller()

    def set_status_bar_visible(self, visible: bool) -> None:
        self.status_bar.set_visible(visible)
        self._sync_poller()

    def shutdown(self) -> None:
        if self._tick_id:
            GLib.source_remove(self._tick_id)
            self._tick_id = 0
        self._stop_poller()

    # -- internals --------------------------------------------------------

    def _active_terminal(self):
        """Terminal of the visible tab.

        ``get_selected_terminal`` prefers the last *focused* terminal, which
        stays the previous tab's terminal until the new one takes focus; the
        status bar must follow the tab, not the focus.
        """
        tabs = self.window.tab_manager
        terminal = tabs.get_selected_terminal()
        page = tabs.pages.get(tabs.active_tab) if tabs.active_tab else None
        if page is not None and (
            terminal is None or tabs.get_page_for_terminal(terminal) is not page
        ):
            terminals = tabs.get_all_terminals_in_page(page)
            if terminals:
                return terminals[0]
        return terminal

    def _tick(self) -> bool:
        try:
            self._evaluate()
        except Exception as exc:  # never let the timer die
            self.logger.debug(f"Status refresh failed: {exc}")
        return GLib.SOURCE_CONTINUE

    def _describe(self, terminal) -> Tuple[Optional[int], str, bool, Optional[SessionItem]]:
        if terminal is None:
            return None, "none", False, None
        terminal_id = getattr(terminal, "terminal_id", None)
        info = (
            self.window.terminal_manager.registry.get_terminal_info(terminal_id)
            if terminal_id is not None
            else None
        )
        if not info:
            return terminal_id, "none", False, None
        identifier = info.get("identifier")
        session = identifier if isinstance(identifier, SessionItem) else None
        live = info.get("status") in LIVE_TERMINAL_STATUSES
        return terminal_id, info.get("type", "none"), live, session

    def _evaluate(self) -> None:
        terminal = self._active_terminal()
        terminal_id, kind, live, session = self._describe(terminal)
        self._update_terminal_info(terminal, kind)
        self._sync_disk_path(terminal)

        signature: _Signature = (terminal_id, kind, live)
        if signature == self._signature:
            return
        self._signature = signature
        self._session = session

        self._stop_poller()
        self.status_bar.set_metrics(None)
        # Charts are kept per terminal: only terminals that are gone are dropped.
        self.monitor_panel.set_terminal(terminal_id)
        self._forget_dead_terminals()

        cached = self._metrics_cache.get(terminal_id) if live else None
        if cached is not None:
            self.status_bar.set_metrics(cached)
            self.monitor_panel.set_metrics(cached)
        elif terminal_id is not None:
            self._metrics_cache.pop(terminal_id, None)

        name = session.name if session else ""
        self.monitor_panel.set_title(name)

        if kind == "ssh":
            if live:
                self.status_bar.set_connection(self.status_bar.CONNECTED)
            else:
                self.status_bar.set_connection(self.status_bar.DISCONNECTED)
        elif kind == "sftp":
            self.status_bar.set_connection(
                self.status_bar.CONNECTED if live else self.status_bar.DISCONNECTED,
                _("SFTP connected") if live else _("Disconnected"),
            )
        elif kind == "local":
            self.status_bar.set_connection(self.status_bar.LOCAL)
        else:
            self.status_bar.set_connection(self.status_bar.NONE)

        self._start_poller(signature, session)

    def _update_terminal_info(self, terminal, kind: str) -> None:
        if terminal is None:
            self.status_bar.set_terminal_info(None, None, None)
            return
        encoding = None
        font_size = None
        try:
            encoding = terminal.get_encoding() or "UTF-8"
            font = terminal.get_font()
            if font is not None and font.get_size() > 0:
                font_size = round(
                    font.get_size() / Pango.SCALE * terminal.get_font_scale(), 1
                )
        except Exception:
            pass
        shell = None
        if kind == "local":
            shell = os.path.basename(os.environ.get("SHELL", "")) or None
            if shell:
                shell = shell.capitalize()
        self.status_bar.set_terminal_info(encoding, shell, font_size)

    @staticmethod
    def _terminal_cwd(terminal) -> str:
        if terminal is None:
            return "/"
        try:
            uri = terminal.get_current_directory_uri()
        except Exception:
            return "/"
        if not uri:
            return "/"
        from urllib.parse import unquote, urlparse

        return unquote(urlparse(uri).path) or "/"

    def _sync_disk_path(self, terminal) -> None:
        """Report the filesystem of the shell's current directory."""
        path = self._terminal_cwd(terminal)
        if path == self._disk_path:
            return
        self._disk_path = path
        if self._collector is not None:
            self._collector.disk_path = path
        if self._poller is not None:
            self._poller.poke()

    def _detail_wanted(self) -> bool:
        """Processes, per-core CPU and GPU are only collected when shown."""
        return self._monitor_active or self.monitor_panel.is_expanded()

    def _on_detail_changed(self) -> None:
        self._sync_poller()

    def _sync_poller(self) -> None:
        """Start/stop collection as visibility changes, without touching data.

        Closing the panel only stops sampling (when nothing else needs it);
        everything already collected stays until its terminal is closed.
        """
        if self._collector is not None:
            detail = self._detail_wanted()
            turned_on = detail and not self._collector.detail
            self._collector.detail = detail
            if turned_on and self._poller is not None:
                # Processes/GPU/cores were not being sampled: fetch them now
                # instead of waiting for the next 3 s tick.
                self._poller.poke()
        wanted = self._metrics_wanted()
        if wanted and self._poller is None and self._signature is not None:
            self._start_poller(self._signature, self._session)
        elif not wanted and self._poller is not None:
            self._stop_poller()

    def _terminal_alive(self, terminal_id: int) -> bool:
        return self.window.terminal_manager.registry.get_terminal_info(terminal_id) is not None

    def _forget_dead_terminals(self) -> None:
        self.monitor_panel.forget_terminals(self._terminal_alive)
        for terminal_id in list(self._metrics_cache):
            if not self._terminal_alive(terminal_id):
                del self._metrics_cache[terminal_id]

    def _metrics_wanted(self) -> bool:
        return self.status_bar.get_visible() or self._detail_wanted()

    def _start_poller(self, signature: _Signature, session: Optional[SessionItem]) -> None:
        _terminal_id, kind, live, = signature
        if not live or not self._metrics_wanted():
            if kind in ("ssh", "local") and not live:
                self.monitor_panel.set_unavailable(_("Not connected"))
            return

        from ..utils.system_metrics import (
            LocalMetricsCollector,
            MetricsPoller,
            RemoteMetricsCollector,
        )

        if kind == "local":
            collector = LocalMetricsCollector()
        elif kind == "ssh" and session is not None:
            spawner = self.window.terminal_manager.spawner
            collector = RemoteMetricsCollector(
                session, spawner._get_ssh_control_path(session)
            )
        else:
            self.monitor_panel.set_unavailable(
                _("Metrics are not available for this tab")
            )
            return

        collector.disk_path = self._disk_path
        collector.detail = self._detail_wanted()
        self._collector = collector
        self._poller = MetricsPoller(collector, interval=POLL_INTERVAL_SECONDS)
        self._poller.start(
            lambda metrics, sig=signature: GLib.idle_add(
                self._apply_sample, sig, metrics
            ),
            lambda message, sig=signature: GLib.idle_add(
                self._apply_error, sig, message
            ),
        )

    def _stop_poller(self) -> None:
        self._collector = None
        if self._poller is not None:
            try:
                self._poller.stop()
            finally:
                self._poller = None

    def _apply_sample(self, signature: _Signature, metrics: "SystemMetrics") -> bool:
        if signature != self._signature:
            return GLib.SOURCE_REMOVE
        terminal_id = signature[0]
        if terminal_id is not None:
            previous = self._metrics_cache.get(terminal_id)
            if previous is not None and not self._detail_wanted():
                # Detail (processes, cores, GPU) is only sampled while shown:
                # keep the last values instead of blanking them.
                from dataclasses import replace

                metrics = replace(
                    metrics,
                    core_percents=previous.core_percents,
                    procs_total=previous.procs_total,
                    top_cpu=previous.top_cpu,
                    top_mem=previous.top_mem,
                    gpu=previous.gpu,
                )
            if len(self._metrics_cache) > 64:
                self._metrics_cache.pop(next(iter(self._metrics_cache)))
            self._metrics_cache[terminal_id] = metrics
        self.status_bar.set_metrics(metrics)
        self.monitor_panel.set_metrics(metrics)
        return GLib.SOURCE_REMOVE

    def _apply_error(self, signature: _Signature, message: str) -> bool:
        if signature != self._signature:
            return GLib.SOURCE_REMOVE
        self.status_bar.set_metrics(None)
        self.monitor_panel.set_unavailable(message)
        return GLib.SOURCE_REMOVE
