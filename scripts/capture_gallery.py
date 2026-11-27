#!/usr/bin/env python3
"""Capture README gallery screenshots via GTK render (no Spectacle focus races).

  PYTHONPATH=src python3 scripts/capture_gallery.py

Tiling compositors resize the window. For consistent 1280x720 shots run it in
a headless nested compositor:

  WLR_BACKENDS=headless WLR_RENDERER=pixman GDK_BACKEND=wayland \\
    env -u WAYLAND_DISPLAY cage -- env PYTHONPATH=src python3 scripts/capture_gallery.py

Uses an isolated HOME (/tmp/demo) so no personal hosts or files are captured.
"""

from __future__ import annotations

import json
import shutil
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "gallery"


def pump(seconds: float = 0.4) -> None:
    from gi.repository import GLib

    deadline = time.monotonic() + seconds
    ctx = GLib.MainContext.default()
    while time.monotonic() < deadline:
        while ctx.pending():
            ctx.iteration(False)
        time.sleep(0.01)


def capture_widget(widget, path: Path) -> None:
    from gi.repository import Gdk, Gtk, Gsk

    pump(0.35)
    width = max(int(widget.get_width()), 1)
    height = max(int(widget.get_height()), 1)
    paintable = Gtk.WidgetPaintable.new(widget)
    snapshot = Gtk.Snapshot()
    paintable.snapshot(snapshot, float(width), float(height))
    node = snapshot.to_node()
    if node is None:
        raise RuntimeError(f"Failed to render widget for {path.name}")
    renderer = Gsk.CairoRenderer()
    renderer.realize_for_display(Gdk.Display.get_default())
    texture = renderer.render_texture(node, None)
    renderer.unrealize()
    path.parent.mkdir(parents=True, exist_ok=True)
    texture.save_to_png(str(path))
    print(f"wrote {path.name} ({path.stat().st_size} bytes, {width}x{height})")


def seed_history(config_dir: Path) -> None:
    """A few recent connections for the History panel screenshot."""
    now = time.time()
    entries = [
        ("prod-api", "api.prod.example", "ssh", 120, 14),
        ("SFTP-prod-api", "api.prod.example", "sftp", 3_600, 3),
        ("lab-db", "db.lab.local", "ssh", 7_200, 6),
        ("Local", "", "local", 86_400, 22),
        ("bastion", "bastion.example", "ssh", 3 * 86_400, 2),
    ]
    app_dir = config_dir / "zashterminal"
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "connection_history.json").write_text(
        json.dumps(
            [
                {"session_name": n, "host": h, "kind": k, "last_used": now - age, "count": c}
                for n, h, k, age, c in entries
            ],
            indent=2,
        ),
        encoding="utf-8",
    )


