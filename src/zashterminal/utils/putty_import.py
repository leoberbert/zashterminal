"""Parse PuTTY session exports (.reg / portable .ini) into SSH session dicts.

Ported from ZashCRT (Windows) import logic — Linux uses file exports only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import unquote


_REG_SECTION = re.compile(
    r"^\[HKEY_CURRENT_USER\\Software\\SimonTatham\\PuTTY\\Sessions\\(.+)\]$",
    re.IGNORECASE,
)
_INI_SECTION = re.compile(r"^\[(.+)\]$")
_STRING_VALUE = re.compile(r'^"([^"]+)"="(.*)"$')
_DWORD_VALUE = re.compile(r'^"([^"]+)"=dword:([0-9A-Fa-f]+)$', re.IGNORECASE)


@dataclass
class PuttyImportedSession:
    name: str
    folder_path: str
    host: str
    user: str = ""
    port: int = 22
    auth_type: str = "password"
    auth_value: str = ""
    post_login_command: str = ""
    port_forwardings: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def decode_putty_session_name(value: str) -> str:
    decoded = unquote(value.replace("\\", "/")).strip()
    return decoded or "Imported PuTTY Session"


def normalize_putty_path(value: str) -> str:
    text = (value or "").strip()
    while len(text) >= 2 and (
        (text[0] == text[-1] == '"') or (text[0] == text[-1] == "'")
    ):
        text = text[1:-1].strip()
    return text.replace("\\", "/")


def unescape_reg_string(value: str) -> str:
    return (
        value.replace("\\\\", "\\")
        .replace('\\"', '"')
        .replace("\\r", "\r")
        .replace("\\n", "\n")
        .replace("\\t", "\t")
    )


def split_putty_name_group(
    name: str, *, split_folders: bool
) -> Tuple[str, str]:
    normalized = name.replace("\\", "/").strip()
    if not split_folders or "/" not in normalized:
        return (normalized or "Imported PuTTY Session", "")
    leaf = normalized.rsplit("/", 1)[-1].strip()
    group = normalized.rsplit("/", 1)[0].strip().strip("/")
    folder = f"/{group}" if group else ""
    return (leaf or "Imported PuTTY Session", folder)


def _putty_int(values: Dict[str, Any], key: str, fallback: int = 0) -> int:
    raw = values.get(key, fallback)
    if isinstance(raw, int):
        return raw
    text = str(raw).strip()
    try:
        if text.lower().startswith("0x"):
            return int(text, 16)
        return int(text, 0)
    except ValueError:
        return fallback


def _putty_str(values: Dict[str, Any], key: str, fallback: str = "") -> str:
    raw = values.get(key, fallback)
    return str(raw) if raw is not None else fallback


def putty_tunnels_to_forwardings(
    value: str,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Convert PuTTY PortForwardings (local L only) to SessionItem dicts."""
    warnings: List[str] = []
    tunnels: List[Dict[str, Any]] = []
    for item in re.split(r"[\s,;]+", (value or "").strip()):
        if not item:
            continue
        kind = item[0].upper()
        body = item[1:]
        if kind != "L":
            warnings.append(
                f"Unsupported PuTTY tunnel '{item}' was skipped."
            )
            continue
        equal_pos = body.find("=")
        if equal_pos <= 0:
            warnings.append(f"Malformed PuTTY tunnel '{item}' was skipped.")
            continue
        left, right = body[:equal_pos], body[equal_pos + 1 :]
        remote_pos = right.rfind(":")
        if remote_pos <= 0 or remote_pos >= len(right) - 1:
            warnings.append(f"Malformed PuTTY tunnel '{item}' was skipped.")
            continue
        remote_host = right[:remote_pos]
        try:
            remote_port = int(right[remote_pos + 1 :])
        except ValueError:
            warnings.append(f"Malformed PuTTY tunnel '{item}' was skipped.")
            continue
        local_pos = left.rfind(":")
        if local_pos > 0 and local_pos < len(left) - 1:
            local_host = left[:local_pos]
            try:
                local_port = int(left[local_pos + 1 :])
            except ValueError:
                warnings.append(f"Malformed PuTTY tunnel '{item}' was skipped.")
                continue
        else:
            local_host = "localhost"
            try:
                local_port = int(left)
            except ValueError:
                warnings.append(f"Malformed PuTTY tunnel '{item}' was skipped.")
                continue
        if not (1024 < local_port <= 65535) or not (1 <= remote_port <= 65535):
            warnings.append(
                f"PuTTY tunnel '{item}' skipped (ports outside supported range)."
            )
            continue
        tunnels.append(
            {
                "name": f"L{local_port}",
                "local_host": local_host or "localhost",
                "local_port": local_port,
                "remote_host": remote_host,
                "remote_port": remote_port,
            }
        )
    return tunnels, warnings


