"""Behavioral tests over private, native Wayland client/server connections."""

from __future__ import annotations

import unittest

from zashterminal.utils.backdrop_blur import _WaylandBlur

from blur_wayland_harness import WaylandCompositor, available


@unittest.skipUnless(available(), "libwayland-client and libwayland-server are required")
class WaylandBlurTests(unittest.TestCase):
    def blur(self, compositor):
        return _WaylandBlur(compositor.display_object, compositor.gtk)

    def test_mapped_idle_surface_on_off_on_is_visible_without_rendering(self):
        with WaylandCompositor() as compositor:
            surface = compositor.surface()
            blur = self.blur(compositor)
            self.assertTrue(blur.supported)
            for enabled in (True, False, True):
                with self.subTest(enabled=enabled):
                    blur.set_enabled(surface, enabled)
                    compositor.sync()
                    self.assertEqual(compositor.covers_surface(surface), enabled)
                    self.assertEqual(compositor.state(surface)["buffer_attaches"], 0)
                    self.assertEqual(compositor.live(b"ext_background_effect_surface_v1"), int(enabled))
                    self.assertEqual(compositor.live(b"wl_region"), 0)
            blur.close()
            compositor.sync()
            self.assertFalse(compositor.covers_surface(surface))
            self.assertEqual(compositor.live(b"ext_background_effect_surface_v1"), 0)
            self.assertEqual(compositor.live(b"ext_background_effect_manager_v1"), 0)

    def test_region_remains_live_across_resize_and_buffer_scale_changes(self):
        with WaylandCompositor() as compositor:
            surface = compositor.surface()
            blur = self.blur(compositor)
            blur.set_enabled(surface, True)
            compositor.sync()
            initial_region = compositor.state(surface)["visible"]
            self.assertTrue(compositor.covers_surface(surface))
            for width, height, scale in ((1920, 1080, 1), (3840, 2160, 2), (97, 53, 3)):
                with self.subTest(size=(width, height), scale=scale):
                    compositor.resize(surface, width, height, scale)
                    self.assertTrue(compositor.covers_surface(surface))
                    self.assertEqual(compositor.state(surface)["scale"], scale)
                    self.assertEqual(compositor.state(surface)["visible"], initial_region)
                    self.assertEqual(compositor.state(surface)["buffer_attaches"], 0)
                    self.assertEqual(compositor.live(b"ext_background_effect_surface_v1"), 1)
            blur.close()

    def test_unmapped_surface_defers_both_enable_and_disable_until_map_commit(self):
        with WaylandCompositor() as compositor:
            surface = compositor.surface(mapped=False)
            blur = self.blur(compositor)
            blur.set_enabled(surface, True)
            compositor.sync()
            self.assertFalse(compositor.covers_surface(surface))
            self.assertIsNotNone(compositor.state(surface)["pending"])
            self.assertEqual(compositor.state(surface)["commits"], 0)
            surface.mapped = True
            compositor.commit(surface)
            self.assertTrue(compositor.covers_surface(surface))
            surface.mapped = False
            blur.set_enabled(surface, False)
            compositor.sync()
            self.assertTrue(compositor.covers_surface(surface))
            self.assertIsNone(compositor.state(surface)["pending"])
            surface.mapped = True
            compositor.commit(surface)
            self.assertFalse(compositor.covers_surface(surface))
            blur.close()

    def test_switching_mapped_surfaces_removes_old_visible_blur_immediately(self):
        with WaylandCompositor() as compositor:
            old_surface = compositor.surface()
            new_surface = compositor.surface()
            blur = self.blur(compositor)
            blur.set_enabled(old_surface, True)
            compositor.sync()
            self.assertTrue(compositor.covers_surface(old_surface))
            blur.set_enabled(new_surface, True)
            compositor.sync()
            self.assertFalse(compositor.covers_surface(old_surface))
            self.assertTrue(compositor.covers_surface(new_surface))
            self.assertEqual(compositor.live(b"ext_background_effect_surface_v1"), 1)
            # Closing an active backend is itself a visible removal, not a
            # promise to remove it on the next unrelated GTK frame.
            blur.close()
            compositor.sync()
            self.assertFalse(compositor.covers_surface(new_surface))
            self.assertIsNone(compositor.state(new_surface)["pending"])
            self.assertEqual(compositor.live(b"ext_background_effect_surface_v1"), 0)
            self.assertEqual(compositor.live(b"ext_background_effect_manager_v1"), 0)
            blur.close()
            compositor.sync()
            self.assertFalse(compositor.covers_surface(new_surface))

    def test_standard_protocol_is_preferred_when_legacy_is_also_available(self):
        with WaylandCompositor(protocol="both") as compositor:
            surface = compositor.surface(mapped=False)
            blur = self.blur(compositor)
            blur.set_enabled(surface, True)
            compositor.sync()
            # Standard state waits for wl_surface.commit; legacy's separate
            # effect commit would have made the region visible already.
            self.assertFalse(compositor.covers_surface(surface))
            self.assertEqual(compositor.live(b"ext_background_effect_surface_v1"), 1)
            self.assertEqual(compositor.live(b"org_kde_kwin_blur"), 0)
            surface.mapped = True
            compositor.commit(surface)
            self.assertTrue(compositor.covers_surface(surface))
            blur.close()

    def test_unsupported_capabilities_never_apply_blur_and_close_manager(self):
        for capabilities in (0, 2):
            with self.subTest(capabilities=capabilities), WaylandCompositor(capabilities=capabilities) as compositor:
                surface = compositor.surface()
                blur = self.blur(compositor)
                self.assertFalse(blur.supported)
                blur.set_enabled(surface, True)
                compositor.sync()
                self.assertFalse(compositor.covers_surface(surface))
                self.assertEqual(compositor.live(b"ext_background_effect_surface_v1"), 0)
                blur.close()
                compositor.sync()
                self.assertEqual(compositor.live(b"ext_background_effect_manager_v1"), 0)

    def test_absent_blur_global_is_a_usable_unsupported_backend(self):
        with WaylandCompositor(protocol="none") as compositor:
            surface = compositor.surface()
            blur = self.blur(compositor)
            self.assertFalse(blur.supported)
            blur.set_enabled(surface, True)
            compositor.sync()
            self.assertFalse(compositor.covers_surface(surface))
            self.assertEqual(compositor.live(b"ext_background_effect_surface_v1"), 0)
            self.assertEqual(compositor.live(b"org_kde_kwin_blur"), 0)
            blur.close()

    def test_protocol_error_reading_capabilities_releases_native_resources(self):
        with WaylandCompositor(refuse_manager=True) as compositor:
            with self.assertRaises(RuntimeError):
                _WaylandBlur(compositor.display_object, compositor.gtk)
            # A genuine server error poisons the connection. The server must
            # release all resources even though another roundtrip is impossible.
            compositor.drop_connection()
            self.assertEqual(compositor.resources, {})
            self.assertEqual(compositor.effects, {})
            self.assertEqual(compositor.regions, {})

    def test_disconnected_discovery_reports_error_without_native_resources(self):
        with WaylandCompositor() as compositor:
            compositor.drop_connection()
            with self.assertRaises(RuntimeError):
                _WaylandBlur(compositor.display_object, compositor.gtk)
            self.assertEqual(compositor.resources, {})
            self.assertEqual(compositor.effects, {})
            self.assertEqual(compositor.regions, {})

    def test_second_effect_object_for_a_surface_is_a_fatal_protocol_error(self):
        # GTK >= 4.23.3 creates its own ext_background_effect_surface_v1 for
        # every wl_surface. Creating another one, as _WaylandBlur does, makes
        # the compositor drop the whole connection. BackdropBlur must therefore
        # route through GTK (CSS backdrop-filter) on those GTK versions.
        with WaylandCompositor() as compositor:
            surface = compositor.surface()
            gtk_owned = self.blur(compositor)
            gtk_owned.set_enabled(surface, True)
            compositor.sync()
            ours = self.blur(compositor)
            ours.set_enabled(surface, True)
            with self.assertRaises(RuntimeError):
                compositor.sync()
            compositor.drop_connection()

    def test_legacy_effect_commits_independently_and_unsets_on_switch_and_close(self):
        with WaylandCompositor(protocol="legacy") as compositor:
            old_surface = compositor.surface(mapped=False)
            new_surface = compositor.surface()
            blur = self.blur(compositor)
            self.assertTrue(blur.supported)
            for enabled in (True, False, True):
                blur.set_enabled(old_surface, enabled)
                compositor.sync()
                self.assertEqual(compositor.covers_surface(old_surface), enabled)
                self.assertEqual(compositor.state(old_surface)["commits"], 0)
                self.assertEqual(compositor.live(b"org_kde_kwin_blur"), int(enabled))
            compositor.resize(old_surface, 2560, 1440, 2)
            self.assertTrue(compositor.covers_surface(old_surface))
            blur.set_enabled(new_surface, True)
            compositor.sync()
            self.assertFalse(compositor.covers_surface(old_surface))
            self.assertTrue(compositor.covers_surface(new_surface))
            self.assertEqual(compositor.live(b"org_kde_kwin_blur"), 1)
            blur.close()
            compositor.sync()
            self.assertFalse(compositor.covers_surface(new_surface))
            self.assertEqual(compositor.live(b"org_kde_kwin_blur"), 0)
            self.assertEqual(compositor.live(b"wl_region"), 0)


if __name__ == "__main__":
    unittest.main()
