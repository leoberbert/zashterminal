"""System metrics (CPU, memory, disk, uptime, latency) for local and SSH hosts.

Pure Python, no GTK imports. Collectors are blocking and meant to be driven by
``MetricsPoller`` from a worker thread.
"""

from __future__ import annotations

import os
import platform
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Protocol, Tuple

from .logger import get_logger
from .platform import _read_os_release, get_command_builder

if TYPE_CHECKING:
    from ..sessions.models import SessionItem

_logger = get_logger("zashterminal.system_metrics")

_MAX_BACKOFF_SECONDS = 15.0
_STOP_JOIN_TIMEOUT = 0.5

# Remote script sentinels and the CPU sampling window used by the script.
_BEGIN_SENTINEL = "@@ZM_BEGIN@@"
_END_SENTINEL = "@@ZM_END@@"
_REMOTE_SLEEP_SECONDS = 0.5
_REMOTE_TIMEOUT_SECONDS = 10.0
_MUX_CHECK_TIMEOUT_SECONDS = 3.0


@dataclass(frozen=True)
class SystemMetrics:
    os_name: str = ""
    kernel: str = ""
    hostname: str = ""
    cpu_percent: Optional[float] = None
    mem_used: Optional[int] = None
    mem_total: Optional[int] = None
    disk_percent: Optional[float] = None
    uptime_seconds: Optional[int] = None
    latency_ms: Optional[float] = None

    @property
    def mem_percent(self) -> Optional[float]:
        if self.mem_used is None or not self.mem_total:
            return None
        return max(0.0, min(100.0, self.mem_used * 100.0 / self.mem_total))


class MetricsUnavailable(Exception):
    """Raised when metrics cannot be collected (no mux socket, ssh failure, garbage)."""


class MetricsCollector(Protocol):
    def sample(self) -> SystemMetrics: ...


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def format_bytes(n: int) -> str:
    """Format a byte count using binary units, e.g. ``3.2 GiB``."""
    value = float(max(0, n))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024.0 or unit == "TiB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} TiB"  # pragma: no cover


def format_uptime(seconds: int) -> str:
    """Format seconds as ``12d 4h 27m`` (leading zero units are omitted)."""
    total_minutes = max(0, int(seconds)) // 60
    days, rem = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


# ---------------------------------------------------------------------------
# CPU helpers
# ---------------------------------------------------------------------------


def parse_cpu_times(stat_line: str) -> Optional[Tuple[int, int]]:
    """Parse the aggregate ``cpu`` line of /proc/stat into ``(idle, total)`` jiffies.

    ``total`` is user+nice+system+idle+iowait+irq+softirq+steal (guest time is
    already accounted inside user/nice); ``idle`` is idle+iowait.
    """
    parts = stat_line.split()
    if len(parts) < 5 or parts[0] != "cpu":
        return None
    try:
        values = [int(p) for p in parts[1:9]]
    except ValueError:
        return None
    if any(v < 0 for v in values):
        return None
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return idle, sum(values)


def cpu_percent_between(
    previous: Optional[Tuple[int, int]], current: Optional[Tuple[int, int]]
) -> Optional[float]:
    """CPU busy percentage between two ``(idle, total)`` snapshots."""
    if previous is None or current is None:
        return None
    delta_total = current[1] - previous[1]
    delta_idle = current[0] - previous[0]
    if delta_total <= 0 or delta_idle < 0:
        return None
    busy = (delta_total - delta_idle) * 100.0 / delta_total
    return max(0.0, min(100.0, busy))


# ---------------------------------------------------------------------------
# Local collector
# ---------------------------------------------------------------------------


