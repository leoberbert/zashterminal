"""Tests for Wayland GTK_IM_MODULE policy (accent/compose support).

Run with: PYTHONPATH=src python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from zashterminal.utils.platform import (
    ensure_wayland_input_method,
    should_set_wayland_im_module,
)


class WaylandImPolicyTests(unittest.TestCase):
    def test_sets_wayland_when_unset_on_wayland_session(self):
        env = {"WAYLAND_DISPLAY": "wayland-0"}
        self.assertTrue(should_set_wayland_im_module(env))

    def test_sets_wayland_when_simple_module(self):
        env = {"WAYLAND_DISPLAY": "wayland-0", "GTK_IM_MODULE": "simple"}
        self.assertTrue(should_set_wayland_im_module(env))

    def test_respects_explicit_im_frameworks(self):
        for module in ("ibus", "fcitx", "fcitx5", "xim", "wayland"):
            with self.subTest(module=module):
                env = {"WAYLAND_DISPLAY": "wayland-0", "GTK_IM_MODULE": module}
                self.assertFalse(should_set_wayland_im_module(env))

    def test_skips_x11_session(self):
        env = {"XDG_SESSION_TYPE": "x11"}
        self.assertFalse(should_set_wayland_im_module(env))

    def test_skips_when_gdk_backend_forced_x11(self):
        # Distrobox/Gentoo launcher may export WAYLAND_DISPLAY but force X11.
        env = {
            "WAYLAND_DISPLAY": "wayland-0",
            "GDK_BACKEND": "x11",
        }
        self.assertFalse(should_set_wayland_im_module(env))

    def test_detects_session_type_wayland_without_display_var(self):
        env = {"XDG_SESSION_TYPE": "wayland"}
        self.assertTrue(should_set_wayland_im_module(env))


class EnsureWaylandInputMethodTests(unittest.TestCase):
    def test_sets_environ_on_wayland(self):
        clean = {
            k: v
            for k, v in os.environ.items()
            if k not in {"GTK_IM_MODULE", "GDK_BACKEND", "WAYLAND_DISPLAY", "XDG_SESSION_TYPE"}
        }
        clean["WAYLAND_DISPLAY"] = "wayland-0"
        with mock.patch.dict(os.environ, clean, clear=True):
            ensure_wayland_input_method()
            self.assertEqual(os.environ.get("GTK_IM_MODULE"), "wayland")

    def test_does_not_override_ibus(self):
        clean = {
            k: v
            for k, v in os.environ.items()
            if k not in {"GTK_IM_MODULE", "GDK_BACKEND", "WAYLAND_DISPLAY", "XDG_SESSION_TYPE"}
        }
        clean["WAYLAND_DISPLAY"] = "wayland-0"
        clean["GTK_IM_MODULE"] = "ibus"
        with mock.patch.dict(os.environ, clean, clear=True):
            ensure_wayland_input_method()
            self.assertEqual(os.environ.get("GTK_IM_MODULE"), "ibus")


if __name__ == "__main__":
    unittest.main()
