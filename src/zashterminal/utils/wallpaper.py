"""On-demand wallpaper discovery and bounded, deterministic palette generation.

No polling, shell integration or desktop configuration writes. GI imports are
lazy so the color math and configuration readers can be tested without GTK.
"""

import copy
import configparser
import json
import signal
import sys
import tempfile
import os
import re
import stat
import subprocess
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote, urlsplit

MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_PIXELS = 40_000_000
ALGORITHM_VERSION = 2


def local_path(value):
    """Accept only absolute local paths / file URIs; never fetch remote images."""
    value = str(value).strip()
    if value.startswith("file:"):
        uri = urlsplit(value)
        if uri.netloc not in ("", "localhost") or uri.query or uri.fragment:
            raise ValueError("The image must be a local file.")
        value = unquote(uri.path)
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("The image must be a local file.")
    return path


def read_kde_wallpapers(config_file):
    """Read static image containments, retaining monitor/activity choices."""
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    with open(config_file, encoding="utf-8") as stream:
        parser.read_file(stream)
    result = []
    for section in sorted(parser.sections()):
        match = re.fullmatch(r"Containments\]\[(\d+)\]\[Wallpaper\]\[org\.kde\.image\]\[General", section)
        if not match:
            continue
        containment = parser[f"Containments][{match[1]}"] if parser.has_section(f"Containments][{match[1]}") else {}
        if containment.get("wallpaperplugin", "org.kde.image") != "org.kde.image":
            continue
        value = parser[section].get("Image", "")
        if value:
            result.append((f"KDE · {match[1]}", value))
    return result


def read_noctalia_wallpapers(config_file):
    """Read Noctalia's per-monitor/default wallpaper from its state settings.toml.

    Noctalia (the shell used with Umbriel, and also on Niri/Hyprland/Sway) owns
    the wallpaper there; the compositor itself has none. A tiny line reader keeps
    Python 3.8 support without a TOML dependency.
    """
    monitors, fallback, section = [], {}, None
    with open(config_file, encoding="utf-8") as stream:
        for raw in stream:
            line = raw.strip()
            if line.startswith("["):
                section = line.strip("[]").strip()
                continue
            match = re.fullmatch(r"""path\s*=\s*(?:"((?:[^"\\]|\\.)*)"|'([^']*)')\s*(?:#.*)?""", line)
            if not match or not section or not section.startswith("wallpaper."):
                continue
            value = re.sub(r"\\(.)", r"\1", match[1]) if match[1] is not None else match[2]
            if section.startswith("wallpaper.monitors."):
                name = section[len("wallpaper.monitors."):].strip('"')
                monitors.append((f"Noctalia · {name}", value))
            elif section in ("wallpaper.default", "wallpaper.last"):
                fallback[section] = value
    if monitors:
        return monitors
    for key in ("wallpaper.default", "wallpaper.last"):
        if fallback.get(key):
            return [("Noctalia", fallback[key])]
    return []


def _gsettings_wallpapers(desktop):
    from gi.repository import Gio

    source = Gio.SettingsSchemaSource.get_default()
    if source is None:
        return []
    if "cinnamon" in desktop:
        schema_name, keys = "org.cinnamon.desktop.background", ["picture-uri"]
    elif "mate" in desktop:
        schema_name, keys = "org.mate.background", ["picture-filename"]
    else:
        schema_name, keys = "org.gnome.desktop.background", ["picture-uri", "picture-uri-dark"]
    schema = source.lookup(schema_name, True)
    if schema is None:
        return []
    settings = Gio.Settings.new_full(schema, None, None)
    if schema.has_key("picture-options") and settings.get_string("picture-options") == "none":
        return []
    return [(key, settings.get_string(key)) for key in keys if schema.has_key(key)]


