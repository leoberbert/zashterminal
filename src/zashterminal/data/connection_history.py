# zashterminal/data/connection_history.py
"""Persistent history of recently opened sessions."""

import json
import os
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional

from ..utils.logger import get_logger

MAX_ENTRIES = 100
VALID_KINDS = ("ssh", "local", "sftp")


@dataclass
class HistoryEntry:
    session_name: str
    host: str
    kind: str
    last_used: float
    count: int = 1


class ConnectionHistory:
    """Thread-safe, JSON-backed list of recently used sessions (newest first)."""

    def __init__(self, path: Optional[Path] = None):
        self.logger = get_logger("zashterminal.history")
        if path is None:
            from ..utils.platform import get_config_directory

            path = Path(get_config_directory()) / "connection_history.json"
        self._path = Path(path)
        self._lock = threading.RLock()
        self._entries: List[HistoryEntry] = self._load()

    def _load(self) -> List[HistoryEntry]:
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as e:
            self.logger.warning(f"Could not read connection history: {e}")
            return []
        if not isinstance(data, list):
            return []
        entries: List[HistoryEntry] = []
        for item in data:
            try:
                entries.append(
                    HistoryEntry(
                        session_name=str(item["session_name"]),
                        host=str(item.get("host", "")),
                        kind=str(item.get("kind", "local")),
                        last_used=float(item.get("last_used", 0.0)),
                        count=max(1, int(item.get("count", 1))),
                    )
                )
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
        entries.sort(key=lambda e: e.last_used, reverse=True)
        return entries[:MAX_ENTRIES]

    def _save(self) -> None:
        tmp_name = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                dir=str(self._path.parent), prefix=".history-", suffix=".tmp"
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump([asdict(e) for e in self._entries], f, indent=2)
            os.replace(tmp_name, self._path)
            tmp_name = None
        except OSError as e:
            self.logger.error(f"Could not save connection history: {e}")
        finally:
            if tmp_name and os.path.exists(tmp_name):
                os.unlink(tmp_name)

    def record(self, session_name: str, host: str, kind: str) -> None:
        with self._lock:
            count = 1
            for i, e in enumerate(self._entries):
                if e.session_name == session_name:
                    count = e.count + 1
                    del self._entries[i]
                    break
            self._entries.insert(
                0, HistoryEntry(session_name, host, kind, time.time(), count)
            )
            del self._entries[MAX_ENTRIES:]
            self._save()

    def recent(self, limit: int = 50) -> List[HistoryEntry]:
        with self._lock:
            return [HistoryEntry(**asdict(e)) for e in self._entries[:limit]]

    def remove(self, session_name: str) -> None:
        with self._lock:
            before = len(self._entries)
            self._entries = [e for e in self._entries if e.session_name != session_name]
            if len(self._entries) != before:
                self._save()

    def clear(self) -> None:
        with self._lock:
            self._entries = []
            self._save()
