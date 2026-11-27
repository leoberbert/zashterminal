"""Status bar controller, manual-SSH tracker, session tree moves and the
remote/local metrics collectors (no display needed)."""

from __future__ import annotations

import os
import shutil
import shlex
import subprocess
import unittest
from types import SimpleNamespace
from unittest import mock

from zashterminal.sessions.models import LayoutItem, SessionFolder, SessionItem
from zashterminal.sessions.tree import SessionTreeView
from zashterminal.terminal import registry as registry_module
from zashterminal.terminal.registry import ManualSSHTracker
from zashterminal.ui import status_controller as sc_module
from zashterminal.ui.status_controller import StatusController
from zashterminal.utils import system_metrics as sm
from zashterminal.utils.system_metrics import ProcInfo, SystemMetrics


# ---------------------------------------------------------------------------
# Status controller
# ---------------------------------------------------------------------------


class FakeTerminal:
    def __init__(self, terminal_id, cwd="/"):
        self.terminal_id = terminal_id
        self.cwd = cwd

    def get_encoding(self):
        return "UTF-8"

    def get_font(self):
        return None

    def get_font_scale(self):
        return 1.0

    def get_current_directory_uri(self):
        return f"file://host{self.cwd}" if self.cwd else None


class FakeTabs:
    def __init__(self):
        self.pages = {}
        self.active_tab = None
        self.focused = None

    def add(self, tab, terminals):
        page = object()
        self.pages[tab] = page
        for t in terminals:
            t.page = page
        self._terms = getattr(self, "_terms", {})
        self._terms[page] = terminals

    def get_selected_terminal(self):
        return self.focused

    def get_page_for_terminal(self, terminal):
        return getattr(terminal, "page", None)

    def get_all_terminals_in_page(self, page):
        return self._terms.get(page, [])


class FakeStatusBar:
    CONNECTED, DISCONNECTED, LOCAL, NONE = "connected", "disconnected", "local", "none"

    def __init__(self):
        self.visible = True
        self.metrics = []
        self.connection = None
        self.info = None

    def get_visible(self):
        return self.visible

    def set_visible(self, visible):
        self.visible = visible

    def set_metrics(self, metrics):
        self.metrics.append(metrics)

    def set_connection(self, state, text=None):
        self.connection = (state, text)

    def set_terminal_info(self, *info):
        self.info = info


class FakeMonitor:
    def __init__(self):
        self.expanded = False
        self.terminal = None
        self.metrics = []
        self.unavailable = None
        self.forgotten = []
        self.on_detail_changed = None

    def is_expanded(self):
        return self.expanded

    def set_terminal(self, key):
        self.terminal = key

    def set_metrics(self, m):
        self.metrics.append(m)

    def set_unavailable(self, reason):
        self.unavailable = reason

    def set_title(self, _text):
        pass

    def forget_terminals(self, is_alive):
        self.forgotten = [k for k in (1, 2, 3, 99) if not is_alive(k)]


class FakePoller:
    instances = []

    def __init__(self, collector, interval):
        self.collector = collector
        self.started = False
        self.stopped = False
        self.pokes = 0
        FakePoller.instances.append(self)

    def start(self, _on_sample, _on_error):
        self.started = True

    def stop(self):
        self.stopped = True

    def poke(self):
        self.pokes += 1


class FakeLocalCollector:
    def __init__(self):
        self.disk_path = "/"
        self.detail = False


