"""System metrics (CPU, memory, disk, uptime, latency) for local and SSH hosts.

Pure Python, no GTK imports. Collectors are blocking and meant to be driven by
``MetricsPoller`` from a worker thread.
"""

from __future__ import annotations

import os
import platform
import re
import shlex
import subprocess
import threading
import time
import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Callable,
    Dict,
    List,
    NamedTuple,
    Optional,
    Protocol,
    Tuple,
)

from .logger import get_logger
from .platform import _read_os_release, get_command_builder

if TYPE_CHECKING:
    from ..sessions.models import SessionItem

_logger = get_logger("zashterminal.system_metrics")

_MAX_BACKOFF_SECONDS = 15.0
_STOP_JOIN_TIMEOUT = 0.5
# While the SSH master is still coming up (login, password prompt) retry fast
# instead of backing off, so the bar fills as soon as the connection is ready.
_STARTUP_RETRY_SECONDS = 0.5
_STARTUP_RETRY_ATTEMPTS = 120
# Short CPU sampling window for the very first sample of a collector.
_FIRST_SAMPLE_SECONDS = 0.2

# Remote script sentinels and the CPU sampling window used by the script.
_BEGIN_SENTINEL = "@@ZM_BEGIN@@"
_END_SENTINEL = "@@ZM_END@@"
_REMOTE_SLEEP_SECONDS = 0.5
_REMOTE_TIMEOUT_SECONDS = 10.0
_MUX_CHECK_TIMEOUT_SECONDS = 3.0


class DiskInfo(NamedTuple):
    mount: str
    percent: float
    size: int  # bytes


class ProcInfo(NamedTuple):
    name: str
    cpu: float  # percent (ps: average since the process started)
    mem: float  # percent of RAM


class GpuInfo(NamedTuple):
    name: str
    util: Optional[float]
    mem_used: Optional[int]  # bytes
    mem_total: Optional[int]  # bytes
    temp: Optional[float]


@dataclass(frozen=True)
class SystemMetrics:
    os_name: str = ""
    kernel: str = ""
    hostname: str = ""
    cpu_percent: Optional[float] = None
    mem_used: Optional[int] = None
    mem_total: Optional[int] = None
    disk_percent: Optional[float] = None
    disk_mount: str = ""
    uptime_seconds: Optional[int] = None
    latency_ms: Optional[float] = None
    # -- extended metrics (all optional: a host may not expose them) ----------
    arch: str = ""
    cpu_model: str = ""
    cores: Optional[int] = None
    load: Optional[Tuple[float, float, float]] = None
    swap_used: Optional[int] = None
    swap_total: Optional[int] = None
    core_percents: Tuple[float, ...] = ()
    net_rx_bps: Optional[float] = None
    net_tx_bps: Optional[float] = None
    disks: Tuple[DiskInfo, ...] = ()
    procs_total: Optional[int] = None
    top_cpu: Tuple[ProcInfo, ...] = ()
    top_mem: Tuple[ProcInfo, ...] = ()
    temp_c: Optional[float] = None
    gpu: Optional[GpuInfo] = None

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
# Extended metric parsers (shared by the local and the remote collector)
#
# Remote output joins lines with ';' so every value fits one key=value line;
# local collectors feed raw multi-line text, hence the [;\n] splitting.
# ---------------------------------------------------------------------------

_CHUNK_SPLIT = re.compile(r"[;\n]")
_NOISE_PROCESSES = frozenset({"ps", "sort", "head", "tr", "grep", "awk", "sleep", "sh"})
_MIB = 1024 * 1024


def _chunks(text: Optional[str]) -> List[str]:
    return [c.strip() for c in _CHUNK_SPLIT.split(text or "") if c.strip()]


def _to_float(value: Optional[str]) -> Optional[float]:
    try:
        return float((value or "").strip())
    except ValueError:
        return None


def parse_core_times(text: Optional[str]) -> List[Tuple[int, int]]:
    """Per-core ``(idle, total)`` pairs from ``cpuN`` lines of /proc/stat."""
    cores: List[Tuple[int, int]] = []
    for chunk in _chunks(text):
        parts = chunk.split()
        if len(parts) < 5 or not parts[0].startswith("cpu") or not parts[0][3:].isdigit():
            continue
        pair = parse_cpu_times("cpu " + " ".join(parts[1:]))
        if pair is not None:
            cores.append(pair)
    return cores


