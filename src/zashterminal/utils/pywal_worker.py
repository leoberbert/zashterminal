"""Private palette-only subprocess. Never invoke wal's CLI or theme application."""

import sys

# Running this file directly must not shadow stdlib platform with utils/platform.py.
if __name__ == "__main__":
    sys.path.pop(0)

import json
import shutil
from importlib.metadata import PackageNotFoundError, version


def generate(filename, mode):
    try:
        backend_version = version("pywal16")
    except PackageNotFoundError:
        return {"error": "missing-pywal"}
    if not (shutil.which("magick") or shutil.which("convert")):
        return {"error": "missing-imagemagick"}
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    fmt, width, height = GdkPixbuf.Pixbuf.get_file_info(filename)
    if (not fmt or fmt.get_name() not in ("png", "jpeg", "webp", "bmp")
            or width <= 0 or height <= 0 or width * height > 40_000_000):
        return {"error": "invalid-image"}
    # Same backend and shading as: wal -i IMAGE --backend wal --cols16.
    # No export, sequences.send, reload, wallpaper.change or user cache writes.
    from pywal.backends import wal
    from pywal.colors import colors_to_dict

    colors = wal.get(filename, light=mode == "light", c16="darken")
    return {"version": backend_version, "palette": colors_to_dict(colors, filename)}


if __name__ == "__main__":
    try:
        result = generate(sys.argv[1], sys.argv[2])
    except (Exception, SystemExit):
        result = {"error": "generation-failed"}
    print(json.dumps(result))
    sys.exit(1 if "error" in result else 0)
