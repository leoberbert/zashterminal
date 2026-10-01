"""Run with: PYTHONPATH=src python3 -m unittest discover -s tests -v."""

import copy
import json
import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from zashterminal.utils.wallpaper import (
    contrast, discover_wallpapers, extract_palette, local_path, preferred_wallpaper, desktop_prefers_dark,
    scheme_from_pywal, read_kde_wallpapers, read_noctalia_wallpapers, valid_scheme, WallpaperBackendError,
)


def pywal_fixture():
    colors = ["#0f1224", "#362f41", "#884240", "#ab7348", "#34336f", "#484373",
              "#72447d", "#92939c", "#5e6074", "#493F57", "#B65956", "#E59A60",
              "#464595", "#605A9A", "#995BA7", "#c3c3c8"]
    return {"special": {"background": colors[0], "foreground": colors[15], "cursor": colors[15]},
            "colors": {f"color{i}": color for i, color in enumerate(colors)}}


def sample_scheme():
    return scheme_from_pywal(pywal_fixture())


class PaletteTests(unittest.TestCase):
    def test_preserves_every_pywal_color_exactly(self):
        original = pywal_fixture()
        scheme = scheme_from_pywal(original)
        self.assertEqual(scheme["palette"], list(original["colors"].values()))
        for key in ("background", "foreground", "cursor"):
            self.assertEqual(scheme[key], original["special"][key])
        self.assertTrue(valid_scheme(scheme))
        self.assertIn(scheme["accent"], scheme["palette"])
        self.assertGreaterEqual(contrast(scheme["selection_foreground"], scheme["selection_background"]), 4.5)

    def test_rejects_invalid_pywal_output(self):
        for value in ({}, None, {**pywal_fixture(), "colors": {"color0": "red; }"}}):
            with self.assertRaises(WallpaperBackendError):
                scheme_from_pywal(value)

    def test_invalid_cached_palette_is_rejected(self):
        scheme = sample_scheme()
        for value in (None, {}, {**scheme, "palette": ["#fff"] * 16}, {**scheme, "accent": "red; }"}):
            self.assertFalse(valid_scheme(value))

    def test_remote_and_relative_sources_rejected(self):
        for path in ("https://example.com/wallpaper.png", "file://remote/image.png", "relative.png"):
            with self.assertRaises(ValueError):
                local_path(path)
        self.assertEqual(local_path("file:///tmp/a%20b.png"), Path("/tmp/a b.png"))


class DiscoveryTests(unittest.TestCase):
    def test_selects_desktop_variant_and_landscape_resolution(self):
        candidates = [(f"KDE · 1 · {variant}", f"/wall/contents/{variant}/{size}.png")
                      for variant in ("images", "images_dark")
                      for size in ("1440x2960", "5120x2880", "7680x2160")]
        self.assertEqual(preferred_wallpaper(candidates, (1920, 1080), True), 4)
        self.assertEqual(preferred_wallpaper(candidates, (1920, 1080), False), 1)
        self.assertEqual(preferred_wallpaper(candidates, (1440, 2960), True), 3)
        self.assertIsNone(preferred_wallpaper(candidates, (1920, 1080), None))
        self.assertIsNone(preferred_wallpaper(candidates + [("KDE · 2", "/other.png")], (1920, 1080), True))

    def test_reads_kde_appearance_independently(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "kdeglobals"
            with patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "KDE", "XDG_CONFIG_HOME": directory}):
                self.assertIsNone(desktop_prefers_dark())
                config.write_text("[Colors:Window]\nBackgroundNormal=32,35,38\n")
                self.assertTrue(desktop_prefers_dark())
                config.write_text("[Colors:Window]\nBackgroundNormal=240,240,240\n")
                self.assertFalse(desktop_prefers_dark())

    def test_kde_package_exposes_all_variants_without_picking_first_file(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "wallpaper"
            expected = set()
            for variant in ("images", "images_dark"):
                folder = package / "contents" / variant
                folder.mkdir(parents=True)
                for resolution in ("1440x2960.png", "5120x2880.png"):
                    image = folder / resolution
                    image.touch()
                    expected.add(str(image))
            with patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "KDE"}), patch("zashterminal.utils.wallpaper.read_kde_wallpapers", return_value=[("KDE · 1", str(package))]):
                candidates = discover_wallpapers()
                self.assertEqual({path for _, path in candidates}, expected)
                self.assertEqual(len(candidates), 4)

    def test_gsettings_adapters_are_schema_guarded(self):
        from gi.repository import Gio
        from zashterminal.utils.wallpaper import _gsettings_wallpapers

        for desktop, expected, key in (
            ("gnome", "org.gnome.desktop.background", "picture-uri"),
            ("cinnamon", "org.cinnamon.desktop.background", "picture-uri"),
            ("mate", "org.mate.background", "picture-filename"),
        ):
            with self.subTest(desktop=desktop):
                schema = Mock()
                schema.has_key.side_effect = lambda name: name == key
                source = Mock()
                source.lookup.return_value = schema
                settings = Mock()
                settings.get_string.return_value = "/tmp/wallpaper.png"
                with patch.object(Gio.SettingsSchemaSource, "get_default", return_value=source), patch.object(Gio.Settings, "new_full", return_value=settings):
                    self.assertEqual(_gsettings_wallpapers(desktop), [(key, "/tmp/wallpaper.png")])
                    source.lookup.assert_called_once_with(expected, True)
                source.lookup.return_value = None
                with patch.object(Gio.SettingsSchemaSource, "get_default", return_value=source), patch.object(Gio.Settings, "new_full") as create:
                    self.assertEqual(_gsettings_wallpapers(desktop), [])
                    create.assert_not_called()

    def test_kde_multiple_containments_and_ignore_non_image_plugin(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plasma-org.kde.plasma.desktop-appletsrc"
            path.write_text("""[Containments][1]
wallpaperplugin=org.kde.image
[Containments][1][Wallpaper][org.kde.image][General]
Image=file:///tmp/a%20b.png
[Containments][2]
wallpaperplugin=org.kde.slideshow
[Containments][2][Wallpaper][org.kde.image][General]
Image=/tmp/stale.png
[Containments][3][Wallpaper][org.kde.image][General]
Image=/tmp/other.png
""")
            self.assertEqual(read_kde_wallpapers(path), [("KDE · 1", "file:///tmp/a%20b.png"), ("KDE · 3", "/tmp/other.png")])

    def test_unknown_desktop_does_not_guess_or_launch_commands(self):
        with patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "unknown"}), patch("subprocess.run") as run:
            self.assertEqual(discover_wallpapers(), [])
            run.assert_not_called()

    def test_xfce_uses_bounded_read_only_command_and_preserves_spaces(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "space name.png"
            image.touch()
            with patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "XFCE"}), patch("subprocess.run", return_value=Mock(stdout=f"/backdrop/screen0/monitor0/workspace0/last-image   {image}\n")) as run:
                self.assertEqual(discover_wallpapers()[0][1], str(image))
                args, kwargs = run.call_args
                self.assertEqual(args[0], ["xfconf-query", "-c", "xfce4-desktop", "-l", "-v"])
                self.assertEqual(kwargs["timeout"], 3)
                self.assertNotIn("shell", kwargs)