def core_percents_between(
    previous: List[Tuple[int, int]], current: List[Tuple[int, int]]
) -> Tuple[float, ...]:
    if not previous or len(previous) != len(current):
        return ()
    out = []
    for before, after in zip(previous, current):
        percent = cpu_percent_between(before, after)
        out.append(percent if percent is not None else 0.0)
    return tuple(out)


def parse_loadavg(text: Optional[str]) -> Optional[Tuple[float, float, float]]:
    parts = (text or "").split()
    try:
        return float(parts[0]), float(parts[1]), float(parts[2])
    except (ValueError, IndexError):
        return None


def parse_net_dev(text: Optional[str]) -> Optional[Tuple[int, int]]:
    """Total ``(rx_bytes, tx_bytes)`` of the physical-looking interfaces."""
    rx = tx = 0
    seen = False
    for chunk in _chunks(text):
        iface, sep, rest = chunk.partition(":")
        iface = iface.strip()
        if not sep or iface == "lo" or iface.startswith(("docker", "veth", "br-")):
            continue
        parts = rest.split()
        if len(parts) < 9:
            continue
        try:
            rx += int(parts[0])
            tx += int(parts[8])
        except ValueError:
            continue
        seen = True
    return (rx, tx) if seen else None


def net_rate(
    previous: Optional[Tuple[int, int]],
    current: Optional[Tuple[int, int]],
    seconds: float,
) -> Tuple[Optional[float], Optional[float]]:
    if previous is None or current is None or seconds <= 0:
        return None, None
    return (
        max(0.0, (current[0] - previous[0]) / seconds),
        max(0.0, (current[1] - previous[1]) / seconds),
    )


def parse_df_disks(text: Optional[str]) -> Tuple[DiskInfo, ...]:
    """Block-device filesystems from ``df -Pk`` output (loop/snap filtered)."""
    seen = set()
    disks: List[DiskInfo] = []
    for chunk in _chunks(text):
        parts = chunk.split(None, 5)
        if len(parts) < 6:
            continue
        device, mount = parts[0], parts[5]
        if not device.startswith("/dev/") or device.startswith("/dev/loop"):
            continue
        if device in seen or mount.startswith(("/snap", "/var/snap")):
            continue
        try:
            percent = float(parts[4].rstrip("%"))
            size = int(parts[1]) * 1024
        except ValueError:
            continue
        seen.add(device)
        disks.append(DiskInfo(mount, max(0.0, min(100.0, percent)), size))
    return tuple(disks)


# Remote one-liner: "pid|comm|utime+stime ticks|rss pages;" for every process.
# ``comm`` sits in parentheses and may itself contain spaces/parentheses, so the
# LAST ")" closes it; the fields after it start at the process state.
_PROC_AWK = (
    "awk '{ s=$0; a=index(s,\"(\"); b=0; "
    "for(i=length(s);i>0;i--) if(substr(s,i,1)==\")\"){b=i;break} "
    "if(!b) next; n=split(substr(s,b+2),f,\" \"); "
    "printf \"%s|%s|%d|%d;\", $1, substr(s,a+1,b-a-1), f[12]+f[13], f[22] }' "
    "/proc/[0-9]*/stat 2>/dev/null"
)

# pid -> (name, cpu ticks, resident pages)
ProcSnapshot = Dict[int, Tuple[str, int, int]]


def parse_proc_snapshot(text: Optional[str]) -> ProcSnapshot:
    """Parse the ``pid|comm|ticks|rss;`` records printed by ``_PROC_AWK``."""
    snapshot: ProcSnapshot = {}
    for chunk in _chunks(text):
        pid_text, _sep, rest = chunk.partition("|")
        head, _sep, rss_text = rest.rpartition("|")
        name, _sep, ticks_text = head.rpartition("|")
        try:
            snapshot[int(pid_text)] = (name, int(ticks_text), int(rss_text))
        except ValueError:
            continue
    return snapshot


