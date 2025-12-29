# zashterminal/core/signals.py
"""
Singleton Event Bus for application-wide signal propagation.

This module provides a centralized, decoupled event system using GObject signals.
Components can emit signals when state changes, and listeners can subscribe to 
these signals without direct references to the emitters.

Usage:
    # Emit a signal
    AppSignals.get().emit("session-created", session_item)
    
    # Listen to a signal
    AppSignals.get().connect("session-created", self._on_session_created)
"""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GObject


class AppSignals(GObject.Object):
    """
    Singleton Event Bus for decoupled component communication.
    
    All signals use GObject signal infrastructure for thread-safety and
    seamless GTK integration. Components should emit/connect via AppSignals.get().
    """

    __gsignals__ = {
        # Session signals
        "session-created": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "session-updated": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "session-deleted": (GObject.SignalFlags.RUN_FIRST, None, (str,)),

        # Folder signals
        "folder-created": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "folder-updated": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "folder-deleted": (GObject.SignalFlags.RUN_FIRST, None, (str,)),

        # UI update requests
        "request-tree-refresh": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "request-session-select": (GObject.SignalFlags.RUN_FIRST, None, (str,)),

        # Settings signals
        "settings-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, object)),
        "color-scheme-changed": (GObject.SignalFlags.RUN_FIRST, None, (int,)),

        # Terminal signals
        "terminal-created": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "terminal-closed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "terminal-title-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
    }

    _instance = None

    def __init__(self):
        super().__init__()

    @classmethod
    def get(cls) -> "AppSignals":
        """
        Get the singleton AppSignals instance.
        
        Returns:
            The global AppSignals instance.
        """
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """
        Reset the singleton instance (useful for testing).
        
        Warning: This will disconnect all signal handlers.
        """
        cls._instance = None


# Convenience function for quick access
def get_app_signals() -> AppSignals:
    """Get the global AppSignals instance."""
    return AppSignals.get()
