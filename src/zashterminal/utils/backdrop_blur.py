"""Live compositor blur, never a screenshot painted inside the window.

The optional Zashterminal KWin effect supports independent strength per window.
Standard Wayland/X11 hints only select a surface/region; their strength belongs
to the desktop. Neither path captures or paints a static background.
"""

from __future__ import annotations

import os
import ctypes as C
from ctypes.util import find_library


class _Interface(C.Structure):
    pass


class _Message(C.Structure):
    _fields_ = [
        ("name", C.c_char_p), ("signature", C.c_char_p),
        ("types", C.POINTER(C.POINTER(_Interface))),
    ]


_Interface._fields_ = [
    ("name", C.c_char_p), ("version", C.c_int),
    ("method_count", C.c_int), ("methods", C.POINTER(_Message)),
    ("event_count", C.c_int), ("events", C.POINTER(_Message)),
]


class _Argument(C.Union):
    _fields_ = [("u", C.c_uint32), ("s", C.c_char_p), ("o", C.c_void_p)]


def _function(library, name, result, *arguments):
    function = getattr(library, name)
    function.restype = result
    function.argtypes = list(arguments)
    return function


def _gpointer(obj):
    """Use PyGObject's native pointer capsule, not a Python object address."""
    pointer = C.pythonapi.PyCapsule_GetPointer
    pointer.restype = C.c_void_p
    pointer.argtypes = [C.py_object, C.c_char_p]
    return pointer(obj.__gpointer__, None)


def _library(name):
    path = find_library(name)
    if path is None:
        raise OSError(f"Native library not found: {name}")
    return C.CDLL(path)


class _WaylandClient:
    """Bind globals on GTK's own Wayland connection.

    Discovery uses a private queue: roundtrips must not dispatch GTK events
    reentrantly. GTK owns buffer rendering and the normal event loop.
    """

    def __init__(self, display, gtk):
        self.lib = _library("wayland-client")
        self._display = _function(
            gtk, "gdk_wayland_display_get_wl_display", C.c_void_p, C.c_void_p,
        )(_gpointer(display))
        self._surface_pointer = _function(
            gtk, "gdk_wayland_surface_get_wl_surface", C.c_void_p, C.c_void_p,
        )
        self._marshal = _function(
            self.lib, "wl_proxy_marshal_array_flags", C.c_void_p,
            C.c_void_p, C.c_uint32, C.POINTER(_Interface), C.c_uint32,
            C.c_uint32, C.POINTER(_Argument),
        )
        self._destroy = _function(self.lib, "wl_proxy_destroy", None, C.c_void_p)
        self._flush = _function(self.lib, "wl_display_flush", C.c_int, C.c_void_p)
        self._set_queue = _function(self.lib, "wl_proxy_set_queue", None, C.c_void_p, C.c_void_p)
        self._roundtrip = _function(
            self.lib, "wl_display_roundtrip_queue", C.c_int, C.c_void_p, C.c_void_p,
        )
        self._add_listener = _function(
            self.lib, "wl_proxy_add_listener", C.c_int,
            C.c_void_p, C.POINTER(C.c_void_p), C.c_void_p,
        )
        self._registry_interface = _Interface.in_dll(self.lib, "wl_registry_interface")

    def _request(self, proxy, opcode, *arguments, interface=None, destroy=False):
        values = (_Argument * len(arguments))(*arguments) if arguments else None
        return self._marshal(
            proxy, opcode, C.pointer(interface) if interface is not None else None,
            1, int(destroy), values,
        )

    def _discover(self, registry, queue):
        global_callback = C.CFUNCTYPE(None, C.c_void_p, C.c_void_p, C.c_uint32, C.c_char_p, C.c_uint32)
        remove_callback = C.CFUNCTYPE(None, C.c_void_p, C.c_void_p, C.c_uint32)
        globals_found = {}

        @global_callback
        def on_global(_data, _registry, name, interface, _version):
            globals_found[interface] = name

        @remove_callback
        def on_remove(_data, _registry, name):
            for interface in list(globals_found):
                if globals_found[interface] == name:
                    del globals_found[interface]

        listeners = (C.c_void_p * 2)(C.cast(on_global, C.c_void_p), C.cast(on_remove, C.c_void_p))
        self._registry_callbacks = (on_global, on_remove, listeners)
        if self._add_listener(registry, listeners, None) != 0 or self._roundtrip(self._display, queue) < 0:
            raise RuntimeError("Wayland global discovery failed")
        return globals_found

    def _bind(self, registry, name, interface):
        return self._request(
            registry, 0, _Argument(u=name), _Argument(s=interface.name),
            _Argument(u=1), _Argument(o=None), interface=interface,
        )

    def _bind_globals(self, bind):
        """Return ``bind(registry, queue, globals_found)``; close() on failure."""
        create_queue = _function(self.lib, "wl_display_create_queue", C.c_void_p, C.c_void_p)
        destroy_queue = _function(self.lib, "wl_event_queue_destroy", None, C.c_void_p)
        queue = create_queue(self._display)
        if not queue:
            raise RuntimeError("Could not create a Wayland discovery queue")
        registry = None
        self._registry_callbacks = None
        try:
            registry = self._request(self._display, 1, _Argument(o=None), interface=self._registry_interface)
            if not registry:
                raise RuntimeError("Could not get the Wayland registry")
            self._set_queue(registry, queue)
            globals_found = self._discover(registry, queue)
            return bind(registry, queue, globals_found)
        except Exception:
            self.close()
            raise
        finally:
            if registry:
                self._destroy(registry)
            self._registry_callbacks = None
            destroy_queue(queue)