class StatusControllerTests(unittest.TestCase):
    def setUp(self):
        FakePoller.instances.clear()
        self.patches = [
            mock.patch.object(sm, "MetricsPoller", FakePoller),
            mock.patch.object(sm, "LocalMetricsCollector", FakeLocalCollector),
            mock.patch.object(sc_module.GLib, "timeout_add", return_value=1),
            mock.patch.object(sc_module.GLib, "source_remove"),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.tabs = FakeTabs()
        self.info = {}
        registry = SimpleNamespace(get_terminal_info=lambda tid: self.info.get(tid))
        spawner = SimpleNamespace(_get_ssh_control_path=lambda s: "/tmp/cm-test")
        self.window = SimpleNamespace(
            tab_manager=self.tabs,
            terminal_manager=SimpleNamespace(registry=registry, spawner=spawner),
        )
        self.bar = FakeStatusBar()
        self.monitor = FakeMonitor()
        self.ctrl = StatusController(self.window, self.bar, self.monitor)
        self.local1 = FakeTerminal(1, "/home")
        self.local2 = FakeTerminal(2, "/srv")
        self.tabs.add("tab1", [self.local1])
        self.tabs.add("tab2", [self.local2])
        self.info[1] = {"type": "local", "status": "running", "identifier": "Local"}
        self.info[2] = {"type": "local", "status": "running", "identifier": "Local"}

    def show(self, tab, focused=None):
        self.tabs.active_tab = tab
        self.tabs.focused = focused
        self.ctrl.refresh()

    def test_follows_visible_tab_not_last_focused_terminal(self):
        self.show("tab2", focused=self.local1)  # focus lags behind the tab switch
        self.assertEqual(self.ctrl._signature, (2, "local", True))
        self.assertEqual(self.monitor.terminal, 2)
        self.assertEqual(self.bar.connection[0], FakeStatusBar.LOCAL)
        self.assertEqual(self.bar.info[0], "UTF-8")

    def test_disk_follows_cwd_and_resamples_immediately(self):
        self.show("tab1", focused=self.local1)
        poller = FakePoller.instances[-1]
        self.assertEqual(poller.collector.disk_path, "/home")
        self.local1.cwd = "/boot"
        self.ctrl.refresh()
        self.assertEqual(poller.collector.disk_path, "/boot")
        self.assertEqual(poller.pokes, 1)

    def test_switching_back_restores_cached_metrics_instantly(self):
        self.show("tab1", focused=self.local1)
        sample = SystemMetrics(hostname="h1", cpu_percent=10.0)
        self.ctrl._apply_sample(self.ctrl._signature, sample)
        self.show("tab2", focused=self.local2)
        self.bar.metrics.clear()
        self.show("tab1", focused=self.local1)
        self.assertIn(sample, self.bar.metrics)

    def test_stale_samples_and_errors_are_ignored(self):
        self.show("tab1", focused=self.local1)
        old = self.ctrl._signature
        self.show("tab2", focused=self.local2)
        self.bar.metrics.clear()
        self.ctrl._apply_sample(old, SystemMetrics(cpu_percent=1.0))
        self.ctrl._apply_error(old, "boom")
        self.assertEqual(self.bar.metrics, [])
        self.ctrl._apply_error(self.ctrl._signature, "ssh down")
        self.assertEqual(self.monitor.unavailable, "ssh down")

    def test_detail_kept_while_monitor_closed(self):
        self.show("tab1", focused=self.local1)
        sig = self.ctrl._signature
        top = (ProcInfo("nginx", 5.0, 1.0),)
        self.ctrl._apply_sample(sig, SystemMetrics(top_cpu=top, procs_total=10))
        self.ctrl._apply_sample(sig, SystemMetrics(cpu_percent=3.0))
        latest = self.ctrl._metrics_cache[1]
        self.assertEqual((latest.top_cpu, latest.procs_total, latest.cpu_percent), (top, 10, 3.0))

    def test_poller_follows_visibility_without_dropping_data(self):
        self.show("tab1", focused=self.local1)
        self.ctrl._apply_sample(self.ctrl._signature, SystemMetrics(cpu_percent=7.0))
        first = FakePoller.instances[-1]
        self.ctrl.set_monitor_active(True)  # detail turns on -> immediate sample
        self.assertTrue(first.collector.detail)
        self.assertEqual(first.pokes, 1)
        self.ctrl.set_status_bar_visible(False)
        self.ctrl.set_monitor_active(False)  # nothing visible -> stop sampling
        self.assertTrue(first.stopped)
        self.assertIsNone(self.ctrl._poller)
        self.assertIn(1, self.ctrl._metrics_cache)
        self.monitor.expanded = True  # expanded window needs data again
        self.ctrl._on_detail_changed()
        self.assertIsNot(FakePoller.instances[-1], first)
        self.assertTrue(FakePoller.instances[-1].started)

    def test_closed_terminals_are_forgotten(self):
        self.show("tab1", focused=self.local1)
        self.ctrl._apply_sample(self.ctrl._signature, SystemMetrics())
        del self.info[1]
        self.show("tab2", focused=self.local2)
        self.assertNotIn(1, self.ctrl._metrics_cache)
        self.assertIn(1, self.monitor.forgotten)

    def test_connection_states(self):
        ssh = FakeTerminal(3)
        self.tabs.add("tab3", [ssh])
        session = SessionItem("srv", host="h")
        self.info[3] = {"type": "ssh", "status": "disconnected", "identifier": session}
        self.show("tab3", focused=ssh)
        self.assertEqual(self.bar.connection[0], FakeStatusBar.DISCONNECTED)
        self.assertIsNotNone(self.monitor.unavailable)
        self.info[3] = {"type": "ssh", "status": "connected", "identifier": session}
        self.ctrl.refresh()
        self.assertEqual(self.bar.connection[0], FakeStatusBar.CONNECTED)
        self.assertIsInstance(FakePoller.instances[-1].collector, sm.RemoteMetricsCollector)
        self.info[3] = {"type": "sftp", "status": "connected", "identifier": session}
        self.ctrl.refresh()
        self.assertEqual(self.bar.connection[0], FakeStatusBar.CONNECTED)
        self.assertIsNotNone(self.monitor.unavailable)  # no metrics for SFTP tabs
        self.tabs.active_tab = None
        self.tabs.focused = None
        self.ctrl.refresh()
        self.assertEqual(self.bar.connection[0], FakeStatusBar.NONE)

    def test_shutdown_never_restarts_collection(self):
        self.show("tab1", focused=self.local1)
        self.ctrl.shutdown()
        count = len(FakePoller.instances)
        self.ctrl.set_monitor_active(True)
        self.show("tab2", focused=self.local2)
        self.assertEqual(len(FakePoller.instances), count)
        self.assertIsNone(self.ctrl._poller)


# ---------------------------------------------------------------------------
# Manual SSH tracker
# ---------------------------------------------------------------------------


class FakeProc:
    def __init__(self, name, cmdline=()):
        self._name, self._cmd = name, list(cmdline)

    def name(self):
        return self._name

    def cmdline(self):
        return self._cmd


class FakeVte:
    """Weak-referenceable stand-in for a Vte.Terminal with a pty."""

    def get_pty(self):
        return SimpleNamespace(get_fd=lambda: 7)


class ManualSSHTrackerTests(unittest.TestCase):
    def setUp(self):
        self.children = []
        self.scans = 0

        def children(recursive=False):
            if recursive:
                self.scans += 1
            return list(self.children)

        class FakePsutil:
            NoSuchProcess = type("NoSuchProcess", (Exception,), {})

            @staticmethod
            def Process(pid):
                return SimpleNamespace(children=children)

        self.psutil = FakePsutil
        p = mock.patch.object(registry_module, "_get_psutil", return_value=FakePsutil)
        p.start()
        self.addCleanup(p.stop)
        idle = mock.patch.object(registry_module.GLib, "idle_add")
        self.idle = idle.start()
        self.addCleanup(idle.stop)

        info = {"type": "local", "process_id": 100}
        registry = SimpleNamespace(get_terminal_info=lambda tid: info)
        self.tracker = ManualSSHTracker(registry, lambda t: None)
        self.terminal = FakeVte()
        self.tracker.track(1, self.terminal)
        self.fg = 100

    def check(self):
        with mock.patch.object(registry_module.os, "tcgetpgrp", side_effect=lambda fd: self.fg):
            self.tracker.check_process_tree(1)

    def test_idle_prompt_never_scans_process_table(self):
        for _ in range(5):
            self.check()
        self.assertEqual(self.scans, 0)
        self.assertIsNone(self.tracker.get_ssh_target(1))

    def test_detects_ssh_once_per_foreground_change(self):
        self.children = [FakeProc("ssh", ["ssh", "-p", "22", "ops@bastion"])]
        self.fg = 200
        self.check()
        self.check()
        self.assertEqual(self.scans, 1)
        self.assertEqual(self.tracker.get_ssh_target(1), "ops@bastion")
        self.fg = 100  # back at the prompt
        self.check()
        self.assertIsNone(self.tracker.get_ssh_target(1))
        self.assertEqual(self.idle.call_count, 2)  # title refreshed on enter and exit

    def test_falls_back_to_child_count_without_pty(self):
        self.terminal.get_pty = lambda: None
        self.children = [FakeProc("vim")]
        self.check()
        self.check()  # same child count -> no rescan
        self.assertEqual(self.scans, 1)

    def test_process_gone_resets_state(self):
        self.children = [FakeProc("ssh", ["ssh", "host"])]
        self.fg = 300
        self.check()
        self.assertEqual(self.tracker.get_ssh_target(1), "ssh")

        def gone(pid):
            raise self.psutil.NoSuchProcess()

        self.psutil.Process = staticmethod(gone)
        self.fg = 301
        self.check()
        self.assertIsNone(self.tracker.get_ssh_target(1))


# ---------------------------------------------------------------------------
# Session tree: drag & drop of several items
# ---------------------------------------------------------------------------


class TreeMoveTests(unittest.TestCase):
    def setUp(self):
        self.tree = SessionTreeView.__new__(SessionTreeView)
        self.sessions = {
            ("a", ""): SessionItem("a", host="h"),
            ("b", "/x"): SessionItem("b", host="h", folder_path="/x"),
        }
        self.moved, self.errors, self.refreshed, self.layouts = [], [], [], []
        ok = SimpleNamespace(success=True, message="")

        def move_session(session, target):
            self.moved.append((session.name, target))
            return ok

        self.tree.operations = SimpleNamespace(
            find_session_by_name_and_path=lambda n, p: (self.sessions.get((n, p)), 0),
            move_session_to_folder=move_session,
            find_folder_by_path=lambda p: (SessionFolder("f", path=p), 0),
            move_folder=lambda f, t: SimpleNamespace(success=False, message="nope"),
        )
        self.tree.parent_window = SimpleNamespace(
            move_layout=lambda *a: self.layouts.append(a),
            _show_error_dialog=lambda title, msg: self.errors.append(msg),
        )
        self.tree.refresh_tree = lambda: self.refreshed.append(True)
        self.tree.logger = mock.Mock()

    def test_encode_drag_items(self):
        enc = SessionTreeView._encode_drag_item
        self.assertEqual(enc(SessionItem("s", folder_path="/p")), "session|s|/p")
        self.assertEqual(enc(SessionFolder("f", path="/f")), "folder|f|/f")
        self.assertEqual(enc(LayoutItem("l", folder_path="/p")), "layout|l|/p")
        self.assertIsNone(enc(object()))

    def test_moves_every_dragged_session_and_refreshes_once(self):
        self.tree._perform_move("session|a|\nsession|b|/x\nlayout|L|/p\n", "/Lab")
        self.assertEqual(self.moved, [("a", "/Lab"), ("b", "/Lab")])
        self.assertEqual(self.layouts, [("L", "/p", "/Lab")])
        self.assertEqual(self.refreshed, [True])

    def test_failure_stops_and_reports(self):
        self.tree._perform_move("session|a|\nfolder|f|/f\nsession|b|/x", "/Lab")
        self.assertEqual(self.errors, ["nope"])
        self.assertEqual(self.moved, [("a", "/Lab")])  # stopped at the failure
        self.tree._perform_move("garbage", "/Lab")
        self.assertEqual(len(self.errors), 2)

    def test_drag_of_selected_row_carries_the_whole_selection(self):
        a, b = self.sessions[("a", "")], self.sessions[("b", "/x")]
        self.tree.get_selected_items = lambda: [a, b]
        self.tree._on_list_pressed(None, 1, 0, 0)
        row = SimpleNamespace(get_item=lambda: SimpleNamespace(get_item=lambda: b))
        with mock.patch("zashterminal.sessions.tree.Gdk.ContentProvider.new_for_value", side_effect=lambda v: v):
            data = self.tree._on_drag_prepare(None, 0, 0, row)
            self.assertEqual(data, "session|a|\nsession|b|/x")
            other = SessionItem("c", host="h")
            row = SimpleNamespace(get_item=lambda: SimpleNamespace(get_item=lambda: other))
            self.assertEqual(self.tree._on_drag_prepare(None, 0, 0, row), "session|c|")

    def test_arrow_click_toggles_folder(self):
        state = {"expanded": False}
        row = SimpleNamespace(
            get_item=lambda: SessionFolder("f", path="/f"),
            get_expanded=lambda: state["expanded"],
            set_expanded=lambda v: state.__setitem__("expanded", v),
        )
        gesture = mock.Mock()
        self.tree._on_arrow_pressed(gesture, 1, 0, 0, SimpleNamespace(get_item=lambda: row))
        self.assertTrue(state["expanded"])
        gesture.set_state.assert_called_once()


# ---------------------------------------------------------------------------
# Metrics collectors
# ---------------------------------------------------------------------------


@unittest.skipUnless(os.path.exists("/proc/stat"), "needs /proc")
class CollectorDetailTests(unittest.TestCase):
    def test_local_detail_sample(self):
        collector = sm.LocalMetricsCollector()
        collector.detail = True
        with mock.patch.object(sm.time, "sleep"):
            collector.sample()
            m = collector.sample()
        self.assertTrue(m.disks)
        self.assertGreater(m.procs_total or 0, 0)
        self.assertTrue(m.top_mem)
        self.assertTrue(m.cpu_model or m.cores)
        collector.detail = False
        self.assertIsNone(collector.sample().procs_total)

    @unittest.skipUnless(shutil.which("sh"), "needs sh")
    def test_remote_sample_runs_script_over_ssh_argv(self):
        """The script ssh would run remotely, executed by the local sh."""
        collector = sm.RemoteMetricsCollector(
            SimpleNamespace(host="h", port=22, user="u", proxy_jump="", auth_value="",
                            uses_key_auth=lambda: False),
            "/tmp/cm-test",
        )
        collector.detail = True
        collector.disk_path = "/"

        real_run = subprocess.run

        def run(argv, **kwargs):
            # argv ends with the remote command "sh -c '<script>'"
            return real_run(shlex.split(argv[-1]), capture_output=True, text=True, timeout=10)

        with mock.patch.object(collector, "_check_mux"), \
                mock.patch.object(sm.subprocess, "run", side_effect=run):
            m = collector.sample()
            m2 = collector.sample()
        self.assertTrue(m.kernel and m.disks and m.hostname)
        self.assertIsNotNone(m.latency_ms)
        self.assertTrue(m2.top_cpu or m2.top_mem)
        self.assertEqual(m2.disk_mount, "/")

    def test_remote_failure_is_reported(self):
        collector = sm.RemoteMetricsCollector(
            SimpleNamespace(host="h", port=22, user="", proxy_jump="", auth_value="",
                            uses_key_auth=lambda: False),
            "/tmp/cm-test",
        )
        failed = SimpleNamespace(returncode=255, stdout="", stderr="mux_client_request: broken\n")
        with mock.patch.object(collector, "_check_mux"), \
                mock.patch.object(sm.subprocess, "run", return_value=failed):
            with self.assertRaises(sm.MetricsUnavailable) as ctx:
                collector.sample()
        self.assertIn("broken", str(ctx.exception))


class PollerPokeTests(unittest.TestCase):
    def test_poke_samples_without_waiting_for_interval(self):
        import threading

        calls = []
        got = threading.Event()

        class Collector:
            def sample(self):
                calls.append(1)
                if len(calls) >= 2:
                    got.set()
                return SystemMetrics()

        poller = sm.MetricsPoller(Collector(), interval=60)
        poller.start(lambda m: None, lambda e: None)
        try:
            for _ in range(50):
                if calls:
                    break
                got.wait(0.02)
            poller.poke()
            self.assertTrue(got.wait(2.0))
        finally:
            poller.stop()


if __name__ == "__main__":
    unittest.main()
