"""Blur whatever is behind this window, following the slider.

Plasma's own blur effect is all-or-nothing and paints black through a fully
transparent window, so the slider cannot drive it. Instead we take one still
of the backdrop and soften that image as the value rises.
"""

from __future__ import annotations

import os
import tempfile
import time
from typing import Optional

EFFECT_BUS = "org.kde.KWin"
SCRIPT_NAME = "zashterminal-backdrop"
CALLBACK_NAME = "org.leoberbert.Zashterminal.Backdrop"


def blur_requested(amount) -> bool:
    """True when the slider asks for blur."""
    try:
        return float(amount) > 0
    except (TypeError, ValueError):
        return False


def blur_divisor(amount: float) -> float:
    """Downsample factor for a blur amount in the 0–100 range.

    Small slider values stay close to the original. The high end is much softer.
    """
    amount = max(0.0, min(100.0, float(amount)))
    if amount <= 0:
        return 1.0
    return 1.0 + (amount / 100.0) ** 1.4 * 23.0


def intermediate_size(width: int, height: int, amount: float) -> tuple[int, int]:
    divisor = blur_divisor(amount)
    return (
        max(1, int(round(width / divisor))),
        max(1, int(round(height / divisor))),
    )


class RawImage:
    def __init__(self, data: bytes, width: int, height: int, stride: int):
        self.data = data
        self.width = width
        self.height = height
        self.stride = stride


class WindowRect:
    def __init__(self, uuid: str, pid: int, x: int, y: int, width: int, height: int, caption: str = ""):
        self.uuid = uuid
        self.pid = pid
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.caption = caption

    @property
    def area(self) -> int:
        return max(0, self.width) * max(0, self.height)


def parse_window_table(text: str) -> list[WindowRect]:
    """Parse the tab-separated window list produced by the KWin script."""
    rows = []
    for line in (text or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 6:
            continue
        try:
            rows.append(WindowRect(
                uuid=parts[0],
                pid=int(float(parts[1])),
                x=int(float(parts[2])),
                y=int(float(parts[3])),
                width=int(float(parts[4])),
                height=int(float(parts[5])),
                caption=parts[6] if len(parts) > 6 else "",
            ))
        except ValueError:
            continue
    return rows


def preferred_window(windows: list[WindowRect], pid: int) -> Optional[WindowRect]:
    """Largest window of this process. Dialogs are smaller than the terminal."""
    ours = [window for window in windows if window.pid == pid and window.area > 0]
    if not ours:
        return None
    return max(ours, key=lambda window: window.area)


def rect_from_info(info: dict) -> Optional[WindowRect]:
    if not info:
        return None
    try:
        rect = WindowRect(
            uuid=str(info.get("uuid") or ""),
            pid=int(info.get("pid") or 0),
            x=int(round(float(info.get("x") or 0))),
            y=int(round(float(info.get("y") or 0))),
            width=int(round(float(info.get("width") or 0))),
            height=int(round(float(info.get("height") or 0))),
            caption=str(info.get("caption") or ""),
        )
    except (TypeError, ValueError):
        return None
    if rect.area <= 0:
        return None
    return rect


def raw_from_capture(info: dict, blob: bytes) -> Optional[RawImage]:
    """Accept KWin's raw RGBX/RGBA capture."""
    if not info or str(info.get("type") or "raw") != "raw":
        return None
    try:
        if int(info.get("format") or 0) not in (16, 17):
            return None
        width = int(info["width"])
        height = int(info["height"])
        stride = int(info["stride"])
    except (KeyError, TypeError, ValueError):
        return None
    needed = height * stride
    if width <= 0 or height <= 0 or stride < width * 4 or len(blob) < needed:
        return None
    return RawImage(bytes(blob[:needed]), width, height, stride)


def window_list_script(bus_name: str) -> str:
    return (
        "var lines = [];\n"
        "var wins = workspace.windowList();\n"
        "for (var i = 0; i < wins.length; ++i) {\n"
        "    var win = wins[i];\n"
        "    var g = win.frameGeometry;\n"
        "    lines.push(String(win.internalId) + '\\t' + win.pid + '\\t' + Math.round(g.x)"
        " + '\\t' + Math.round(g.y) + '\\t' + Math.round(g.width) + '\\t' + Math.round(g.height)"
        " + '\\t' + win.caption);\n"
        "}\n"
        f'callDBus("{bus_name}", "/Backdrop", "{bus_name}", "Windows", lines.join("\\n"));\n'
    )


def blur_rgbx(data: bytes, width: int, height: int, stride: int, amount: float, max_edge: int = 1600) -> bytes:
    """Return a PNG of ``data`` softened by ``amount`` (0–100)."""
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf, GLib

    pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(data), GdkPixbuf.Colorspace.RGB, True, 8, width, height, stride,
    )
    long_edge = max(pixbuf.get_width(), pixbuf.get_height(), 1)
    if long_edge > max_edge:
        scale = max_edge / long_edge
        pixbuf = pixbuf.scale_simple(
            max(1, int(round(pixbuf.get_width() * scale))),
            max(1, int(round(pixbuf.get_height() * scale))),
            GdkPixbuf.InterpType.BILINEAR,
        )
    target_w = pixbuf.get_width()
    target_h = pixbuf.get_height()
    small_w, small_h = intermediate_size(target_w, target_h, amount)
    if (small_w, small_h) != (target_w, target_h):
        softened = pixbuf.scale_simple(small_w, small_h, GdkPixbuf.InterpType.BILINEAR)
        pixbuf = softened.scale_simple(target_w, target_h, GdkPixbuf.InterpType.BILINEAR)
    _success, encoded = pixbuf.save_to_bufferv("png", [], [])
    return bytes(encoded)