def convert_putty_session(
    source_name: str,
    values: Dict[str, Any],
    *,
    split_folders: bool = True,
) -> Optional[PuttyImportedSession]:
    """Map one PuTTY session. Returns None for non-SSH / invalid hosts."""
    protocol = _putty_str(values, "Protocol", "ssh").strip().lower()
    name, folder_path = split_putty_name_group(source_name, split_folders=split_folders)
    host = _putty_str(values, "HostName").strip()
    warnings: List[str] = []

    if protocol not in {"ssh", "ssh2"}:
        warnings.append(
            f"Unsupported protocol '{protocol}' in PuTTY session '{source_name}'."
        )
        return None

    user = _putty_str(values, "UserName").strip()
    if not user and "@" in host:
        user, host = host.split("@", 1)

    private_key = normalize_putty_path(_putty_str(values, "PublicKeyFile"))
    password = _putty_str(values, "Password").strip() if not private_key else ""
    port = _putty_int(values, "PortNumber", 22) or 22
    remote_command = _putty_str(values, "RemoteCommand").strip()
    tunnels, tunnel_warnings = putty_tunnels_to_forwardings(
        _putty_str(values, "PortForwardings")
    )
    warnings.extend(tunnel_warnings)
    if private_key.lower().endswith(".ppk"):
        warnings.append(
            f"Session '{name}' uses a PuTTY .ppk key; convert it to OpenSSH format."
        )

    if not host:
        return None

    auth_type = "key" if private_key else ("password" if password else "")
    auth_value = private_key if private_key else password

    return PuttyImportedSession(
        name=name,
        folder_path=folder_path,
        host=host,
        user=user,
        port=port,
        auth_type=auth_type,
        auth_value=auth_value,
        post_login_command=remote_command,
        port_forwardings=tunnels,
        warnings=warnings,
    )


def parse_putty_reg_or_ini(path: Path) -> List[Tuple[str, Dict[str, Any]]]:
    """Parse a PuTTY .reg export or portable .ini into (name, values) pairs."""
    text = path.read_text(encoding="utf-8", errors="ignore")
    sessions: List[Tuple[str, Dict[str, Any]]] = []
    current_name = ""
    current_values: Dict[str, Any] = {}

    def flush() -> None:
        nonlocal current_name, current_values
        if current_name:
            sessions.append((decode_putty_session_name(current_name), current_values))
        current_name = ""
        current_values = {}

    for raw in text.splitlines():
        line = raw.strip().lstrip("\ufeff")
        if not line:
            continue
        reg_match = _REG_SECTION.match(line)
        if reg_match:
            flush()
            current_name = reg_match.group(1)
            continue
        ini_match = _INI_SECTION.match(line)
        if ini_match:
            flush()
            section = ini_match.group(1).replace("\\", "/")
            marker = section.lower().find("/sessions/")
            current_name = (
                section[marker + len("/sessions/") :].strip("/")
                if marker >= 0
                else ""
            )
            continue
        if not current_name:
            continue
        dword_match = _DWORD_VALUE.match(line)
        if dword_match:
            current_values[dword_match.group(1)] = int(dword_match.group(2), 16)
            continue
        string_match = _STRING_VALUE.match(line)
        if string_match:
            current_values[string_match.group(1)] = unescape_reg_string(
                string_match.group(2)
            )
            continue
        equal_pos = line.find("=")
        if equal_pos > 0:
            key = line[:equal_pos].strip().strip('"')
            value = line[equal_pos + 1 :].strip()
            if len(value) >= 2 and value[0] == value[-1] == '"':
                value = value[1:-1]
            current_values[key] = unescape_reg_string(value)

    flush()
    return sessions


def import_putty_sessions_from_file(
    path: Path, *, split_folders: bool = True
) -> Tuple[List[PuttyImportedSession], List[str]]:
    """High-level: parse file and convert SSH sessions."""
    source = parse_putty_reg_or_ini(path)
    imported: List[PuttyImportedSession] = []
    warnings: List[str] = []
    for name, values in source:
        entry = convert_putty_session(name, values, split_folders=split_folders)
        if entry is None:
            protocol = _putty_str(values, "Protocol", "ssh").strip().lower()
            if protocol and protocol not in {"ssh", "ssh2"}:
                warnings.append(
                    f"Skipped PuTTY session '{name}' (protocol '{protocol}')."
                )
            elif not _putty_str(values, "HostName").strip():
                warnings.append(f"Skipped PuTTY session '{name}' (missing HostName).")
            continue
        warnings.extend(entry.warnings)
        imported.append(entry)
    return imported, warnings
