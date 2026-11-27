# zashterminal/ui/dialogs/preferences_dialog.py

import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Graphene, Gtk, Pango

from ...settings.config import APP_TITLE, APP_VERSION, ISSUE_URL, WEBSITE
from ...settings.manager import SettingsManager
from ...utils.icons import icon_image, set_image_from_icon
from ...utils.logger import get_logger
from ...utils.translation_utils import _
from .preferences_preview import SchemeSwatchesCard, TerminalPreviewCard

_STYLES_DIR = Path(__file__).parent.parent.parent / "data" / "styles"
_css_loaded = False

# Keys whose change must refresh the live terminal preview.
_PREVIEW_KEYS = frozenset(
    {
        "font",
        "font_scale",
        "color_scheme",
        "wallpaper_theme",
        "transparency",
        "line_spacing",
        "bold_is_bright",
        "cursor_shape",
        "cursor_blink",
        "text_blink_mode",
        "bidi_enabled",
        "enable_shaping",
        "cjk_ambiguous_width",
    }
)

_SEARCH_PAGE = "__search__"


def _ensure_css() -> None:
    """Load the preferences stylesheet once per process."""
    global _css_loaded
    if _css_loaded:
        return
    css_file = _STYLES_DIR / "preferences.css"
    display = Gdk.Display.get_default()
    if display is None or not css_file.exists():
        return
    provider = Gtk.CssProvider()
    provider.load_from_path(str(css_file))
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 10
    )
    _css_loaded = True


def _categories() -> List[tuple]:
    """(id, title, subtitle, icon) for every category, in sidebar order."""
    return [
        (
            "general",
            _("General"),
            _("Startup behavior, interface and updates."),
            "preferences-system-symbolic",
        ),
        (
            "appearance",
            _("Appearance"),
            _("Customize the look, colors and window behavior of the terminal."),
            "preferences-desktop-display-symbolic",
        ),
        (
            "terminal",
            _("Terminal"),
            _("Cursor, scrolling and shell behavior."),
            "utilities-terminal-symbolic",
        ),
        (
            "profiles",
            _("Profiles & Sessions"),
            _("Session restore and backups."),
            "folder-saved-search-symbolic",
        ),
        (
            "ssh",
            _("SSH Connections"),
            _("Connection persistence for SSH sessions."),
            "network-server-symbolic",
        ),
        (
            "security",
            _("Security"),
            _("Temporary files created while editing remote files."),
            "system-lock-screen-symbolic",
        ),
        (
            "ai",
            _("AI Assistant"),
            _("Configure the AI assistant."),
            "ai-assistant-symbolic",
        ),
        (
            "shortcuts",
            _("Keyboard Shortcuts"),
            _("View and customize keyboard shortcuts."),
            "input-keyboard-symbolic",
        ),
        (
            "advanced",
            _("Advanced"),
            _("Text rendering, compatibility, logs and maintenance."),
            "preferences-other-symbolic",
        ),
        (
            "about",
            _("About"),
            _("Version and project information."),
            "help-about-symbolic",
        ),
    ]


@dataclass
class _IndexedRow:
    """A searchable setting row and where it lives."""

    category: str
    group: str
    row: Gtk.Widget
    title: str
    subtitle: str