class _WaylandBlur(_WaylandClient):
    """Bind the standard background-effect protocol, or KDE's older blur v1.

    Effect changes on mapped surfaces need their own metadata-only surface
    commit: GTK may skip a frame if none of its pixels changed.
    Protocols: https://wayland.app/protocols/ext-background-effect-v1
    and https://wayland.app/protocols/kde-blur
    """

    def __init__(self, display, gtk):
        super().__init__(display, gtk)
        self._surface = None
        self._surface_object = None
        self._blur = None
        self._manager = None
        self._compositor = None
        self._modern = False
        self._capabilities = 0
        self._manager = self._bind_globals(self._bind_effect_manager)

    def _define_protocol(self):
        # All interface/type arrays must outlive the proxies referring to them.
        self._surface_interface = _Interface.in_dll(self.lib, "wl_surface_interface")
        self._region_interface = _Interface.in_dll(self.lib, "wl_region_interface")
        name = b"ext_background_effect_surface_v1" if self._modern else b"org_kde_kwin_blur"
        self._blur_interface = _Interface(name, 1, 0, None, 0, None)
        self._create_types = (C.POINTER(_Interface) * 2)(
            C.pointer(self._blur_interface), C.pointer(self._surface_interface),
        )
        self._surface_types = (C.POINTER(_Interface) * 1)(C.pointer(self._surface_interface))
        self._region_types = (C.POINTER(_Interface) * 1)(C.pointer(self._region_interface))
        if self._modern:
            self._blur_methods = (_Message * 2)(
                _Message(b"destroy", b"", None),
                _Message(b"set_blur_region", b"?o", self._region_types),
            )
            self._manager_methods = (_Message * 2)(
                _Message(b"destroy", b"", None),
                _Message(b"get_background_effect", b"no", self._create_types),
            )
            self._manager_events = (_Message * 1)(_Message(b"capabilities", b"u", None))
            manager_name = b"ext_background_effect_manager_v1"
        else:
            self._blur_methods = (_Message * 3)(
                _Message(b"commit", b"", None),
                _Message(b"set_region", b"?o", self._region_types),
                _Message(b"release", b"", None),
            )
            self._manager_methods = (_Message * 2)(
                _Message(b"create", b"no", self._create_types),
                _Message(b"unset", b"o", self._surface_types),
            )
            self._manager_events = None
            manager_name = b"org_kde_kwin_blur_manager"
        self._blur_interface.method_count = len(self._blur_methods)
        self._blur_interface.methods = self._blur_methods
        self._manager_interface = _Interface(
            manager_name, 1, 2, self._manager_methods,
            int(self._modern), self._manager_events,
        )

    def _watch_capabilities(self, manager, queue):
        callback = C.CFUNCTYPE(None, C.c_void_p, C.c_void_p, C.c_uint32)

        @callback
        def on_capabilities(_data, _manager, flags):
            self._capabilities = flags

        self._capability_callback = on_capabilities
        self._capability_listener = (C.c_void_p * 1)(C.cast(on_capabilities, C.c_void_p))
        if (
            self._add_listener(manager, self._capability_listener, None) != 0
            or self._roundtrip(self._display, queue) < 0
        ):
            raise RuntimeError("Could not read compositor blur capabilities")

    def _bind_effect_manager(self, registry, queue, globals_found):
        self._modern = b"ext_background_effect_manager_v1" in globals_found
        self._define_protocol()
        name = globals_found.get(self._manager_interface.name)
        if name is None:
            return None
        self._manager = self._bind(registry, name, self._manager_interface)
        if not self._manager:
            raise RuntimeError("Could not bind the compositor blur manager")
        if self._modern:
            compositor_name = globals_found.get(b"wl_compositor")
            if compositor_name is None:
                raise RuntimeError("The Wayland compositor is unavailable")
            self._compositor_interface = _Interface.in_dll(self.lib, "wl_compositor_interface")
            self._compositor = self._bind(
                registry, compositor_name, self._compositor_interface,
            )
            if not self._compositor:
                raise RuntimeError("Could not bind the Wayland compositor")
            self._watch_capabilities(self._manager, queue)
            self._set_queue(self._compositor, None)
        self._set_queue(self._manager, None)
        return self._manager

    @property
    def supported(self):
        return self._manager is not None and (not self._modern or bool(self._capabilities & 1))

    def set_enabled(self, surface, enabled):
        pointer = self._surface_pointer(_gpointer(surface))
        if self._surface == pointer and bool(self._blur) == enabled:
            return
        self._disable()
        if enabled and pointer and self.supported:
            self._surface = pointer
            self._surface_object = surface
            self._blur = self._request(
                self._manager, int(self._modern), _Argument(o=None), _Argument(o=pointer),
                interface=self._blur_interface,
            )
            if not self._blur:
                raise RuntimeError("Could not create the compositor blur object")
            self._set_region()
            if self._modern and surface.get_mapped():
                # GTK can skip unchanged pixels, so commit effect-only state.
                self._request(pointer, 6)  # wl_surface.commit; no new buffer.
        self._flush(self._display)

    def _set_region(self):
        if self._modern:
            region = self._request(self._compositor, 1, _Argument(o=None), interface=self._region_interface)
            if not region:
                raise RuntimeError("Could not create the compositor blur region")
            try:
                # Surface-local coordinates are scale independent and compositor
                # clipped. A large region follows resize without per-frame IPC.
                self._request(region, 1, _Argument(u=0), _Argument(u=0),
                              _Argument(u=2147483647), _Argument(u=2147483647))
                self._request(self._blur, 1, _Argument(o=region))
            finally:
                self._request(region, 0, destroy=True)
        else:
            self._request(self._blur, 1, _Argument(o=None))
            self._request(self._blur, 0)

    def _disable(self):
        if self._blur:
            if not self._modern:
                self._request(self._manager, 1, _Argument(o=self._surface))
            self._request(self._blur, 0 if self._modern else 2, destroy=True)
            self._blur = None
            if self._modern and self._surface_object.get_mapped():
                # Removal belongs to the old surface, not the next one passed
                # to set_enabled. Do not leave blur waiting for its next frame.
                self._request(self._surface, 6)
        self._surface = None
        self._surface_object = None

    def close(self):
        self._disable()
        if self._manager:
            if self._modern:
                self._request(self._manager, 0, destroy=True)
            else:
                self._destroy(self._manager)
            self._manager = None
        if self._compositor:
            self._destroy(self._compositor)
            self._compositor = None
        self._flush(self._display)
        self._capability_listener = None
        self._capability_callback = None