def discover_wallpapers():
    """Best-effort candidates for the active desktop, independent of distro.

    Unsupported/live wallpapers intentionally return no candidates. The caller
    always offers a local file picker. Call only from a background worker.
    """
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", os.environ.get("DESKTOP_SESSION", "")).lower()
    candidates = []
    if "kde" in desktop or "plasma" in desktop:
        config = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
        candidates = read_kde_wallpapers(config / "plasma-org.kde.plasma.desktop-appletsrc")
    elif any(name in desktop for name in ("gnome", "cinnamon", "mate", "unity", "budgie")):
        candidates = _gsettings_wallpapers(desktop)
    elif "xfce" in desktop:
        # Read the live xfconf channel rather than its potentially stale XML.
        output = subprocess.run(
            ["xfconf-query", "-c", "xfce4-desktop", "-l", "-v"],
            capture_output=True, text=True, timeout=3, check=True,
        ).stdout
        for line in output.splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2 and parts[0].endswith(("/last-image", "/image-path")):
                candidates.append((parts[0], parts[1].strip()))
    if not candidates:
        # Compositors such as Umbriel delegate wallpapers to the shell.
        state = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state")))
        noctalia = state / "noctalia" / "settings.toml"
        if noctalia.is_file():
            try:
                candidates = read_noctalia_wallpapers(noctalia)
            except (OSError, UnicodeDecodeError):
                candidates = []
    result, seen = [], set()
    for label, value in candidates:
        try:
            path = local_path(value)
            choices = [(label, path)]
            if path.is_dir():
                # Expose both variants instead of guessing the active appearance.
                choices = []
                for variant in ("images", "images_dark"):
                    images = sorted((path / "contents" / variant).glob("*"))
                    for image in images:
                        if image.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                            choices.append((f"{label} · {variant}", image))
            for source_label, image in choices:
                if image.is_file() and str(image) not in seen:
                    seen.add(str(image))
                    result.append((source_label, str(image)))
        except (ValueError, OSError):
            continue
    return result


def desktop_prefers_dark():
    """Read desktop appearance, independently of the terminal's own theme."""
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    try:
        if "kde" in desktop or "plasma" in desktop:
            config = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            parser.read(config / "kdeglobals", encoding="utf-8")
            rgb = [int(v) for v in parser["Colors:Window"]["BackgroundNormal"].split(",")]
            if len(rgb) == 3 and all(0 <= v <= 255 for v in rgb):
                return sum(v * w for v, w in zip(rgb, (0.299, 0.587, 0.114))) < 128
        elif "gnome" in desktop:
            from gi.repository import Gio

            source = Gio.SettingsSchemaSource.get_default()
            schema = source.lookup("org.gnome.desktop.interface", True) if source else None
            if schema and schema.has_key("color-scheme"):
                return Gio.Settings.new_full(schema, None, None).get_string("color-scheme") == "prefer-dark"
    except (OSError, KeyError, ValueError):
        pass
    return None


def preferred_wallpaper(candidates, screen_size, dark):
    """Select a variant only within an unambiguous desktop wallpaper source.

    KDE package resolution matching is best effort; multiple containments remain
    explicit choices because they may belong to different monitors/activities.
    """
    if len(candidates) == 1:
        return 0
    if not candidates:
        return None
    labels = {label for label, _ in candidates}
    if labels <= {"picture-uri", "picture-uri-dark"} and dark is not None:
        wanted = "picture-uri-dark" if dark else "picture-uri"
        return next((i for i, (label, _) in enumerate(candidates) if label == wanted), None)
    roots = {label.removesuffix(" · images_dark").removesuffix(" · images") for label in labels}
    if len(roots) != 1 or not all(label.endswith((" · images", " · images_dark")) for label in labels):
        return None
    variants = {Path(path).parent.name for _, path in candidates}
    if len(variants) > 1 and dark is None:
        return None
    variant = "images_dark" if dark and "images_dark" in variants else "images"
    choices = [(i, path) for i, (_, path) in enumerate(candidates) if Path(path).parent.name == variant]
    if len(choices) == 1:
        return choices[0][0]
    if not screen_size or not choices:
        return None
    width, height = screen_size
    if width <= 0 or height <= 0:
        return None
    ranked = []
    for index, path in choices:
        match = re.fullmatch(r"(\d+)x(\d+)", Path(path).stem)
        if not match:
            return None
        w, h = map(int, match.groups())
        if not w or not h:
            return None
        ranked.append(((abs(w / h - width / height), w < width or h < height,
                        abs(w * h - width * height)), index))
    ranked.sort()
    if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
        return None
    return ranked[0][1]


