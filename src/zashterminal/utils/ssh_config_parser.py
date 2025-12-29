# zashterminal/utils/ssh_config_parser.py

import glob
import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

from .logger import get_logger


@dataclass(slots=True)
class SSHConfigHost:
    """Lightweight representation of a host entry inside ssh_config."""

    alias: str
    hostname: Optional[str] = None
    user: Optional[str] = None
    port: Optional[int] = None
    identity_file: Optional[str] = None
    forward_x11: Optional[bool] = None


class SSHConfigParser:
    """Simple parser for OpenSSH-style config files."""

    def __init__(self) -> None:
        self.logger = get_logger("zashterminal.utils.sshconfig")
        self._entries: List[SSHConfigHost] = []
        self._visited: Set[Path] = set()

    def parse(self, config_path: Path) -> List[SSHConfigHost]:
        """Parses the provided ssh_config file and returns host entries."""
        self._entries.clear()
        self._visited.clear()

        expanded_path = config_path.expanduser()
        self._parse_file(expanded_path)
        return self._entries

    # --- Internal helpers -------------------------------------------------

    def _parse_file(self, path: Path) -> None:
        try:
            resolved = path.resolve()
        except FileNotFoundError:
            self.logger.warning(f"SSH config path does not exist: {path}")
            return

        if resolved in self._visited:
            return
        if not resolved.is_file():
            self.logger.warning(f"SSH config path is not a file: {path}")
            return

        self._visited.add(resolved)
        directory = resolved.parent

        current_patterns: List[str] = []
        current_options: Dict[str, str] = {}

        with resolved.open("r", encoding="utf-8", errors="ignore") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue

                tokens = self._tokenize(line)
                if not tokens:
                    continue

                keyword = tokens[0].lower()
                values = tokens[1:]

                if keyword == "match":
                    # We do not support Match blocks; stop processing further.
                    self._flush_hosts(current_patterns, current_options)
                    current_patterns = []
                    current_options = {}
                    break
                elif keyword == "host":
                    self._flush_hosts(current_patterns, current_options)
                    current_patterns = values
                    current_options = {}
                elif keyword == "include":
                    self._flush_hosts(current_patterns, current_options)
                    current_patterns = []
                    current_options = {}
                    self._handle_include(values, directory)
                else:
                    if not current_patterns:
                        # Apply to the implicit global host; skip as we only care about concrete hosts.
                        continue
                    if values:
                        current_options[keyword] = " ".join(values)

        # Flush last host
        self._flush_hosts(current_patterns, current_options)

    def _handle_include(self, patterns: Iterable[str], base_dir: Path) -> None:
        for pattern in patterns:
            expanded = self._expand_path(pattern, base_dir)
            for match in glob.glob(str(expanded), recursive=True):
                self._parse_file(Path(match))

    def _flush_hosts(self, patterns: List[str], options: Dict[str, str]) -> None:
        if not patterns:
            return

        for alias in patterns:
            if not alias or any(ch in alias for ch in ["*", "?", "!"]):
                # Skip wildcard or negated hosts
                continue

            entry = SSHConfigHost(alias=alias)
            if hostname := options.get("hostname"):
                entry.hostname = hostname
            if user := options.get("user"):
                entry.user = user
            if port := options.get("port"):
                try:
                    entry.port = int(port)
                except ValueError:
                    self.logger.debug(
                        f"Invalid port '{port}' for host '{alias}' in ssh config."
                    )
            if identity := options.get("identityfile"):
                entry.identity_file = identity
            if forward := options.get("forwardx11"):
                entry.forward_x11 = forward.lower() in {"yes", "true", "on"}

            self._entries.append(entry)

    @staticmethod
    def _expand_path(path_str: str, base_dir: Path) -> Path:
        expanded = Path(os.path.expanduser(path_str))
        if not expanded.is_absolute():
            expanded = base_dir / expanded
        return expanded

    @staticmethod
    def _tokenize(line: str) -> List[str]:
        lexer = shlex.shlex(line, posix=True)
        lexer.commenters = "#"
        lexer.whitespace_split = True
        return list(lexer)