def top_processes(
    before: ProcSnapshot,
    after: ProcSnapshot,
    seconds: float,
    clk_tck: int,
    page_size: int,
    mem_total: Optional[int],
    limit: int = 5,
) -> Tuple[int, Tuple[ProcInfo, ...], Tuple[ProcInfo, ...]]:
    """``(process count, top by CPU, top by memory)``.

    CPU is the *current* usage like ``top``: CPU ticks used between the two
    snapshots over the elapsed time, per core (a busy 4-thread process can
    exceed 100%). Processes new in ``after`` count from zero.
    """
    window = clk_tck * seconds
    rows: List[ProcInfo] = []
    for pid, (name, ticks, rss) in after.items():
        if name in _NOISE_PROCESSES:
            continue
        previous = before.get(pid)
        # Match by pid only: kernel threads rename themselves (kworker/4:1 ->
        # kworker/4:1-mm_percpu_wq). Lower ticks than before means pid reuse.
        used = ticks - previous[1] if previous and ticks >= previous[1] else ticks
        cpu = max(0, used) * 100.0 / window if window > 0 else 0.0
        mem = rss * page_size * 100.0 / mem_total if mem_total else 0.0
        rows.append(ProcInfo(name, cpu, mem))
    by_cpu = tuple(sorted(rows, key=lambda p: p.cpu, reverse=True)[:limit])
    by_mem = tuple(sorted(rows, key=lambda p: p.mem, reverse=True)[:limit])
    return len(after), by_cpu, by_mem


def _remote_process_fields(
    fields: Dict[str, str], seconds: float, mem_total: Optional[int]
) -> Dict[str, object]:
    before = parse_proc_snapshot(fields.get("proc1"))
    after = parse_proc_snapshot(fields.get("proc2"))
    if not after:
        return {}
    total, by_cpu, by_mem = top_processes(
        before,
        after,
        seconds,
        _to_int(fields.get("clktck")) or 100,
        _to_int(fields.get("pagesize")) or 4096,
        mem_total,
    )
    return {"procs_total": total, "top_cpu": by_cpu, "top_mem": by_mem}


def parse_gpu(text: Optional[str]) -> Optional[GpuInfo]:
    """First line of ``nvidia-smi --query-gpu=name,utilization.gpu,memory.used,
    memory.total,temperature.gpu --format=csv,noheader,nounits``."""
    line = next(iter(_chunks(text)), "")
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 5 or not parts[0]:
        return None
    used, total = _to_float(parts[2]), _to_float(parts[3])
    return GpuInfo(
        name=parts[0],
        util=_to_float(parts[1]),
        mem_used=int(used * _MIB) if used is not None else None,
        mem_total=int(total * _MIB) if total is not None else None,
        temp=_to_float(parts[4]),
    )


def parse_temperature(text: Optional[str]) -> Optional[float]:
    """Hottest value of thermal-zone files (millidegrees Celsius)."""
    best: Optional[float] = None
    for chunk in _chunks(text):
        value = _to_float(chunk)
        if value is None:
            continue
        celsius = value / 1000.0 if value > 1000 else value
        if 0.0 < celsius < 150.0 and (best is None or celsius > best):
            best = celsius
    return best


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


def _read_cpu_info() -> Tuple[str, Optional[int]]:
    """``(model name, logical cores)`` from /proc/cpuinfo."""
    model = ""
    cores = 0
    for line in (_read_text("/proc/cpuinfo") or "").splitlines():
        key, _sep, value = line.partition(":")
        key = key.strip()
        if key == "processor":
            cores += 1
        elif key in ("model name", "Model", "Hardware") and not model:
            model = value.strip()
    return model, (cores or os.cpu_count())


def _parse_swap(text: Optional[str]) -> Tuple[Optional[int], Optional[int]]:
    """``(swap_used, swap_total)`` in bytes from /proc/meminfo text."""
    total = free = None
    for line in (text or "").splitlines():
        key, _sep, rest = line.partition(":")
        if key in ("SwapTotal", "SwapFree") and rest.split():
            try:
                kib = int(rest.split()[0])
            except ValueError:
                continue
            if key == "SwapTotal":
                total = kib * 1024
            else:
                free = kib * 1024
    if not total or free is None:
        return None, total
    return max(0, total - free), total