def _rgb(value):
    return tuple(int(value[i:i + 2], 16) / 255 for i in (1, 3, 5))


def luminance(color):
    channels = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in _rgb(color)]
    return sum(v * weight for v, weight in zip(channels, (0.2126, 0.7152, 0.0722)))


def contrast(first, second):
    a, b = sorted((luminance(first), luminance(second)))
    return (b + 0.05) / (a + 0.05)


class WallpaperBackendError(ValueError):
    """An actionable failure from the isolated Pywal worker."""

    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def scheme_from_pywal(result):
    """Keep Pywal's terminal palette exact; derive UI roles from its colors."""
    try:
        special = result["special"]
        palette = [result["colors"][f"color{i}"] for i in range(16)]
        background = special["background"]
        foreground = special["foreground"]
        # Validate before any color arithmetic or CSS construction.
        for color in [background, foreground, special["cursor"], *palette]:
            if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
                raise ValueError("Invalid Pywal color")
        candidates = palette[1:7] + palette[9:15]
        accent = max(candidates, key=lambda color: contrast(color, background))
        selection_fg = max(("#000000", "#ffffff"), key=lambda color: contrast(color, accent))
        return {
            "name": "Wallpaper", "background": background, "foreground": foreground,
            "cursor": special["cursor"], "palette": palette,
            "headerbar_background": background,
            "accent": accent, "selection_background": accent,
            "selection_foreground": selection_fg,
        }
    except (KeyError, TypeError, ValueError) as error:
        raise WallpaperBackendError("invalid-result") from error


def valid_scheme(scheme):
    if not isinstance(scheme, dict):
        return False
    keys = ("background", "foreground", "headerbar_background", "cursor", "selection_background", "selection_foreground", "accent")
    palette = scheme.get("palette")
    return (isinstance(palette, list) and len(palette) == 16
            and all(isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value)
                    for value in [*(scheme.get(key) for key in keys), *palette]))


def extract_palette(filename, mode="dark"):
    """Generate on demand, with a bounded in-memory cache and isolated worker."""
    if mode not in ("dark", "light"):
        raise ValueError("Invalid palette mode.")
    path = local_path(filename)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
        raise ValueError("Choose a regular image file smaller than 32 MiB.")
    return copy.deepcopy(_extract_cached(str(path), info.st_mtime_ns, info.st_size, mode))


@lru_cache(maxsize=4)
def _extract_cached(filename, mtime_ns, size, mode):
    """Cache only four small palettes, never full images or pixel buffers."""
    worker = Path(__file__).with_name("pywal_worker.py")
    # Even Pywal's cache/config paths are isolated from the user's wal setup.
    with tempfile.TemporaryDirectory(prefix="zash-pywal-") as directory:
        environment = dict(os.environ, XDG_CONFIG_HOME=directory,
                           XDG_CACHE_HOME=directory, PYWAL_CACHE_DIR=directory,
                           MAGICK_TEMPORARY_PATH=directory)
        process = subprocess.Popen(
            [sys.executable, str(worker), filename, mode],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=environment, start_new_session=True,
        )
        try:
            output, _stderr = process.communicate(timeout=30)
        except subprocess.TimeoutExpired as error:
            # Kill the worker and its ImageMagick child, not just the Python PID.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
            raise WallpaperBackendError("timeout") from error
        try:
            result = json.loads(output)
        except (ValueError, TypeError) as error:
            raise WallpaperBackendError("generation-failed") from error
        if process.returncode or "error" in result:
            raise WallpaperBackendError(result.get("error", "generation-failed"))
    current = Path(filename).stat()
    if current.st_mtime_ns != mtime_ns or current.st_size != size:
        raise WallpaperBackendError("image-changed")
    return {
        "path": filename, "mode": mode, "version": ALGORITHM_VERSION,
        "backend": "pywal16/wal/darken", "backend_version": result["version"],
        "scheme": scheme_from_pywal(result["palette"]),
    }