class PreferencesDialog(Adw.Window):
    """Preferences window: category sidebar, setting cards and live preview.

    Every control still applies immediately through the settings manager.
    The window remembers the first value of every key it changes since the
    last snapshot: Apply and Save Changes keep the values, Cancel (and the
    close button / Escape) restores them.
    """

    __gsignals__ = {
        "transparency-changed": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
        "headerbar-transparency-changed": (
            GObject.SignalFlags.RUN_FIRST,
            None,
            (float,),
        ),
        "font-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "setting-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, object)),
    }

    _PREVIEW_CATEGORIES = ("appearance", "terminal")

    def __init__(self, parent_window, settings_manager: SettingsManager):
        super().__init__(
            title=_("Preferences") + " — " + APP_TITLE,
            transient_for=parent_window,
            modal=False,
            default_width=1180,
            default_height=760,
        )
        _ensure_css()
        self.add_css_class("zashterminal-dialog")
        self.add_css_class("prefs-window")
        self.logger = get_logger("zashterminal.ui.dialogs.preferences")
        self.settings_manager = settings_manager

        self._pending: Dict[str, Any] = {}
        self._committed = False
        self._index: List[_IndexedRow] = []
        self._pages: Dict[str, Adw.PreferencesPage] = {}
        self._category_rows: Dict[str, Gtk.ListBoxRow] = {}
        self._category_meta: Dict[str, tuple] = {}
        self._current_category = "appearance"
        self._syncing_selection = False
        self._syncing_auto_hide = False
        self._syncing_font = False
        self._syncing_theme = False
        self._syncing_scheme = False
        self.auto_hide_sidebar_row = None
        self._preview: Optional[TerminalPreviewCard] = None
        self._swatches: Optional[SchemeSwatchesCard] = None

        self._build_layout()
        self._setup_general_page()
        self._setup_appearance_page()
        self._setup_terminal_page()
        self._setup_profiles_page()
        self._setup_ssh_page()
        self._setup_security_page()
        self._setup_ai_page()
        self._setup_shortcuts_page()
        self._setup_advanced_page()
        self._setup_about_page()

        self.settings_manager.add_change_listener(self._on_external_setting)
        self.connect("close-request", self._on_close_request)
        self.connect("map", self._on_map)
        self._show_category(self._current_category)
        self.logger.info("Preferences dialog initialized")

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_layout(self) -> None:
        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toast_overlay.set_child(root)

        header = Adw.HeaderBar()
        header.set_title_widget(Gtk.Box())
        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        title_box.append(icon_image("utilities-terminal-symbolic", 18))
        title_label = Gtk.Label(label=self.get_title())
        title_label.add_css_class("prefs-window-title")
        title_box.append(title_label)
        header.pack_start(title_box)
        root.append(header)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, vexpand=True)
        root.append(body)
        body.append(self._build_sidebar())
        body.append(self._build_content())

        shortcuts = Gtk.ShortcutController()
        shortcuts.add_shortcut(
            Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string("<Control>k"),
                Gtk.CallbackAction.new(self._focus_search),
            )
        )
        shortcuts.add_shortcut(
            Gtk.Shortcut.new(
                Gtk.ShortcutTrigger.parse_string("Escape"),
                Gtk.CallbackAction.new(self._on_escape),
            )
        )
        self.add_controller(shortcuts)

    def _build_sidebar(self) -> Gtk.Box:
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        sidebar.add_css_class("prefs-sidebar")
        sidebar.set_size_request(270, -1)
        for side in ("start", "end", "top", "bottom"):
            getattr(sidebar, f"set_margin_{side}")(0)

        search_overlay = Gtk.Overlay()
        search_overlay.set_margin_top(14)
        search_overlay.set_margin_start(14)
        search_overlay.set_margin_end(14)
        self.search_entry = Gtk.SearchEntry(placeholder_text=_("Search settings..."))
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("stop-search", self._on_stop_search)
        search_overlay.set_child(self.search_entry)
        self.search_badge = Gtk.Label(label="Ctrl+K")
        self.search_badge.add_css_class("prefs-search-badge")
        self.search_badge.set_halign(Gtk.Align.END)
        self.search_badge.set_valign(Gtk.Align.CENTER)
        self.search_badge.set_margin_end(8)
        self.search_badge.set_can_target(False)
        search_overlay.add_overlay(self.search_badge)
        sidebar.append(search_overlay)

        self.category_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.category_list.add_css_class("prefs-category-list")
        self.category_list.set_margin_start(10)
        self.category_list.set_margin_end(10)
        self.category_list.connect("row-selected", self._on_category_selected)
        for category_id, title, subtitle, icon in _categories():
            self._category_meta[category_id] = (title, subtitle, icon)
            row = Gtk.ListBoxRow()
            row.category_id = category_id
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            box.append(icon_image(icon, 18))
            box.append(Gtk.Label(label=title, xalign=0, hexpand=True))
            row.set_child(box)
            self.category_list.append(row)
            self._category_rows[category_id] = row
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(self.category_list)
        sidebar.append(scroller)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        footer.add_css_class("prefs-sidebar-footer")
        footer.set_margin_start(14)
        footer.set_margin_end(14)
        footer.set_margin_bottom(14)
        app_icon = icon_image("utilities-terminal-symbolic", 20)
        app_icon.add_css_class("prefs-app-icon")
        footer.append(app_icon)
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        text_box.set_valign(Gtk.Align.CENTER)
        name_label = Gtk.Label(label=APP_TITLE, xalign=0)
        name_label.add_css_class("prefs-app-name")
        version_label = Gtk.Label(
            label=f"v{APP_VERSION} • Linux ({platform.machine()})", xalign=0
        )
        version_label.add_css_class("prefs-app-version")
        text_box.append(name_label)
        text_box.append(version_label)
        footer.append(text_box)
        sidebar.append(footer)
        return sidebar

    def _build_content(self) -> Gtk.Box:
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)

        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        title_row.set_margin_top(18)
        title_row.set_margin_bottom(6)
        title_row.set_margin_start(24)
        title_row.set_margin_end(24)
        self.page_icon = icon_image("preferences-desktop-display-symbolic", 30)
        self.page_icon.add_css_class("prefs-page-icon")
        title_row.append(self.page_icon)
        title_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.page_title = Gtk.Label(xalign=0)
        self.page_title.add_css_class("prefs-title")
        self.page_subtitle = Gtk.Label(xalign=0, wrap=True)
        self.page_subtitle.add_css_class("prefs-subtitle")
        title_text.append(self.page_title)
        title_text.append(self.page_subtitle)
        title_row.append(title_text)
        content.append(title_row)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, vexpand=True)
        self.stack = Gtk.Stack(hexpand=True, vexpand=True)
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(self._build_results_page(), _SEARCH_PAGE)
        body.append(self.stack)

        self.preview_scroller = Gtk.ScrolledWindow()
        self.preview_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.preview_scroller.set_size_request(450, -1)
        self.preview_scroller.set_visible(False)
        self.preview_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.preview_column.set_margin_top(12)
        self.preview_column.set_margin_bottom(12)
        self.preview_column.set_margin_start(6)
        self.preview_column.set_margin_end(24)
        self.preview_scroller.set_child(self.preview_column)
        body.append(self.preview_scroller)
        content.append(body)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        footer.add_css_class("prefs-footer")
        footer.set_halign(Gtk.Align.FILL)
        spacer = Gtk.Box(hexpand=True)
        footer.append(spacer)

        cancel_button = Gtk.Button(label=_("Cancel"))
        cancel_button.connect("clicked", lambda _b: self.close())
        footer.append(cancel_button)

        apply_button = Gtk.Button(label=_("Apply"))
        apply_button.connect("clicked", self._on_apply_clicked)
        footer.append(apply_button)

        save_button = Gtk.Button()
        save_button.add_css_class("suggested-action")
        save_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        save_content.append(icon_image("object-select-symbolic", 16))
        save_content.append(Gtk.Label(label=_("Save Changes")))
        save_button.set_child(save_content)
        save_button.connect("clicked", self._on_save_clicked)
        footer.append(save_button)
        content.append(footer)
        return content

    def _build_results_page(self) -> Gtk.ScrolledWindow:
        scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        clamp = Adw.Clamp(maximum_size=720)
        clamp.set_margin_start(12)
        clamp.set_margin_end(12)
        clamp.set_margin_bottom(18)
        self.results_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        clamp.set_child(self.results_box)
        scroller.set_child(clamp)
        return scroller

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def set_visible_page_name(self, name: str) -> None:
        """Show a category page by name (ids: general, appearance, ...)."""
        if name in self._pages:
            self._show_category(name)

    def get_visible_page_name(self) -> str:
        return self._current_category

    def show_setting(self, title: str) -> bool:
        """Open the category holding the row called ``title`` and highlight it."""
        wanted = title.casefold()
        for item in self._index:
            if item.title.casefold() == wanted:
                self._open_indexed_row(item)
                return True
        return False

    def _show_category(self, category_id: str) -> None:
        if category_id not in self._pages:
            return
        self._current_category = category_id
        if self.search_entry.get_text():
            self.search_entry.set_text("")
        title, subtitle, icon = self._category_meta[category_id]
        self.page_title.set_label(title)
        self.page_subtitle.set_label(subtitle)
        set_image_from_icon(self.page_icon, icon, 30)
        self._syncing_selection = True
        self.category_list.select_row(self._category_rows[category_id])
        self._syncing_selection = False
        self.stack.set_visible_child_name(category_id)
        show_preview = category_id in self._PREVIEW_CATEGORIES
        self.preview_scroller.set_visible(show_preview)
        if show_preview and self.get_mapped():
            self._ensure_preview()

    def _on_category_selected(self, _listbox, row) -> None:
        if row is None or self._syncing_selection:
            return
        self._show_category(row.category_id)

    def _on_map(self, *_args) -> None:
        if self._current_category in self._PREVIEW_CATEGORIES:
            self._ensure_preview()

    def _ensure_preview(self) -> None:
        """Build the live preview column on first use (never for throwaway dialogs)."""
        if self._preview is not None:
            return
        self._preview = TerminalPreviewCard(self.settings_manager)
        self._swatches = SchemeSwatchesCard(
            self.settings_manager,
            self._on_swatch_selected,
            lambda: self._activate_parent_action("win.highlight-settings"),
        )
        self.preview_column.append(self._preview)
        self.preview_column.append(self._swatches)

    def _focus_search(self, *_args) -> bool:
        self.search_entry.grab_focus()
        return True

    def _on_escape(self, *_args) -> bool:
        if self.search_entry.get_text():
            self.search_entry.set_text("")
            return True
        self.close()
        return True

    def _on_stop_search(self, _entry) -> None:
        self.search_entry.set_text("")

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def _register_row(self, group: Adw.PreferencesGroup, row: Gtk.Widget) -> None:
        if hasattr(row, "set_use_markup"):
            row.set_use_markup(False)
        group.add(row)
        title = row.get_title() if hasattr(row, "get_title") else ""
        subtitle = row.get_subtitle() if hasattr(row, "get_subtitle") else ""
        if not title:
            return
        self._index.append(
            _IndexedRow(
                category=group.category_id,
                group=group.plain_title,
                row=row,
                title=title,
                subtitle=subtitle or "",
            )
        )

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        text = entry.get_text().strip()
        self.search_badge.set_visible(not entry.get_text())
        if not text:
            if self.stack.get_visible_child_name() == _SEARCH_PAGE:
                self._show_category(self._current_category)
            return
        self._show_results(text)

    def _show_results(self, text: str) -> None:
        terms = text.casefold().split()
        child = self.results_box.get_first_child()
        while child is not None:
            self.results_box.remove(child)
            child = self.results_box.get_first_child()

        matches: Dict[str, List[_IndexedRow]] = {}
        count = 0
        for item in self._index:
            haystack = " ".join(
                (
                    item.title,
                    item.subtitle,
                    item.group,
                    self._category_meta[item.category][0],
                )
            ).casefold()
            if all(term in haystack for term in terms):
                matches.setdefault(item.category, []).append(item)
                count += 1

        for category_id, items in matches.items():
            heading = Gtk.Label(label=self._category_meta[category_id][0], xalign=0)
            heading.add_css_class("prefs-result-heading")
            self.results_box.append(heading)
            result_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            result_list.add_css_class("prefs-result-list")
            result_list.connect("row-activated", self._on_result_activated)
            for item in items:
                result_list.append(self._build_result_row(item))
            self.results_box.append(result_list)
        if not matches:
            self.results_box.append(
                Adw.StatusPage(
                    title=_("No settings found"),
                    description=_("Try a different search term."),
                    icon_name="edit-find-symbolic",
                )
            )

        self.page_title.set_label(_("Search Results"))
        self.page_subtitle.set_label(
            _("{count} matching settings").format(count=count)
        )
        set_image_from_icon(self.page_icon, "edit-find-symbolic", 30)
        self._syncing_selection = True
        self.category_list.unselect_all()
        self._syncing_selection = False
        self.preview_scroller.set_visible(False)
        self.stack.set_visible_child_name(_SEARCH_PAGE)

    def _build_result_row(self, item: _IndexedRow) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow(activatable=True)
        row.indexed = item
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        title = Gtk.Label(label=item.title, xalign=0, wrap=True)
        text_box.append(title)
        detail = item.subtitle or item.group
        if detail:
            subtitle = Gtk.Label(
                label=detail,
                xalign=0,
                wrap=True,
                ellipsize=Pango.EllipsizeMode.END,
                lines=2,
            )
            subtitle.add_css_class("prefs-result-subtitle")
            text_box.append(subtitle)
        box.append(text_box)
        box.append(icon_image("go-next-symbolic", 16))
        row.set_child(box)
        return row

    def _on_result_activated(self, _listbox, row) -> None:
        self._open_indexed_row(row.indexed)

    def _open_indexed_row(self, item: _IndexedRow) -> None:
        self._show_category(item.category)
        GLib.timeout_add(150, self._reveal_row, item)

    def _reveal_row(self, item: _IndexedRow) -> bool:
        page = self._pages.get(item.category)
        if page is not None:
            self._scroll_to_row(page, item.row)
        item.row.add_css_class("prefs-row-flash")
        GLib.timeout_add(1600, self._clear_flash, item.row)
        return False

    @staticmethod
    def _clear_flash(row: Gtk.Widget) -> bool:
        row.remove_css_class("prefs-row-flash")
        return False

    @staticmethod
    def _find_scrolled(widget: Gtk.Widget) -> Optional[Gtk.ScrolledWindow]:
        if isinstance(widget, Gtk.ScrolledWindow):
            return widget
        child = widget.get_first_child()
        while child is not None:
            found = PreferencesDialog._find_scrolled(child)
            if found is not None:
                return found
            child = child.get_next_sibling()
        return None

    def _scroll_to_row(self, page: Gtk.Widget, row: Gtk.Widget) -> None:
        scrolled = self._find_scrolled(page)
        if scrolled is None:
            return
        child = scrolled.get_child()
        content = child.get_child() if isinstance(child, Gtk.Viewport) else child
        if content is None:
            return
        success, point = row.compute_point(content, Graphene.Point())
        if not success:
            return
        adjustment = scrolled.get_vadjustment()
        limit = max(0.0, adjustment.get_upper() - adjustment.get_page_size())
        adjustment.set_value(max(0.0, min(point.y - 24, limit)))

    # ------------------------------------------------------------------
    # Page and row builders
    # ------------------------------------------------------------------

    def _make_page(self, category_id: str) -> Adw.PreferencesPage:
        title, _subtitle, icon = self._category_meta[category_id]
        page = Adw.PreferencesPage(name=category_id, title=title, icon_name=icon)
        self._pages[category_id] = page
        self.stack.add_named(page, category_id)
        return page

    def _make_group(
        self, page: Adw.PreferencesPage, title: str = ""
    ) -> Adw.PreferencesGroup:
        # Group titles are rendered as markup: escape "&" and friends.
        group = Adw.PreferencesGroup(title=GLib.markup_escape_text(title))
        group.category_id = page.get_name()
        group.plain_title = title
        page.add(group)
        return group

    def _create_switch_row(
        self,
        title: str,
        subtitle: str,
        setting_key: str,
        default_value: bool = False,
    ) -> Adw.SwitchRow:
        """Create a standard switch row bound to a setting.

        Args:
            title: Row title
            subtitle: Row subtitle/description
            setting_key: Settings key to bind to
            default_value: Default value if setting not found

        Returns:
            Configured Adw.SwitchRow
        """
        row = Adw.SwitchRow(title=title, subtitle=subtitle)
        row.set_active(self.settings_manager.get(setting_key, default_value))
        row.connect(
            "notify::active",
            lambda r, _p: self._on_setting_changed(setting_key, r.get_active()),
        )
        return row

    def _create_scale(
        self, minimum: float, maximum: float, value: float, percent: bool = False
    ) -> Gtk.Scale:
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, minimum, maximum, 1)
        scale.set_value(value)
        scale.set_draw_value(True)
        scale.set_value_pos(Gtk.PositionType.RIGHT)
        scale.set_size_request(220, -1)
        scale.set_valign(Gtk.Align.CENTER)
        if percent:
            scale.set_format_value_func(lambda _s, v: f"{int(v)}%")
        return scale

    def _create_launcher_row(
        self, title: str, subtitle: str, action: str
    ) -> Adw.ActionRow:
        """Row that opens a settings dialog owned by the main window."""
        row = Adw.ActionRow(
            title=title, subtitle=subtitle, activatable=True, use_markup=False
        )
        row.add_suffix(icon_image("go-next-symbolic", 16))
        row.connect("activated", self._on_related_settings_activated, action)
        return row

    def _create_link_row(self, title: str, subtitle: str, callback) -> Adw.ActionRow:
        row = Adw.ActionRow(title=title, subtitle=subtitle, activatable=True)
        row.add_suffix(icon_image("go-next-symbolic", 16))
        row.connect("activated", lambda _r: callback())
        return row

    # --- General -------------------------------------------------------

    def _setup_general_page(self) -> None:
        page = self._make_page("general")

        startup_group = self._make_group(page, _("Startup"))
        instance_behavior_row = Adw.ComboRow(
            title=_("When Already Running"),
            subtitle=_("Action when the application is launched while already open"),
        )
        behavior_map = ["new_tab", "new_window", "focus_existing"]
        behavior_strings = [
            _("Open a new tab"),
            _("Open a new window"),
            _("Focus existing window"),
        ]
        instance_behavior_row.set_model(Gtk.StringList.new(behavior_strings))
        current_behavior = self.settings_manager.get("new_instance_behavior", "new_tab")
        try:
            behavior_index = behavior_map.index(current_behavior)
        except ValueError:
            behavior_index = 0
        instance_behavior_row.set_selected(behavior_index)
        instance_behavior_row.connect(
            "notify::selected", self._on_instance_behavior_changed, behavior_map
        )
        self._register_row(startup_group, instance_behavior_row)

        interface_group = self._make_group(page, _("Interface"))

        self.auto_hide_sidebar_row = Adw.SwitchRow(
            title=_("Auto-Hide Sessions Panel"),
            subtitle=_(
                "When on, the panel covers the terminal and closes after you open a session. The pin in the panel does the same."
            ),
        )
        self.auto_hide_sidebar_row.set_active(
            self.settings_manager.get("auto_hide_sidebar", True)
        )
        self.auto_hide_sidebar_row.connect(
            "notify::active",
            lambda r, _p: self._on_auto_hide_sidebar_changed(r.get_active()),
        )
        self._register_row(interface_group, self.auto_hide_sidebar_row)

        tab_alignment_row = Adw.ComboRow(title=_("Tab Alignment"))
        tab_alignment_row.set_model(Gtk.StringList.new([_("Left"), _("Center")]))
        current_alignment = self.settings_manager.get("tab_alignment", "center")
        tab_alignment_row.set_selected(0 if current_alignment == "left" else 1)
        tab_alignment_row.connect("notify::selected", self._on_tab_alignment_changed)
        self._register_row(interface_group, tab_alignment_row)

        # Icon Theme Strategy - Performance optimization for GTK4 startup
        icon_theme_row = Adw.ComboRow(title=_("Icon Theme"))
        icon_theme_row.set_model(
            Gtk.StringList.new([_("Zashterminal Icons (Bundled)"), _("System Icons")])
        )
        current_strategy = self.settings_manager.get("icon_theme_strategy", "zashterminal")
        icon_theme_row.set_selected(0 if current_strategy == "zashterminal" else 1)
        icon_theme_row.connect("notify::selected", self._on_icon_theme_changed)
        self._register_row(interface_group, icon_theme_row)

        # Headerbar buttons behavior when maximized
        # (for KDE Plasma Active Window Control / Borderless Maximized Windows)
        headerbar_buttons_row = Adw.ComboRow(
            title=_("Window Buttons When Maximized"),
            subtitle=_("For KDE Plasma panel integration"),
        )
        headerbar_buttons_row.set_model(
            Gtk.StringList.new(
                [
                    _("Auto-detect"),
                    _("Always hide"),
                    _("Never hide"),
                ]
            )
        )
        current_btn_setting = self.settings_manager.get(
            "hide_headerbar_buttons_when_maximized", "auto"
        )
        btn_setting_map = {"auto": 0, "always": 1, "never": 2}
        headerbar_buttons_row.set_selected(btn_setting_map.get(current_btn_setting, 0))
        headerbar_buttons_row.connect(
            "notify::selected", self._on_headerbar_buttons_mode_changed
        )
        self._register_row(interface_group, headerbar_buttons_row)

        updates_group = self._make_group(page, _("Updates"))
        self._register_row(
            updates_group,
            self._create_switch_row(
                _("Check for updates on startup"),
                _(
                    "Look for newer Releases or tags on GitHub when the app starts "
                    "(unreleased main tip is ignored unless ZASHTERMINAL_UPDATE_ALLOW_MAIN=1). "
                    "Disable entirely with ZASHTERMINAL_DISABLE_AUTO_UPDATE=1."
                ),
                "auto_update_check",
                default_value=True,
            ),
        )

    # --- Appearance ----------------------------------------------------

    def _setup_appearance_page(self) -> None:
        page = self._make_page("appearance")

        font_group = self._make_group(page, _("Font & Text"))

        font_row = Adw.ActionRow(title=_("Terminal Font"))
        self.font_button = Gtk.FontButton()
        self.font_button.set_valign(Gtk.Align.CENTER)
        self.font_button.set_size_request(220, -1)
        self.font_button.set_font(self.settings_manager.get("font", "Monospace 10"))
        # Filter to show only monospace fonts
        self.font_button.set_filter_func(self._font_filter_func)
        self.font_button.connect("font-set", self._on_font_changed)
        font_row.add_suffix(self.font_button)
        font_row.set_activatable_widget(self.font_button)
        self._register_row(font_group, font_row)

        font_size_row = Adw.ActionRow(title=_("Font Size"))
        self.font_size_spin = Gtk.SpinButton.new_with_range(6, 72, 1)
        self.font_size_spin.set_valign(Gtk.Align.CENTER)
        self.font_size_spin.set_value(self._font_size_from(self.font_button.get_font()))
        self.font_size_spin.connect("value-changed", self._on_font_size_changed)
        font_size_row.add_suffix(self.font_size_spin)
        font_size_row.set_activatable_widget(self.font_size_spin)
        self._register_row(font_group, font_size_row)

        line_spacing_row = Adw.ActionRow(title=_("Line Spacing"))
        self.spacing_spin = Gtk.SpinButton.new_with_range(0.8, 2.0, 0.05)
        self.spacing_spin.set_digits(2)
        self.spacing_spin.set_valign(Gtk.Align.CENTER)
        self.spacing_spin.set_value(self.settings_manager.get("line_spacing", 1.0))
        self.spacing_spin.connect("value-changed", self._on_line_spacing_changed)
        line_spacing_row.add_suffix(self.spacing_spin)
        line_spacing_row.set_activatable_widget(self.spacing_spin)
        self._register_row(font_group, line_spacing_row)

        self._register_row(
            font_group,
            self._create_switch_row(
                _("Use Bright Colors for Bold Text"),
                "",
                "bold_is_bright",
                default_value=True,
            ),
        )

        text_blink_row = Adw.ComboRow(title=_("Blinking Text"))
        text_blink_row.set_model(Gtk.StringList.new([_("When focused"), _("Always")]))
        text_blink_row.set_selected(self.settings_manager.get("text_blink_mode", 0))
        text_blink_row.connect("notify::selected", self._on_text_blink_mode_changed)
        self._register_row(font_group, text_blink_row)

        colors_group = self._make_group(page, _("Colors & Theme"))

        theme_row = Adw.ComboRow(title=_("Interface Theme"))
        self._theme_values = ["default", "light", "dark", "terminal"]
        theme_row.set_model(
            Gtk.StringList.new(
                [
                    _("Follow System Style"),
                    _("Light Style"),
                    _("Dark Style"),
                    _("Match Terminal Colors"),
                ]
            )
        )
        current_theme = self.settings_manager.get("gtk_theme", "default")
        try:
            theme_row.set_selected(self._theme_values.index(current_theme))
        except ValueError:
            theme_row.set_selected(0)
        theme_row.connect("notify::selected", self._on_interface_theme_changed)
        self._theme_row = theme_row
        self._register_row(colors_group, theme_row)

        scheme_order = self.settings_manager.get_scheme_order()
        all_schemes = self.settings_manager.get_all_schemes()
        scheme_row = Adw.ComboRow(title=_("Terminal Color Scheme"))
        scheme_row.set_model(
            Gtk.StringList.new(
                [all_schemes.get(key, {}).get("name", key) for key in scheme_order]
            )
        )
        scheme_row.set_selected(
            min(self.settings_manager.get("color_scheme", 0), len(scheme_order) - 1)
        )
        scheme_row.connect("notify::selected", self._on_scheme_row_changed)
        self._scheme_row = scheme_row
        self._register_row(colors_group, scheme_row)

        self._register_row(
            colors_group,
            self._create_launcher_row(
                _("Colors & Highlighting"),
                _("Color schemes, wallpaper colors and highlight rules"),
                "win.highlight-settings",
            ),
        )

        window_group = self._make_group(page, _("Window & Effects"))

        transparency_row = Adw.ActionRow(title=_("Terminal Transparency"))
        self.transparency_scale = self._create_scale(
            0, 100, self.settings_manager.get("transparency", 0), percent=True
        )
        self.transparency_scale.connect("value-changed", self._on_transparency_changed)
        transparency_row.add_suffix(self.transparency_scale)
        transparency_row.set_activatable_widget(self.transparency_scale)
        self._register_row(window_group, transparency_row)

        parent = self.get_transient_for()
        blur_supported = parent.background_blur_supported()
        self._blur_strength_supported = parent.background_blur_strength_supported()
        self.blur_row = Adw.SwitchRow(
            title=_("Background Blur"),
            subtitle=_(
                "Live blur follows this window. Strength affects only this terminal; requires transparency."
            )
            if self._blur_strength_supported
            else _(
                "Live blur follows the window. Strength is controlled by the desktop's blur effect; requires transparency."
            )
            if blur_supported
            else _("Background blur is not supported by the current compositor."),
            active=bool(self.settings_manager.get("background_blur", False)),
            sensitive=blur_supported,
        )
        self.blur_row.connect("notify::active", self._on_background_blur_changed)
        self._register_row(window_group, self.blur_row)

        self.blur_strength_row = Adw.ActionRow(
            title=_("Blur Strength"),
            subtitle=_("Applies only to this terminal window.")
            if self._blur_strength_supported
            else _(
                "Install the Zashterminal KWin effect to control strength independently."
            ),
            sensitive=self._blur_strength_supported and self.blur_row.get_active(),
        )
        self.blur_strength_scale = self._create_scale(
            1, 15, parent.background_blur_strength
        )
        self.blur_strength_scale.connect("value-changed", self._on_blur_strength_changed)
        self.blur_strength_row.add_suffix(self.blur_strength_scale)
        self.blur_strength_row.set_activatable_widget(self.blur_strength_scale)
        self._register_row(window_group, self.blur_strength_row)

        headerbar_transparency_row = Adw.ActionRow(title=_("Headerbar Transparency"))
        self.headerbar_transparency_scale = self._create_scale(
            0, 100, self.settings_manager.get("headerbar_transparency", 0), percent=True
        )
        self.headerbar_transparency_scale.connect(
            "value-changed", self._on_headerbar_transparency_changed
        )
        headerbar_transparency_row.add_suffix(self.headerbar_transparency_scale)
        headerbar_transparency_row.set_activatable_widget(
            self.headerbar_transparency_scale
        )
        self._register_row(window_group, headerbar_transparency_row)

        self._register_row(
            window_group,
            self._create_switch_row(
                _("Show Status Bar"),
                _(
                    "Connection, CPU/RAM/disk, encoding and font at the bottom of the window."
                ),
                "status_bar_visible",
                default_value=True,
            ),
        )

    # --- Terminal ------------------------------------------------------

    def _setup_terminal_page(self) -> None:
        page = self._make_page("terminal")

        behavior_group = self._make_group(page, _("Behavior"))
        self._register_row(
            behavior_group,
            self._create_switch_row(
                _("Copy on Select"),
                _("Selecting text copies it to the clipboard"),
                "copy_on_select",
                default_value=True,
            ),
        )

        cursor_group = self._make_group(page, _("Cursor"))

        cursor_shape_row = Adw.ComboRow(title=_("Cursor Shape"))
        cursor_shape_row.set_model(
            Gtk.StringList.new([_("Block"), _("I-Beam"), _("Underline")])
        )
        cursor_shape_row.set_selected(self.settings_manager.get("cursor_shape", 0))
        cursor_shape_row.connect("notify::selected", self._on_cursor_shape_changed)
        self._register_row(cursor_group, cursor_shape_row)

        cursor_blink_row = Adw.ComboRow(title=_("Cursor Blinking"))
        cursor_blink_row.set_model(
            Gtk.StringList.new([_("Follow System"), _("On"), _("Off")])
        )
        cursor_blink_row.set_selected(self.settings_manager.get("cursor_blink", 0))
        cursor_blink_row.connect("notify::selected", self._on_cursor_blink_changed)
        self._register_row(cursor_group, cursor_blink_row)

        scrolling_group = self._make_group(page, _("Scrolling"))

        scrollback_row = Adw.ActionRow(
            title=_("Scrollback Lines"),
            subtitle=_("0 for unlimited"),
        )
        scrollback_spin = Gtk.SpinButton.new_with_range(0, 1000000, 1000)
        scrollback_spin.set_valign(Gtk.Align.CENTER)
        scrollback_spin.set_value(self.settings_manager.get("scrollback_lines", 5000))
        scrollback_spin.connect("value-changed", self._on_scrollback_changed)
        scrollback_row.add_suffix(scrollback_spin)
        scrollback_row.set_activatable_widget(scrollback_spin)
        self._register_row(scrolling_group, scrollback_row)

        mouse_scroll_row = Adw.ActionRow(
            title=_("Mouse Scroll Sensitivity"),
            subtitle=_("Lower is slower"),
        )
        mouse_scroll_spin = Gtk.SpinButton.new_with_range(1, 500, 1)
        mouse_scroll_spin.set_valign(Gtk.Align.CENTER)
        mouse_scroll_spin.set_value(
            self.settings_manager.get("mouse_scroll_sensitivity", 30.0)
        )
        mouse_scroll_spin.connect(
            "value-changed", self._on_mouse_scroll_sensitivity_changed
        )
        mouse_scroll_row.add_suffix(mouse_scroll_spin)
        mouse_scroll_row.set_activatable_widget(mouse_scroll_spin)
        self._register_row(scrolling_group, mouse_scroll_row)

        touchpad_scroll_row = Adw.ActionRow(
            title=_("Touchpad Scroll Sensitivity"),
            subtitle=_("Lower is slower"),
        )
        touchpad_scroll_spin = Gtk.SpinButton.new_with_range(1, 500, 1)
        touchpad_scroll_spin.set_valign(Gtk.Align.CENTER)
        touchpad_scroll_spin.set_value(
            self.settings_manager.get("touchpad_scroll_sensitivity", 30.0)
        )
        touchpad_scroll_spin.connect(
            "value-changed", self._on_touchpad_scroll_sensitivity_changed
        )
        touchpad_scroll_row.add_suffix(touchpad_scroll_spin)
        touchpad_scroll_row.set_activatable_widget(touchpad_scroll_spin)
        self._register_row(scrolling_group, touchpad_scroll_row)

        self._register_row(
            scrolling_group,
            self._create_switch_row(
                _("Scroll on Paste"),
                "",
                "scroll_on_insert",
                default_value=True,
            ),
        )

        shell_group = self._make_group(page, _("Shell"))
        self._register_row(
            shell_group,
            self._create_switch_row(
                _("Run Command as a Login Shell"),
                "",
                "use_login_shell",
                default_value=False,
            ),
        )
        self._register_row(
            shell_group,
            self._create_switch_row(
                _("Audible Bell"),
                "",
                "bell_sound",
                default_value=False,
            ),
        )

    # --- Profiles & sessions -------------------------------------------

    def _setup_profiles_page(self) -> None:
        page = self._make_page("profiles")

        startup_group = self._make_group(page, _("Startup"))
        restore_policy_row = Adw.ComboRow(title=_("On Startup"))
        policy_map = ["always", "ask", "never"]
        policy_strings = [
            _("Always restore previous session"),
            _("Ask to restore previous session"),
            _("Never restore previous session"),
        ]
        restore_policy_row.set_model(Gtk.StringList.new(policy_strings))
        current_policy = self.settings_manager.get("session_restore_policy", "never")
        try:
            selected_index = policy_map.index(current_policy)
        except ValueError:
            selected_index = 2
        restore_policy_row.set_selected(selected_index)
        restore_policy_row.connect(
            "notify::selected", self._on_restore_policy_changed, policy_map
        )
        self._register_row(startup_group, restore_policy_row)

        backup_group = self._make_group(page, _("Backup"))

        backup_now_row = Adw.ActionRow(
            title=_("Create Backup"),
            subtitle=_("Encrypted backup of sessions and settings"),
        )
        backup_now_button = Gtk.Button(label=_("Create Backup..."))
        backup_now_button.set_valign(Gtk.Align.CENTER)
        backup_now_button.connect("clicked", self._on_backup_now_clicked)
        backup_now_row.add_suffix(backup_now_button)
        backup_now_row.set_activatable_widget(backup_now_button)
        self._register_row(backup_group, backup_now_row)

        restore_row = Adw.ActionRow(title=_("Restore from Backup"))
        restore_button = Gtk.Button(label=_("Restore..."))
        restore_button.set_valign(Gtk.Align.CENTER)
        restore_button.connect("clicked", self._on_restore_backup_clicked)
        restore_row.add_suffix(restore_button)
        restore_row.set_activatable_widget(restore_button)
        self._register_row(backup_group, restore_row)

    # --- SSH connections -----------------------------------------------

    def _setup_ssh_page(self) -> None:
        page = self._make_page("ssh")
        ssh_group = self._make_group(page, "SSH")

        persist_row = Adw.ActionRow(
            title=_("SSH Connection Persistence"),
            subtitle=_("Seconds to keep connections alive (0 to disable)"),
        )
        persist_spin = Gtk.SpinButton.new_with_range(0, 3600, 60)
        persist_spin.set_valign(Gtk.Align.CENTER)
        persist_spin.set_value(
            self.settings_manager.get("ssh_control_persist_duration", 600)
        )
        persist_spin.connect("value-changed", self._on_ssh_persist_changed)
        persist_row.add_suffix(persist_spin)
        persist_row.set_activatable_widget(persist_spin)
        self._register_row(ssh_group, persist_row)

    # --- Security ------------------------------------------------------

    def _setup_security_page(self) -> None:
        page = self._make_page("security")
        remote_edit_group = self._make_group(page, _("Remote Editing"))

        self._register_row(
            remote_edit_group,
            self._create_switch_row(
                _("Use System Temporary Directory"),
                _("Use /tmp for remote editing files"),
                "use_system_tmp_for_edit",
                default_value=False,
            ),
        )
        self._register_row(
            remote_edit_group,
            self._create_switch_row(
                _("Clear Remote Edit Files on Exit"),
                "",
                "clear_remote_edit_files_on_exit",
                default_value=False,
            ),
        )

    # --- Launcher pages ------------------------------------------------

    def _setup_ai_page(self) -> None:
        page = self._make_page("ai")
        group = self._make_group(page, _("AI Assistant"))
        self._register_row(
            group,
            self._create_launcher_row(
                _("AI Assistant"),
                _("Open the AI assistant settings"),
                "win.configure-ai",
            ),
        )

    def _setup_shortcuts_page(self) -> None:
        page = self._make_page("shortcuts")
        group = self._make_group(page, _("Keyboard Shortcuts"))
        self._register_row(
            group,
            self._create_launcher_row(
                _("Keyboard Shortcuts"),
                _("Open the keyboard shortcuts editor"),
                "win.shortcuts",
            ),
        )

    # --- Advanced ------------------------------------------------------

    def _setup_advanced_page(self) -> None:
        page = self._make_page("advanced")

        features_group = self._make_group(page, _("Text Rendering"))
        self._register_row(
            features_group,
            self._create_switch_row(
                _("Bidirectional Text Support"),
                _("For Arabic and Hebrew (affects performance)"),
                "bidi_enabled",
                default_value=False,
            ),
        )
        self._register_row(
            features_group,
            self._create_switch_row(
                _("Enable Arabic Text Shaping"),
                _("Render ligatures for Arabic script"),
                "enable_shaping",
                default_value=False,
            ),
        )
        self._register_row(
            features_group,
            self._create_switch_row(
                _("SIXEL Graphics Support"),
                _("Display SIXEL images (experimental)"),
                "sixel_enabled",
                default_value=True,
            ),
        )

        compatibility_group = self._make_group(page, _("Compatibility"))

        backspace_row = Adw.ComboRow(title=_("Backspace Key"))
        backspace_row.set_model(
            Gtk.StringList.new(
                [
                    _("Automatic"),
                    _("ASCII BACKSPACE (^H)"),
                    _("ASCII DELETE"),
                    _("Escape Sequence"),
                ]
            )
        )
        backspace_row.set_selected(self.settings_manager.get("backspace_binding", 0))
        backspace_row.connect("notify::selected", self._on_backspace_binding_changed)
        self._register_row(compatibility_group, backspace_row)

        delete_row = Adw.ComboRow(title=_("Delete Key"))
        delete_row.set_model(
            Gtk.StringList.new(
                [
                    _("Automatic"),
                    _("ASCII DELETE"),
                    _("Escape Sequence"),
                ]
            )
        )
        delete_row.set_selected(self.settings_manager.get("delete_binding", 0))
        delete_row.connect("notify::selected", self._on_delete_binding_changed)
        self._register_row(compatibility_group, delete_row)

        cjk_width_row = Adw.ComboRow(title=_("Ambiguous-width Characters"))
        cjk_width_row.set_model(
            Gtk.StringList.new([_("Narrow (single-cell)"), _("Wide (double-cell)")])
        )
        cjk_width_row.set_selected(
            self.settings_manager.get("cjk_ambiguous_width", 1) - 1
        )
        cjk_width_row.connect("notify::selected", self._on_cjk_width_changed)
        self._register_row(compatibility_group, cjk_width_row)

        selection_group = self._make_group(page, _("Selection"))
        word_chars_row = Adw.EntryRow(title=_("Word Characters"))
        word_chars_row.set_text(
            self.settings_manager.get("word_char_exceptions", "-_.:/~")
        )
        word_chars_row.connect("changed", self._on_word_chars_changed)
        self._register_row(selection_group, word_chars_row)

        log_group = self._make_group(page, _("Logs & Transcripts"))
        self._register_row(
            log_group,
            self._create_switch_row(
                _("Save Logs to File"),
                "",
                "log_to_file",
                default_value=False,
            ),
        )

        from ...utils.transcript import default_transcript_directory

        default_transcript_path = default_transcript_directory(
            self.settings_manager.get("transcript_directory", "") or ""
        )
        self._register_row(
            log_group,
            self._create_switch_row(
                _("Enable Session Transcripts"),
                _(
                    "Off by default. When enabled, reopen terminals to start recording. "
                    "Disabling stops open recordings immediately. "
                    "Files: TIMESTAMP_session_host.log under {path}."
                ).format(path=str(default_transcript_path)),
                "transcript_logging_enabled",
                default_value=False,
            ),
        )
        self._register_row(
            log_group,
            self._create_switch_row(
                _("Strip ANSI Codes from Transcripts"),
                _("Remove color/control sequences for readable log files"),
                "transcript_strip_ansi",
                default_value=True,
            ),
        )

        transcript_dir_row = Adw.ActionRow(
            title=_("Transcript Directory"),
            subtitle=_(
                "Leave empty to use {path}. Change takes effect for new sessions."
            ).format(path=str(default_transcript_directory(""))),
        )
        transcript_dir_row.set_activatable(False)
        transcript_dir_entry = Gtk.Entry()
        transcript_dir_entry.set_valign(Gtk.Align.CENTER)
        transcript_dir_entry.set_hexpand(True)
        transcript_dir_entry.set_size_request(220, -1)
        transcript_dir_entry.set_placeholder_text(str(default_transcript_directory("")))
        transcript_dir_entry.set_text(
            self.settings_manager.get("transcript_directory", "") or ""
        )
        transcript_dir_entry.connect(
            "changed",
            lambda entry: self._on_setting_changed(
                "transcript_directory", entry.get_text().strip()
            ),
        )
        transcript_dir_row.add_suffix(transcript_dir_entry)
        self._register_row(log_group, transcript_dir_row)

        log_level_row = Adw.ComboRow(title=_("Console Log Level"))
        log_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        log_level_row.set_model(Gtk.StringList.new(log_levels))
        current_level = self.settings_manager.get("console_log_level", "ERROR")
        try:
            selected_index = log_levels.index(current_level.upper())
        except ValueError:
            selected_index = 3
        log_level_row.set_selected(selected_index)
        log_level_row.connect("notify::selected", self._on_log_level_changed)
        self._register_row(log_group, log_level_row)

        reset_group = self._make_group(page)
        reset_row = Adw.ActionRow(title=_("Reset All Settings"))
        reset_button = Gtk.Button(label=_("Reset"), css_classes=["destructive-action"])
        reset_button.set_valign(Gtk.Align.CENTER)
        reset_button.connect("clicked", self._on_reset_settings_clicked)
        reset_row.add_suffix(reset_button)
        reset_row.set_activatable_widget(reset_button)
        self._register_row(reset_group, reset_row)

    # --- About ---------------------------------------------------------

    def _setup_about_page(self) -> None:
        page = self._make_page("about")
        group = self._make_group(page, APP_TITLE)

        version_row = Adw.ActionRow(title=_("Version"), subtitle=APP_VERSION)
        version_row.set_subtitle_selectable(True)
        self._register_row(group, version_row)
        self._register_row(
            group,
            self._create_link_row(
                _("About"),
                "",
                lambda: self._activate_app_action("about"),
            ),
        )
        self._register_row(
            group,
            self._create_link_row(
                _("Source Code"), WEBSITE, lambda: self._open_uri(WEBSITE)
            ),
        )
        self._register_row(
            group,
            self._create_link_row(
                _("Report an Issue"), ISSUE_URL, lambda: self._open_uri(ISSUE_URL)
            ),
        )

    def _open_uri(self, uri: str) -> None:
        try:
            Gio.AppInfo.launch_default_for_uri(uri, None)
        except GLib.Error as exc:
            self.logger.error(f"Failed to open {uri}: {exc}")

    def _activate_app_action(self, name: str) -> None:
        parent = self.get_transient_for()
        app = parent.get_application() if parent is not None else None
        if app:
            app.activate_action(name, None)

    def _activate_parent_action(self, action: str) -> None:
        parent = self.get_transient_for()
        if parent is not None:
            parent.activate_action(action, None)

    def _on_related_settings_activated(self, _row, action: str) -> None:
        self._activate_parent_action(action)

    # ------------------------------------------------------------------
    # Setting changes
    # ------------------------------------------------------------------

    def _set(self, key: str, value: Any) -> None:
        """Apply a setting and remember its pre-snapshot value for Cancel."""
        if key not in self._pending:
            self._pending[key] = self.settings_manager.get(key)
        self.settings_manager.set(key, value)

    def _on_setting_changed(self, key: str, value) -> None:
        self._set(key, value)
        self.emit("setting-changed", key, value)

    @staticmethod
    def _font_size_from(font: str) -> float:
        size = Pango.FontDescription.from_string(font).get_size() / Pango.SCALE
        return size if size > 0 else 10.0

    def _on_font_changed(self, font_button) -> None:
        font = font_button.get_font()
        self._set("font", font)
        self._syncing_font = True
        self.font_size_spin.set_value(self._font_size_from(font))
        self._syncing_font = False
        self.emit("font-changed", font)

    def _on_font_size_changed(self, spin_button) -> None:
        if self._syncing_font:
            return
        description = Pango.FontDescription.from_string(self.font_button.get_font())
        description.set_size(int(round(spin_button.get_value() * Pango.SCALE)))
        font = description.to_string()
        self.font_button.set_font(font)
        self._set("font", font)
        self.emit("font-changed", font)

    @staticmethod
    def _font_filter_func(family, _face):
        """Filter function to show only monospace fonts."""
        return family.is_monospace()

    def _on_line_spacing_changed(self, spin_button) -> None:
        value = spin_button.get_value()
        self._on_setting_changed("line_spacing", value)

    def _on_interface_theme_changed(self, combo_row, _param) -> None:
        if self._syncing_theme:
            return
        index = combo_row.get_selected()
        if 0 <= index < len(self._theme_values):
            self._on_setting_changed("gtk_theme", self._theme_values[index])

    def _on_scheme_row_changed(self, combo_row, _param) -> None:
        if self._syncing_scheme:
            return
        self._apply_color_scheme(combo_row.get_selected())

    def _on_swatch_selected(self, scheme_name: str) -> None:
        order = self.settings_manager.get_scheme_order()
        if scheme_name in order:
            self._apply_color_scheme(order.index(scheme_name))

    def _apply_color_scheme(self, index: int) -> None:
        """Pick a manual color scheme exactly like the color scheme dialogs do."""
        settings = self.settings_manager
        if settings.get("wallpaper_theme") is not None:
            self._set("wallpaper_theme", None)
        self._set("color_scheme", index)
        self.emit("setting-changed", "color_scheme", index)
        try:
            from ...terminal.highlighter import get_shell_input_highlighter

            get_shell_input_highlighter().refresh_settings()
        except Exception as exc:
            self.logger.debug(f"Shell input highlighter refresh skipped: {exc}")

    def _on_transparency_changed(self, scale) -> None:
        value = scale.get_value()
        self._set("transparency", value)
        self.emit("transparency-changed", value)

    def _on_background_blur_changed(self, row, _param) -> None:
        self._set("background_blur", row.get_active())
        self.blur_strength_row.set_sensitive(
            self._blur_strength_supported and row.get_active()
        )

    def _on_blur_strength_changed(self, scale) -> None:
        value = int(scale.get_value())
        self._set("background_blur_strength", value)
        window = self.get_transient_for()
        window.background_blur_strength = value
        window.apply_background_blur()

    def _on_headerbar_transparency_changed(self, scale) -> None:
        value = scale.get_value()
        self._set("headerbar_transparency", value)
        self.emit("headerbar-transparency-changed", value)

    def _on_restore_policy_changed(self, combo_row, _param, policy_map):
        index = combo_row.get_selected()
        if 0 <= index < len(policy_map):
            policy = policy_map[index]
            self._on_setting_changed("session_restore_policy", policy)

    def _on_instance_behavior_changed(self, combo_row, _param, behavior_map):
        index = combo_row.get_selected()
        if 0 <= index < len(behavior_map):
            behavior = behavior_map[index]
            self._on_setting_changed("new_instance_behavior", behavior)

    def _on_backup_now_clicked(self, button):
        self._activate_app_action("backup-now")

    def _on_restore_backup_clicked(self, button):
        self._activate_app_action("restore-backup")

    def _on_log_level_changed(self, combo_row, _param):
        selected_item = combo_row.get_selected_item()
        if selected_item:
            level_str = selected_item.get_string()
            self._on_setting_changed("console_log_level", level_str)

    def _on_scrollback_changed(self, spin_button) -> None:
        value = int(spin_button.get_value())
        self._on_setting_changed("scrollback_lines", value)

    def _on_mouse_scroll_sensitivity_changed(self, spin_button) -> None:
        value = spin_button.get_value()
        self._on_setting_changed("mouse_scroll_sensitivity", value)

    def _on_touchpad_scroll_sensitivity_changed(self, spin_button) -> None:
        value = spin_button.get_value()
        self._on_setting_changed("touchpad_scroll_sensitivity", value)

    def _on_cursor_shape_changed(self, combo_row, _param) -> None:
        index = combo_row.get_selected()
        self._on_setting_changed("cursor_shape", index)

    def _on_cursor_blink_changed(self, combo_row, _param) -> None:
        index = combo_row.get_selected()
        self._on_setting_changed("cursor_blink", index)

    def _on_text_blink_mode_changed(self, combo_row, _param) -> None:
        index = combo_row.get_selected()
        self._on_setting_changed("text_blink_mode", index)

    def _on_tab_alignment_changed(self, combo_row, _param) -> None:
        index = combo_row.get_selected()
        alignment = "left" if index == 0 else "center"
        self._on_setting_changed("tab_alignment", alignment)

    def _on_icon_theme_changed(self, combo_row, _param) -> None:
        """Handle icon theme strategy change.

        Note: This change requires application restart to take full effect
        since icon paths are configured at startup for performance.
        """
        index = combo_row.get_selected()
        strategy = "zashterminal" if index == 0 else "system"
        self._on_setting_changed("icon_theme_strategy", strategy)
        # Show restart notice
        self._show_restart_required_dialog(
            _("Icon Theme Changed"),
            _(
                "The icon theme change will take effect after restarting the application."
            ),
        )

    def _on_headerbar_buttons_mode_changed(self, combo_row, _param) -> None:
        """Handle window buttons visibility mode change for maximized windows."""
        index = combo_row.get_selected()
        mode_map = {0: "auto", 1: "always", 2: "never"}
        mode = mode_map.get(index, "auto")
        self._on_setting_changed("hide_headerbar_buttons_when_maximized", mode)

    def _show_restart_required_dialog(self, title: str, message: str) -> None:
        """Show a dialog informing the user that a restart is required."""
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=title,
            body=message,
        )
        dialog.add_response("ok", _("OK"))
        dialog.set_default_response("ok")
        dialog.present()

    def _on_word_chars_changed(self, entry_row):
        text = entry_row.get_text()
        self._on_setting_changed("word_char_exceptions", text)

    def _on_cjk_width_changed(self, combo_row, _param) -> None:
        value = combo_row.get_selected() + 1  # 0 -> 1 (Narrow), 1 -> 2 (Wide)
        self._on_setting_changed("cjk_ambiguous_width", value)

    def _on_backspace_binding_changed(self, combo_row, _param) -> None:
        index = combo_row.get_selected()
        self._on_setting_changed("backspace_binding", index)

    def _on_delete_binding_changed(self, combo_row, _param) -> None:
        index = combo_row.get_selected()
        self._on_setting_changed("delete_binding", index)

    def _on_ssh_persist_changed(self, spin_button) -> None:
        value = int(spin_button.get_value())
        self._on_setting_changed("ssh_control_persist_duration", value)

    # ------------------------------------------------------------------
    # External changes (panel pin, header menu, other dialogs)
    # ------------------------------------------------------------------

    def _on_external_setting(self, key: str, _old_value, new_value) -> None:
        if key == "auto_hide_sidebar" and self.auto_hide_sidebar_row is not None:
            active = bool(new_value)
            if self.auto_hide_sidebar_row.get_active() != active:
                self._syncing_auto_hide = True
                self.auto_hide_sidebar_row.set_active(active)
                self._syncing_auto_hide = False
            return

        if key == "gtk_theme":
            self._sync_theme_row()
        elif key in ("color_scheme", "wallpaper_theme"):
            self._sync_scheme_row()
            if self._swatches is not None:
                self._swatches.sync()

        if self._preview is not None and (
            key in _PREVIEW_KEYS or key.startswith("custom_schemes")
        ):
            self._preview.refresh()

    def _sync_theme_row(self) -> None:
        theme = self.settings_manager.get("gtk_theme", "default")
        index = self._theme_values.index(theme) if theme in self._theme_values else 0
        if self._theme_row.get_selected() != index:
            self._syncing_theme = True
            self._theme_row.set_selected(index)
            self._syncing_theme = False

    def _sync_scheme_row(self) -> None:
        total = len(self.settings_manager.get_scheme_order())
        index = min(self.settings_manager.get("color_scheme", 0), total - 1)
        if self._scheme_row.get_selected() != index:
            self._syncing_scheme = True
            self._scheme_row.set_selected(index)
            self._syncing_scheme = False

    def _on_auto_hide_sidebar_changed(self, new_value: bool) -> None:
        """Keep the preference switch and the panel pin on the same setting."""
        if self._syncing_auto_hide:
            return
        if self.settings_manager.get("auto_hide_sidebar", True) == new_value:
            return
        self._on_setting_changed("auto_hide_sidebar", new_value)

    # ------------------------------------------------------------------
    # Footer actions: Cancel / Apply / Save Changes
    # ------------------------------------------------------------------

    def _on_apply_clicked(self, _button) -> None:
        """Keep the current values and make them the new Cancel baseline."""
        self._pending.clear()
        self.toast_overlay.add_toast(Adw.Toast(title=_("Settings applied")))

    def _on_save_clicked(self, _button) -> None:
        self._pending.clear()
        self.close()

    def _on_close_request(self, *_args) -> bool:
        self.settings_manager.remove_change_listener(self._on_external_setting)
        self._revert_pending()
        return False

    def _revert_pending(self) -> None:
        """Restore every key changed since the last snapshot (Cancel)."""
        pending, self._pending = self._pending, {}
        if not pending:
            return
        window = self.get_transient_for()
        for key, old_value in reversed(list(pending.items())):
            if old_value is None and key != "wallpaper_theme":
                continue
            try:
                self.settings_manager.set(key, old_value)
            except Exception as exc:
                self.logger.error(f"Failed to restore setting '{key}': {exc}")
                continue
            if key == "background_blur_strength" and window is not None:
                window.background_blur_strength = old_value
                window.apply_background_blur()
            elif key == "font":
                self.emit("font-changed", old_value)
            elif key == "transparency":
                self.emit("transparency-changed", float(old_value))
            elif key == "headerbar_transparency":
                self.emit("headerbar-transparency-changed", float(old_value))
            self.emit("setting-changed", key, old_value)
        self.logger.info("Preferences cancelled: restored previous values")

    def _on_reset_settings_clicked(self, button) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self,
            title=_("Reset All Settings"),
            body=_(
                "Are you sure you want to reset all settings to their default values? This action cannot be undone."
            ),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("reset", _("Reset All Settings"))
        dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(dlg, response_id):
            if response_id == "reset":
                try:
                    self.settings_manager.reset_to_defaults()
                    # A reset cannot be undone: nothing is left to restore.
                    self._pending.clear()
                    success_dialog = Adw.MessageDialog(
                        transient_for=self,
                        title=_("Settings Reset"),
                        body=_(
                            "All settings have been reset to their default values. Please restart the application for all changes to take effect."
                        ),
                    )
                    success_dialog.add_response("ok", _("OK"))
                    success_dialog.present()
                    self.logger.info("All settings reset to defaults")
                except Exception as e:
                    self.logger.error(f"Failed to reset settings: {e}")
                    error_dialog = Adw.MessageDialog(
                        transient_for=self,
                        title=_("Reset Failed"),
                        body=_("Failed to reset settings: {}").format(e),
                    )
                    error_dialog.add_response("ok", _("OK"))
                    error_dialog.present()
            dlg.close()

        dialog.connect("response", on_response)
        dialog.present()