def seed_sessions(config_dir: Path) -> None:
    payload = {
        "sessions": [
            {
                "name": "prod-api",
                "session_type": "ssh",
                "host": "api.prod.example",
                "user": "deploy",
                "auth_type": "key",
                "auth_value": str(Path.home() / ".ssh" / "id_ed25519"),
                "folder_path": "/Production",
                "port": 22,
                "source": "user",
            },
            {
                "name": "lab-db",
                "session_type": "ssh",
                "host": "db.lab.local",
                "user": "dba",
                "auth_type": "key",
                "auth_value": str(Path.home() / ".ssh" / "id_ed25519"),
                "folder_path": "/Lab",
                "port": 22,
                "source": "user",
            },
            {
                "name": "bastion",
                "session_type": "ssh",
                "host": "bastion.example",
                "user": "ops",
                "auth_type": "key",
                "auth_value": str(Path.home() / ".ssh" / "id_ed25519"),
                "folder_path": "",
                "port": 22,
                "source": "user",
            },
        ],
        "folders": [
            {"name": "Production", "path": "/Production", "parent_path": ""},
            {"name": "Lab", "path": "/Lab", "parent_path": ""},
        ],
    }
    app_dir = config_dir / "zashterminal"
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "sessions.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def demo_metrics(step: int):
    """Deterministic, fictional host metrics for screenshots."""
    import math

    from zashterminal.utils.system_metrics import DiskInfo, ProcInfo, SystemMetrics

    wave = math.sin(step / 3.0)
    return SystemMetrics(
        os_name="Ubuntu 24.04 LTS",
        kernel="6.8.0-45-generic",
        arch="x86_64",
        hostname="web-01",
        cpu_model="AMD EPYC 7763 64-Core Processor",
        cores=8,
        cpu_percent=32 + 18 * wave + (step % 5) * 3,
        mem_used=int(5.4 * 2**30),
        mem_total=16 * 2**30,
        swap_used=0,
        swap_total=4 * 2**30,
        disk_percent=43.0,
        disk_mount="/",
        uptime_seconds=1_067_220,
        load=(0.82, 0.64, 0.51),
        latency_ms=None,
        net_rx_bps=180_000 + 90_000 * wave,
        net_tx_bps=40_000 + 25_000 * math.cos(step / 2.0),
        core_percents=tuple(20 + (i * 9 + step * 7) % 60 for i in range(8)),
        disks=(
            DiskInfo("/", 43.0, 100 * 2**30),
            DiskInfo("/var/lib/docker", 71.0, 200 * 2**30),
            DiskInfo("/boot", 18.0, 2**30),
        ),
        procs_total=214,
        top_cpu=(
            ProcInfo("nginx", 12.4, 1.2),
            ProcInfo("python3", 8.1, 4.6),
            ProcInfo("postgres", 5.3, 9.8),
            ProcInfo("node", 2.2, 3.1),
            ProcInfo("sshd", 0.4, 0.1),
        ),
        top_mem=(
            ProcInfo("postgres", 5.3, 9.8),
            ProcInfo("python3", 8.1, 4.6),
            ProcInfo("node", 2.2, 3.1),
            ProcInfo("redis-server", 0.9, 2.4),
            ProcInfo("nginx", 12.4, 1.2),
        ),
    )


def show_demo_metrics(window, terminal) -> None:
    """Stop live collection and fill the status bar and monitor with demo data."""
    window.status_controller.shutdown()
    panel = window.ui_builder.monitor_panel
    panel.set_terminal(getattr(terminal, "terminal_id", None))
    panel.set_title("web-01")
    for step in range(45):
        metrics = demo_metrics(step)
        panel.set_metrics(metrics)
    window.ui_builder.status_bar.set_metrics(metrics)


def seed_home(home: Path) -> None:
    for d in ("projects/api", "projects/infra", "Documents", "Downloads", ".ssh"):
        (home / d).mkdir(parents=True, exist_ok=True)
    (home / "README.md").write_text("# demo\n", encoding="utf-8")
    # Neutral prompt in every shell/pane (no real user or host name in shots)
    (home / ".bashrc").write_text("PS1='\\[\\e[1;32m\\]demo@web-01\\[\\e[0m\\]:\\[\\e[1;34m\\]\\w\\[\\e[0m\\]$ '\n", encoding="utf-8")
    (home / "deploy.sh").write_text("#!/bin/sh\necho deploy\n", encoding="utf-8")
    (home / "notes.txt").write_text("todo: rotate keys\n", encoding="utf-8")
    (home / "projects/api/main.py").write_text("print('ok')\n", encoding="utf-8")


