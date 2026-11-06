"""Backend selection contracts for BackdropBlur on Wayland compositors."""

from __future__ import annotations

import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from zashterminal.utils import backdrop_blur


def _wayland_display():
    display = Mock()
    display.__gtype__ = SimpleNamespace(name="GdkWaylandDisplay")
    return display


class BackdropBlurBackendSelectionTests(unittest.TestCase):
    def _create(self, desktop):
        env = {k: v for k, v in os.environ.items() if k != "XDG_CURRENT_DESKTOP"}
        if desktop is not None:
            env["XDG_CURRENT_DESKTOP"] = desktop
        with patch.dict(os.environ, env, clear=True), \
                patch.object(backdrop_blur, "_library", return_value=Mock()), \
                patch.object(backdrop_blur._KWinEffectBlur, "connect", return_value=None) as kwin_connect, \
                patch.object(backdrop_blur, "_WaylandBlur") as wayland_blur:
            backdrop_blur.BackdropBlur(_wayland_display())
        return kwin_connect, wayland_blur

    def test_kwin_backend_is_not_probed_outside_kde_sessions(self):
        for desktop in ("niri", "GNOME", "sway", None):
            with self.subTest(desktop=desktop):
                kwin_connect, wayland_blur = self._create(desktop)
                kwin_connect.assert_not_called()
                wayland_blur.assert_called_once()

    def test_kwin_backend_is_probed_in_kde_sessions(self):
        for desktop in ("KDE", "ubuntu:GNOME:KDE"):
            with self.subTest(desktop=desktop):
                kwin_connect, _ = self._create(desktop)
                kwin_connect.assert_called_once()

    def test_wayland_fallback_is_used_when_kwin_is_unavailable(self):
        kwin_connect, wayland_blur = self._create("KDE")
        kwin_connect.assert_called_once()
        wayland_blur.assert_called_once()


if __name__ == "__main__":
    unittest.main()
