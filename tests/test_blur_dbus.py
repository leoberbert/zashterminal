"""Exercise the optional KWin effect over a private, real D-Bus transport.

Requires PyGObject/Gio, dbus-daemon and libwayland-client; no display, KWin,
installed native effect or session-bus environment changes are needed.
"""

from __future__ import annotations

import ctypes as C
from ctypes.util import find_library
import os
from pathlib import Path
import select
import shutil
import socket
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from zashterminal.utils import backdrop_blur as blur

try:
    from gi.repository import Gio, GLib
except ImportError:
    Gio = GLib = None


_EFFECTS_XML = """
<node><interface name="org.kde.kwin.Effects">
  <method name="isEffectLoaded">
    <arg type="s" direction="in"/><arg type="b" direction="out"/>
  </method>
  <method name="loadEffect">
    <arg type="s" direction="in"/><arg type="b" direction="out"/>
  </method>
</interface></node>
"""
_BLUR_XML = """
<node><interface name="org.leoberbert.ZashterminalBlur">
  <method name="protocolVersion"><arg type="u" direction="out"/></method>
  <method name="setWindowBlur">
    <arg type="u" direction="in"/><arg type="t" direction="in"/>
    <arg type="u" direction="in"/><arg type="b" direction="out"/>
  </method>
</interface></node>
"""


