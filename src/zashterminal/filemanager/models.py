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
    _ANSI_CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
    _ANSI_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
    _DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    _TIME_RE = re.compile(r"^\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$")

    @classmethod
    def _get_ls_regex(cls):
        """Lazy load regex pattern - only compiled when needed."""
        if cls._LS_RE is None:
            cls._LS_RE = re.compile(
                r"^(?P<perms>[.\-dlpscb?][rwxSsTt-]{9})(?:[.+@])?\s+"
                r"(?P<links>\d+|-)\s+"
                r"(?P<owner>[^\s]+)\s+"
                r"(?P<group>[^\s]+)\s+"
                r"(?P<size>\d+|-)\s+"
                r"(?P<datetime>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:\s+[+-]\d{4})?)\s+"
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
            # Defensive cleanup for terminals/tools that still emit ANSI/OSC escapes.
            clean_line = cls._ANSI_OSC_RE.sub("", line)
            clean_line = cls._ANSI_CSI_RE.sub("", clean_line)
            line = clean_line.strip()

            # Fast path supporting GNU ls and eza long output:
            # - GNU ls: perms links owner group size YYYY-MM-DD HH:MM name
            # - eza:    perms links size owner group YYYY-MM-DD HH:MM name
            parts = line.split()
            if len(parts) < 6:
                # Fallback to regex for edge cases
                return cls._from_ls_line_regex(line)

            raw_perms = parts[0]
            # eza uses "." prefix for regular files (e.g. ".rw-r--r--")
            perms = f"-{raw_perms[1:]}" if raw_perms.startswith(".") else raw_perms
            links = parts[1]

            # Find the first YYYY-MM-DD + HH:MM token pair. This is the most
            # stable anchor across ls/eza variants and optional columns.
            date_idx = -1
            for i in range(1, len(parts) - 1):
                if cls._DATE_RE.fullmatch(parts[i]) and cls._TIME_RE.fullmatch(
                    parts[i + 1]
                ):
                    date_idx = i
                    break
            if date_idx == -1:
                return cls._from_ls_line_regex(line)

            meta = parts[2:date_idx]
            date_ymd = parts[date_idx]
            time_hms = parts[date_idx + 1]
            remaining = parts[date_idx + 2 :]
            if not remaining:
                return cls._from_ls_line_regex(line)

            owner = "unknown"
            group = "unknown"
            size_token = "0"
            # Common layouts:
            # - ls:  owner group size
            # - eza: size owner group
            # - eza (smart-group): size owner
            if len(meta) >= 3:
                if meta[0] == "-" or meta[0].isdigit():
                    size_token, owner, group = meta[0], meta[1], meta[2]
                elif meta[2] == "-" or meta[2].isdigit():
                    owner, group, size_token = meta[0], meta[1], meta[2]
                else:
                    owner, group = meta[0], meta[1]
            elif len(meta) == 2:
                if meta[0] == "-" or meta[0].isdigit():
                    size_token, owner = meta[0], meta[1]
                    group = owner
                elif meta[1] == "-" or meta[1].isdigit():
                    owner, size_token = meta[0], meta[1]
                    group = owner
                else:
                    owner, group = meta[0], meta[1]
            elif len(meta) == 1:
                owner = meta[0]
                group = owner

            # Optional timezone token (e.g. +0000)
            if re.fullmatch(r"[+-]\d{4}", remaining[0]):
                remaining = remaining[1:]
            if not remaining:
                return cls._from_ls_line_regex(line)
            name = " ".join(remaining)

            # Performance: Use fromisoformat (24x faster than strptime)
            # Convert "2024-01-15 10:30:00.123456789" to "2024-01-15T10:30:00"
            try:
                time_part = time_hms.split(".")[0]  # Remove fractional seconds if present
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
            # Some eza versions quote names with spaces unless --no-quotes is set.
            if len(name) >= 2 and name[0] == name[-1] and name[0] in ("'", '"'):
                name = name[1:-1]

            return cls(
                name=name,
                perms=perms,
                size=int(size_token) if size_token.isdigit() else 0,
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
        if len(name) >= 2 and name[0] == name[-1] and name[0] in ("'", '"'):
            name = name[1:-1]
        perms = data["perms"]
        if perms.startswith("."):
            perms = f"-{perms[1:]}"
        size_token = data["size"]
        return cls(
            name=name,
            perms=perms,
            size=int(size_token) if size_token.isdigit() else 0,
            date=date_obj,
            owner=data["owner"],
            group=data["group"],
            is_link=perms.startswith("l"),
            link_target=data.get("link_target", ""),
        )