def _local_disks() -> Tuple[DiskInfo, ...]:
    """Mounted block-device filesystems with usage (like ``df``)."""
    lines = []
    for line in (_read_text("/proc/mounts") or "").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            lines.append((parts[0], parts[1].replace("\\040", " ")))
    seen = set()
    disks: List[DiskInfo] = []
    for device, mount in lines:
        if not device.startswith("/dev/") or device.startswith("/dev/loop"):
            continue
        if device in seen or mount.startswith(("/snap", "/var/snap")):
            continue
        try:
            st = os.statvfs(mount)
        except OSError:
            continue
        used = (st.f_blocks - st.f_bfree) * st.f_frsize
        usable = used + st.f_bavail * st.f_frsize
        if usable <= 0:
            continue
        seen.add(device)
        disks.append(
            DiskInfo(mount, max(0.0, min(100.0, used * 100.0 / usable)), st.f_blocks * st.f_frsize)
        )
    return tuple(disks)


def _local_temperature() -> Optional[float]:
    values = []
    for zone in sorted(Path("/sys/class/thermal").glob("thermal_zone*/temp")):
        text = _read_text(str(zone))
        if text:
            values.append(text.strip())
    return parse_temperature(";".join(values))


def _read_proc_snapshot() -> ProcSnapshot:
    """Local equivalent of ``_PROC_AWK``."""
    snapshot: ProcSnapshot = {}
    try:
        names = os.listdir("/proc")
    except OSError:
        return snapshot
    for entry in names:
        if not entry.isdigit():
            continue
        text = _read_text(f"/proc/{entry}/stat")
        if not text:
            continue
        start, end = text.find("("), text.rfind(")")
        if start < 0 or end < start:
            continue
        fields = text[end + 2 :].split()
        try:
            snapshot[int(entry)] = (
                text[start + 1 : end],
                int(fields[11]) + int(fields[12]),
                int(fields[21]),
            )
        except (ValueError, IndexError):
            continue
    return snapshot