def _gtk_owns_background_effect():
    """Return True when GTK itself binds ext-background-effect (GTK >= 4.23.3).

    Since GTK 4.23.3 (stable in 4.24), GDK creates one
    ``ext_background_effect_surface_v1`` for *every* wl_surface as soon as the
    compositor advertises the manager, and derives its blur region from the
    widget tree (CSS ``backdrop-filter: blur()``). The protocol allows only one
    such object per surface, so creating a second one ourselves raises the
    fatal ``background_effect_exists`` error and the compositor drops the
    whole GTK connection.
    """
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk

        return Gtk.check_version(4, 23, 3) is None
    except (ImportError, ValueError, AttributeError):
        return False


class _GtkBackgroundEffectBlur:
    """Request standard Wayland blur through GTK's own background-effect object.

    GTK reports a blur region for the parts of the surface that sit behind a
    ``backdrop-filter: blur()`` of at least 10px (GSK's threshold). Putting
    that filter on the toplevel window marks the whole window, never the CSD
    shadow, and GTK commits it with its next frame. The compositor decides the
    actual strength, exactly as with the hand-written protocol path.

    ``probe`` is a ``_WaylandBlur`` used only to read the compositor's
    capability; it never creates a per-surface object.
    """

    CSS_CLASS = "zashterminal-background-blur"
    _CSS = (
        f".{CSS_CLASS} {{ backdrop-filter: blur(20px); }}"
    )

    def __init__(self, probe):
        self._probe = probe
        self._widget = None
        self._provider = None

    @property
    def supported(self):
        return self._probe.supported

    def _ensure_provider(self, widget):
        if self._provider is not None:
            return
        from gi.repository import Gtk

        self._provider = Gtk.CssProvider()
        self._provider.load_from_string(self._CSS)
        Gtk.StyleContext.add_provider_for_display(
            widget.get_display(), self._provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1,
        )

    def set_enabled(self, surface, enabled):
        from gi.repository import Gtk

        widget = Gtk.Native.get_for_surface(surface)
        if self._widget is not None and self._widget is not widget:
            self._widget.remove_css_class(self.CSS_CLASS)
            self._widget = None
        if widget is None:
            return
        if enabled and self.supported:
            self._ensure_provider(widget)
            widget.add_css_class(self.CSS_CLASS)
            self._widget = widget
        else:
            widget.remove_css_class(self.CSS_CLASS)
            self._widget = None

    def close(self):
        if self._widget is not None:
            self._widget.remove_css_class(self.CSS_CLASS)
            self._widget = None
        if self._provider is not None:
            from gi.repository import Gdk, Gtk

            display = Gdk.Display.get_default()
            if display is not None:
                Gtk.StyleContext.remove_provider_for_display(display, self._provider)
            self._provider = None
        self._probe.close()