def _read_text(path: str) -> Optional[str]:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _parse_meminfo(text: Optional[str]) -> Tuple[Optional[int], Optional[int]]:
    """Return ``(mem_used, mem_total)`` in bytes from /proc/meminfo text."""
    if not text:
        return None, None
    total = available = None
    for line in text.splitlines():
        key, _sep, rest = line.partition(":")
        if key not in ("MemTotal", "MemAvailable"):
            continue
        fields = rest.split()
        if not fields:
            continue
        try:
            kib = int(fields[0])
        except ValueError:
            continue
        if key == "MemTotal":
            total = kib * 1024
        else:
            available = kib * 1024
    if total is None:
        return None, None
    if available is None:
        return None, total
    return max(0, total - min(available, total)), total


class LocalMetricsCollector:
    """Collects metrics of the machine the application is running on."""

    def __init__(self) -> None:
        uname = platform.uname()
        self._kernel = uname.release
        self._hostname = uname.node
        self._os_name = _read_os_release().get("PRETTY_NAME", "") or uname.system
        self._prev_cpu: Optional[Tuple[int, int]] = None

    def sample(self) -> SystemMetrics:
        stat_text = _read_text("/proc/stat")
        current = parse_cpu_times(stat_text.splitlines()[0]) if stat_text else None
        cpu = cpu_percent_between(self._prev_cpu, current)
        if current is not None:
            self._prev_cpu = current

        mem_used, mem_total = _parse_meminfo(_read_text("/proc/meminfo"))

        disk_percent: Optional[float] = None
        try:
            st = os.statvfs("/")
            used = (st.f_blocks - st.f_bfree) * st.f_frsize
            usable = used + st.f_bavail * st.f_frsize
            if usable > 0:
                disk_percent = max(0.0, min(100.0, used * 100.0 / usable))
        except OSError:
            pass

        uptime: Optional[int] = None
        uptime_text = _read_text("/proc/uptime")
        if uptime_text:
            try:
                uptime = int(float(uptime_text.split()[0]))
            except (ValueError, IndexError):
                pass

        return SystemMetrics(
            os_name=self._os_name,
            kernel=self._kernel,
            hostname=self._hostname,
            cpu_percent=cpu,
            mem_used=mem_used,
            mem_total=mem_total,
            disk_percent=disk_percent,
            uptime_seconds=uptime,
            latency_ms=None,
        )


# ---------------------------------------------------------------------------
# Remote script + parser
# ---------------------------------------------------------------------------


def build_remote_script(sleep_seconds: str = "0.5") -> str:
    """POSIX ``sh`` script (dash/busybox safe) printing sentinel-delimited key=value lines.

    The script avoids bashisms and backslash pairs/single-quote escapes that
    would be mangled when the remote login shell is fish or csh-like.
    """
    return "\n".join(
        [
            "LC_ALL=C",
            "export LC_ALL",
            f"echo '{_BEGIN_SENTINEL}'",
            "printf 'os=%s\\n' \"$(sed -n 's/^PRETTY_NAME=//p' /etc/os-release 2>/dev/null | head -n 1)\"",
            "printf 'kernel=%s\\n' \"$(uname -r 2>/dev/null)\"",
            "printf 'host=%s\\n' \"$(hostname 2>/dev/null || cat /proc/sys/kernel/hostname 2>/dev/null)\"",
            "printf 'stat1=%s\\n' \"$(head -n 1 /proc/stat 2>/dev/null)\"",
            f"if sleep {sleep_seconds} 2>/dev/null; then s={sleep_seconds}; else sleep 1; s=1; fi",
            "printf 'stat2=%s\\n' \"$(head -n 1 /proc/stat 2>/dev/null)\"",
            "printf 'sleep=%s\\n' \"$s\"",
            "printf 'memtotal=%s\\n' \"$(awk '/^MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null)\"",
            "printf 'memavail=%s\\n' \"$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo 2>/dev/null)\"",
            "printf 'disk=%s\\n' \"$(df -Pk / 2>/dev/null | tail -n 1)\"",
            "printf 'uptime=%s\\n' \"$(awk '{print $1}' /proc/uptime 2>/dev/null)\"",
            f"echo '{_END_SENTINEL}'",
        ]
    )


