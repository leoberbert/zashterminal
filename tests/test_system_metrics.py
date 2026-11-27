"""System metrics tests (pure logic, no GTK display, no network).

Run with: PYTHONPATH=src python3 -m unittest tests.test_system_metrics
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from zashterminal.utils.system_metrics import (
    LocalMetricsCollector,
    MetricsPoller,
    MetricsUnavailable,
    RemoteMetricsCollector,
    SystemMetrics,
    build_remote_script,
    cpu_percent_between,
    format_bytes,
    format_uptime,
    core_percents_between,
    net_rate,
    parse_core_times,
    parse_cpu_times,
    parse_df_disks,
    parse_gpu,
    parse_net_dev,
    parse_proc_snapshot,
    parse_remote_output,
    parse_temperature,
    remote_latency_ms,
    top_processes,
)

FULL_OUTPUT = """Welcome banner noise
@@ZM_BEGIN@@
os="Ubuntu 24.04 LTS"
kernel=6.8.0-45-generic
host=web-01
stat1=cpu  1000 0 500 8000 100 0 0 0 0 0
stat2=cpu  1050 0 550 8100 100 0 0 0 0 0
sleep=0.5
memtotal=8000000
memavail=2000000
disk=/dev/sda1 51474044 20589618 28247284 43% /
uptime=1067220.55
@@ZM_END@@
trailing noise
"""


class FakeSession:
    host = "example.com"
    user = "alice"
    port = 2222
    auth_value = "/home/alice/.ssh/id_ed25519"
    proxy_jump = "bastion"

    def uses_key_auth(self):
        return True


class ParseRemoteOutputTests(unittest.TestCase):
    def test_full_output(self):
        m = parse_remote_output(FULL_OUTPUT)
        self.assertEqual(m.os_name, "Ubuntu 24.04 LTS")
        self.assertEqual(m.kernel, "6.8.0-45-generic")
        self.assertEqual(m.hostname, "web-01")
        # delta total = 200, delta idle = 100 -> 50 %
        self.assertAlmostEqual(m.cpu_percent, 50.0)
        self.assertEqual(m.mem_total, 8000000 * 1024)
        self.assertEqual(m.mem_used, 6000000 * 1024)
        self.assertAlmostEqual(m.mem_percent, 75.0)
        self.assertEqual(m.disk_percent, 43.0)
        self.assertEqual(m.uptime_seconds, 1067220)
        self.assertIsNone(m.latency_ms)

    def test_partial_output_gives_none_fields(self):
        m = parse_remote_output(
            "@@ZM_BEGIN@@\nkernel=5.10.0\nmemtotal=1024\nmemavail=\ndisk=\n"
        )
        self.assertEqual(m.kernel, "5.10.0")
        self.assertEqual(m.mem_total, 1024 * 1024)
        self.assertIsNone(m.mem_used)
        self.assertIsNone(m.mem_percent)
        self.assertIsNone(m.cpu_percent)
        self.assertIsNone(m.disk_percent)
        self.assertIsNone(m.uptime_seconds)
        self.assertEqual(m.os_name, "")

    def test_truncated_output_without_end_sentinel(self):
        m = parse_remote_output("@@ZM_BEGIN@@\nhost=box\nuptime=90\n")
        self.assertEqual(m.hostname, "box")
        self.assertEqual(m.uptime_seconds, 90)

    def test_garbage_raises(self):
        for text in ("", "permission denied", "hello\nworld=1\n", "@@ZM_BEGIN@@\nnonsense\n"):
            with self.subTest(text=text):
                with self.assertRaises(MetricsUnavailable):
                    parse_remote_output(text)

    def test_bad_numbers_become_none(self):
        m = parse_remote_output(
            "@@ZM_BEGIN@@\nhost=h\nmemtotal=abc\nmemavail=-5\nuptime=x\n"
            "disk=weird line\nstat1=cpu a b\nstat2=cpu 1 2 3 4\n"
        )
        self.assertIsNone(m.mem_total)
        self.assertIsNone(m.uptime_seconds)
        self.assertIsNone(m.disk_percent)
        self.assertIsNone(m.cpu_percent)

    def test_script_runs_under_posix_sh(self):
        sh = shutil.which("sh")
        if not sh or not os.path.exists("/proc/stat"):
            self.skipTest("needs sh and /proc")
        out = subprocess.run(
            [sh, "-c", build_remote_script("0.1")],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
        m = parse_remote_output(out)
        self.assertTrue(m.kernel)
        self.assertIsNotNone(m.mem_total)
        self.assertIsNotNone(m.uptime_seconds)
        self.assertIsNotNone(m.disk_percent)
        if m.cpu_percent is not None:
            self.assertTrue(0.0 <= m.cpu_percent <= 100.0)


class CpuMathTests(unittest.TestCase):
    def test_parse_cpu_times(self):
        self.assertEqual(
            parse_cpu_times("cpu  100 10 50 800 40 5 5 0 0 0"),
            (840, 1010),
        )
        self.assertEqual(parse_cpu_times("cpu 1 2 3 4"), (4, 10))
        self.assertIsNone(parse_cpu_times("cpu0 1 2 3 4 5"))
        self.assertIsNone(parse_cpu_times("cpu a b c d e"))
        self.assertIsNone(parse_cpu_times(""))

    def test_cpu_percent_between(self):
        self.assertAlmostEqual(cpu_percent_between((100, 200), (150, 300)), 50.0)
        self.assertAlmostEqual(cpu_percent_between((0, 0), (100, 100)), 0.0)
        self.assertAlmostEqual(cpu_percent_between((0, 0), (0, 100)), 100.0)
        self.assertIsNone(cpu_percent_between(None, (1, 2)))
        self.assertIsNone(cpu_percent_between((1, 2), None))
        self.assertIsNone(cpu_percent_between((5, 10), (5, 10)))  # no elapsed time
        self.assertIsNone(cpu_percent_between((5, 10), (3, 20)))  # counter went back


class MetricsModelTests(unittest.TestCase):
    def test_mem_percent(self):
        self.assertAlmostEqual(SystemMetrics(mem_used=25, mem_total=100).mem_percent, 25.0)
        self.assertIsNone(SystemMetrics(mem_used=None, mem_total=100).mem_percent)
        self.assertIsNone(SystemMetrics(mem_used=1, mem_total=None).mem_percent)
        self.assertIsNone(SystemMetrics(mem_used=1, mem_total=0).mem_percent)

    def test_format_bytes(self):
        self.assertEqual(format_bytes(0), "0 B")
        self.assertEqual(format_bytes(512), "512 B")
        self.assertEqual(format_bytes(1024), "1.0 KiB")
        self.assertEqual(format_bytes(int(3.2 * 1024**3)), "3.2 GiB")
        self.assertEqual(format_bytes(5 * 1024**4), "5.0 TiB")
        self.assertEqual(format_bytes(2048 * 1024**4), "2048.0 TiB")

    def test_format_uptime(self):
        self.assertEqual(format_uptime(0), "0m")
        self.assertEqual(format_uptime(59), "0m")
        self.assertEqual(format_uptime(3 * 3600 + 5 * 60), "3h 5m")
        self.assertEqual(format_uptime(12 * 86400 + 4 * 3600 + 27 * 60), "12d 4h 27m")


class LocalCollectorTests(unittest.TestCase):
    @unittest.skipUnless(os.path.exists("/proc/stat"), "needs /proc")
    def test_sample_types(self):
        collector = LocalMetricsCollector()
        first = collector.sample()
        second = collector.sample()
        for m in (first, second):
            self.assertIsInstance(m, SystemMetrics)
            self.assertTrue(m.kernel)
            self.assertTrue(m.hostname)
            self.assertIsInstance(m.mem_total, int)
            self.assertIsInstance(m.mem_used, int)
            self.assertLessEqual(m.mem_used, m.mem_total)
            self.assertTrue(0.0 <= m.disk_percent <= 100.0)
            self.assertGreater(m.uptime_seconds, 0)
            self.assertIsNone(m.latency_ms)
        for m in (first, second):
            if m.cpu_percent is not None:
                self.assertTrue(0.0 <= m.cpu_percent <= 100.0)


class RemoteCollectorTests(unittest.TestCase):
    def test_missing_socket_raises_without_running_ssh(self):
        with tempfile.TemporaryDirectory() as tmp:
            collector = RemoteMetricsCollector(FakeSession(), os.path.join(tmp, "cm_x"))
            with patch("zashterminal.utils.system_metrics.subprocess.run") as run:
                with self.assertRaises(MetricsUnavailable):
                    collector.sample()
                run.assert_not_called()

    @unittest.skipUnless(shutil.which("ssh"), "needs ssh")
    def test_argv_reuses_mux_and_never_prompts(self):
        collector = RemoteMetricsCollector(FakeSession(), "/tmp/cm_test")
        argv = collector._build_argv("echo hi")
        joined = " ".join(argv)
        for expected in (
            "ControlMaster=no",
            "ControlPath=/tmp/cm_test",
            "BatchMode=yes",
            "ConnectTimeout=5",
            "ProxyJump=bastion",
            "-i /home/alice/.ssh/id_ed25519",
            "-p 2222",
            "alice@example.com",
        ):
            self.assertIn(expected, joined)
        self.assertNotIn("ControlMaster=auto", joined)
        self.assertTrue(argv[-1].startswith("sh -c "))


class _FakeCollector:
    def __init__(self, results):
        self._results = list(results)
        self.calls = 0
        self.done = threading.Event()

    def sample(self):
        self.calls += 1
        if not self._results:
            self.done.set()
            return SystemMetrics()
        item = self._results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class MetricsPollerTests(unittest.TestCase):
    def test_samples_and_stop(self):
        collector = _FakeCollector([SystemMetrics(hostname="a"), SystemMetrics(hostname="b")])
        got = []
        enough = threading.Event()

        def on_sample(m):
            got.append(m)
            if len(got) >= 3:
                enough.set()

        poller = MetricsPoller(collector, interval=0.02)
        poller.start(on_sample, lambda msg: self.fail(msg))
        self.assertTrue(enough.wait(2.0))
        poller.stop()
        poller.stop()  # idempotent
        self.assertEqual([m.hostname for m in got[:2]], ["a", "b"])
        calls = collector.calls
        threading.Event().wait(0.25)
        self.assertEqual(collector.calls, calls)

    def test_errors_reported_once_per_message_and_polling_continues(self):
        collector = _FakeCollector(
            [
                MetricsUnavailable("down"),
                MetricsUnavailable("down"),
                MetricsUnavailable("other"),
                SystemMetrics(hostname="ok"),
            ]
        )
        errors = []
        samples = []
        ok = threading.Event()

        def on_sample(m):
            samples.append(m)
            ok.set()

        # interval small so exponential backoff (interval * 2**n) stays fast.
        poller = MetricsPoller(collector, interval=0.02)
        poller.start(on_sample, errors.append)
        self.assertTrue(ok.wait(5.0))
        poller.stop()
        self.assertEqual(errors, ["down", "other"])
        self.assertEqual(samples[0].hostname, "ok")

    def test_callback_exception_does_not_kill_thread(self):
        collector = _FakeCollector([SystemMetrics(), SystemMetrics()])
        seen = threading.Event()
        count = []

        def on_sample(_m):
            count.append(1)
            if len(count) == 1:
                raise RuntimeError("boom")
            seen.set()

        poller = MetricsPoller(collector, interval=0.02)
        poller.start(on_sample, lambda _msg: None)
        self.assertTrue(seen.wait(2.0))
        poller.stop()


class ExtendedMetricsParserTests(unittest.TestCase):
    def test_net_rate_ignores_loopback_and_virtual_interfaces(self):
        text = (
            "Inter-|   Receive\n face |bytes\n"
            "    lo: 999 1 0 0 0 0 0 0 999 1 0 0 0 0 0 0\n"
            "  eth0: 1000 1 0 0 0 0 0 0 400 1 0 0 0 0 0 0\n"
            "docker0: 5000 1 0 0 0 0 0 0 5000 1 0 0 0 0 0 0\n"
        )
        later = text.replace("1000 1", "3000 1").replace("400 1", "900 1")
        rx, tx = net_rate(parse_net_dev(text), parse_net_dev(later), 2.0)
        self.assertEqual((rx, tx), (1000.0, 250.0))

    def test_net_rate_joined_with_semicolons_like_the_remote_script(self):
        joined = "  eth0: 10 1 0 0 0 0 0 0 20 1 0 0 0 0 0 0;"
        self.assertEqual(parse_net_dev(joined), (10, 20))

    def test_disks_keep_block_devices_and_drop_loop_snap_duplicates(self):
        df = (
            "Filesystem 1024-blocks Used Available Capacity Mounted on;"
            "tmpfs 100 1 99 1% /run;"
            "/dev/sda1 1000 580 420 58% /;"
            "/dev/sda1 1000 580 420 58% /mnt/bind;"
            "/dev/loop3 10 10 0 100% /snap/core/1;"
            "/dev/sdb 2000 500 1500 25% /home dir;"
        )
        disks = parse_df_disks(df)
        self.assertEqual([d.mount for d in disks], ["/", "/home dir"])
        self.assertEqual(disks[1].percent, 25.0)
        self.assertEqual(disks[1].size, 2000 * 1024)

    def test_top_processes_use_current_cpu_not_lifetime_average(self):
        before = parse_proc_snapshot(
            "1|busy|1000|10;2|idle app|9000|5000;3|Web Content (x)|10|20;4|awk|5|1;"
        )
        after = parse_proc_snapshot(
            "1|busy|1060|10;2|idle app|9000|5000;3|Web Content (x)|10|20;"
            "4|awk|99|1;9|new|30|30;"
        )
        total, by_cpu, by_mem = top_processes(
            before, after, seconds=1.0, clk_tck=100, page_size=4096, mem_total=10000 * 4096
        )
        self.assertEqual(total, 5)
        self.assertEqual([p.name for p in by_cpu[:2]], ["busy", "new"])
        self.assertAlmostEqual(by_cpu[0].cpu, 60.0)
        self.assertAlmostEqual(by_cpu[1].cpu, 30.0)  # started inside the window
        idle = next(p for p in by_cpu if p.name == "idle app")
        self.assertEqual(idle.cpu, 0.0)  # high lifetime usage, idle right now
        self.assertEqual(by_mem[0].name, "idle app")
        self.assertAlmostEqual(by_mem[0].mem, 50.0)
        self.assertNotIn("awk", [p.name for p in by_cpu])  # collector noise

    def test_latency_excludes_remote_script_time(self):
        # 0.62 s wall, script ran 0.55 s remotely (sleep + /proc scans) -> 70 ms
        fields = {"t0": "100.10", "t1": "100.65", "sleep": "0.5"}
        self.assertAlmostEqual(remote_latency_ms(fields, 0.62), 70.0, places=3)
        # Without timestamps only the sleep is subtracted
        self.assertAlmostEqual(remote_latency_ms({"sleep": "0.5"}, 0.62), 120.0, places=3)

    def test_core_percents(self):
        before = parse_core_times("cpu0 100 0 100 800 0 0 0 0;cpu1 0 0 0 100 0 0 0 0;")
        after = parse_core_times("cpu0 150 0 150 900 0 0 0 0;cpu1 0 0 0 200 0 0 0 0;")
        self.assertEqual(core_percents_between(before, after), (50.0, 0.0))

    def test_gpu_and_temperature_absent_or_present(self):
        gpu = parse_gpu("NVIDIA RTX 3060, 21, 960, 12288, 39")
        self.assertEqual((gpu.name, gpu.util, gpu.temp), ("NVIDIA RTX 3060", 21.0, 39.0))
        self.assertEqual(gpu.mem_total, 12288 * 1024 * 1024)
        self.assertIsNone(parse_gpu(""))
        self.assertIsNone(parse_gpu("N/A"))
        self.assertEqual(parse_temperature("42000;51000;"), 51.0)
        self.assertIsNone(parse_temperature(""))


if __name__ == "__main__":
    unittest.main()