class _X11Blur:
    """Use the compositor's _KDE_NET_WM_BLUR_BEHIND_REGION hint on X11."""

    def __init__(self, display, gtk):
        self.lib = _library("X11")
        self._display = _function(
            gtk, "gdk_x11_display_get_xdisplay", C.c_void_p, C.c_void_p,
        )(_gpointer(display))
        self._surface_pointer = _function(
            gtk, "gdk_x11_surface_get_xid", C.c_ulong, C.c_void_p,
        )
        self._atom = _function(self.lib, "XInternAtom", C.c_ulong, C.c_void_p, C.c_char_p, C.c_int)(
            self._display, b"_KDE_NET_WM_BLUR_BEHIND_REGION", 0,
        )
        self._change = _function(
            self.lib, "XChangeProperty", C.c_int, C.c_void_p, C.c_ulong, C.c_ulong,
            C.c_ulong, C.c_int, C.c_int, C.c_void_p, C.c_int,
        )
        self._delete = _function(self.lib, "XDeleteProperty", C.c_int, C.c_void_p, C.c_ulong, C.c_ulong)
        self._flush = _function(self.lib, "XFlush", C.c_int, C.c_void_p)
        self._surface = None
        import gi

        gi.require_version("GdkX11", "4.0")
        from gi.repository import GdkX11

        self.supported = GdkX11.X11Display.get_screen(display).supports_net_wm_hint(
            "_KDE_NET_WM_BLUR_BEHIND_REGION"
        )

    def set_enabled(self, surface, enabled):
        xid = self._surface_pointer(_gpointer(surface))
        self.close()
        if enabled:
            # An empty CARDINAL (6) property requests blur of the entire window.
            self._change(self._display, xid, self._atom, 6, 32, 0, None, 0)
            self._surface = xid
        self._flush(self._display)

    def close(self):
        if self._surface:
            self._delete(self._display, self._surface, self._atom)
            self._surface = None
            self._flush(self._display)


