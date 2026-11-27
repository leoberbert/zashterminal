# zashterminal/ui/status_controller.py
"""Keeps the status bar, connection banner and monitor panel in sync with the
active terminal, polling host metrics in the background."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Optional, Set, Tuple

from gi.repository import GLib, Pango

from ..sessions.models import SessionItem
from ..terminal.registry import LIVE_TERMINAL_STATUSES
from ..utils.logger import get_logger
from ..utils.translation_utils import _

if TYPE_CHECKING:
    from ..utils.system_metrics import SystemMetrics
    from ..window import ZashTerminalWindow
    from .widgets.monitor_panel import MonitorPanel
    from .widgets.status_bar import ConnectionBanner, StatusBar

POLL_INTERVAL_SECONDS = 3.0
STATE_TICK_SECONDS = 2

# (terminal id, terminal type, live)
_Signature = Tuple[Optional[int], str, bool]


class StatusController:
    def __init__(
        self,
        window: "ZashTerminalWindow",
        status_bar: "StatusBar",
        banner: "ConnectionBanner",
        monitor_panel: "MonitorPanel",
    ) -> None:
        self.window = window
        self.status_bar = status_bar
        self.banner = banner
        self.monitor_panel = monitor_panel
        self.logger = get_logger("zashterminal.ui.status")

        self._poller: Any = None
        self._signature: Optional[_Signature] = None
        self._dismissed: Set[int] = set()
        self._last_os_name = ""
        self._monitor_active = False
        self._tick_id = GLib.timeout_add_seconds(STATE_TICK_SECONDS, self._tick)

    # -- public API -------------------------------------------------------

    def refresh(self) -> None:
        """Re-evaluate the active terminal (tab switch, focus, zoom, connect)."""
        self._evaluate()

    def set_monitor_active(self, active: bool) -> None:
        """The monitor panel needs data even when the status bar is hidden."""
        if self._monitor_active != active:
            self._monitor_active = active
            self._signature = None
            self._evaluate()

    def set_status_bar_visible(self, visible: bool) -> None:
        self.status_bar.set_visible(visible)
        self._signature = None
        self._evaluate()

    def dismiss_banner(self) -> None:
        if self._signature and self._signature[0] is not None:
            self._dismissed.add(self._signature[0])
        self.banner.hide_banner()

    def shutdown(self) -> None:
        if self._tick_id:
            GLib.source_remove(self._tick_id)
            self._tick_id = 0
        self._stop_poller()

    # -- internals --------------------------------------------------------

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
        terminal = self.window.tab_manager.get_selected_terminal()
        terminal_id, kind, live, session = self._describe(terminal)
        self._update_terminal_info(terminal, kind)

        signature: _Signature = (terminal_id, kind, live)
        if signature == self._signature:
            return
        previous = self._signature
        self._signature = signature
        self._last_os_name = ""

        self._stop_poller()
        self.status_bar.set_metrics(None)
        self.monitor_panel.clear()

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

        self._update_banner(terminal_id, kind, live, name, previous)
        self._start_poller(signature, session)

    def _update_banner(self, terminal_id, kind, live, name, previous) -> None:
        if kind == "ssh" and live and terminal_id not in self._dismissed:
            self.banner.show_connected(name)
        else:
            self.banner.hide_banner()

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

    def _metrics_wanted(self) -> bool:
        return self.status_bar.get_visible() or self._monitor_active

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
        if self._poller is not None:
            try:
                self._poller.stop()
            finally:
                self._poller = None

    def _apply_sample(self, signature: _Signature, metrics: "SystemMetrics") -> bool:
        if signature != self._signature:
            return GLib.SOURCE_REMOVE
        self.status_bar.set_metrics(metrics)
        self.monitor_panel.set_metrics(metrics)
        if metrics.os_name and metrics.os_name != self._last_os_name:
            self._last_os_name = metrics.os_name
            terminal_id, kind, live = signature
            if kind == "ssh" and live and terminal_id not in self._dismissed:
                session = self._session_for_signature()
                if session is not None:
                    self.banner.show_connected(session.name, metrics.os_name)
        return GLib.SOURCE_REMOVE

    def _apply_error(self, signature: _Signature, message: str) -> bool:
        if signature != self._signature:
            return GLib.SOURCE_REMOVE
        self.status_bar.set_metrics(None)
        self.monitor_panel.set_unavailable(message)
        return GLib.SOURCE_REMOVE

    def _session_for_signature(self) -> Optional[SessionItem]:
        terminal = self.window.tab_manager.get_selected_terminal()
        return self._describe(terminal)[3]