class _PrivateKWin:
    """A stateful service dispatched independently of synchronous clients."""

    def __init__(self):
        self.lock = threading.Lock()
        self.loaded = True
        self.allow_load = True
        self.version = 1
        self.reject = False
        self.error_method = None
        self.surfaces = set()
        self.strengths = {}
        self.connection = None
        self.client = None
        self.context = None
        self.loop = None
        self.ready = threading.Event()
        self.startup_error = None
        self.thread = None
        self.directory = tempfile.TemporaryDirectory(prefix="zashterminal-test-bus-")
        config = Path(self.directory.name) / "bus.conf"
        # Deliberately omit all service directories and system/session includes:
        # an absent org.kde.KWin must never activate an installed desktop service.
        config.write_text(
            "<busconfig><type>session</type>"
            f"<listen>unix:tmpdir={self.directory.name}</listen>"
            "<auth>EXTERNAL</auth><policy context='default'>"
            "<allow user='*'/><allow own='*'/>"
            "<allow send_destination='*'/><allow receive_sender='*'/>"
            "</policy></busconfig>",
            encoding="utf-8",
        )
        self.daemon = subprocess.Popen(
            [shutil.which("dbus-daemon"), f"--config-file={config}", "--nofork", "--print-address=1"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            if not select.select([self.daemon.stdout], [], [], 5)[0]:
                raise RuntimeError("Private dbus-daemon did not publish an address")
            self.address = self.daemon.stdout.readline().strip()
            if not self.address:
                raise RuntimeError("Private dbus-daemon exited before publishing an address")
            self.thread = threading.Thread(target=self._serve, daemon=True)
            self.thread.start()
            if not self.ready.wait(5):
                raise RuntimeError("Private KWin service did not start")
            if self.startup_error is not None:
                raise self.startup_error
            self.client = self._new_connection()
        except BaseException:
            self.close()
            raise

    def _new_connection(self):
        connection = Gio.DBusConnection.new_for_address_sync(
            self.address,
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
            | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None, None,
        )
        connection.set_exit_on_close(False)
        return connection

    def _serve(self):
        self.context = GLib.MainContext.new()
        self.context.push_thread_default()
        self.registrations = {}
        try:
            self.connection = self._new_connection()
            self.loop = GLib.MainLoop.new(self.context, False)
            for path, xml in (("/Effects", _EFFECTS_XML), (blur._KWinEffectBlur._PATH, _BLUR_XML)):
                interface = Gio.DBusNodeInfo.new_for_xml(xml).interfaces[0]
                self.registrations[path] = self.connection.register_object(
                    path, interface, self._method_call, None, None,
                )
            self.connection.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                "RequestName", GLib.Variant("(su)", ("org.kde.KWin", 0)),
                GLib.VariantType.new("(u)"), Gio.DBusCallFlags.NONE, 1000, None,
            )
            self.ready.set()
            self.loop.run()
        except BaseException as error:
            self.startup_error = error
            self.ready.set()
        finally:
            if self.connection is not None:
                for registration in self.registrations.values():
                    self.connection.unregister_object(registration)
                if not self.connection.is_closed():
                    self.connection.close_sync(None)
            with self.lock:
                self.strengths.clear()
            self.context.pop_thread_default()

    def _method_call(self, connection, sender, path, interface, method, parameters, invocation):
        with self.lock:
            if self.error_method == method:
                invocation.return_dbus_error("org.kde.KWin.Error.Failed", "Effect unavailable")
                return
            if method == "isEffectLoaded":
                result = self.loaded and parameters.unpack() == ("zashterminalblur",)
                reply = GLib.Variant("(b)", (result,))
            elif method == "loadEffect":
                self.loaded = self.allow_load and parameters.unpack() == ("zashterminalblur",)
                reply = GLib.Variant("(b)", (self.loaded,))
            elif method == "protocolVersion":
                reply = GLib.Variant("(u)", (self.version,))
            else:
                pid, surface, strength = parameters.unpack()
                accepted = (
                    self.loaded and not self.reject and pid == os.getpid()
                    and surface in self.surfaces and 0 <= strength <= 15
                )
                if accepted:
                    if strength:
                        self.strengths[surface] = strength
                    else:
                        self.strengths.pop(surface, None)
                reply = GLib.Variant("(b)", (accepted,))
            invocation.return_value(reply)

    def configure(self, **values):
        with self.lock:
            for name, value in values.items():
                setattr(self, name, value)

    def snapshot(self):
        with self.lock:
            return dict(self.strengths)

    def remove_extension(self):
        removed = threading.Event()

        def unregister(*unused):
            registration = self.registrations.pop(blur._KWinEffectBlur._PATH)
            self.connection.unregister_object(registration)
            with self.lock:
                self.strengths.clear()
            removed.set()
            return GLib.SOURCE_REMOVE

        source = GLib.idle_source_new()
        source.set_callback(unregister)
        source.attach(self.context)
        if not removed.wait(5):
            raise RuntimeError("Private KWin extension failed to unregister")

    def stop_service(self):
        if self.thread is not None and self.thread.is_alive():
            # Attach to the server context: a client cannot race loop.run startup.
            source = GLib.idle_source_new()
            source.set_callback(lambda *unused: self.loop.quit() or GLib.SOURCE_REMOVE)
            source.attach(self.context)
            self.thread.join(5)
            if self.thread.is_alive():
                raise RuntimeError("Private KWin dispatch thread failed to stop")

    def close(self):
        try:
            if self.client is not None and not self.client.is_closed():
                self.client.close_sync(None)
            self.stop_service()
        finally:
            if self.daemon.poll() is None:
                self.daemon.terminate()
                try:
                    self.daemon.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.daemon.kill()
                    self.daemon.wait(timeout=5)
            self.daemon.stdout.close()
            self.daemon.stderr.close()
            self.directory.cleanup()


class _NativeSurfaceIds:
    """Real Wayland proxy allocation, with only the GTK pointer bridge replaced.

    A registry proxy is sufficient here: KWin's client only reads wl_proxy's
    object ID, so no compositor, GDK display or Wayland roundtrip is necessary.
    """

    def __init__(self):
        self.lib = C.CDLL(find_library("wayland-client"))
        self.connect = blur._function(self.lib, "wl_display_connect_to_fd", C.c_void_p, C.c_int)
        self.disconnect = blur._function(self.lib, "wl_display_disconnect", None, C.c_void_p)
        self.destroy = blur._function(self.lib, "wl_proxy_destroy", None, C.c_void_p)
        self.proxy_id = blur._function(self.lib, "wl_proxy_get_id", C.c_uint32, C.c_void_p)
        self.marshal = blur._function(
            self.lib, "wl_proxy_marshal_array_flags", C.c_void_p,
            C.c_void_p, C.c_uint32, C.POINTER(blur._Interface), C.c_uint32,
            C.c_uint32, C.POINTER(blur._Argument),
        )
        self.interface = blur._Interface.in_dll(self.lib, "wl_registry_interface")
        client, self.peer = socket.socketpair()
        self.display = self.connect(client.detach())
        self.proxies = []
        if not self.display:
            self.peer.close()
            raise RuntimeError("Unable to allocate a private Wayland client")
        self.getter = C.CFUNCTYPE(C.c_void_p, C.c_void_p)(lambda pointer: pointer)
        self.gtk = SimpleNamespace(gdk_wayland_surface_get_wl_surface=self.getter)

    def surface(self):
        pointer = self.marshal(
            self.display, 1, C.byref(self.interface), 1, 0, (blur._Argument * 1)(),
        )
        if not pointer:
            raise RuntimeError("Unable to allocate a Wayland registry proxy")
        self.proxies.append(pointer)
        capsule = C.pythonapi.PyCapsule_New
        capsule.restype = C.py_object
        capsule.argtypes = [C.c_void_p, C.c_char_p, C.c_void_p]
        return SimpleNamespace(
            __gpointer__=capsule(pointer, None, None), surface_id=self.proxy_id(pointer),
        )

    def close(self):
        for proxy in self.proxies:
            self.destroy(proxy)
        self.disconnect(self.display)
        self.peer.close()


@unittest.skipUnless(
    Gio is not None and shutil.which("dbus-daemon") and find_library("wayland-client"),
    "Requires PyGObject/Gio, dbus-daemon and libwayland-client",
)
class KWinEffectDBusTests(unittest.TestCase):
    def setUp(self):
        self.server = _PrivateKWin()
        self.addCleanup(self.server.close)
        self.native = _NativeSurfaceIds()
        self.addCleanup(self.native.close)
        self.first, self.second, self.third = (self.native.surface() for _ in range(3))
        self.server.configure(surfaces={
            self.first.surface_id, self.second.surface_id, self.third.surface_id,
        })
        # Redirect only session-bus acquisition. Both ends still use independent
        # authenticated Gio connections and every RPC crosses the private socket.
        self.bus_patch = patch.object(Gio, "bus_get_sync", return_value=self.server.client)
        self.bus_patch.start()
        self.addCleanup(self.bus_patch.stop)

    def connect(self):
        backend = blur._KWinEffectBlur.connect(self.native.gtk)
        self.assertIsNotNone(backend)
        self.assertTrue(backend.supported)
        return backend

    def test_independent_strengths_updates_disable_and_close(self):
        first, second = self.connect(), self.connect()
        first.apply(self.first, 4)
        second.apply(self.second, 12)
        self.assertEqual(self.server.snapshot(), {
            self.first.surface_id: 4, self.second.surface_id: 12,
        })
        first.apply(self.first, 8)
        self.assertEqual(self.server.snapshot(), {
            self.first.surface_id: 8, self.second.surface_id: 12,
        })
        first.apply(self.first, 0)
        self.assertEqual(self.server.snapshot(), {self.second.surface_id: 12})
        first.close()
        first.close()
        self.assertEqual(self.server.snapshot(), {self.second.surface_id: 12})
        second.close()
        self.assertEqual(self.server.snapshot(), {})

    def test_identical_apply_remains_usable_when_effect_rejects_new_changes(self):
        first, second = self.connect(), self.connect()
        first.apply(self.first, 5)
        second.apply(self.second, 10)
        self.server.configure(reject=True)
        first.apply(self.first, 5)
        self.assertEqual(self.server.snapshot(), {
            self.first.surface_id: 5, self.second.surface_id: 10,
        })
        with self.assertRaises(RuntimeError):
            first.apply(self.first, 6)
        self.assertEqual(self.server.snapshot(), {
            self.first.surface_id: 5, self.second.surface_id: 10,
        })
        self.server.configure(reject=False)
        first.apply(self.first, 6)
        self.assertEqual(self.server.snapshot(), {
            self.first.surface_id: 6, self.second.surface_id: 10,
        })

    def test_switch_surface_cleans_only_previous_surface(self):
        first, second = self.connect(), self.connect()
        first.apply(self.first, 3)
        second.apply(self.second, 11)
        first.apply(self.third, 7)
        self.assertEqual(self.server.snapshot(), {
            self.second.surface_id: 11, self.third.surface_id: 7,
        })
        first.close()
        self.assertEqual(self.server.snapshot(), {self.second.surface_id: 11})
        second.close()
        self.assertEqual(self.server.snapshot(), {})

    def test_discovery_loads_unloaded_extension(self):
        self.server.configure(loaded=False)
        backend = self.connect()
        self.assertTrue(self.server.loaded)
        backend.apply(self.first, 15)
        self.assertEqual(self.server.snapshot(), {self.first.surface_id: 15})
        backend.close()
        self.assertEqual(self.server.snapshot(), {})

    def test_failed_load_does_not_activate_extension(self):
        self.server.configure(loaded=False, allow_load=False)
        self.assertIsNone(blur._KWinEffectBlur.connect(self.native.gtk))
        self.assertFalse(self.server.loaded)
        self.assertEqual(self.server.snapshot(), {})

    def test_absent_extension_does_not_activate_blur(self):
        self.server.remove_extension()
        self.assertIsNone(blur._KWinEffectBlur.connect(self.native.gtk))
        self.assertEqual(self.server.snapshot(), {})

    def test_incompatible_protocol_does_not_modify_surfaces(self):
        owner = self.connect()
        owner.apply(self.first, 9)
        self.server.configure(version=2)
        self.assertIsNone(blur._KWinEffectBlur.connect(self.native.gtk))
        self.assertEqual(self.server.snapshot(), {self.first.surface_id: 9})
        owner.close()
        self.assertEqual(self.server.snapshot(), {})

    def test_discovery_errors_are_optional_extension_unavailability(self):
        for method in ("isEffectLoaded", "loadEffect", "protocolVersion"):
            with self.subTest(method=method):
                self.server.configure(loaded=False, error_method=method)
                self.assertIsNone(blur._KWinEffectBlur.connect(self.native.gtk))
                self.assertEqual(self.server.snapshot(), {})
        self.server.configure(error_method=None)
        backend = self.connect()
        backend.apply(self.first, 1)
        self.assertEqual(self.server.snapshot(), {self.first.surface_id: 1})

    def test_rejected_initial_apply_can_be_retried(self):
        backend = self.connect()
        self.server.configure(reject=True)
        with self.assertRaises(RuntimeError):
            backend.apply(self.first, 5)
        self.assertEqual(self.server.snapshot(), {})
        self.server.configure(reject=False)
        backend.apply(self.first, 5)
        self.assertEqual(self.server.snapshot(), {self.first.surface_id: 5})
        backend.close()
        self.assertEqual(self.server.snapshot(), {})

    def test_switch_rejection_preserves_other_surfaces_and_retry_recovers(self):
        first, second = self.connect(), self.connect()
        first.apply(self.first, 3)
        second.apply(self.second, 11)
        self.server.configure(reject=True)
        with self.assertRaises(RuntimeError):
            first.apply(self.third, 7)
        self.assertEqual(self.server.snapshot(), {
            self.first.surface_id: 3, self.second.surface_id: 11,
        })
        # Clearing the old surface now succeeds, but the compositor has not
        # observed the replacement surface yet.
        self.server.configure(
            reject=False, surfaces={self.first.surface_id, self.second.surface_id},
        )
        with self.assertRaises(RuntimeError):
            first.apply(self.third, 7)
        self.assertEqual(self.server.snapshot(), {self.second.surface_id: 11})
        self.server.configure(surfaces={
            self.first.surface_id, self.second.surface_id, self.third.surface_id,
        })
        first.apply(self.third, 7)
        self.assertEqual(self.server.snapshot(), {
            self.third.surface_id: 7, self.second.surface_id: 11,
        })
        first.close()
        self.assertEqual(self.server.snapshot(), {self.second.surface_id: 11})
        second.close()
        self.assertEqual(self.server.snapshot(), {})

    def test_apply_bus_error_preserves_previous_strength_and_allows_retry(self):
        backend = self.connect()
        backend.apply(self.first, 2)
        self.server.configure(error_method="setWindowBlur")
        with self.assertRaises(GLib.Error):
            backend.apply(self.first, 13)
        self.assertEqual(self.server.snapshot(), {self.first.surface_id: 2})
        self.server.configure(error_method=None)
        backend.apply(self.first, 13)
        self.assertEqual(self.server.snapshot(), {self.first.surface_id: 13})
        backend.close()
        self.assertEqual(self.server.snapshot(), {})

    def test_rejected_close_retains_request_until_cleanup_succeeds(self):
        backend = self.connect()
        backend.apply(self.first, 4)
        self.server.configure(reject=True)
        with self.assertRaises(RuntimeError):
            backend.close()
        self.assertEqual(self.server.snapshot(), {self.first.surface_id: 4})
        self.server.configure(reject=False)
        backend.close()
        self.assertEqual(self.server.snapshot(), {})

    def test_close_bus_error_forgets_stale_request_and_next_apply_recovers(self):
        backend = self.connect()
        backend.apply(self.first, 7)
        self.server.configure(error_method="setWindowBlur")
        backend.close()
        self.assertEqual(self.server.snapshot(), {self.first.surface_id: 7})
        # Simulate effect restart: old compositor resources no longer exist.
        self.server.configure(error_method=None, strengths={})
        backend.apply(self.first, 7)
        self.assertEqual(self.server.snapshot(), {self.first.surface_id: 7})
        backend.close()
        self.assertEqual(self.server.snapshot(), {})

    def test_service_disconnect_cleans_remote_resources_and_close_is_safe(self):
        backend = self.connect()
        backend.apply(self.first, 6)
        self.assertEqual(self.server.snapshot(), {self.first.surface_id: 6})
        self.server.stop_service()
        self.assertEqual(self.server.snapshot(), {})
        with self.assertRaises(GLib.Error):
            backend.apply(self.first, 8)
        backend.close()
        self.assertIsNone(blur._KWinEffectBlur.connect(self.native.gtk))
        self.assertEqual(self.server.snapshot(), {})


if __name__ == "__main__":
    unittest.main()
