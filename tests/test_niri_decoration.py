"""niri-only server-decoration claim must never touch other desktops."""

from __future__ import annotations

import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from zashterminal.utils import niri_decoration


def _display(name="GdkWaylandDisplay"):
    display = Mock()
    display.__gtype__ = SimpleNamespace(name=name)
    return display


class NiriServerDecorationSelectionTests(unittest.TestCase):
    def _create(self, desktop, display_name="GdkWaylandDisplay"):
        env = {k: v for k, v in os.environ.items() if k != "XDG_CURRENT_DESKTOP"}
        if desktop is not None:
            env["XDG_CURRENT_DESKTOP"] = desktop
        with patch.dict(os.environ, env, clear=True), \
                patch.object(niri_decoration, "_library", return_value=Mock()), \
                patch.object(niri_decoration.NiriServerDecoration, "__init__", return_value=None) as init, \
                patch.object(niri_decoration.NiriServerDecoration, "_manager", object(), create=True):
            result = niri_decoration.NiriServerDecoration.for_display(_display(display_name))
        return init, result

    def test_other_desktops_never_bind_the_protocol(self):
        for desktop in ("KDE", "GNOME", "ubuntu:GNOME", "sway", None):
            with self.subTest(desktop=desktop):
                init, result = self._create(desktop)
                init.assert_not_called()
                self.assertIsNone(result)

    def test_niri_on_x11_never_binds_the_protocol(self):
        init, result = self._create("niri", "GdkX11Display")
        init.assert_not_called()
        self.assertIsNone(result)

    def test_niri_wayland_session_binds_the_protocol(self):
        for desktop in ("niri", "niri:GNOME"):
            with self.subTest(desktop=desktop):
                init, result = self._create(desktop)
                init.assert_called_once()
                self.assertIsInstance(result, niri_decoration.NiriServerDecoration)

    def test_unusable_protocol_is_reported_and_ignored(self):
        logger = Mock()
        with patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "niri"}), \
                patch.object(niri_decoration, "_library", return_value=Mock()), \
                patch.object(niri_decoration.NiriServerDecoration, "__init__",
                             side_effect=OSError("no libwayland")):
            result = niri_decoration.NiriServerDecoration.for_display(_display(), logger)
        self.assertIsNone(result)
        logger.warning.assert_called_once()

    def test_compositor_without_manager_global_closes_the_client(self):
        with patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "niri"}), \
                patch.object(niri_decoration, "_library", return_value=Mock()), \
                patch.object(niri_decoration.NiriServerDecoration, "__init__", return_value=None), \
                patch.object(niri_decoration.NiriServerDecoration, "_manager", None, create=True), \
                patch.object(niri_decoration.NiriServerDecoration, "close") as close:
            result = niri_decoration.NiriServerDecoration.for_display(_display())
        self.assertIsNone(result)
        close.assert_called_once()


MANAGER_NAME = b"org_kde_kwin_server_decoration_manager"