def _local_gpu(nvidia_smi: str) -> Optional[GpuInfo]:
    try:
        out = subprocess.run(
            [
                nvidia_smi,
                "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=3,
            stdin=subprocess.DEVNULL,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_gpu(out)


class LocalMetricsCollector:
    """Collects metrics of the machine the application is running on."""

    def __init__(self) -> None:
        uname = platform.uname()
        self._kernel = uname.release
        self._hostname = uname.node
        self._arch = uname.machine
        self._os_name = _read_os_release().get("PRETTY_NAME", "") or uname.system
        self._cpu_model, self._cores = _read_cpu_info()
        self._nvidia_smi = shutil.which("nvidia-smi")
        self._prev_cpu: Optional[Tuple[int, int]] = None
        self._prev_cores: List[Tuple[int, int]] = []
        self._prev_net: Optional[Tuple[int, int]] = None
        self._prev_net_time = 0.0
        self._prev_procs: Optional[ProcSnapshot] = None
        self._prev_procs_time = 0.0
        # Directory whose filesystem is reported; the UI follows the shell's cwd.
        self.disk_path = "/"
        # Processes and GPU cost extra subprocesses: only when a view shows them.
        self.detail = False

    def _sample_processes(
        self, mem_total: Optional[int]
    ) -> Tuple[int, Tuple[ProcInfo, ...], Tuple[ProcInfo, ...]]:
        """Top processes by *current* CPU (delta since the previous sample)."""
        now = time.monotonic()
        snapshot = _read_proc_snapshot()
        if self._prev_procs is None:
            # First detail sample: short baseline so CPU is not empty.
            before, started = snapshot, now
            time.sleep(_FIRST_SAMPLE_SECONDS)
            snapshot = _read_proc_snapshot()
            now = time.monotonic()
        else:
            before, started = self._prev_procs, self._prev_procs_time
        self._prev_procs, self._prev_procs_time = snapshot, now
        return top_processes(
            before,
            snapshot,
            now - started,
            os.sysconf("SC_CLK_TCK"),
            os.sysconf("SC_PAGE_SIZE"),
            mem_total,
        )

    def sample(self) -> SystemMetrics:
        stat_text = _read_text("/proc/stat") or ""
        current = parse_cpu_times(stat_text.splitlines()[0]) if stat_text else None
        if self._prev_cpu is None and current is not None:
            # First sample: take a short baseline so CPU and network rates
            # show immediately.
            self._prev_net = parse_net_dev(_read_text("/proc/net/dev"))
            self._prev_net_time = time.monotonic()
            self._prev_cores = parse_core_times(stat_text)
            time.sleep(_FIRST_SAMPLE_SECONDS)
            self._prev_cpu = current
            stat_text = _read_text("/proc/stat") or ""
            current = parse_cpu_times(stat_text.splitlines()[0]) if stat_text else None
        cpu = cpu_percent_between(self._prev_cpu, current)
        if current is not None:
            self._prev_cpu = current
        cores_now = parse_core_times(stat_text)
        core_percents = core_percents_between(self._prev_cores, cores_now)
        self._prev_cores = cores_now

        now = time.monotonic()
        net = parse_net_dev(_read_text("/proc/net/dev"))
        rx_bps, tx_bps = net_rate(self._prev_net, net, now - self._prev_net_time)
        self._prev_net, self._prev_net_time = net, now

        meminfo = _read_text("/proc/meminfo")
        mem_used, mem_total = _parse_meminfo(meminfo)
        swap_used, swap_total = _parse_swap(meminfo)

        disk_percent: Optional[float] = None
        disk_path = self.disk_path if os.path.isdir(self.disk_path) else "/"
        try:
            st = os.statvfs(disk_path)
            used = (st.f_blocks - st.f_bfree) * st.f_frsize
            usable = used + st.f_bavail * st.f_frsize
            if usable > 0:
                disk_percent = max(0.0, min(100.0, used * 100.0 / usable))
        except OSError:
            pass
        disk_mount = _mount_point(disk_path)

        uptime: Optional[int] = None
        uptime_text = _read_text("/proc/uptime")
        if uptime_text:
            try:
                uptime = int(float(uptime_text.split()[0]))
            except (ValueError, IndexError):
                pass

        procs_total: Optional[int] = None
        top_cpu: Tuple[ProcInfo, ...] = ()
        top_mem: Tuple[ProcInfo, ...] = ()
        gpu: Optional[GpuInfo] = None
        if self.detail:
            total, top_cpu, top_mem = self._sample_processes(mem_total)
            procs_total = total or None
            if self._nvidia_smi:
                gpu = _local_gpu(self._nvidia_smi)
        else:
            # A stale baseline would average CPU over a long gap.
            self._prev_procs = None

        return SystemMetrics(
            os_name=self._os_name,
            kernel=self._kernel,
            hostname=self._hostname,
            cpu_percent=cpu,
            mem_used=mem_used,
            mem_total=mem_total,
            disk_percent=disk_percent,
            disk_mount=disk_mount,
            uptime_seconds=uptime,
            latency_ms=None,
            arch=self._arch,
            cpu_model=self._cpu_model,
            cores=self._cores,
            load=parse_loadavg(_read_text("/proc/loadavg")),
            swap_used=swap_used,
            swap_total=swap_total,
            core_percents=core_percents,
            net_rx_bps=rx_bps,
            net_tx_bps=tx_bps,
            disks=_local_disks(),
            procs_total=procs_total,
            top_cpu=top_cpu,
            top_mem=top_mem,
            temp_c=_local_temperature(),
            gpu=gpu,
        )


# ---------------------------------------------------------------------------
# Remote script + parser
# ---------------------------------------------------------------------------


def build_remote_script(
    sleep_seconds: str = "0.5", disk_path: str = "/", detail: bool = False
) -> str:
    """POSIX ``sh`` script (dash/busybox safe) printing sentinel-delimited key=value lines.

    The script avoids bashisms and backslash pairs/single-quote escapes that
    would be mangled when the remote login shell is fish or csh-like.
    Multi-line outputs are joined with ';' (parsed by the shared parsers).
    ``detail`` adds per-core CPU, processes and the GPU (extra remote forks).
    """
    join = "tr '\\n' ';'"
    lines = [
        "LC_ALL=C",
        "export LC_ALL",
        f"echo '{_BEGIN_SENTINEL}'",
        # Remote-side run time (uptime clock, 10 ms resolution) so the
        # latency excludes the script's own work, not just its sleep.
        "printf 't0=%s\\n' \"$(cut -d' ' -f1 /proc/uptime 2>/dev/null)\"",
        "printf 'os=%s\\n' \"$(sed -n 's/^PRETTY_NAME=//p' /etc/os-release 2>/dev/null | head -n 1)\"",
        "printf 'kernel=%s\\n' \"$(uname -r 2>/dev/null)\"",
        "printf 'arch=%s\\n' \"$(uname -m 2>/dev/null)\"",
        "printf 'host=%s\\n' \"$(hostname 2>/dev/null || cat /proc/sys/kernel/hostname 2>/dev/null)\"",
        "printf 'cpumodel=%s\\n' \"$(awk -F: '/model name/ {print $2; exit}' /proc/cpuinfo 2>/dev/null)\"",
        "printf 'cores=%s\\n' \"$(grep -c '^processor' /proc/cpuinfo 2>/dev/null)\"",
        "printf 'stat1=%s\\n' \"$(head -n 1 /proc/stat 2>/dev/null)\"",
        f"printf 'net1=%s\\n' \"$(cat /proc/net/dev 2>/dev/null | {join})\"",
    ]
    if detail:
        lines += [
            f"printf 'cores1=%s\\n' \"$(grep '^cpu[0-9]' /proc/stat 2>/dev/null | {join})\"",
            f"p1=$({_PROC_AWK})",
            "printf 'clktck=%s\\n' \"$(getconf CLK_TCK 2>/dev/null)\"",
            "printf 'pagesize=%s\\n' \"$(getconf PAGESIZE 2>/dev/null)\"",
        ]
    lines += [
        f"if sleep {sleep_seconds} 2>/dev/null; then s={sleep_seconds}; else sleep 1; s=1; fi",
        "printf 'stat2=%s\\n' \"$(head -n 1 /proc/stat 2>/dev/null)\"",
        f"printf 'net2=%s\\n' \"$(cat /proc/net/dev 2>/dev/null | {join})\"",
        "printf 'sleep=%s\\n' \"$s\"",
    ]
    if detail:
        lines += [
            f"printf 'cores2=%s\\n' \"$(grep '^cpu[0-9]' /proc/stat 2>/dev/null | {join})\"",
            f"p2=$({_PROC_AWK})",
            "printf 'proc1=%s\\n' \"$p1\"",
            "printf 'proc2=%s\\n' \"$p2\"",
            "printf 'gpu=%s\\n' \"$(command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu --format=csv,noheader,nounits 2>/dev/null | head -n 1)\"",
        ]
    lines += [
        "printf 'memtotal=%s\\n' \"$(awk '/^MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null)\"",
        "printf 'memavail=%s\\n' \"$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo 2>/dev/null)\"",
        "printf 'swaptotal=%s\\n' \"$(awk '/^SwapTotal:/ {print $2}' /proc/meminfo 2>/dev/null)\"",
        "printf 'swapfree=%s\\n' \"$(awk '/^SwapFree:/ {print $2}' /proc/meminfo 2>/dev/null)\"",
        "printf 'load=%s\\n' \"$(cat /proc/loadavg 2>/dev/null)\"",
        "printf 'temp=%s\\n' \"$(cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null | " + join + ")\"",
        f"printf 'disk=%s\\n' \"$(df -Pk {_df_path_arg(disk_path)} 2>/dev/null | tail -n 1)\"",
        f"printf 'disks=%s\\n' \"$(df -Pk 2>/dev/null | {join})\"",
        "printf 'uptime=%s\\n' \"$(awk '{print $1}' /proc/uptime 2>/dev/null)\"",
        "printf 't1=%s\\n' \"$(cut -d' ' -f1 /proc/uptime 2>/dev/null)\"",
        f"echo '{_END_SENTINEL}'",
    ]
    return "\n".join(lines)


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


def _parse_disk_mount(line: Optional[str]) -> str:
    """Mount point (last column) of a ``df -Pk`` data line."""
    if not line:
        return ""
    parts = line.split(None, 5)
    return parts[5].strip() if len(parts) >= 6 else ""


def _mount_point(path: str) -> str:
    """Mount point containing ``path`` (local filesystem)."""
    try:
        path = os.path.realpath(path)
        while not os.path.ismount(path):
            parent = os.path.dirname(path)
            if parent == path:
                break
            path = parent
    except OSError:
        return "/"
    return path


_SAFE_DF_PATH = re.compile(r"^/[A-Za-z0-9_./@+:,= -]*$")


def _df_path_arg(path: str) -> str:
    """Shell-safe ``df`` operand; anything unusual falls back to ``/``."""
    if not path or not _SAFE_DF_PATH.match(path):
        return "/"
    return shlex.quote(path)


def _strip_quotes(value: str) -> str:
    return value.strip().strip('"').strip("'")


def _metrics_from_fields(fields: Dict[str, str]) -> SystemMetrics:
    mem_total_kib = _to_int(fields.get("memtotal"))
    mem_avail_kib = _to_int(fields.get("memavail"))
    mem_total = mem_total_kib * 1024 if mem_total_kib else None
    mem_used = None
    if mem_total is not None and mem_avail_kib is not None:
        mem_used = max(0, mem_total - min(mem_avail_kib * 1024, mem_total))

    swap_total_kib = _to_int(fields.get("swaptotal"))
    swap_free_kib = _to_int(fields.get("swapfree"))
    swap_total = swap_total_kib * 1024 if swap_total_kib else None
    swap_used = None
    if swap_total is not None and swap_free_kib is not None:
        swap_used = max(0, swap_total - min(swap_free_kib * 1024, swap_total))

    slept = _to_float(fields.get("sleep")) or 0.0
    rx_bps, tx_bps = net_rate(
        parse_net_dev(fields.get("net1")), parse_net_dev(fields.get("net2")), slept
    )
    gpu_text = fields.get("gpu")

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
        disk_mount=_parse_disk_mount(fields.get("disk")),
        uptime_seconds=_to_int(fields.get("uptime")),
        latency_ms=None,
        arch=fields.get("arch", ""),
        cpu_model=fields.get("cpumodel", ""),
        cores=_to_int(fields.get("cores")) or None,
        load=parse_loadavg(fields.get("load")),
        swap_used=swap_used,
        swap_total=swap_total,
        core_percents=core_percents_between(
            parse_core_times(fields.get("cores1")),
            parse_core_times(fields.get("cores2")),
        ),
        net_rx_bps=rx_bps,
        net_tx_bps=tx_bps,
        disks=parse_df_disks(fields.get("disks")),
        **_remote_process_fields(fields, slept, mem_total),
        temp_c=parse_temperature(fields.get("temp")),
        gpu=parse_gpu(gpu_text) if gpu_text else None,
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


def remote_latency_ms(fields: Dict[str, str], elapsed: float) -> float:
    """Round trip of a sample: wall time minus the time the script ran remotely.

    Falls back to subtracting only the CPU sampling sleep when the host has no
    /proc/uptime timestamps.
    """
    t0, t1 = _to_float(fields.get("t0")), _to_float(fields.get("t1"))
    if t0 is not None and t1 is not None and t1 >= t0:
        busy = t1 - t0
    else:
        busy = _to_float(fields.get("sleep"))
        if busy is None:
            busy = _REMOTE_SLEEP_SECONDS
    return max(0.0, (elapsed - busy) * 1000.0)


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
        self._sampled_once = False
        self.disk_path = "/"
        # Per-core CPU, processes and GPU cost extra remote forks: opt-in.
        self.detail = False

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
        window = _REMOTE_SLEEP_SECONDS if self._sampled_once else _FIRST_SAMPLE_SECONDS
        argv = self._build_argv(
            build_remote_script(str(window), self.disk_path, self.detail)
        )
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
        latency_ms = remote_latency_ms(fields, elapsed)
        self._sampled_once = True
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
        self._wake = threading.Event()
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
            self._wake.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=_STOP_JOIN_TIMEOUT)

    def poke(self) -> None:
        """Sample again right away instead of waiting out the interval."""
        self._wake.set()

    def _run(
        self,
        stop: threading.Event,
        on_sample: Callable[[SystemMetrics], None],
        on_error: Callable[[str], None],
    ) -> None:
        failures = 0
        ever_sampled = False
        last_error: Optional[str] = None
        max_wait = max(_MAX_BACKOFF_SECONDS, self._interval)
        while not stop.is_set():
            try:
                metrics = self._collector.sample()
            except Exception as exc:
                message = str(exc) or type(exc).__name__
                failures += 1
                wait = min(self._interval * (2**failures), max_wait)
                if not ever_sampled and failures <= _STARTUP_RETRY_ATTEMPTS:
                    wait = min(wait, _STARTUP_RETRY_SECONDS)
                if message != last_error:
                    last_error = message
                    if not stop.is_set():
                        self._deliver(on_error, message)
            else:
                failures = 0
                ever_sampled = True
                last_error = None
                wait = self._interval
                if not stop.is_set():
                    self._deliver(on_sample, metrics)
            self._wake.wait(wait)
            self._wake.clear()
            if stop.is_set():
                break

    @staticmethod
    def _deliver(callback: Callable, argument) -> None:
        try:
            callback(argument)
        except Exception:
            _logger.exception("Metrics callback failed")
