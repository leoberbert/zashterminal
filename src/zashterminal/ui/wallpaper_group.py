"""Explicit, preview-first wallpaper colors. No work runs while inactive."""

import copy
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import Adw, Gdk, GLib, Gtk, Vte

from ..utils.translation_utils import _
from ..utils.wallpaper import WallpaperBackendError, discover_wallpapers, extract_palette, desktop_prefers_dark, preferred_wallpaper


class WallpaperGroup(Adw.PreferencesGroup):
    def __init__(self, dialog, settings, refresh_schemes):
        super().__init__(
            title=_("Wallpaper colors"),
            description=_("Generate a Pywal palette from a local image. Preview first; colors change only when you apply. Updates are manual."),
        )
        self.dialog, self.settings = dialog, settings
        self._refresh_schemes = refresh_schemes
        self._closed = False
        self._generation = 0
        self._refresh_source = 0
        self._busy = False
        self._pending = None
        self._path = None
        self._candidates = []
        self._updating = False
        self._chooser = None

        self.status = Adw.ActionRow(title=_("Manual theme active"))
        self.status.set_subtitle_lines(2)
        self.add(self.status)
        actions = Gtk.Box(spacing=6, margin_top=6, margin_bottom=6)
        self.detect = Gtk.Button(label=_("Detect wallpaper"))
        self.choose = Gtk.Button(label=_("Choose image…"))
        self.detect.connect("clicked", self._detect)
        self.choose.connect("clicked", self._choose)
        actions.append(self.detect)
        actions.append(self.choose)
        self.add(actions)

        self.sources = Adw.ComboRow(title=_("Detected images"), visible=False)
        self.sources.connect("notify::selected", self._source_changed)
        self.add(self.sources)
        self.mode = Adw.ComboRow(title=_("Appearance"), model=Gtk.StringList.new([_("Dark"), _("Light")]))
        self.mode.connect("notify::selected", self._mode_changed)
        self.add(self.mode)
        self.interface = Adw.ActionRow(
            title=_("Apply colors to the interface"),
            subtitle=_("When off, your existing interface theme preference is used."),
        )
        self.interface_switch = Gtk.Switch(active=True, valign=Gtk.Align.CENTER)
        self.interface.add_suffix(self.interface_switch)
        self.interface.set_activatable_widget(self.interface_switch)
        self.add(self.interface)

        self.preview_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, visible=False, margin_top=8, margin_bottom=8)
        self.preview_box.add_css_class("card")
        self.preview_header = Gtk.Label(label=_("Preview · tabs and header"), height_request=32)
        self.preview_box.append(self.preview_header)
        self.preview = Vte.Terminal()
        self.preview.set_size(54, 8)
        self.preview.set_input_enabled(False)
        self.preview.set_scrollback_lines(0)
        self.preview.set_can_focus(False)
        self.preview.set_hexpand(True)
        self.preview.set_size_request(-1, 180)
        self.preview_box.append(self.preview)
        self.add(self.preview_box)

        controls = Gtk.Box(spacing=6, margin_top=6, margin_bottom=6)
        self.apply = Gtk.Button(label=_("Apply"), sensitive=False, css_classes=["suggested-action"])
        self.save = Gtk.Button(label=_("Save as theme"), sensitive=False)
        self.restore = Gtk.Button(label=_("Restore manual theme"))
        self.apply.connect("clicked", self._apply)
        self.save.connect("clicked", self._save)
        self.restore.connect("clicked", self._restore)
        for button in (self.apply, self.save, self.restore):
            controls.append(button)
        self.add(controls)
        self.add(Gtk.Label(
            label=_("Transparency can reduce contrast. Programs with their own RGB themes may keep their colors."),
            wrap=True, xalign=0, css_classes=["dim-label"], margin_bottom=6,
        ))
        settings.add_change_listener(self._setting_changed)
        dialog.connect("close-request", self._close)
        dialog.connect("map", self._reopen)
        self._sync_status()
        current = settings.get("wallpaper_theme")
        if settings._valid_wallpaper(current):
            self._pending = copy.deepcopy(current)
            self._path = current.get("path")
            self._updating = True
            self.mode.set_selected(1 if current.get("mode") == "light" else 0)
            self.interface_switch.set_active(current["apply_to_interface"])
            self._updating = False
            self._show_preview()

    def _sync_status(self):
        active = self.settings._valid_wallpaper(self.settings.get("wallpaper_theme"))
        self.status.set_title(_("Wallpaper colors active") if active else _("Manual theme active"))
        self.restore.set_sensitive(active and not self._busy)

    def _setting_changed(self, key, old, new):
        if key in ("wallpaper_theme", "color_scheme") and not self._closed:
            self._sync_status()
            if key == "wallpaper_theme" and not self._refresh_source:
                self._refresh_source = GLib.idle_add(self._refresh_list)

    def _refresh_list(self):
        self._refresh_source = 0
        if not self._closed:
            self._refresh_schemes()
        return GLib.SOURCE_REMOVE

    def _close(self, *_args):
        self._closed = True
        self._generation += 1
        if self._refresh_source:
            GLib.source_remove(self._refresh_source)
            self._refresh_source = 0
        self.settings.remove_change_listener(self._setting_changed)
        if self._chooser:
            self._chooser.destroy()
        return False

    def _reopen(self, *_args):
        if self._closed:
            self._closed = False
            self.settings.add_change_listener(self._setting_changed)
            self._set_busy(False)

    def _set_busy(self, busy):
        self._busy = busy
        for widget in (self.detect, self.choose, self.sources, self.mode, self.interface_switch):
            widget.set_sensitive(not busy)
        self.apply.set_sensitive(not busy and self._pending is not None)
        self.save.set_sensitive(not busy and self._pending is not None)
        self._sync_status()

    def _work(self, operation, completed):
        if self._closed or self._busy:
            return
        self._set_busy(True)
        self._generation += 1
        generation = self._generation
        self.status.set_subtitle(_("Preparing preview…"))

        def finish(result, error):
            if not self._closed and generation == self._generation:
                self._set_busy(False)
                if error:
                    messages = {
                        "missing-pywal": _("Install pywal16 in the Python environment running Zashterminal. Your current theme was kept."),
                        "missing-imagemagick": _("Install ImageMagick to generate wallpaper colors with Pywal. Your current theme was kept."),
                        "timeout": _("Pywal took too long to generate colors. Try another image. Your current theme was kept."),
                    }
                    self.status.set_subtitle(messages.get(error, _("Could not generate the Pywal palette. Choose a valid local PNG, JPEG, WebP or BMP (up to 32 MiB and 40 MP). Your current theme was kept.")))
                else:
                    completed(result)
            return GLib.SOURCE_REMOVE

        def run():
            try:
                result = operation()
            except WallpaperBackendError as error:
                GLib.idle_add(finish, None, error.reason)
            except Exception:
                GLib.idle_add(finish, None, True)
            else:
                GLib.idle_add(finish, result, False)

        threading.Thread(target=run, name="wallpaper-preview", daemon=True).start()

    def _detect(self, _button):
        # GTK display objects must only be accessed on the main thread.
        screen_size = None
        display = self.dialog.get_display()
        surface = self.dialog.get_surface()
        monitor = display.get_monitor_at_surface(surface) if surface else None
        if monitor is None and display.get_monitors().get_n_items() == 1:
            monitor = display.get_monitors().get_item(0)
        if monitor is not None:
            geometry = monitor.get_geometry()
            scale = monitor.get_scale_factor()
            screen_size = (geometry.width * scale, geometry.height * scale)
        def discover():
            try:
                candidates = discover_wallpapers()
                return candidates, preferred_wallpaper(candidates, screen_size, desktop_prefers_dark())
            except Exception:
                return [], None

        def completed(result):
            candidates, preferred = result
            self._updating = True
            self._candidates = candidates
            labels = []
            for label, path in candidates:
                label = label.replace(" · images_dark", " · " + _("Dark wallpaper"))
                label = label.replace(" · images", " · " + _("Light wallpaper"))
                labels.append(f"{label} · {Path(path).name}")
            self.sources.set_model(Gtk.StringList.new([_("Choose wallpaper image"), *labels]))
            self.sources.set_selected(0)
            self.sources.set_visible(bool(candidates))
            self._updating = False
            if preferred is not None:
                self._updating = True
                self.sources.set_selected(preferred + 1)
                self._updating = False
                self._load(candidates[preferred][1])
            elif candidates:
                self._pending = None
                self._path = None
                self.preview_box.set_visible(False)
                self.apply.set_sensitive(False)
                self.save.set_sensitive(False)
                self.status.set_subtitle(_("Several wallpaper variants were found. Select the image matching your desktop; light and dark variants can have different colors."))
            else:
                self.status.set_subtitle(_("No static wallpaper detected for this desktop. Use Choose image; it works on any Linux distribution."))

        self._work(discover, completed)

    def _source_changed(self, *_args):
        index = self.sources.get_selected()
        if not self._updating and 1 <= index <= len(self._candidates):
            self._load(self._candidates[index - 1][1])

    def _choose(self, _button):
        chooser = Gtk.FileChooserNative.new(_("Choose wallpaper image"), self.dialog, Gtk.FileChooserAction.OPEN, _("Open"), _("Cancel"))
        image_filter = Gtk.FileFilter()
        image_filter.set_name(_("Static images"))
        for mime in ("image/png", "image/jpeg", "image/webp", "image/bmp"):
            image_filter.add_mime_type(mime)
        chooser.add_filter(image_filter)

        def response(native, response_id):
            if response_id == Gtk.ResponseType.ACCEPT and not self._closed:
                file = native.get_file()
                if file and file.get_path():
                    self.sources.set_visible(False)
                    self._load(file.get_path())
                else:
                    self.status.set_subtitle(_("Choose a local image file."))
            native.destroy()
            self._chooser = None

        chooser.connect("response", response)
        self._chooser = chooser
        chooser.show()

    def _mode_changed(self, *_args):
        if self._path and not self._updating:
            self._load(self._path)

    def _load(self, path):
        if self._busy or self._closed:
            return
        self._path = path
        self.sources.set_tooltip_text(path)
        self._pending = None
        self.preview_box.set_visible(False)
        mode = "light" if self.mode.get_selected() else "dark"

        def completed(result):
            self._pending = result
            self._show_preview()
            self.status.set_subtitle(_("Preview ready: {name}. Apply to use these colors.").format(name=Path(path).name))

        self._work(lambda: extract_palette(path, mode), completed)

    def _show_preview(self):
        scheme = self._pending["scheme"]

        def rgba(value):
            color = Gdk.RGBA()
            color.parse(value)
            return color

        self.preview.set_colors(rgba(scheme["foreground"]), rgba(scheme["background"]), [rgba(c) for c in scheme["palette"]])
        self.preview.set_color_cursor(rgba(scheme["cursor"]))
        self.preview.set_color_highlight(rgba(scheme["selection_background"]))
        self.preview.set_color_highlight_foreground(rgba(scheme["selection_foreground"]))
        provider = Gtk.CssProvider()
        provider.load_from_data(f"label {{ background-color: {scheme['headerbar_background']}; color: {scheme['foreground']}; }}".encode())
        context = self.preview_header.get_style_context()
        if hasattr(self, "_header_provider"):
            context.remove_provider(self._header_provider)
        context.add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._header_provider = provider
        # Feed clear/home in the same FIFO as the text. VTE.feed is asynchronous;
        # reset() could otherwise run before an earlier queued sample is parsed.
        sample = (
            "\x1b[0m\x1b[2J\x1b[3J\x1b[H$ git status\r\n"
            f"\x1b[32m{_('Success')}\x1b[0m  "
            f"\x1b[33m{_('Warning')}\x1b[0m  "
            f"\x1b[31m{_('Error')}\x1b[0m\r\n"
        )
        for start in (0, 8):
            sample += " ".join(f"\x1b[38;5;{i}mANSI {i:02d}\x1b[0m" for i in range(start, start + 8)) + "\r\n"
        fg = scheme["selection_foreground"]
        bg = scheme["selection_background"]
        selection_fg = ";".join(str(int(fg[i:i + 2], 16)) for i in (1, 3, 5))
        selection_bg = ";".join(str(int(bg[i:i + 2], 16)) for i in (1, 3, 5))
        sample += f"\x1b[38;2;{selection_fg};48;2;{selection_bg}m {_('Selection')} \x1b[0m  $ "
        self.preview.feed(sample.encode())
        self.preview_box.set_visible(True)
        self.apply.set_sensitive(True)
        self.save.set_sensitive(True)

    def _apply(self, _button):
        if self._pending is None or self._busy:
            return
        value = copy.deepcopy(self._pending)
        value["apply_to_interface"] = self.interface_switch.get_active()
        self.settings.set("wallpaper_theme", value)
        self.status.set_subtitle(_("Colors applied. Sessions and terminal history are preserved."))

    def _restore(self, _button):
        self.settings.set("wallpaper_theme", None)
        self.status.set_subtitle(_("Your manual theme and interface preference have been restored."))

    def _save(self, _button):
        if self._pending is None or self._busy:
            return
        from ..helpers import generate_unique_name

        schemes = self.settings.get_all_schemes()
        manual_key = self.settings.get_color_scheme_name()
        name = generate_unique_name(_("Wallpaper"), set(schemes) | {s.get("name", "") for s in schemes.values()})
        key = generate_unique_name("custom_wallpaper", set(schemes))
        scheme = copy.deepcopy(self._pending["scheme"])
        scheme["name"] = name
        self.settings.custom_schemes[key] = scheme
        if not self.settings.save_custom_schemes():
            del self.settings.custom_schemes[key]
            self.status.set_subtitle(_("Could not save the theme. Your active theme was kept."))
            return
        # Custom schemes are sorted; insertion must not move the saved selection.
        manual_index = self.settings.get_scheme_order().index(manual_key)
        if manual_index != self.settings.get("color_scheme"):
            self.settings.set("color_scheme", manual_index)
        self._refresh_schemes()
        self.status.set_subtitle(_("Saved as {name}. Your active theme was kept.").format(name=name))