class NiriServerDecorationLifecycleTests(unittest.TestCase):
    def setUp(self):
        client = niri_decoration.NiriServerDecoration.__new__(niri_decoration.NiriServerDecoration)
        client._display = "wl_display"
        client._manager = "manager"
        client._surface = None
        client._decoration = None
        client._decoration_interface = "decoration_interface"
        client._manager_interface = SimpleNamespace(name=MANAGER_NAME)
        client._surface_pointer = Mock(side_effect=lambda surface: surface)
        gpointer = patch.object(niri_decoration, "_gpointer", side_effect=lambda surface: surface)
        gpointer.start()
        self.addCleanup(gpointer.stop)
        self.decorations = iter(("decoration-1", "decoration-2"))
        client._request = Mock(side_effect=self._request)
        client._flush = Mock()
        client._destroy = Mock()
        client._bind = Mock(return_value="bound-manager")
        client._set_queue = Mock()
        self.client = client

    def _request(self, proxy, opcode, *args, interface=None, destroy=False):
        if proxy == "manager" and opcode == 0:
            return next(self.decorations)
        return None

    def _requests(self):
        return [(c.args[0], c.args[1], c.kwargs.get("destroy", False))
                for c in self.client._request.call_args_list]

    def test_apply_requests_server_mode_for_the_surface(self):
        self.client.apply(0x10)
        self.assertEqual(self._requests(), [("manager", 0, False), ("decoration-1", 1, False)])
        mode = self.client._request.call_args_list[1].args[2]
        self.assertEqual(mode.u, niri_decoration._SERVER_MODE)
        self.client._flush.assert_called_once_with("wl_display")

    def test_reapplying_the_same_surface_is_a_no_op(self):
        self.client.apply(0x10)
        self.client._request.reset_mock()
        self.client.apply(0x10)
        self.client._request.assert_not_called()

    def test_new_surface_releases_previous_decoration_first(self):
        self.client.apply(0x10)
        self.client._request.reset_mock()
        self.client.apply(0x20)
        self.assertEqual(self._requests(), [
            ("decoration-1", 0, True), ("manager", 0, False), ("decoration-2", 1, False),
        ])

    def test_apply_without_manager_or_surface_does_nothing(self):
        self.client.apply(0)
        self.client._manager = None
        self.client.apply(0x10)
        self.client._request.assert_not_called()

    def test_failed_creation_raises_and_forgets_surface(self):
        self.client._request = Mock(return_value=None)
        with self.assertRaises(RuntimeError):
            self.client.apply(0x10)
        self.assertIsNone(self.client._surface)

    def test_close_releases_decoration_and_manager_once(self):
        self.client.apply(0x10)
        self.client._request.reset_mock()
        self.client.close()
        self.client.close()
        self.assertEqual(self._requests(), [("decoration-1", 0, True)])
        self.client._destroy.assert_called_once_with("manager")
        self.assertIsNone(self.client._manager)

    def test_bind_manager_skips_compositors_without_the_global(self):
        self.assertIsNone(self.client._bind_manager("registry", "queue", {}))
        self.client._bind.assert_not_called()

    def test_bind_manager_detaches_events_from_gtk_queue(self):
        manager = self.client._bind_manager("registry", "queue", {MANAGER_NAME: 7})
        self.assertEqual(manager, "bound-manager")
        self.client._bind.assert_called_once_with("registry", 7, self.client._manager_interface)
        self.client._set_queue.assert_called_once_with("bound-manager", None)

    def test_bind_manager_failure_raises(self):
        self.client._bind.return_value = None
        with self.assertRaises(RuntimeError):
            self.client._bind_manager("registry", "queue", {MANAGER_NAME: 7})


class NiriServerDecorationProtocolTests(unittest.TestCase):
    def test_init_defines_kde_protocol_and_binds_manager(self):
        surface_interface = niri_decoration._Interface(b"wl_surface", 6, 0, None, 0, None)
        with patch.object(niri_decoration._WaylandClient, "__init__", return_value=None), \
                patch.object(niri_decoration.NiriServerDecoration, "lib", None, create=True), \
                patch.object(niri_decoration._Interface, "in_dll", return_value=surface_interface), \
                patch.object(niri_decoration.NiriServerDecoration, "_bind_globals",
                             return_value="manager") as bind_globals:
            client = niri_decoration.NiriServerDecoration("display", "gtk")

        self.assertEqual(client._manager, "manager")
        bind_globals.assert_called_once_with(client._bind_manager)
        manager = client._manager_interface
        decoration = client._decoration_interface
        self.assertEqual(manager.name, MANAGER_NAME)
        self.assertEqual((manager.methods[0].name, manager.methods[0].signature), (b"create", b"no"))
        self.assertEqual(decoration.name, b"org_kde_kwin_server_decoration")
        self.assertEqual([decoration.methods[i].name for i in range(decoration.method_count)],
                         [b"release", b"request_mode"])
        self.assertEqual(decoration.methods[1].signature, b"u")
        create_types = manager.methods[0].types
        self.assertEqual(create_types[0].contents.name, decoration.name)
        self.assertEqual(create_types[1].contents.name, b"wl_surface")


if __name__ == "__main__":
    unittest.main()
