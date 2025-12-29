# zashterminal/filemanager/models.py
import gi

gi.require_version("Gtk", "4.0")
import re
from datetime import datetime

from gi.repository import Gio, GObject


class FileItem(GObject.GObject):
    """Data model for an item in the file manager.

    Performance optimizations:
    - Uses datetime.fromisoformat() instead of strptime (24x faster)
    - Defers icon resolution to first access (lazy loading)
    - Directories get folder icon immediately, files defer MIME lookup
    """

    # Lazy-loaded regex for fallback parsing (rarely used)
    _LS_RE = None

    @classmethod
    def _get_ls_regex(cls):
        """Lazy load regex pattern - only compiled when needed."""
        if cls._LS_RE is None:
            cls._LS_RE = re.compile(
                r"^(?P<perms>[-dlpscb?][rwxSsTt-]{9})(?:[.+@])?\s+"
                r"(?P<links>\d+)\s+"
                r"(?P<owner>[\w\d._-]+)\s+"
                r"(?P<group>[\w\d._-]+)\s+"
                r"(?P<size>\d+)\s+"
                r"(?P<datetime>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+\s+[+-]\d{4})\s+"
                r"(?P<name>.+?)(?: -> (?P<link_target>.+))?$"
            )
        return cls._LS_RE

    # Define GObject properties
    __gproperties__ = {
        "name": (str, "Name", "File name", "", GObject.ParamFlags.READABLE),
        "permissions": (
            str,
            "Permissions",
            "File permissions",
            "",
            GObject.ParamFlags.READABLE,
        ),
        "size": (
            int,
            "Size",
            "File size",
            0,
            GObject.G_MAXINT,
            0,
            GObject.ParamFlags.READABLE,
        ),
        "owner": (str, "Owner", "File owner", "", GObject.ParamFlags.READABLE),
        "group": (str, "Group", "File group", "", GObject.ParamFlags.READABLE),
        "is-directory": (
            bool,
            "Is Directory",
            "Whether item is a directory",
            False,
            GObject.ParamFlags.READABLE,
        ),
        "is-link": (
            bool,
            "Is Link",
            "Whether item is a symbolic link",
            False,
            GObject.ParamFlags.READABLE,
        ),
        "icon-name": (
            str,
            "Icon Name",
            "Icon name for the file",
            "",
            GObject.ParamFlags.READABLE,
        ),
    }

    def __init__(
        self, name, perms, size, date, owner, group, is_link=False, link_target=""
    ):
        super().__init__()
        self._name = name
        self._permissions = perms
        self._size = size
        self._date = date
        self._owner = owner
        self._group = group
        self._link_target = link_target
        # Performance: Defer icon resolution - set to None for lazy loading
        # Only directories get immediate icon (no MIME lookup needed)
        if perms.startswith("d") or (
            perms.startswith("l") and link_target.endswith("/")
        ):
            self._cached_icon_name = "folder-symbolic"
        else:
            self._cached_icon_name = None  # Lazy - resolved on first access

    @property
    def name(self) -> str:
        return self._name

    @property
    def permissions(self) -> str:
        return self._permissions

    @property
    def size(self) -> int:
        return self._size

    @property
    def date(self) -> datetime:
        return self._date

    @property
    def owner(self) -> str:
        return self._owner

    @property
    def group(self) -> str:
        return self._group

    @property
    def is_directory(self) -> bool:
        return self._permissions.startswith("d")

    @property
    def is_link(self) -> bool:
        return self._permissions.startswith("l")

    @property
    def is_directory_like(self) -> bool:
        """Returns True if the item is a directory or a link to a directory."""
        return self.is_directory or (
            self.is_link and self._link_target and self._link_target.endswith("/")
        )

    def _resolve_icon_name(self) -> str:
        """Resolve icon name from MIME type (expensive - only for files)."""
        mime_type, _ = Gio.content_type_guess(self._name, None)
        if mime_type:
            gicon = Gio.content_type_get_icon(mime_type)
            if isinstance(gicon, Gio.ThemedIcon) and gicon.get_names():
                return gicon.get_names()[0]
        return "text-x-generic-symbolic"

    @property
    def icon_name(self) -> str:
        """Return cached icon name, resolving lazily if needed."""
        if self._cached_icon_name is None:
            self._cached_icon_name = self._resolve_icon_name()
        return self._cached_icon_name

    @classmethod
    def from_ls_line(cls, line: str):
        """Optimized parsing using str.split instead of regex.

        Performance optimizations:
        - str.split is faster than regex for columnar data
        - datetime.fromisoformat is 24x faster than strptime
        - Single pass through data with minimal string operations
        """
        try:
            # Fast path using str.split
            parts = line.split(maxsplit=8)
            if len(parts) < 9:
                # Fallback to regex for edge cases
                return cls._from_ls_line_regex(line)

            perms, links, owner, group, size, date_ymd, time_hms, time_zone, name = (
                parts
            )

            # Performance: Use fromisoformat (24x faster than strptime)
            # Convert "2024-01-15 10:30:00.123456789" to "2024-01-15T10:30:00"
            try:
                time_part = time_hms.split(".")[0]  # Remove nanoseconds
                date_obj = datetime.fromisoformat(f"{date_ymd}T{time_part}")
            except ValueError:
                date_obj = datetime.now()

            # Fast name cleanup - handle symlinks and type indicators
            link_target = ""
            if " -> " in name:
                name, link_target = name.split(" -> ", 1)

            # Remove file type indicators added by --classify
            if name and name[-1] in "/@=*|>":
                name = name[:-1]

            return cls(
                name=name,
                perms=perms,
                size=int(size),
                date=date_obj,
                owner=owner,
                group=group,
                is_link=perms.startswith("l"),
                link_target=link_target,
            )

        except (ValueError, IndexError):
            # Fallback to Regex for edge cases
            return cls._from_ls_line_regex(line)

    @classmethod
    def _from_ls_line_regex(cls, line: str):
        """Fallback regex parser for edge cases."""
        match = cls._get_ls_regex().match(line)
        if not match:
            return None
        data = match.groupdict()
        try:
            datetime_str = data["datetime"]
            date_part = datetime_str.split(".")[0]
            date_obj = datetime.fromisoformat(date_part.replace(" ", "T"))
        except ValueError:
            date_obj = datetime.now()
        name = data["name"]
        name = name.rstrip("/@=*|>")
        return cls(
            name=name,
            perms=data["perms"],
            size=int(data["size"]),
            date=date_obj,
            owner=data["owner"],
            group=data["group"],
            is_link=data["perms"].startswith("l"),
            link_target=data.get("link_target", ""),
        )