def wallpaper_image(theme=None) -> Optional[RawImage]:
    """Still of the desktop wallpaper, used when a live capture is unavailable."""
    path = _wallpaper_path(theme)
    if not path:
        return None
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
    if pixbuf is None:
        return None
    if not pixbuf.get_has_alpha():
        pixbuf = pixbuf.add_alpha(False, 0, 0, 0)
    return RawImage(
        bytes(pixbuf.get_pixels()),
        pixbuf.get_width(),
        pixbuf.get_height(),
        pixbuf.get_rowstride(),
    )


def _wallpaper_path(theme) -> Optional[str]:
    if isinstance(theme, dict):
        path = theme.get("path")
        if isinstance(path, str) and path and os.path.isfile(path):
            return path
    try:
        from .wallpaper import discover_wallpapers, preferred_wallpaper
    except Exception:
        return None
    try:
        candidates = discover_wallpapers()
    except Exception:
        return None
    if not candidates:
        return None
    index = preferred_wallpaper(candidates, None, None)
    if index is None:
        index = 0
    return candidates[index][1]


class BackdropBlur:
    """Decide when to recapture, and render the slider amount from the last still."""

    def __init__(self, logger=None, kwin=None):
        self._logger = logger
        self.kwin = kwin if kwin is not None else KWinBus()
        self.sharp: Optional[RawImage] = None
        self.size = None
        self._warned = False

    def plan(self, amount, window_size=None) -> str:
        if not blur_requested(amount):
            self.sharp = None
            return "clear"
        if self.sharp is None:
            return "capture"
        if window_size and self.size and tuple(window_size) != tuple(self.size):
            return "refresh"
        return "paint"

    def remember(self, image: Optional[RawImage], window_size=None) -> None:
        if image is None:
            return
        self.sharp = image
        if window_size:
            self.size = tuple(window_size)

    def render(self, amount: float) -> Optional[bytes]:
        if self.sharp is None:
            return None
        return blur_rgbx(
            self.sharp.data, self.sharp.width, self.sharp.height, self.sharp.stride, amount,
        )

    def locate(self, pid: int) -> Optional[WindowRect]:
        try:
            return self.kwin.geometry(pid)
        except Exception as exc:
            self._warn(f"Could not find this window for backdrop blur: {exc}")
            return None

    def capture(self, rect: WindowRect) -> Optional[RawImage]:
        try:
            return self.kwin.capture_area(rect)
        except Exception as exc:
            self._warn(f"Could not capture the backdrop: {exc}")
            return None

    def remember_wallpaper(self, theme) -> bool:
        try:
            image = wallpaper_image(theme)
        except Exception as exc:
            self._warn(f"Could not read the wallpaper: {exc}")
            return False
        if image is None:
            return False
        self.remember(image, self.size)
        return True

    def _warn(self, message: str) -> None:
        if self._warned:
            return
        self._warned = True
        if self._logger is not None:
            self._logger.warning(message)