class ImageTests(unittest.TestCase):
    def test_missing_dependencies_report_actionable_errors(self):
        from importlib.metadata import PackageNotFoundError
        from zashterminal.utils.pywal_worker import generate
        with patch("zashterminal.utils.pywal_worker.version", side_effect=PackageNotFoundError):
            self.assertEqual(generate("unused.png", "dark"), {"error": "missing-pywal"})
        with patch("zashterminal.utils.pywal_worker.version", return_value="3.8.15"), patch("zashterminal.utils.pywal_worker.shutil.which", return_value=None):
            self.assertEqual(generate("unused.png", "dark"), {"error": "missing-imagemagick"})

    def test_timeout_cleans_up_process_group(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "timeout.png"
            path.write_bytes(b"fixture")
            process = Mock(pid=12345)
            process.communicate.side_effect = [subprocess.TimeoutExpired("worker", 30), ("", "")]
            with patch("zashterminal.utils.wallpaper.subprocess.Popen", return_value=process) as launch, patch("zashterminal.utils.wallpaper.os.killpg") as kill:
                with self.assertRaises(WallpaperBackendError) as failure:
                    extract_palette(path)
                self.assertEqual(failure.exception.reason, "timeout")
                kill.assert_called_once()
                self.assertTrue(launch.call_args.kwargs["start_new_session"])

    def test_large_file_rejected_before_decoder(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "too-large.png"
            with path.open("wb") as stream:
                stream.truncate(33 * 1024 * 1024)
            with self.assertRaises(ValueError):
                extract_palette(path)

    def test_real_decoder_and_corrupt_image(self):
        import gi
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.png"
            pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 300, 120)
            pixbuf.fill(0x3377AAFF)
            pixbuf.savev(str(path), "png", [], [])
            value = extract_palette(path)
            self.assertTrue(valid_scheme(value["scheme"]))
            self.assertEqual(value["path"], str(path))
            # Compare against the exact API used by wal --backend wal --cols16.
            from pywal import colors
            expected = colors.get(str(path), light=False, backend="wal", c16="darken", cache_dir=directory)
            self.assertEqual(value["scheme"]["palette"], list(expected["colors"].values()))
            for key in ("background", "foreground", "cursor"):
                self.assertEqual(value["scheme"][key], expected["special"][key])
            # Cache hits must not expose mutable shared palettes.
            value["scheme"]["palette"][1] = "#000000"
            self.assertNotEqual(extract_palette(path)["scheme"]["palette"][1], "#000000")
            path.write_bytes(b"invalid")
            with self.assertRaises((ValueError, TypeError)):
                extract_palette(path)


class SettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Config module initializes paths at import; never touch user settings.
        cls.temp = tempfile.TemporaryDirectory()
        cls.environment = patch.dict(os.environ, {"XDG_CONFIG_HOME": cls.temp.name, "XDG_CACHE_HOME": cls.temp.name})
        cls.environment.start()
        from zashterminal.settings.manager import SettingsManager
        cls.manager_class = SettingsManager

    @classmethod
    def tearDownClass(cls):
        cls.environment.stop()
        cls.temp.cleanup()

    def setUp(self):
        self.manager = self.manager_class.__new__(self.manager_class)
        self.manager._lock = threading.RLock()
        self.manager._settings = {"color_scheme": 0, "gtk_theme": "light", "transparency": 0}
        self.manager.custom_schemes = {}
        self.manager._wallpaper_checked = None
        self.manager._wallpaper_active = None
        self.manager.logger = Mock()
        self.value = {"apply_to_interface": True, "scheme": sample_scheme()}

    def test_snapshot_restoration_and_invalid_persisted_data(self):
        original = self.manager.get_color_scheme_data()
        self.manager._settings["wallpaper_theme"] = json.loads(json.dumps(self.value))
        self.assertEqual(self.manager.get("gtk_theme"), "terminal")
        self.assertEqual(self.manager.get_color_scheme_data(), self.value["scheme"])
        self.assertEqual(self.manager._settings["gtk_theme"], "light")
        self.manager._settings["wallpaper_theme"] = None
        self.assertEqual(self.manager.get_color_scheme_data(), original)
        self.assertEqual(self.manager.get("gtk_theme"), "light")
        self.manager._settings["wallpaper_theme"] = {"apply_to_interface": True, "scheme": {}}
        self.assertEqual(self.manager.get_color_scheme_data(), original)
        self.assertEqual(self.manager.get("gtk_theme"), "light")

    def test_color_update_never_touches_font_pty_or_scrollback(self):
        terminal = Mock(spec=["set_colors", "set_color_cursor", "set_color_highlight", "set_color_highlight_foreground"])
        self.manager._settings["wallpaper_theme"] = self.value
        self.manager.apply_terminal_colors(terminal)
        terminal.set_colors.assert_called_once()
        self.manager._settings["wallpaper_theme"] = None
        self.manager.apply_terminal_colors(terminal)
        terminal.set_color_highlight.assert_called_with(None)
        terminal.set_color_highlight_foreground.assert_called_with(None)

    def test_highlight_cache_tracks_the_resolved_palette(self):
        from zashterminal.settings.highlights import HighlightManager
        manager = Mock()
        manager._settings_manager = self.manager
        manager._current_theme_name = None
        manager._color_cache = {"old": {"red": "#123456"}}
        self.manager._settings["wallpaper_theme"] = self.value
        palette = HighlightManager.get_current_theme_palette(manager)
        self.assertEqual(palette, self.value["scheme"])
        self.assertEqual(manager._color_cache, {})
        first_signature = manager._current_theme_name
        changed = copy.deepcopy(self.value)
        changed["scheme"]["palette"][1] = "#f12345"
        self.manager._settings["wallpaper_theme"] = changed
        self.assertEqual(HighlightManager.get_current_theme_palette(manager)["palette"][1], "#f12345")
        self.assertNotEqual(first_signature, manager._current_theme_name)


if __name__ == "__main__":
    unittest.main()


class NoctaliaTests(unittest.TestCase):
    SAMPLE = """[bar]
end = [ "tray", "wallpaper" ]

[wallpaper]
directory = "/home/u/Imagens/Wallpapers"
fill_mode = "span"

    [wallpaper.default]
    path = "/home/u/Imagens/Área/default.jpg"

    [wallpaper.last]
    path = "/home/u/Imagens/last.jpg"

    [wallpaper.monitors.HDMI-A-1]
    path = "/home/u/Imagens/hdmi.jpg"
"""

    def write(self, directory, text):
        path = Path(directory) / "noctalia" / "settings.toml"
        path.parent.mkdir(parents=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_reads_monitor_wallpapers_first(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.write(directory, self.SAMPLE)
            self.assertEqual(read_noctalia_wallpapers(config),
                             [("Noctalia · HDMI-A-1", "/home/u/Imagens/hdmi.jpg")])

    def test_falls_back_to_default_and_keeps_unicode(self):
        with tempfile.TemporaryDirectory() as directory:
            text = self.SAMPLE.split("    [wallpaper.monitors")[0]
            config = self.write(directory, text)
            self.assertEqual(read_noctalia_wallpapers(config),
                             [("Noctalia", "/home/u/Imagens/Área/default.jpg")])

    def test_umbriel_session_discovers_noctalia_wallpaper(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "wall.jpg"
            image.write_bytes(b"x")
            self.write(directory, f'[wallpaper.monitors.HDMI-A-1]\npath = "{image}"\n'
                                  f'[wallpaper.monitors.DP-1]\npath = "{image}"\n')
            env = {"XDG_CURRENT_DESKTOP": "umbriel", "XDG_STATE_HOME": directory}
            with patch.dict(os.environ, env, clear=False):
                found = discover_wallpapers()
            self.assertEqual(found, [("Noctalia · HDMI-A-1", str(image))])
            self.assertEqual(preferred_wallpaper(found, (1920, 1080), None), 0)
