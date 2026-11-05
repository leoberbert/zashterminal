"""Progressive backdrop blur: slider curve, window choice, and KWin capture parsing."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from zashterminal.utils.backdrop_blur import (
    BackdropBlur,
    KWinBus,
    RawImage,
    WindowRect,
    blur_divisor,
    blur_requested,
    blur_rgbx,
    intermediate_size,
    parse_window_table,
    preferred_window,
    raw_from_capture,
    rect_from_info,
    wallpaper_image,
    window_list_script,
)


def _rgbx(width: int, height: int, paint) -> bytes:
    raw = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            red, green, blue = paint(x, y)
            index = (y * width + x) * 4
            raw[index:index + 4] = bytes((red, green, blue, 255))
    return bytes(raw)


def _contrast(png: bytes) -> float:
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf, Gio, GLib

    stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(png))
    pixbuf = GdkPixbuf.Pixbuf.new_from_stream(stream, None)
    data = bytes(pixbuf.get_pixels())
    stride = pixbuf.get_rowstride()
    channels = pixbuf.get_n_channels()
    values = []
    for y in range(pixbuf.get_height()):
        row = data[y * stride:y * stride + pixbuf.get_width() * channels]
        values.extend(row[index] for index in range(0, len(row), channels))
    mean = sum(values) / len(values)
    return sum((value - mean) ** 2 for value in values) / len(values)


class Reply:
    def __init__(self, value):
        self.value = value

    def unpack(self):
        return (self.value,)


class Invocation:
    def __init__(self):
        self.returned = False

    def return_value(self, _value):
        self.returned = True


class FakeConnection:
    skip_own = True

    def __init__(self, table: str, info: dict, blob: bytes):
        self.table = table
        self.info = info
        self.blob = blob
        self.loads = 0
        self.callback = None
        self.unloaded = 0

    def register_object(self, _path, _interface, callback, *_args):
        self.callback = callback
        return 1

    def call_sync(self, _bus, _path, _interface, method, params, *_rest):
        if method == "loadScript":
            self.loads += 1
            return Reply(-1 if self.loads == 1 else 3)
        if method == "start":
            invocation = Invocation()
            self.callback(None, None, None, None, None, Reply(self.table), invocation)
            self.started = invocation.returned
            return None
        if method == "unloadScript":
            self.unloaded += 1
            return Reply(True)
        if method == "getWindowInfo":
            return Reply(self.info)
        raise AssertionError(method)

    def call_with_unix_fd_list_sync(self, *_args, **_kwargs):
        descriptors = _args[8]
        os.write(descriptors.get(0), self.blob)
        return Reply({
            "type": "raw", "format": 16, "width": 2, "height": 2, "stride": 8, "scale": 1.0,
        }), None


TABLE = "\n".join([
    "{main}\t42\t10\t20\t800\t600\tTerminal Zash",
    "{dialog}\t42\t40\t40\t200\t100\tPreferences",
    "{other}\t7\t0\t0\t1920\t1080\tFirefox",
    "bad line",
    "nope\tx\t1\t2\t3\t4",
])


class BlurAmountTests(unittest.TestCase):
    def test_only_a_positive_amount_blurs(self):
        self.assertFalse(blur_requested(0))
        self.assertFalse(blur_requested(None))
        self.assertFalse(blur_requested("nope"))
        self.assertTrue(blur_requested(8))
        self.assertTrue(blur_requested("28"))

    def test_the_slider_gets_softer_as_it_rises(self):
        self.assertEqual(blur_divisor(0), 1.0)
        self.assertLess(blur_divisor(8), blur_divisor(40))
        self.assertLess(blur_divisor(40), blur_divisor(100))
        self.assertEqual(blur_divisor(150), blur_divisor(100))
        self.assertEqual(intermediate_size(800, 400, 0), (800, 400))
        self.assertLess(intermediate_size(800, 400, 80)[0], 800)

    def test_a_higher_amount_softens_the_captured_pixels(self):
        data = _rgbx(24, 16, lambda x, y: (255, 255, 255) if (x // 2 + y // 2) % 2 == 0 else (0, 0, 0))
        mild = blur_rgbx(data, 24, 16, 24 * 4, 8)
        strong = blur_rgbx(data, 24, 16, 24 * 4, 90)
        self.assertGreater(_contrast(mild), _contrast(strong))


class WindowChoiceTests(unittest.TestCase):
    def test_the_largest_window_of_this_process_is_the_terminal(self):
        windows = parse_window_table(TABLE)
        self.assertEqual(len(windows), 3)
        chosen = preferred_window(windows, 42)
        self.assertEqual(chosen.caption, "Terminal Zash")
        self.assertIsNone(preferred_window(windows, 99))
        self.assertIsNone(rect_from_info({}))
        self.assertIsNone(rect_from_info({"width": "nope"}))
        rect = rect_from_info({"uuid": "{main}", "pid": 42, "x": 1.2, "y": 3.8, "width": 10, "height": 12, "caption": "T"})
        self.assertEqual((rect.x, rect.y, rect.width, rect.height), (1, 4, 10, 12))
        self.assertIsNone(raw_from_capture({"type": "file"}, b""))
        self.assertIsNone(raw_from_capture({"type": "raw", "format": 4, "width": 1, "height": 1, "stride": 4}, b"\x00" * 4))
        image = raw_from_capture(
            {"type": "raw", "format": 16, "width": 2, "height": 2, "stride": 8}, b"\x00" * 20,
        )
        self.assertEqual((image.width, len(image.data)), (2, 16))

    def test_the_script_calls_back_with_the_window_table(self):
        script = window_list_script("org.leoberbert.Zashterminal.Backdrop")
        self.assertIn('callDBus("org.leoberbert.Zashterminal.Backdrop"', script)
        self.assertIn("workspace.windowList()", script)


class BackdropPlanTests(unittest.TestCase):
    def _image(self):
        return RawImage(_rgbx(4, 4, lambda x, y: (x * 40, y * 40, 10)), 4, 4, 16)

    def test_the_slider_paints_from_one_still_and_recaptures_when_resized(self):
        blur = BackdropBlur(kwin=Mock())
        self.assertEqual(blur.plan(0, (800, 600)), "clear")
        self.assertEqual(blur.plan(8, (800, 600)), "capture")
        blur.remember(self._image(), (800, 600))
        self.assertEqual(blur.plan(40, (800, 600)), "paint")
        self.assertIsNotNone(blur.render(40))
        self.assertEqual(blur.plan(80, (900, 600)), "refresh")
        blur.remember(None, (900, 600))
        self.assertIsNotNone(blur.sharp)

    def test_a_missing_window_falls_back_and_errors_are_warned_once(self):
        logger = Mock()
        kwin = Mock()
        kwin.geometry.side_effect = RuntimeError("no kwin")
        kwin.capture_area.side_effect = RuntimeError("no capture")
        blur = BackdropBlur(logger, kwin)
        self.assertIsNone(blur.locate(1))
        self.assertIsNone(blur.capture(WindowRect("id", 1, 0, 0, 10, 10)))
        self.assertIsNone(blur.locate(1))
        self.assertEqual(logger.warning.call_count, 1)
        self.assertIsNone(blur.render(10))
        with patch("zashterminal.utils.backdrop_blur._wallpaper_path", return_value=None):
            self.assertFalse(blur.remember_wallpaper(None))
        with patch(
            "zashterminal.utils.backdrop_blur.wallpaper_image",
            side_effect=OSError("unread"),
        ):
            self.assertFalse(blur.remember_wallpaper(None))

    def test_a_stored_wallpaper_can_be_softened(self):
        import gi

        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "wall.png")
            pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 6, 4)
            pixbuf.fill(0x336699ff)
            pixbuf.savev(path, "png", [], [])
            image = wallpaper_image({"path": path})
            self.assertEqual((image.width, image.height), (6, 4))
            blur = BackdropBlur(kwin=Mock())
            self.assertTrue(blur.remember_wallpaper({"path": path}))
            self.assertTrue(blur.render(30).startswith(b"\x89PNG"))
        with patch("zashterminal.utils.backdrop_blur._wallpaper_path", return_value=None):
            self.assertIsNone(wallpaper_image(None))


class KWinBusTests(unittest.TestCase):
    def test_geometry_is_cached_and_the_area_is_read_back(self):
        info = {
            "uuid": "{main}", "pid": 42, "x": 10, "y": 20, "width": 800, "height": 600, "caption": "Terminal Zash",
        }
        blob = _rgbx(2, 2, lambda x, y: (10, 20, 30))
        connection = FakeConnection(TABLE, info, blob)
        bus = KWinBus(connection)
        rect = bus.geometry(42)
        self.assertEqual(rect.caption, "Terminal Zash")
        self.assertEqual(connection.loads, 2)
        again = bus.geometry(42)
        self.assertEqual(again.uuid, "{main}")
        self.assertEqual(connection.loads, 2)
        image = bus.capture_area(rect)
        self.assertEqual((image.width, image.height), (2, 2))
        self.assertGreaterEqual(connection.unloaded, 1)
        bus._uuid = "{gone}"
        connection.info = {"uuid": "{gone}", "pid": 99, "x": 0, "y": 0, "width": 10, "height": 10}
        self.assertEqual(bus.geometry(42).uuid, "{main}")

    def test_a_failed_script_load_returns_no_windows(self):
        connection = FakeConnection(TABLE, {}, b"")
        connection.call_sync = lambda *args, **kwargs: Reply(-1)
        bus = KWinBus(connection)
        self.assertEqual(bus.list_windows(), [])
        connection.call_sync = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("dbus"))
        bus._unload(connection)

    def test_script_is_private_unique_and_does_not_follow_a_planted_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            victim = os.path.join(directory, "unrelated.txt")
            with open(victim, "w", encoding="utf-8") as handle:
                handle.write("preserve-me")
            os.symlink(victim, os.path.join(directory, "zashterminal-backdrop.js"))
            connection = FakeConnection(TABLE, {}, b"")
            original_call = connection.call_sync
            paths = []

            def call_sync(*args, **kwargs):
                if args[3] == "loadScript":
                    path = args[4].unpack()[0]
                    paths.append(path)
                    self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
                    self.assertEqual(os.stat(path).st_uid, os.getuid())
                return original_call(*args, **kwargs)

            connection.call_sync = call_sync
            bus = KWinBus(connection)
            with (
                patch.dict(os.environ, {"XDG_RUNTIME_DIR": directory}),
                patch.object(tempfile, "tempdir", directory),
            ):
                for _ in range(2):
                    self.assertEqual(
                        [window.uuid for window in bus.list_windows()],
                        ["{main}", "{dialog}", "{other}"],
                    )
            self.assertEqual(len(set(paths)), 2)
            for path in paths:
                self.assertFalse(os.path.exists(path))
            with open(victim, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "preserve-me")

    def test_script_is_removed_when_kwin_rejects_it_or_dbus_raises(self):
        for failure in ("rejected", "loadScript", "start"):
            with self.subTest(failure=failure):
                connection = FakeConnection(TABLE, {}, b"")
                original_call = connection.call_sync
                paths = []

                def call_sync(*args, **kwargs):
                    method = args[3]
                    if method == "loadScript":
                        paths.append(args[4].unpack()[0])
                        if failure == "rejected":
                            return Reply(-1)
                    if method == failure:
                        raise RuntimeError("dbus")
                    return original_call(*args, **kwargs)

                connection.call_sync = call_sync
                bus = KWinBus(connection)
                if failure == "rejected":
                    self.assertEqual(bus.list_windows(), [])
                else:
                    with self.assertRaisesRegex(RuntimeError, "dbus"):
                        bus.list_windows()
                for path in paths:
                    self.assertFalse(os.path.exists(path))
                self.assertGreaterEqual(connection.unloaded, 1)


class BackgroundBlurSettingTests(unittest.TestCase):
    def test_background_blur_setting_accepts_a_percentage(self):
        from zashterminal.settings.config import DefaultSettings
        from zashterminal.settings.manager import SettingsManager, SettingsValidator
        from zashterminal.utils.exceptions import ConfigValidationError

        self.assertEqual(DefaultSettings.get_defaults()["background_blur"], 0)
        validator = SettingsValidator()
        errors = validator.validate_settings_structure(
            {
                "color_scheme": 0,
                "font": "Monospace 10",
                "shortcuts": {},
                "background_blur": 25,
            },
            30,
        )
        self.assertFalse(any("background_blur" in error for error in errors))
        manager = SettingsManager.__new__(SettingsManager)
        manager.validator = validator
        manager.logger = Mock()
        manager._validate_setting_value("background_blur", 0)
        manager._validate_setting_value("background_blur", 100)
        with self.assertRaises(ConfigValidationError):
            manager._validate_setting_value("background_blur", 101)


if __name__ == "__main__":
    unittest.main()