class KWinBus:
    """Read this process's window rectangle and capture that screen area."""

    def __init__(self, connection=None):
        self._connection = connection
        self._uuid = None
        self._registered = False
        self._result = None

    def connection(self):
        if self._connection is None:
            from gi.repository import Gio

            self._connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        return self._connection

    def geometry(self, pid: int) -> Optional[WindowRect]:
        if self._uuid:
            current = self.window_info(self._uuid)
            if current is not None and current.pid == pid:
                return current
            self._uuid = None
        chosen = preferred_window(self.list_windows(), pid)
        if chosen is not None:
            self._uuid = chosen.uuid
        return chosen

    def window_info(self, uuid: str) -> Optional[WindowRect]:
        from gi.repository import Gio, GLib

        reply = self.connection().call_sync(
            EFFECT_BUS, "/KWin", EFFECT_BUS, "getWindowInfo",
            GLib.Variant("(s)", (uuid,)),
            GLib.VariantType.new("(a{sv})"),
            Gio.DBusCallFlags.NONE, 2000, None,
        )
        return rect_from_info(reply.unpack()[0])

    def capture_area(self, rect: WindowRect) -> Optional[RawImage]:
        import os

        from gi.repository import Gio, GLib

        read_fd, write_fd = os.pipe()
        try:
            os.set_inheritable(write_fd, True)
            descriptors = Gio.UnixFDList.new()
            index = descriptors.append(write_fd)
            os.close(write_fd)
            write_fd = -1
            reply, _out = self.connection().call_with_unix_fd_list_sync(
                EFFECT_BUS, "/org/kde/KWin/ScreenShot2", "org.kde.KWin.ScreenShot2",
                "CaptureArea",
                GLib.Variant("(iiuua{sv}h)", (rect.x, rect.y, rect.width, rect.height, {}, index)),
                GLib.VariantType.new("(a{sv})"),
                Gio.DBusCallFlags.NONE, 3000, descriptors, None,
            )
            info = reply.unpack()[0]
            needed = int(info.get("height") or 0) * int(info.get("stride") or 0)
            return raw_from_capture(info, _read_capture(read_fd, needed))
        finally:
            if write_fd >= 0:
                os.close(write_fd)
            os.close(read_fd)

    def list_windows(self) -> list[WindowRect]:
        from gi.repository import Gio, GLib

        self._ensure_callback()
        connection = self.connection()
        self._result = None
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix="zashterminal-backdrop-", suffix=".js",
        ) as script:
            script.write(window_list_script(CALLBACK_NAME))
            script.flush()
            try:
                if self._load(connection, script.name) < 0:
                    self._unload(connection)
                    if self._load(connection, script.name) < 0:
                        return []
                connection.call_sync(
                    EFFECT_BUS, "/Scripting", "org.kde.kwin.Scripting", "start",
                    None, None, Gio.DBusCallFlags.NONE, 2000, None,
                )
                if self._result is None:
                    self._wait_for_result(0.8)
                return parse_window_table(self._result or "")
            finally:
                self._unload(connection)

    def _connection_is_fake(self) -> bool:
        return bool(getattr(self._connection, "skip_own", False))

    def _ensure_callback(self) -> None:
        if self._registered:
            return
        from gi.repository import Gio

        connection = self.connection()
        if not self._connection_is_fake():
            Gio.bus_own_name_on_connection(
                connection, CALLBACK_NAME,
                Gio.BusNameOwnerFlags.ALLOW_REPLACEMENT | Gio.BusNameOwnerFlags.REPLACE,
                None, None,
            )
        info = Gio.DBusNodeInfo.new_for_xml(
            "<node><interface name='org.leoberbert.Zashterminal.Backdrop'>"
            "<method name='Windows'><arg name='text' type='s' direction='in'/></method>"
            "</interface></node>"
        )
        connection.register_object("/Backdrop", info.interfaces[0], self._on_windows, None, None)
        self._registered = True

    def _on_windows(self, *args) -> None:
        params, invocation = args[5], args[6]
        self._result = params.unpack()[0]
        invocation.return_value(None)

    def _load(self, connection, path: str) -> int:
        from gi.repository import Gio, GLib

        reply = connection.call_sync(
            EFFECT_BUS, "/Scripting", "org.kde.kwin.Scripting", "loadScript",
            GLib.Variant("(ss)", (path, SCRIPT_NAME)),
            GLib.VariantType.new("(i)"),
            Gio.DBusCallFlags.NONE, 2000, None,
        )
        return int(reply.unpack()[0])

    def _unload(self, connection) -> None:
        from gi.repository import Gio, GLib

        try:
            connection.call_sync(
                EFFECT_BUS, "/Scripting", "org.kde.kwin.Scripting", "unloadScript",
                GLib.Variant("(s)", (SCRIPT_NAME,)),
                GLib.VariantType.new("(b)"),
                Gio.DBusCallFlags.NONE, 2000, None,
            )
        except Exception:
            return

    def _wait_for_result(self, seconds: float) -> None:
        from gi.repository import GLib

        context = GLib.MainContext.default()
        deadline = time.monotonic() + seconds
        while self._result is None and time.monotonic() < deadline:
            if not context.iteration(False):
                time.sleep(0.01)


def _read_capture(read_fd: int, needed: int) -> bytes:
    import os

    os.set_blocking(read_fd, False)
    chunks = []
    total = 0
    deadline = time.monotonic() + 1.0
    while total < needed and time.monotonic() < deadline:
        try:
            chunk = os.read(read_fd, max(needed - total, 1))
        except BlockingIOError:
            time.sleep(0.005)
            continue
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)
