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

class GtkOwnedBackgroundEffectTests(unittest.TestCase):
    def _backend(self, supported=True):
        probe = Mock(supported=supported)
        return backdrop_blur._GtkBackgroundEffectBlur(probe), probe

    def test_gtk_ownership_follows_gtk_version(self):
        for result, expected in ((None, True), ("GTK too old", False)):
            with self.subTest(check_version=result), \
                    patch("gi.repository.Gtk.check_version", return_value=result):
                self.assertIs(backdrop_blur._gtk_owns_background_effect(), expected)

    def test_supported_mirrors_compositor_capability(self):
        for supported in (True, False):
            backend, _ = self._backend(supported)
            self.assertIs(backend.supported, supported)

    def test_unsupported_compositor_never_adds_blur_class(self):
        backend, _ = self._backend(supported=False)
        window = Mock()
        with patch("gi.repository.Gtk.Native.get_for_surface", return_value=window):
            backend.set_enabled(Mock(), True)
        window.add_css_class.assert_not_called()
        window.remove_css_class.assert_called_once_with(backend.CSS_CLASS)

    def test_surface_without_native_is_ignored(self):
        backend, _ = self._backend()
        with patch("gi.repository.Gtk.Native.get_for_surface", return_value=None):
            backend.set_enabled(Mock(), True)
        self.assertIsNone(backend._widget)

    def test_switching_windows_clears_the_previous_one(self):
        backend, _ = self._backend()
        first, second = Mock(), Mock()
        with patch.object(backend, "_ensure_provider"), \
                patch("gi.repository.Gtk.Native.get_for_surface", side_effect=[first, second]):
            backend.set_enabled(Mock(), True)
            backend.set_enabled(Mock(), True)
        first.remove_css_class.assert_called_once_with(backend.CSS_CLASS)
        second.add_css_class.assert_called_once_with(backend.CSS_CLASS)
        self.assertIs(backend._widget, second)

    def test_provider_is_installed_once_and_removed_on_close(self):
        backend, probe = self._backend()
        window = Mock()
        with patch("gi.repository.Gtk.Native.get_for_surface", return_value=window), \
                patch("gi.repository.Gtk.CssProvider") as provider_cls, \
                patch("gi.repository.Gtk.StyleContext") as style_context, \
                patch("gi.repository.Gdk.Display.get_default", return_value=Mock()):
            backend.set_enabled(Mock(), True)
            backend.set_enabled(Mock(), True)
            provider_cls.return_value.load_from_string.assert_called_once()
            style_context.add_provider_for_display.assert_called_once()
            backend.close()
            style_context.remove_provider_for_display.assert_called_once()
        window.remove_css_class.assert_called_with(backend.CSS_CLASS)
        probe.close.assert_called_once()
        self.assertIsNone(backend._provider)


if __name__ == "__main__":
    unittest.main()
