"""Normalize and validate OpenSSH ProxyJump values for sessions."""

from __future__ import annotations

from typing import List, Optional, Tuple


def normalize_proxy_jump(value: Optional[str]) -> str:
    """Return a cleaned ProxyJump string (comma-separated hops) or empty."""
    if not value:
        return ""
    hops: List[str] = []
    for raw in str(value).split(","):
        hop = raw.strip()
        if hop:
            hops.append(hop)
    return ",".join(hops)


def validate_proxy_jump(value: Optional[str]) -> Tuple[bool, str]:
    """Validate a ProxyJump string.

    Returns (ok, error_message). Empty is valid (no jump).
    """
    normalized = normalize_proxy_jump(value)
    if not normalized:
        return True, ""

    for hop in normalized.split(","):
        # Reject whitespace-only remnants and obvious junk.
        if not hop or any(ch.isspace() for ch in hop):
            return False, f"Invalid ProxyJump hop: {hop!r}"
        # Must contain a host-like token (after optional user@).
        host_part = hop.rsplit("@", 1)[-1]
        # Optional :port
        if host_part.count(":") > 1:
            # IPv6 in brackets is rare in ProxyJump UI; keep simple.
            if not (host_part.startswith("[") and "]" in host_part):
                return False, f"Invalid ProxyJump hop host: {hop!r}"
        host_only = host_part.split(":")[0].strip("[]")
        if not host_only:
            return False, f"Invalid ProxyJump hop host: {hop!r}"
    return True, ""
