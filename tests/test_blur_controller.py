"""Public blur capabilities and surface isolation over private native protocols."""

from contextlib import closing
import logging
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from zashterminal.utils import backdrop_blur as blur
from blur_wayland_harness import WaylandCompositor, available
from test_blur_dbus import _PrivateKWin, Gio


@unittest.skipUnless(available() and Gio is not None and shutil.which("dbus-daemon"),
                     "Wayland libraries, Gio and dbus-daemon required")
class BlurControllerTests(unittest.TestCase):
    def bridge(self, compositor, server):
        compositor.display_object.__gtype__ = SimpleNamespace(name="GdkWaylandDisplay")
        original = blur._library
        native_library = patch.object(
            blur, "_library", side_effect=lambda name: compositor.gtk if name == "gtk-4" else original(name),
        )
        private_bus = patch.object(Gio, "bus_get_sync", return_value=server.client)
        return native_library, private_bus

    def test_custom_strength_is_independent_and_closed_controller_cannot_reactivate_it(self):
        with closing(_PrivateKWin()) as server, WaylandCompositor() as compositor:
            a, b = compositor.surface(), compositor.surface()
            aid, bid = compositor.proxy_id(a.pointer), compositor.proxy_id(b.pointer)
            server.configure(surfaces={aid, bid})
            libraries, bus = self.bridge(compositor, server)
            with libraries, bus:
                first = blur.BackdropBlur(compositor.display_object)
                second = blur.BackdropBlur(compositor.display_object)
                try:
                    self.assertTrue(first.supported)
                    self.assertTrue(first.independent_strength)
                    first.apply(a, True, 2)
                    second.apply(b, True, 14)
                    self.assertEqual(server.snapshot(), {aid: 2, bid: 14})
                    first.apply(a, False, 9)
                    self.assertEqual(server.snapshot(), {bid: 14})
                    first.apply(a, True, 9)
                    first.close()
                    first.apply(a, True, 15)
                    self.assertFalse(first.supported)
                    self.assertFalse(first.independent_strength)
                    self.assertEqual(server.snapshot(), {bid: 14})
                    second.close()
                    self.assertEqual(server.snapshot(), {})
                finally:
                    first.close()
                    second.close()

    def test_absent_extension_keeps_live_standard_blur_without_claiming_strength_control(self):
        with closing(_PrivateKWin()) as server, WaylandCompositor() as compositor:
            server.configure(loaded=False, allow_load=False)
            a, b = compositor.surface(), compositor.surface()
            libraries, bus = self.bridge(compositor, server)
            with libraries, bus:
                first = blur.BackdropBlur(compositor.display_object)
                second = blur.BackdropBlur(compositor.display_object)
                try:
                    self.assertTrue(first.supported)
                    self.assertFalse(first.independent_strength)
                    first.apply(a, True, 2)
                    second.apply(b, True, 14)
                    compositor.sync()
                    self.assertTrue(compositor.covers_surface(a))
                    self.assertTrue(compositor.covers_surface(b))
                    self.assertEqual(server.snapshot(), {})
                    first.apply(a, False, 2)
                    compositor.sync()
                    self.assertFalse(compositor.covers_surface(a))
                    self.assertTrue(compositor.covers_surface(b))
                    second.close()
                    compositor.sync()
                    self.assertFalse(compositor.covers_surface(b))
                finally:
                    first.close()
                    second.close()

    def test_unavailable_native_library_does_not_clear_another_windows_blur(self):
        with closing(_PrivateKWin()) as server, WaylandCompositor() as compositor:
            server.configure(loaded=False, allow_load=False)
            surface = compositor.surface()
            libraries, bus = self.bridge(compositor, server)
            with libraries, bus:
                working = blur.BackdropBlur(compositor.display_object)
                try:
                    working.apply(surface, True, 5)
                    compositor.sync()
                    real = blur._library

                    def missing(name):
                        if name == "wayland-client":
                            raise OSError("Wayland client library missing")
                        return real(name)

                    logger = logging.getLogger("blur-library-regression")
                    with patch.object(blur, "_library", side_effect=missing), self.assertLogs(logger):
                        unavailable = blur.BackdropBlur(compositor.display_object, logger)
                    self.assertFalse(unavailable.supported)
                    self.assertFalse(unavailable.independent_strength)
                    unavailable.apply(surface, False, 5)
                    unavailable.close()
                    compositor.sync()
                    self.assertTrue(compositor.covers_surface(surface))
                finally:
                    working.close()

    def test_unsupported_display_never_applies_a_different_backends_effect(self):
        with closing(_PrivateKWin()) as server, WaylandCompositor() as compositor:
            server.configure(loaded=False, allow_load=False)
            surface = compositor.surface()
            libraries, bus = self.bridge(compositor, server)
            with libraries, bus:
                working = blur.BackdropBlur(compositor.display_object)
                try:
                    working.apply(surface, True, 5)
                    compositor.sync()
                    compositor.display_object.__gtype__.name = "GdkBroadwayDisplay"
                    unsupported = blur.BackdropBlur(compositor.display_object)
                    self.assertFalse(unsupported.supported)
                    self.assertFalse(unsupported.independent_strength)
                    unsupported.apply(surface, False, 1)
                    unsupported.close()
                    compositor.sync()
                    self.assertTrue(compositor.covers_surface(surface))
                finally:
                    working.close()

    def test_incomplete_compositor_is_unavailable_without_leaking_manager(self):
        with closing(_PrivateKWin()) as server, WaylandCompositor(advertise_compositor=False) as compositor:
            server.configure(loaded=False, allow_load=False)
            libraries, bus = self.bridge(compositor, server)
            with libraries, bus:
                unavailable = blur.BackdropBlur(compositor.display_object)
                self.assertFalse(unavailable.supported)
                self.assertFalse(unavailable.independent_strength)
                compositor.sync()
                self.assertEqual(compositor.resources, {})
                unavailable.close()
