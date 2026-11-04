"""Session transcript (terminal output) logging helpers."""

from __future__ import annotations

import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, TextIO

_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def default_transcript_directory(custom: Optional[str] = None) -> Path:
    """Resolve the transcript directory (custom path or XDG data home default)."""
    text = (custom or "").strip()
    if text:
        return Path(text).expanduser()
    data_home = Path(
        os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
    )
    return data_home / "zashterminal" / "transcripts"


def strip_ansi(text: str) -> str:
    """Remove CSI/OSC-style ANSI escape sequences from text."""
    return _ANSI_RE.sub("", text or "")


def resolve_transcript_enabled(
    *,
    global_enabled: bool,
    session_override: Optional[bool],
) -> bool:
    """Resolve tri-state session override against the global default."""
    if session_override is None:
        return bool(global_enabled)
    return bool(session_override)


def build_transcript_path(
    directory: Path,
    *,
    session_name: str,
    host: str = "",
    when: Optional[datetime] = None,
) -> Path:
    """Build a filesystem-safe transcript path under *directory*."""
    stamp = (when or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
    safe_name = re.sub(r"[^\w.\-]+", "_", (session_name or "session").strip()) or "session"
    safe_host = re.sub(r"[^\w.\-]+", "_", (host or "").strip())
    suffix = f"_{safe_host}" if safe_host else ""
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{stamp}_{safe_name}{suffix}.log"


class TranscriptWriter:
    """Append-only transcript file writer (thread-safe)."""

    def __init__(self, path: Path, *, strip_escapes: bool = True) -> None:
        self.path = Path(path)
        self.strip_escapes = bool(strip_escapes)
        self._lock = threading.Lock()
        self._fh: Optional[TextIO] = None
        self._last_row = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8", errors="replace")
        header = f"# zashterminal transcript started {datetime.now().isoformat(timespec='seconds')}\n"
        self._fh.write(header)
        self._fh.flush()

    @property
    def last_row(self) -> int:
        return self._last_row

    def note_row(self, row: int) -> None:
        if row > self._last_row:
            self._last_row = row

    def write(self, text: str) -> None:
        if not text:
            return
        payload = strip_ansi(text) if self.strip_escapes else text
        if not payload:
            return
        with self._lock:
            if self._fh is None:
                return
            self._fh.write(payload)
            if not payload.endswith("\n"):
                # Keep streaming chunks readable when VTE returns partial lines.
                pass
            self._fh.flush()

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                try:
                    self._fh.write(
                        f"\n# zashterminal transcript ended "
                        f"{datetime.now().isoformat(timespec='seconds')}\n"
                    )
                    self._fh.flush()
                except Exception:
                    pass
                try:
                    self._fh.close()
                except Exception:
                    pass
                self._fh = None
