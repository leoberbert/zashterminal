# zashterminal/utils/osc7_tracker.py

import threading
from typing import Any, Callable, Dict, Optional
from urllib.parse import unquote, urlparse
from weakref import WeakKeyDictionary

import gi

gi.require_version("Vte", "3.91")
from gi.repository import GLib, Vte

from .logger import get_logger
from .osc7 import OSC7Info, OSC7Parser


class OSC7TerminalTracker:
    """Tracks OSC7 sequences from VTE terminals and manages directory information."""

    def __init__(self, settings_manager=None):
        self.logger = get_logger("zashterminal.utils.osc7.tracker")
        self.settings_manager = settings_manager
        self.parser = OSC7Parser()
        self._terminals: WeakKeyDictionary[Vte.Terminal, Dict[str, Any]] = (
            WeakKeyDictionary()
        )
        self._lock = threading.RLock()
        self.on_directory_changed: Optional[
            Callable[[Vte.Terminal, OSC7Info], None]
        ] = None

    def untrack_terminal(self, terminal: Vte.Terminal) -> None:
        """Stop tracking OSC7 sequences for a terminal."""
        try:
            with self._lock:
                if terminal in self._terminals:
                    del self._terminals[terminal]
        except Exception as e:
            self.logger.error(f"Failed to untrack terminal: {e}")

    def _on_directory_uri_changed(self, terminal: Vte.Terminal, _param_spec) -> None:
        """Handle directory change detected from VTE's current directory URI."""
        try:
            with self._lock:
                if terminal not in self._terminals:
                    return
                terminal_data = self._terminals[terminal]
                directory_uri = terminal.get_current_directory_uri()
                if directory_uri:
                    self._handle_directory_uri_change(
                        terminal, terminal_data, directory_uri
                    )
        except Exception as e:
            self.logger.error(f"Terminal contents change processing failed: {e}")

    def _handle_directory_uri_change(
        self, terminal: Vte.Terminal, terminal_data: Dict[str, Any], directory_uri: str
    ) -> None:
        """Handle directory change detected from VTE's current directory URI."""
        try:
            parsed_uri = urlparse(directory_uri)
            if parsed_uri.scheme != "file":
                return

            path = unquote(parsed_uri.path)
            hostname = parsed_uri.hostname or "localhost"
            display_path = self.parser._create_display_path(path)
            osc7_info = OSC7Info(
                hostname=hostname, path=path, display_path=display_path
            )

            last_osc7 = terminal_data.get("last_osc7")
            if last_osc7 and last_osc7.path == osc7_info.path:
                return

            self._handle_osc7_detected(terminal, terminal_data, osc7_info)
        except Exception as e:
            self.logger.error(f"Directory URI change handling failed: {e}")

    def _handle_osc7_detected(
        self, terminal: Vte.Terminal, terminal_data: Dict[str, Any], osc7_info: OSC7Info
    ) -> None:
        """Handle detected OSC7 sequence and update tab title."""
        try:
            last_osc7 = terminal_data.get("last_osc7")
            if last_osc7 and last_osc7.path == osc7_info.path:
                return

            terminal_data["current_dir"] = osc7_info.path
            terminal_data["last_osc7"] = osc7_info

            if self.on_directory_changed:
                GLib.idle_add(self._call_callback_safe, terminal, osc7_info)
        except Exception as e:
            self.logger.error(f"OSC7 handling failed: {e}")

    def _call_callback_safe(self, terminal: Vte.Terminal, osc7_info: OSC7Info) -> bool:
        """Safely call external callback on main thread."""
        try:
            if self.on_directory_changed:
                self.on_directory_changed(terminal, osc7_info)
        except Exception as e:
            self.logger.error(f"OSC7 callback failed: {e}")
        return False


# Global tracker instance
_global_tracker: Optional[OSC7TerminalTracker] = None
_tracker_lock = threading.Lock()


def get_osc7_tracker(settings_manager=None) -> OSC7TerminalTracker:
    """Get global OSC7 tracker instance."""
    global _global_tracker
    with _tracker_lock:
        if _global_tracker is None:
            _global_tracker = OSC7TerminalTracker(settings_manager)
        return _global_tracker
