#!/usr/bin/env python3
"""Capture README gallery screenshots via GTK render (no Spectacle focus races).

  PYTHONPATH=src python3 scripts/capture_gallery.py
"""

from __future__ import annotations

import json
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
    path.parent.mkdir(parents=True, exist_ok=True)
    texture.save_to_png(str(path))
    print(f"wrote {path.name} ({path.stat().st_size} bytes, {width}x{height})")


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
        os.environ["XDG_CONFIG_HOME"] = str(config_home)
        os.environ["XDG_CACHE_HOME"] = str(config_home / "cache")
        sys.path.insert(0, str(ROOT / "src"))

        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        gi.require_version("Vte", "3.91")
        gi.require_version("Gdk", "4.0")
        gi.require_version("Gsk", "4.0")
        from gi.repository import Adw, GLib

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

        def on_activate(application):
            settings = SettingsManager(config_home / "zashterminal" / "settings.json")
            settings.set("auto_update_check", False)
            settings.set("ai_assistant_enabled", True)
            settings.set("search_all_tabs", True)
            settings.set("gtk_theme", "dark")
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
                window.create_local_tab(working_directory=str(ROOT))
                pump(1.2)
                terminal = window.tab_manager.get_selected_terminal()
            else:
                pump(0.6)

            if terminal:
                terminal.feed_child(
                    (
                        "printf '\\n\\033[1;36mZashterminal\\033[0m gallery demo\\n'; "
                        "printf '\\033[1;32mOK\\033[0m sessions  "
                        "\\033[1;32mOK\\033[0m file manager  "
                        "\\033[1;32mOK\\033[0m AI\\n\\n'; "
                        "ls --color=always -lah | head -n 14\n"
                    ).encode()
                )
                pump(1.0)

            try:
                window.refresh_tree()
            except Exception:
                pass

            capture_widget(window, OUT / "01-main-terminal.png")

            if terminal:
                window.tab_manager.split_vertical(terminal)
                pump(0.7)
                right = window.tab_manager.get_selected_terminal()
                if right:
                    right.feed_child(
                        b"printf '\\033[1;35m[monitor]\\033[0m CPU \\033[32m12%%\\033[0m "
                        b"MEM \\033[33m41%%\\033[0m\\n'\n"
                    )
                window.tab_manager.split_horizontal(right or terminal)
                pump(0.7)
                bottom = window.tab_manager.get_selected_terminal()
                if bottom:
                    bottom.feed_child(
                        b"printf '\\033[1;34mjournal\\033[0m nginx\\n"
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
            window.terminal_search_entry.set_text("gallery")
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
                    window.split_view.set_show_sidebar(True)
                    menu_btn = window.ui_builder.menu_button
                    menu_btn.popup()
                    pump(0.6)
                    capture_widget(window, OUT / "13-main-menu.png")
                    menu_btn.popdown()
                except Exception as exc:
                    print(f"menu skipped: {exc}")

                try:
                    window.split_view.set_show_sidebar(True)
                    pump(0.3)
                    capture_widget(window, OUT / "14-sessions-sidebar.png")
                except Exception as exc:
                    print(f"sidebar skipped: {exc}")
            except Exception:
                traceback.print_exc()
            finally:
                GLib.timeout_add(200, application.quit)

        app.connect("activate", on_activate)
        return int(app.run([]) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
