"""Explicit GTK/VTE smoke test using temporary configuration, no user shell.

Run from the repository: PYTHONPATH=src python3 scripts/test_wallpaper_ui.py
Requires a graphical session. Does not present windows or alter desktop settings.
"""

import copy
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


def main():
    candidates = []
    if "--desktop" in sys.argv:
        from zashterminal.utils.wallpaper import discover_wallpapers

        candidates = discover_wallpapers()
        print(f"Detected {len(candidates)} local desktop wallpaper candidate(s).")
    with tempfile.TemporaryDirectory(prefix="zash-wallpaper-ui-") as directory:
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": directory, "XDG_CACHE_HOME": directory}):
            import gi
            gi.require_version("Adw", "1")
            gi.require_version("Gtk", "4.0")
            gi.require_version("Vte", "3.91")
            gi.require_version("GdkPixbuf", "2.0")
            from gi.repository import Adw, GdkPixbuf, GLib, Gtk, Vte
            from zashterminal.settings.manager import SettingsManager
            from zashterminal.ui.color_scheme_dialog import ColorSchemeDialog
            from zashterminal.utils.theme_engine import ThemeEngine
            from zashterminal.window import CommTerminalWindow

            Adw.init()

            def pump_until(condition, timeout=35):
                deadline = time.monotonic() + timeout
                context = GLib.MainContext.default()
                while time.monotonic() < deadline:
                    while context.pending():
                        context.iteration(False)
                    if condition():
                        return
                    time.sleep(0.005)
                raise AssertionError("GTK operation timed out")

            # Settings saves are disabled; custom theme save writes only to temp.
            with patch.object(SettingsManager, "save_settings"):
                settings = SettingsManager(Path(directory) / "settings.json")
                import zashterminal.settings.manager as manager_module
                manager_module._settings_manager = settings
                settings.set("gtk_theme", "light")
                manual_scheme = copy.deepcopy(settings.get_color_scheme_data())
                parent = Adw.Window()
                parent.header_bar = Adw.HeaderBar()
                parent.set_content(parent.header_bar)
                terminal = Vte.Terminal()
                def terminal_text():
                    if hasattr(terminal, "get_text_format"):
                        return terminal.get_text_format(Vte.Format.TEXT) or ""
                    return terminal.get_text()[0] or ""

                terminal.feed(b"persistent terminal content\r\n")
                pump_until(lambda: "persistent" in terminal_text())
                before = terminal_text()
                registry = SimpleNamespace(get_all_terminal_ids=lambda: [1], get_terminal=lambda _id: terminal)
                fake_window = SimpleNamespace(
                    settings_manager=settings,
                    terminal_manager=SimpleNamespace(registry=registry),
                    header_bar=parent.header_bar,
                    queue_draw=parent.queue_draw,
                    _update_tooltip_colors=Mock(),
                    _update_file_manager_transparency=Mock(),
                )
                listener = lambda *args: CommTerminalWindow._on_setting_changed(fake_window, *args) if args[0] == "wallpaper_theme" else None
                settings.add_change_listener(listener)
                image_path = Path(directory) / "wallpaper.png"
                image = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 1024, 768)
                image.fill(0x3388BBFF)
                image.savev(str(image_path), "png", [], [])

                with patch("zashterminal.terminal.highlighter.get_shell_input_highlighter", return_value=Mock()):
                    dialog = ColorSchemeDialog(parent, settings, fake_window)
                    group = dialog.wallpaper_group
                    source_path = candidates[0][1] if candidates else str(image_path)
                    ticks = [time.monotonic()]
                    def pulse():
                        ticks.append(time.monotonic())
                        return GLib.SOURCE_CONTINUE

                    heartbeat = GLib.timeout_add(10, pulse)
                    started = time.monotonic()
                    group._load(source_path)
                    pump_until(lambda: not group._busy)
                    GLib.source_remove(heartbeat)
                    elapsed = (time.monotonic() - started) * 1000
                    max_tick = max((b - a for a, b in zip(ticks, ticks[1:])), default=0) * 1000
                    print(f"Background preview: {elapsed:.1f} ms; sampled main-loop interval: {max_tick:.1f} ms (10 ms timer, idle test).")
                    assert group._pending is not None
                    with patch("zashterminal.ui.wallpaper_group.discover_wallpapers", return_value=[("KDE · 1 · images", source_path), ("KDE · 1 · images_dark", source_path)]):
                        group._detect(None)
                        pump_until(lambda: not group._busy)
                    assert group.sources.get_selected() == 0
                    assert group._pending is None
                    assert not group.apply.get_sensitive()
                    group.sources.set_selected(2)
                    pump_until(lambda: not group._busy)
                    assert group._pending is not None
                    with patch("zashterminal.ui.wallpaper_group.discover_wallpapers", return_value=[("picture-uri", source_path), ("picture-uri-dark", source_path)]), patch("zashterminal.ui.wallpaper_group.desktop_prefers_dark", return_value=True):
                        group._detect(None)
                        pump_until(lambda: not group._busy)
                    assert group.sources.get_selected() == 2
                    assert group._pending is not None
                    assert group.apply.get_sensitive()
                    from pywal import colors as pywal_colors
                    reference = pywal_colors.get(source_path, light=False, backend="wal", c16="darken", cache_dir=directory)
                    assert group._pending["scheme"]["palette"] == list(reference["colors"].values())
                    for key in ("background", "foreground", "cursor"):
                        assert group._pending["scheme"][key] == reference["special"][key]
                    group._show_preview()
                    group._show_preview()
                    def preview_text():
                        if hasattr(group.preview, "get_text_format"):
                            return group.preview.get_text_format(Vte.Format.TEXT) or ""
                        return group.preview.get_text()[0] or ""
                    pump_until(lambda: "$ git status" in preview_text())
                    assert preview_text().count("$ git status") == 1, "Preview accumulated old samples"
                    assert settings.get_color_scheme_data() == manual_scheme, "Preview modified active theme"
                    group._apply(None)
                    pump_until(lambda: True)
                    assert settings.get("gtk_theme") == "terminal"
                    assert terminal_text() == before, "Color change modified terminal content"
                    assert fake_window._update_tooltip_colors.called, "Window color event failed"
                    assert Adw.StyleManager.get_default().get_dark()
                    assert settings.get_color_scheme_data() == group._pending["scheme"]
                    active = copy.deepcopy(settings.get("wallpaper_theme"))
                    # The main menu uses HighlightDialog, not the legacy selector.
                    from zashterminal.ui.dialogs.highlight_dialog import HighlightDialog
                    integrated = HighlightDialog(parent)
                    assert settings.get("wallpaper_theme") == active
                    assert integrated.wallpaper_group._pending is not None
                    integrated.wallpaper_group._save(None)
                    assert settings.get("wallpaper_theme") == active
                    integrated._scheme_listbox.select_row(integrated._scheme_listbox.get_row_at_index(1))
                    pump_until(lambda: True)
                    assert settings.get("wallpaper_theme") is None
                    assert settings.get("color_scheme") == 1
                    settings.set("color_scheme", 8)
                    settings.set("wallpaper_theme", active)
                    pump_until(lambda: True)
                    integrated.wallpaper_group._close()
                    integrated.destroy()
                    # Opening a second selector must not apply its selected manual row.
                    second = ColorSchemeDialog(parent, settings, fake_window)
                    assert settings.get("wallpaper_theme") == active
                    second.wallpaper_group._close()
                    second.destroy()

                    group.mode.set_selected(1)
                    pump_until(lambda: not group._busy)
                    group._apply(None)
                    assert not Adw.StyleManager.get_default().get_dark()
                    group._save(None)
                    assert settings.custom_schemes
                    assert all(key.startswith("custom_") for key in settings.custom_schemes)
                    assert (Path(directory) / "zashterminal" / "custom_schemes.json").exists()
                    assert settings.get("wallpaper_theme") is not None

                    # Both modern and legacy CSS must parse without errors.
                    for modern in (False, True):
                        params = ThemeEngine.get_theme_params(settings.get_color_scheme_data())
                        with patch.object(ThemeEngine, "_supports_modern_css", return_value=modern):
                            css = ThemeEngine.generate_app_css(params, "terminal")
                        provider = Gtk.CssProvider()
                        errors = []
                        provider.connect("parsing-error", lambda _p, _section, error: errors.append(error))
                        provider.load_from_data(settings._normalize_css_for_compat(css, modern).encode())
                        assert not errors, errors

                    group._restore(None)
                    assert settings.get("gtk_theme") == "light"
                    assert settings.get_color_scheme_data() == manual_scheme
                    assert terminal_text() == before
                    group._load(str(Path(directory) / "missing.png"))
                    pump_until(lambda: not group._busy)
                    assert not group.apply.get_sensitive()
                    assert settings.get_color_scheme_data() == manual_scheme
                    group._load(source_path)
                    group._close()
                    # A result queued before close cannot resurrect the preview.
                    time.sleep(0.03)
                    pump_until(lambda: True)
                    assert group._pending is None
                    dialog.destroy()
                    settings.remove_change_listener(listener)
                    parent.destroy()
                    pump_until(lambda: True)
    print("GTK/VTE smoke test passed: preview, dark/light apply, content preservation, restore, save, reopen, CSS and missing file.")


if __name__ == "__main__":
    main()