def _extract_fields(text: str) -> Dict[str, str]:
    """Extract key=value lines between the sentinels; ignore surrounding noise."""
    if not text or _BEGIN_SENTINEL not in text:
        raise MetricsUnavailable("Remote metrics output not recognised")
    body = text.split(_BEGIN_SENTINEL, 1)[1].split(_END_SENTINEL, 1)[0]
    fields: Dict[str, str] = {}
    for line in body.splitlines():
        key, sep, value = line.partition("=")
        key = key.strip()
        if sep and key.isalnum():
            fields[key] = value.strip()
    if not fields:
        raise MetricsUnavailable("Remote metrics output is empty")
    return fields


def _to_int(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    try:
        number = int(float(value))
    except ValueError:
        return None
    return number if number >= 0 else None


def _parse_disk_percent(line: Optional[str]) -> Optional[float]:
    """Extract the ``Use%`` column from a ``df -Pk /`` data line."""
    if not line:
        return None
    for token in line.split():
        if token.endswith("%") and token[:-1].isdigit():
            return max(0.0, min(100.0, float(token[:-1])))
    return None


def _strip_quotes(value: str) -> str:
    return value.strip().strip('"').strip("'")


def _metrics_from_fields(fields: Dict[str, str]) -> SystemMetrics:
    mem_total_kib = _to_int(fields.get("memtotal"))
    mem_avail_kib = _to_int(fields.get("memavail"))
    mem_total = mem_total_kib * 1024 if mem_total_kib else None
    mem_used = None
    if mem_total is not None and mem_avail_kib is not None:
        mem_used = max(0, mem_total - min(mem_avail_kib * 1024, mem_total))

    return SystemMetrics(
        os_name=_strip_quotes(fields.get("os", "")),
        kernel=fields.get("kernel", ""),
        hostname=fields.get("host", ""),
        cpu_percent=cpu_percent_between(
            parse_cpu_times(fields.get("stat1", "")),
            parse_cpu_times(fields.get("stat2", "")),
        ),
        mem_used=mem_used,
        mem_total=mem_total,
        disk_percent=_parse_disk_percent(fields.get("disk")),
        uptime_seconds=_to_int(fields.get("uptime")),
        latency_ms=None,
    )


def parse_remote_output(text: str) -> SystemMetrics:
    """Parse the output of ``build_remote_script``.

    Missing fields become ``None``/empty; output without the begin sentinel or
    without any key=value line raises ``MetricsUnavailable``.
    """
    return _metrics_from_fields(_extract_fields(text))


# ---------------------------------------------------------------------------
# Remote collector
# ---------------------------------------------------------------------------


class RemoteMetricsCollector:
    """Collects metrics of an SSH host over the app's existing ControlMaster socket.

    Never creates a new master connection and never prompts: when the mux socket
    is absent or no longer answers ``ssh -O check`` a ``MetricsUnavailable`` is
    raised.

    ``latency_ms`` is the wall-clock time of the whole ssh invocation minus the
    time the script itself spent sleeping for the CPU sample window, i.e. the
    mux round trip plus negligible remote command overhead.
    """

    def __init__(self, session: "SessionItem", control_path: str) -> None:
        self._session = session
        self._control_path = control_path

    def _ssh_options(self, connect_timeout: int = 5) -> Dict[str, str]:
        options = {
            "ConnectTimeout": str(connect_timeout),
            "ControlMaster": "no",
            "ControlPath": self._control_path,
            "BatchMode": "yes",
            "ServerAliveInterval": "5",
            "ServerAliveCountMax": "2",
        }
        proxy_jump = (getattr(self._session, "proxy_jump", "") or "").strip()
        if proxy_jump:
            options["ProxyJump"] = proxy_jump
        return options

    def _build_argv(self, script: str) -> List[str]:
        session = self._session
        try:
            argv = get_command_builder().build_remote_command(
                "ssh",
                hostname=session.host,
                port=session.port if session.port != 22 else None,
                username=session.user if session.user else None,
                key_file=session.auth_value if session.uses_key_auth() else None,
                options=self._ssh_options(),
            )
        except Exception as exc:
            raise MetricsUnavailable(f"ssh unavailable: {exc}") from exc
        argv.append(f"sh -c {shlex.quote(script)}")
        return argv

    def _check_mux(self) -> None:
        try:
            is_socket = Path(self._control_path).is_socket()
        except OSError:
            is_socket = False
        if not is_socket:
            raise MetricsUnavailable("SSH connection is not active")
        target = self._session.host
        if self._session.user:
            target = f"{self._session.user}@{target}"
        try:
            result = subprocess.run(
                ["ssh", "-O", "check", "-S", self._control_path, target],
                capture_output=True,
                timeout=_MUX_CHECK_TIMEOUT_SECONDS,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise MetricsUnavailable(f"SSH connection check failed: {exc}") from exc
        if result.returncode != 0:
            raise MetricsUnavailable("SSH connection is not active")

    def sample(self) -> SystemMetrics:
        self._check_mux()
        argv = self._build_argv(build_remote_script(str(_REMOTE_SLEEP_SECONDS)))
        started = time.monotonic()
        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=_REMOTE_TIMEOUT_SECONDS,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as exc:
            raise MetricsUnavailable("Remote metrics timed out") from exc
        except OSError as exc:
            raise MetricsUnavailable(f"Failed to run ssh: {exc}") from exc
        elapsed = time.monotonic() - started

        if result.returncode != 0:
            detail = (result.stderr or "").strip().splitlines()
            raise MetricsUnavailable(
                detail[-1] if detail else f"ssh exited with status {result.returncode}"
            )

        fields = _extract_fields(result.stdout)
        metrics = _metrics_from_fields(fields)
        try:
            slept = float(fields.get("sleep", _REMOTE_SLEEP_SECONDS))
        except ValueError:
            slept = _REMOTE_SLEEP_SECONDS
        latency_ms = max(0.0, (elapsed - slept) * 1000.0)
        return replace(metrics, latency_ms=latency_ms)


# ---------------------------------------------------------------------------
# Poller
# ---------------------------------------------------------------------------


class MetricsPoller:
    """Polls a collector from a daemon thread.

    Callbacks run on the worker thread; consumers must marshal to the UI thread
    (e.g. with ``GLib.idle_add``).
    """

    def __init__(self, collector: MetricsCollector, interval: float = 3.0) -> None:
        self._collector = collector
        self._interval = max(0.01, float(interval))
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def start(
        self,
        on_sample: Callable[[SystemMetrics], None],
        on_error: Callable[[str], None],
    ) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event = threading.Event()
            self._thread = threading.Thread(
                target=self._run,
                args=(self._stop_event, on_sample, on_error),
                name="metrics-poller",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            thread, self._thread = self._thread, None
            self._stop_event.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=_STOP_JOIN_TIMEOUT)

    def _run(
        self,
        stop: threading.Event,
        on_sample: Callable[[SystemMetrics], None],
        on_error: Callable[[str], None],
    ) -> None:
        failures = 0
        last_error: Optional[str] = None
        max_wait = max(_MAX_BACKOFF_SECONDS, self._interval)
        while not stop.is_set():
            try:
                metrics = self._collector.sample()
            except Exception as exc:
                message = str(exc) or type(exc).__name__
                failures += 1
                wait = min(self._interval * (2**failures), max_wait)
                if message != last_error:
                    last_error = message
                    if not stop.is_set():
                        self._deliver(on_error, message)
            else:
                failures = 0
                last_error = None
                wait = self._interval
                if not stop.is_set():
                    self._deliver(on_sample, metrics)
            if stop.wait(wait):
                break

    @staticmethod
    def _deliver(callback: Callable, argument) -> None:
        try:
            callback(argument)
        except Exception:
            _logger.exception("Metrics callback failed")
