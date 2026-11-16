"""Run the local shell on the host when Zashterminal lives in a Distrobox.

Inside a Distrobox container the terminal would otherwise open the container's
shell. ``distrobox-host-exec`` (host-spawn) runs a command on the host while the
PTY stays attached to the VTE widget, so the user gets their real host shell.

Opt out with ``ZASHTERMINAL_SHELL_IN_CONTAINER=1``.
"""

import os
import shutil
import subprocess
import tempfile
import threading
from typing import Dict, List, Optional

from .logger import get_logger

HOST_EXEC = "distrobox-host-exec"
OPT_OUT_ENV = "ZASHTERMINAL_SHELL_IN_CONTAINER"
# The host-side shell does not inherit our environment: only these cross over.
FORWARDED_VARS = ("TERM", "COLORTERM", "ZDOTDIR", "VTE_VERSION")
FORWARDED_PREFIX = "ZASHTERMINAL_"
_PROBE_TIMEOUT = 10

_logger = get_logger("zashterminal.host_shell")
_lock = threading.Lock()
_enabled: Optional[bool] = None
_host_shell: Optional[str] = None


def in_distrobox() -> bool:
    return bool(os.environ.get("CONTAINER_ID")) and os.path.exists("/run/.containerenv")


def _run_on_host(args: List[str]) -> Optional[str]:
    try:
        result = subprocess.run(
            [HOST_EXEC, *args],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT,
            stdin=subprocess.DEVNULL,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as error:
        _logger.warning(f"Host probe {args} failed: {error}")
        return None
    return result.stdout.strip()


def _tmp_is_shared() -> bool:
    """The generated rcfile lives in /tmp; the host shell must be able to read it."""
    with tempfile.NamedTemporaryFile(prefix="zashterminal_probe_") as probe:
        output = _run_on_host(["sh", "-c", 'test -e "$1" && echo yes', "sh", probe.name])
    return output == "yes"


def host_shell_enabled() -> bool:
    """True when local shells should be spawned on the host (cached, probed once)."""
    global _enabled
    with _lock:
        if _enabled is None:
            _enabled = (
                in_distrobox()
                and not os.environ.get(OPT_OUT_ENV)
                and shutil.which(HOST_EXEC) is not None
                and _tmp_is_shared()
            )
            _logger.info(f"Distrobox host shell: {'enabled' if _enabled else 'disabled'}")
        return _enabled


def host_user_shell() -> str:
    """Login shell of the host account, not the container's passwd entry."""
    global _host_shell
    with _lock:
        if _host_shell is None:
            output = _run_on_host(
                ["sh", "-c", 'getent passwd "$(id -un)" | cut -d: -f7']
            )
            _host_shell = output if output and output.startswith("/") else "/bin/sh"
        return _host_shell


def wrap_command(cmd: List[str], env: Dict[str, str]) -> List[str]:
    """Prefix ``cmd`` so it runs on the host with the forwarded environment."""
    forwarded = [
        f"{key}={value}"
        for key, value in env.items()
        if key in FORWARDED_VARS or key.startswith(FORWARDED_PREFIX)
    ]
    return [HOST_EXEC, "env", *forwarded, *cmd]
