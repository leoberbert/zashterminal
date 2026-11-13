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

    def _select(self, modern, gtk_owns):
        probe = Mock(_modern=modern, supported=True)
        with patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "niri"}), \
                patch.object(backdrop_blur, "_library", return_value=Mock()), \
                patch.object(backdrop_blur, "_WaylandBlur", return_value=probe), \
                patch.object(backdrop_blur, "_gtk_owns_background_effect", return_value=gtk_owns):
            blur = backdrop_blur.BackdropBlur(_wayland_display())
        return blur._backend, probe

    def test_gtk_owned_background_effect_never_creates_a_second_surface_object(self):
        backend, probe = self._select(modern=True, gtk_owns=True)
        self.assertIsInstance(backend, backdrop_blur._GtkBackgroundEffectBlur)
        surface, window = Mock(), Mock()
        with patch("gi.repository.Gtk.Native.get_for_surface", return_value=window), \
                patch.object(backend, "_ensure_provider"):
            backend.set_enabled(surface, True)
            window.add_css_class.assert_called_once_with(backend.CSS_CLASS)
            backend.set_enabled(surface, False)
            window.remove_css_class.assert_called_with(backend.CSS_CLASS)
        probe.set_enabled.assert_not_called()

    def test_older_gtk_or_legacy_protocol_keeps_the_native_wayland_backend(self):
        for modern, gtk_owns in ((True, False), (False, True), (False, False)):
            with self.subTest(modern=modern, gtk_owns=gtk_owns):
                backend, probe = self._select(modern, gtk_owns)
                self.assertIs(backend, probe)


if __name__ == "__main__":
    unittest.main()