class _KWinEffectBlur:
    """Address one Wayland surface in Zashterminal's optional GPU effect."""

    _PATH = "/org/leoberbert/ZashterminalBlur"
    _INTERFACE = "org.leoberbert.ZashterminalBlur"

    @classmethod
    def connect(cls, gtk):
        from gi.repository import Gio, GLib

        try:
            connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            loaded = connection.call_sync(
                "org.kde.KWin", "/Effects", "org.kde.kwin.Effects", "isEffectLoaded",
                GLib.Variant("(s)", ("zashterminalblur",)), None,
                Gio.DBusCallFlags.NONE, 1000, None,
            )
            if not loaded.unpack()[0]:
                loaded = connection.call_sync(
                    "org.kde.KWin", "/Effects", "org.kde.kwin.Effects", "loadEffect",
                    GLib.Variant("(s)", ("zashterminalblur",)), None,
                    Gio.DBusCallFlags.NONE, 1000, None,
                )
                if not loaded.unpack()[0]:
                    return None
            version = connection.call_sync(
                "org.kde.KWin", cls._PATH, cls._INTERFACE, "protocolVersion",
                None, None, Gio.DBusCallFlags.NONE, 1000, None,
            )
            if version.unpack()[0] != 1:
                return None
            return cls(connection, gtk)
        except GLib.Error:
            return None  # Optional extension is not installed on this desktop.

    def __init__(self, connection, gtk):
        self._connection = connection
        self._surface_pointer = _function(
            gtk, "gdk_wayland_surface_get_wl_surface", C.c_void_p, C.c_void_p,
        )
        self._wayland = _library("wayland-client")
        self._proxy_id = _function(self._wayland, "wl_proxy_get_id", C.c_uint32, C.c_void_p)
        self._surface_id = None
        self._strength = 0

    @property
    def supported(self):
        return True

    def _send(self, surface_id, strength):
        import os
        from gi.repository import Gio, GLib

        reply = self._connection.call_sync(
            "org.kde.KWin", self._PATH, self._INTERFACE, "setWindowBlur",
            GLib.Variant("(utu)", (os.getpid(), surface_id, strength)),
            None, Gio.DBusCallFlags.NONE, 1000, None,
        )
        if not reply.unpack()[0]:
            raise RuntimeError("The KWin effect rejected this window's blur request")

    def apply(self, surface, strength):
        surface_id = self._proxy_id(self._surface_pointer(_gpointer(surface)))
        if self._surface_id == surface_id and self._strength == strength:
            return
        if self._surface_id is not None and self._surface_id != surface_id:
            self._send(self._surface_id, 0)
        self._send(surface_id, strength)
        self._surface_id = surface_id
        self._strength = strength

    def close(self):
        from gi.repository import GLib

        if self._surface_id is not None:
            try:
                self._send(self._surface_id, 0)
            except GLib.Error:
                pass  # KWin may already be exiting during GTK teardown.
            self._surface_id = None
            self._strength = 0


class BackdropBlur:
    """Own the native blur request for one GTK window's surface."""

    def __init__(self, display, logger=None):
        self._backend = None
        try:
            gtk = _library("gtk-4")
            name = display.__gtype__.name
            if name == "GdkWaylandDisplay":
                if "KDE" in os.environ.get("XDG_CURRENT_DESKTOP", "").split(":"):
                    self._backend = _KWinEffectBlur.connect(gtk)
                if self._backend is None:
                    wayland = _WaylandBlur(display, gtk)
                    if wayland._modern and _gtk_owns_background_effect():
                        # GTK already holds this surface's effect object; a
                        # second one is a fatal protocol error.
                        self._backend = _GtkBackgroundEffectBlur(wayland)
                    else:
                        self._backend = wayland
            elif name == "GdkX11Display":
                self._backend = _X11Blur(display, gtk)
        except (OSError, AttributeError, RuntimeError) as error:
            if logger:
                logger.warning(f"Compositor blur unavailable: {error}")
        self._display = display  # GDK owns the native connection.

    @property
    def supported(self):
        return self._backend is not None and self._backend.supported

    @property
    def independent_strength(self):
        return isinstance(self._backend, _KWinEffectBlur)

    def apply(self, surface, enabled, strength):
        if surface is not None and self._backend is not None:
            if self.independent_strength:
                self._backend.apply(surface, int(strength) if enabled else 0)
            else:
                self._backend.set_enabled(surface, bool(enabled) and self.supported)

    def close(self):
        if self._backend is not None:
            self._backend.close()
            self._backend = None