def main() -> int:
    os.environ.setdefault("LANG", "en_US.UTF-8")
    os.environ.setdefault("LC_MESSAGES", "en_US.UTF-8")
    os.environ["ZASHTERMINAL_DISABLE_AUTO_UPDATE"] = "1"

    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.png"):
        old.unlink()

    with tempfile.TemporaryDirectory(prefix="zash-gallery-") as tmp:
        config_home = Path(tmp)
        seed_sessions(config_home)
        seed_history(config_home)
        os.environ["XDG_CONFIG_HOME"] = str(config_home)
        os.environ["XDG_CACHE_HOME"] = str(config_home / "cache")
        # Isolated HOME: no real ~/.ssh/config hosts or personal files in shots.
        demo_home = Path("/tmp/demo")
        shutil.rmtree(demo_home, ignore_errors=True)
        seed_home(demo_home)
        os.environ["HOME"] = str(demo_home)
        os.chdir(demo_home)
        sys.path.insert(0, str(ROOT / "src"))

        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        gi.require_version("Vte", "3.91")
        gi.require_version("Gdk", "4.0")
        gi.require_version("Gsk", "4.0")
        from gi.repository import Adw, Gio, GLib

        from zashterminal.app import ZashTerminalApp
        from zashterminal.sessions.models import SessionItem
        from zashterminal.settings.manager import SettingsManager
        from zashterminal.ui.color_scheme_dialog import ColorSchemeDialog
        from zashterminal.ui.dialogs import PreferencesDialog, ShortcutsDialog
        from zashterminal.ui.dialogs.ai_config_dialog import AIConfigDialog
        from zashterminal.ui.dialogs.highlight_dialog import HighlightDialog
        from zashterminal.ui.dialogs.session_edit_dialog import SessionEditDialog
        from zashterminal.window import ZashTerminalWindow

        Adw.init()
        app = ZashTerminalApp()
        # Do not forward to a running user instance.
        app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)

        def on_activate(application):
            settings = SettingsManager(config_home / "zashterminal" / "settings.json")
            settings.set("auto_update_check", False)
            settings.set("ai_assistant_enabled", True)
            settings.set("search_all_tabs", True)
            settings.set("gtk_theme", "dark")
            settings.set("auto_hide_sidebar", False)
            application.settings_manager = settings

            window = ZashTerminalWindow(
                application=application, settings_manager=settings
            )
            window.set_default_size(1360, 860)
            window.set_title("Zashterminal Gallery")
            window.present()
            pump(1.0)
            try:
                window.split_view.set_show_sidebar(True)
            except Exception:
                pass

            terminal = window.tab_manager.get_selected_terminal()
            if terminal is None:
                window.create_local_tab(working_directory=str(Path.home()))
                pump(1.2)
                terminal = window.tab_manager.get_selected_terminal()
            else:
                pump(0.6)

            if terminal:
                terminal.feed_child(
                    (
                        "clear; printf '\\033[1;36mZashterminal\\033[0m "
                        "- sessions, files, search and AI\\n\\n'; "
                        "ls --color=always -lhgo\n"
                    ).encode()
                )
                pump(1.0)

            try:
                window.refresh_tree()
            except Exception:
                pass

            # Demo metrics instead of this machine's (hostname, CPU, processes)
            show_demo_metrics(window, terminal)
            pump(0.3)

            capture_widget(window, OUT / "01-main-terminal.png")

            try:
                if terminal:
                    terminal.feed_child(b"clear\n")
                    pump(0.5)
                from zashterminal.ui.dialogs.command_palette_dialog import (
                    CommandPalette,
                )

                palette = CommandPalette(window)
                palette.present(window)
                pump(1.8)
                capture_widget(window, OUT / "14-command-palette.png")
                palette.close()
                pump(0.2)
            except Exception as exc:
                print(f"palette skipped: {exc}")


            if terminal:
                terminal.feed_child(b"ls --color=always -lhgo\n")
                pump(0.6)

            if terminal:
                window.tab_manager.split_vertical(terminal)
                pump(0.7)
                right = window.tab_manager.get_selected_terminal()
                if right:
                    right.feed_child(
                        b"clear; printf '\\033[1;35m[monitor]\\033[0m CPU \\033[32m12%%\\033[0m "
                        b"MEM \\033[33m41%%\\033[0m\\n'\n"
                    )
                window.tab_manager.split_horizontal(right or terminal)
                pump(0.7)
                bottom = window.tab_manager.get_selected_terminal()
                if bottom:
                    bottom.feed_child(
                        b"clear; printf '\\033[1;34mjournal\\033[0m nginx\\n"
                        b"15:20:11 \\033[32m200\\033[0m GET /\\n'\n"
                    )
                pump(0.6)
            capture_widget(window, OUT / "02-multipane.png")

            window.file_manager_button.set_active(True)
            pump(0.9)
            capture_widget(window, OUT / "03-file-manager.png")

            window.search_button.set_active(True)
            pump(0.35)
            window.search_all_tabs_switch.set_active(True)
            window.terminal_search_entry.set_text("deploy")
            pump(0.5)
            capture_widget(window, OUT / "04-search-all-tabs.png")
            window.search_button.set_active(False)
            window.file_manager_button.set_active(False)
            pump(0.25)

            window.broadcast_button.set_active(True)
            window.broadcast_entry.set_text("systemctl status nginx --no-pager")
            pump(0.4)
            capture_widget(window, OUT / "05-broadcast.png")
            window.broadcast_button.set_active(False)

            try:
                colors = ColorSchemeDialog(window, settings, window)
                colors.present()
                pump(0.7)
                capture_widget(colors, OUT / "06-color-schemes.png")
                colors.close()
                pump(0.2)

                prefs = PreferencesDialog(window, settings)
                prefs.present()
                pump(0.7)
                capture_widget(prefs, OUT / "07-preferences.png")
                prefs.close()
                pump(0.2)

                ai = AIConfigDialog(window, settings)
                ai.present()
                pump(0.7)
                capture_widget(ai, OUT / "08-ai-config.png")
                ai.close()
                pump(0.2)

                hl = HighlightDialog(window)
                hl.present()
                pump(0.7)
                capture_widget(hl, OUT / "09-highlight.png")
                hl.close()
                pump(0.2)

                session = SessionItem(
                    name="prod-api",
                    session_type="ssh",
                    host="api.prod.example",
                    user="deploy",
                    port=22,
                    auth_type="key",
                    auth_value=str(Path.home() / ".ssh" / "id_ed25519"),
                    folder_path="/Production",
                    post_login_command_enabled=True,
                    post_login_command="sudo -i",
                )
                edit = SessionEditDialog(
                    window,
                    session,
                    window.session_store,
                    -1,
                    folder_store=window.folder_store,
                    settings_manager=settings,
                )
                edit.present()
                pump(0.7)
                capture_widget(edit, OUT / "10-session-edit.png")
                edit.close()
                pump(0.2)

                shortcuts = ShortcutsDialog(window)
                shortcuts.present()
                pump(0.6)
                capture_widget(shortcuts, OUT / "11-shortcuts.png")
                shortcuts.close()
                pump(0.2)

                try:
                    window._show_command_manager_dialog()
                    pump(0.8)
                    dlg = window.command_manager_dialog
                    capture_widget(dlg or window, OUT / "12-command-manager.png")
                    if dlg:
                        dlg.close()
                except Exception as exc:
                    print(f"command manager skipped: {exc}")

                try:
                    window.ui_builder._on_rail_page_toggled("sessions", True)
                    pump(0.3)
                    capture_widget(window, OUT / "13-sessions-sidebar.png")
                except Exception as exc:
                    print(f"sidebar skipped: {exc}")

                try:
                    ui = window.ui_builder
                    ui._on_rail_page_toggled("monitor", True)
                    pump(0.5)
                    capture_widget(window, OUT / "15-monitor-panel.png")
                    ui.monitor_panel._on_expand_clicked(None)
                    pump(0.8)
                    monitor_window = ui.monitor_panel._window
                    capture_widget(monitor_window, OUT / "16-monitor-window.png")
                    monitor_window.close()
                    pump(0.2)
                    ui._on_rail_page_toggled("history", True)
                    pump(0.4)
                    capture_widget(window, OUT / "17-history.png")
                except Exception as exc:
                    print(f"monitor skipped: {exc}")
            except Exception:
                traceback.print_exc()
            finally:
                GLib.timeout_add(200, application.quit)

        app.connect("activate", on_activate)
        return int(app.run([]) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
