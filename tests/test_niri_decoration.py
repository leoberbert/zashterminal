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


if __name__ == "__main__":
    unittest.main()
