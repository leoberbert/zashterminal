"""Keep niri from painting its focus ring behind translucent windows.

niri draws the focus ring and border of client-decorated windows as a solid
shape *behind* the window, so a transparent terminal turns the color of the
focus ring whenever it is focused. Windows using server-side decorations
(Alacritty with ``prefer-no-csd``) only get an outline.

GTK4 announces client-side decorations through KDE's server-decoration
protocol, and niri keeps the mode last requested for each surface. A second
decoration object requesting server mode therefore makes niri draw only the
outline. niri never draws title bars, so GTK's header bar stays the only one.
Protocol: https://wayland.app/protocols/kde-server-decoration
"""

from __future__ import annotations

import ctypes as C
import os

from .backdrop_blur import _Argument, _gpointer, _Interface, _library, _Message, _WaylandClient

_SERVER_MODE = 2


def is_niri_session() -> bool:
    return "niri" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower().split(":")


class NiriServerDecoration(_WaylandClient):
    """Own the server-side decoration request for one GTK window's surface."""

    @classmethod
    def for_display(cls, display, logger=None):
        """Return an instance only on a niri Wayland session advertising the protocol."""
        if not is_niri_session() or display.__gtype__.name != "GdkWaylandDisplay":
            return None
        try:
            decoration = cls(display, _library("gtk-4"))
        except (OSError, AttributeError, RuntimeError) as error:
            if logger:
                logger.warning(f"niri server-side decoration unavailable: {error}")
            return None
        if decoration._manager is None:
            decoration.close()
            return None
        return decoration

    def __init__(self, display, gtk):
        super().__init__(display, gtk)
        self._surface = None
        self._decoration = None
        self._manager = None
        self._define_protocol()
        self._manager = self._bind_globals(self._bind_manager)

    def _define_protocol(self):
        # All interface/type arrays must outlive the proxies referring to them.
        self._surface_interface = _Interface.in_dll(self.lib, "wl_surface_interface")
        self._decoration_methods = (_Message * 2)(
            _Message(b"release", b"", None),
            _Message(b"request_mode", b"u", None),
        )
        self._decoration_events = (_Message * 1)(_Message(b"mode", b"u", None))
        self._decoration_interface = _Interface(
            b"org_kde_kwin_server_decoration", 1,
            2, self._decoration_methods, 1, self._decoration_events,
        )
        self._create_types = (C.POINTER(_Interface) * 2)(
            C.pointer(self._decoration_interface), C.pointer(self._surface_interface),
        )
        self._manager_methods = (_Message * 1)(_Message(b"create", b"no", self._create_types))
        self._manager_events = (_Message * 1)(_Message(b"default_mode", b"u", None))
        self._manager_interface = _Interface(
            b"org_kde_kwin_server_decoration_manager", 1,
            1, self._manager_methods, 1, self._manager_events,
        )

    def _bind_manager(self, registry, _queue, globals_found):
        name = globals_found.get(self._manager_interface.name)
        if name is None:
            return None
        manager = self._bind(registry, name, self._manager_interface)
        if not manager:
            raise RuntimeError("Could not bind the server decoration manager")
        # Events without listeners are dropped on GTK's queue.
        self._set_queue(manager, None)
        return manager

    def apply(self, surface):
        pointer = self._surface_pointer(_gpointer(surface))
        if not self._manager or not pointer or pointer == self._surface:
            return
        self._release()
        self._decoration = self._request(
            self._manager, 0, _Argument(o=None), _Argument(o=pointer),
            interface=self._decoration_interface,
        )
        if not self._decoration:
            raise RuntimeError("Could not create the server decoration object")
        self._surface = pointer
        self._request(self._decoration, 1, _Argument(u=_SERVER_MODE))
        self._flush(self._display)

    def _release(self):
        if self._decoration:
            self._request(self._decoration, 0, destroy=True)
            self._decoration = None
        self._surface = None

    def close(self):
        self._release()
        if self._manager:
            self._destroy(self._manager)
            self._manager = None
        self._flush(self._display)
