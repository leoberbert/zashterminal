"""Widget-free logic added by the UX redesign (runs without a display)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GObject, Gtk

from zashterminal.helpers import normalize_accelerator
from zashterminal.sessions.models import SessionItem
from zashterminal.sessions import tree as tree_module
from zashterminal.sessions.tree import SectionHeader, SessionTreeView
from zashterminal.ui import menus
from zashterminal.ui.command_palette import (
    _action_entries,
    _individual_preference_entries,
    _preferences_entries,
    build_entries,
)
from zashterminal.utils import tooltip_helper


def _menu_actions(menu: Gio.MenuModel) -> list:
    """Every action name in a menu model, sections and submenus flattened."""
    found = []
    for i in range(menu.get_n_items()):
        action = menu.get_item_attribute_value(i, "action")
        if action is not None:
            found.append(action.get_string())
        for link in ("section", "submenu"):
            child = menu.get_item_link(i, link)
            if child is not None:
                found.extend(_menu_actions(child))
    return found


class FavoriteSessionTests(unittest.TestCase):
    def test_favorite_defaults_off_and_round_trips(self):
        self.assertFalse(SessionItem("a").favorite)
        item = SessionItem("a", favorite=True)
        self.assertTrue(SessionItem.from_dict(item.to_dict()).favorite)

    def test_legacy_data_without_favorite_loads(self):
        data = SessionItem("a").to_dict()
        del data["favorite"]
        self.assertFalse(SessionItem.from_dict(data).favorite)

    def test_setter_marks_modified_only_on_change(self):
        item = SessionItem("a")
        with mock.patch.object(item, "_mark_modified") as marked:
            item.favorite = False
            marked.assert_not_called()
            item.favorite = True
            marked.assert_called_once()


class NormalizeAcceleratorTests(unittest.TestCase):
    def test_modifier_order_is_canonical(self):
        self.assertEqual(
            normalize_accelerator("<Control><Shift>c"),
            normalize_accelerator("<Shift><Control>c"),
        )

    def test_empty_and_unparsable_pass_through(self):
        self.assertEqual(normalize_accelerator(""), "")
        self.assertEqual(normalize_accelerator("not an accel"), "not an accel")


class AccessibleLabelTests(unittest.TestCase):
    def test_icon_only_controls_get_the_tooltip_as_name(self):
        for cls in (Gtk.Button, Gtk.MenuButton, Gtk.Switch):
            widget = mock.MagicMock(spec=cls)
            if hasattr(widget, "get_label"):
                widget.get_label.return_value = None
            tooltip_helper.set_accessible_label(widget, "Close")
            widget.update_property.assert_called_once_with(
                [Gtk.AccessibleProperty.LABEL], ["Close"]
            )

    def test_buttons_with_visible_text_and_other_widgets_are_untouched(self):
        labelled = mock.MagicMock(spec=Gtk.Button)
        labelled.get_label.return_value = "Save"
        tooltip_helper.set_accessible_label(labelled, "Save")
        labelled.update_property.assert_not_called()

        label = mock.MagicMock(spec=Gtk.Label)
        tooltip_helper.set_accessible_label(label, "x")
        label.update_property.assert_not_called()


class TooltipHelperTests(unittest.TestCase):
    def _helper(self, native=False):
        helper = tooltip_helper.TooltipHelper.__new__(tooltip_helper.TooltipHelper)
        helper._use_native_tooltips = native
        return helper

    def test_native_tooltip_path_sets_text_and_accessible_name(self):
        widget = mock.MagicMock(spec=Gtk.Button)
        widget.get_label.return_value = None
        self._helper(native=True).add_tooltip(widget, "Hello")
        widget.set_tooltip_text.assert_called_once_with("Hello")
        widget.update_property.assert_called_once()

    def test_empty_text_does_nothing(self):
        widget = mock.MagicMock(spec=Gtk.Button)
        self._helper().add_tooltip(widget, "")
        widget.set_tooltip_text.assert_not_called()

    def test_updating_text_reuses_one_hover_controller(self):
        helper = self._helper()
        widget = mock.MagicMock(spec=Gtk.Button)
        widget.get_label.return_value = "x"
        with mock.patch.object(
            tooltip_helper.Gtk.EventControllerMotion, "new"
        ) as new_controller:
            helper.add_tooltip(widget, "first")
            helper.add_tooltip(widget, "second")
        new_controller.assert_called_once()
        widget.add_controller.assert_called_once()
        self.assertEqual(widget._custom_tooltip_text, "second")
        self.assertIsNone(widget._custom_tooltip_action)

    def test_shortcut_tooltip_appends_label_and_keeps_action(self):
        helper = self._helper()
        widget = mock.MagicMock(spec=Gtk.Button)
        widget.get_label.return_value = "x"
        with mock.patch.object(
            helper, "_get_shortcut_label", return_value="Ctrl+K"
        ), mock.patch.object(tooltip_helper.Gtk.EventControllerMotion, "new"):
            helper.add_tooltip_with_shortcut(widget, "Search", "toggle-search")
        self.assertEqual(widget._custom_tooltip_text, "Search (Ctrl+K)")
        self.assertEqual(widget._custom_tooltip_action, "toggle-search")

    def test_shortcut_tooltip_native_path_includes_shortcut(self):
        helper = self._helper(native=True)
        widget = mock.MagicMock(spec=Gtk.Button)
        widget.get_label.return_value = "x"
        with mock.patch.object(helper, "_get_shortcut_label", return_value=None):
            helper.add_tooltip_with_shortcut(widget, "Search", "toggle-search")
        widget.set_tooltip_text.assert_called_once_with("Search")


class SessionTreeLogicTests(unittest.TestCase):
    def _tree(self, terminals):
        tree = SessionTreeView.__new__(SessionTreeView)
        tab_manager = SimpleNamespace(get_all_terminals_across_tabs=lambda: terminals)
        tree.parent_window = SimpleNamespace(tab_manager=tab_manager)
        return tree

    def test_section_header_is_a_childless_named_row(self):
        header = SectionHeader("Favorites")
        self.assertEqual(header.name, "Favorites")
        self.assertIsNone(header.children)

    def test_row_markup_shows_target_for_ssh_and_escapes_markup(self):
        ssh = SessionItem("a&b", host="h", user="u", favorite=True)
        markup = SessionTreeView._row_markup(ssh)
        self.assertTrue(markup.startswith("<b>★ a&amp;b</b>\n"))
        self.assertIn("u@h", markup)

    def test_row_markup_is_title_only_without_a_subtitle(self):
        local = SessionTreeView._row_markup(SessionItem("l", session_type="local"))
        self.assertTrue(local.startswith("<b>l</b>\n"))  # "Local terminal" subtitle
        self.assertEqual(
            SessionTreeView._row_markup(SessionItem("x", host="")), "<b>x</b>"
        )
        # Folders and other rows stay plain text
        self.assertEqual(SessionTreeView._row_markup(SimpleNamespace(name=None)), "")

    def test_open_session_names_only_counts_saved_sessions(self):
        saved = SessionItem("saved")
        terminals = [
            SimpleNamespace(zashterminal_session_item=saved),
            SimpleNamespace(zashterminal_session_item=None),
            SimpleNamespace(),
        ]
        self.assertEqual(self._tree(terminals)._open_session_names(), {"saved"})

    def test_open_session_names_survives_missing_or_broken_tab_manager(self):
        tree = SessionTreeView.__new__(SessionTreeView)
        tree.parent_window = SimpleNamespace()
        self.assertEqual(tree._open_session_names(), set())

        def boom():
            raise RuntimeError("tabs gone")

        tree.parent_window = SimpleNamespace(
            tab_manager=SimpleNamespace(get_all_terminals_across_tabs=boom)
        )
        self.assertEqual(tree._open_session_names(), set())

    def test_refresh_only_when_connected_set_changes(self):
        tree = self._tree([SimpleNamespace(zashterminal_session_item=SessionItem("a"))])
        tree._connected_names = {"a"}
        with mock.patch.object(tree, "refresh_tree") as refresh:
            tree.refresh_if_connections_changed()
            refresh.assert_not_called()
            tree._connected_names = set()
            tree.refresh_if_connections_changed()
            refresh.assert_called_once()

    def test_search_hides_section_headers(self):
        tree = SessionTreeView.__new__(SessionTreeView)
        tree._filter_text = "a"
        tree._top_rows = 0
        self.assertFalse(tree._filter_func(SectionHeader("All")))
        self.assertTrue(tree._filter_func(SimpleNamespace(name="alpha")))

    def test_refresh_builds_connected_then_favorites_then_all(self):
        connected = SessionItem("conn")
        fav = SessionItem("fav", favorite=True)
        both = SessionItem("both", favorite=True)
        plain = SessionItem("plain")
        tree = SessionTreeView.__new__(SessionTreeView)
        tree.root_store = Gio.ListStore.new(GObject.GObject)
        tree.folder_store = Gio.ListStore.new(GObject.GObject)
        tree.session_store = [plain, fav, both, connected]
        tree._populated_folders = set()
        terminals = [
            SimpleNamespace(zashterminal_session_item=connected),
            SimpleNamespace(zashterminal_session_item=both),
        ]
        tree.parent_window = SimpleNamespace(
            layouts=[],
            tab_manager=SimpleNamespace(get_all_terminals_across_tabs=lambda: terminals),
        )
        with mock.patch.object(tree_module.GLib, "idle_add"):
            tree.refresh_tree()
        rows = [
            f"#{i.name}" if isinstance(i, SectionHeader) else i.name
            for i in tree.root_store
        ]
        self.assertEqual(
            rows,
            ["#Connected", "both", "conn", "#Favorites", "fav", "#All"]
            + ["both", "conn", "fav", "plain"],
        )
        self.assertEqual(tree._top_rows, 6)

    def test_refresh_without_connected_or_favorites_has_no_headers(self):
        tree = SessionTreeView.__new__(SessionTreeView)
        tree.root_store = Gio.ListStore.new(GObject.GObject)
        tree.folder_store = Gio.ListStore.new(GObject.GObject)
        tree.session_store = [SessionItem("only")]
        tree._populated_folders = set()
        tree.parent_window = SimpleNamespace(layouts=[])
        with mock.patch.object(tree_module.GLib, "idle_add"):
            tree.refresh_tree()
        self.assertEqual([i.name for i in tree.root_store], ["only"])
        self.assertEqual(tree._top_rows, 0)


class MenuModelTests(unittest.TestCase):
    def test_main_menu_exposes_every_action(self):
        window = SimpleNamespace(tftp_server=None)
        menu, tools = menus.MainApplicationMenu.build_menu_model(window)
        actions = _menu_actions(menu)
        for name in (
            "win.new-window",
            "win.command-palette",
            "win.preferences",
            "win.highlight-settings",
            "win.configure-ai",
            "win.shortcuts",
            "win.import-securecrt-sessions",
            "win.import-putty-sessions",
            "win.import-multiputty-sessions",
            "win.import-mremoteng-sessions",
            "win.toggle-tftp-server",
            "app.about",
            "app.quit",
        ):
            self.assertIn(name, actions)
        self.assertEqual(tools.get_n_items(), 1)

    def test_tftp_label_follows_server_state_in_place(self):
        window = SimpleNamespace(tftp_server=SimpleNamespace(is_running=False))
        _menu, tools = menus.MainApplicationMenu.build_menu_model(window)
        label = lambda: tools.get_item_attribute_value(0, "label").get_string()
        stopped = label()
        window.tftp_server.is_running = True
        popover = SimpleNamespace(tftp_section=tools)
        menus.MainApplicationMenu.update_tftp_server_label(window, popover)
        self.assertNotEqual(label(), stopped)
        self.assertEqual(tools.get_n_items(), 1)
        menus.MainApplicationMenu.update_tftp_server_label(window, SimpleNamespace())

    def test_session_menu_offers_sftp_only_for_ssh(self):
        ssh = _menu_actions(menus.create_session_menu(SessionItem("s"), None, 0))
        local = _menu_actions(
            menus.create_session_menu(SessionItem("l", session_type="local"), None, 0)
        )
        self.assertIn("win.connect-sftp", ssh)
        self.assertNotIn("win.connect-sftp", local)

    def test_move_to_folder_needs_folders(self):
        item = SessionItem("s")
        none = SimpleNamespace(get_n_items=lambda: 0)
        some = SimpleNamespace(get_n_items=lambda: 2)
        self.assertNotIn(
            "win.move-session-to-folder",
            _menu_actions(menus.create_session_menu(item, None, 0, none)),
        )
        self.assertIn(
            "win.move-session-to-folder",
            _menu_actions(menus.create_session_menu(item, None, 0, some)),
        )

    def test_paste_entries_follow_clipboard(self):
        self.assertNotIn("win.paste-item", _menu_actions(menus.create_folder_menu(None, None, 0)))
        self.assertIn(
            "win.paste-item",
            _menu_actions(menus.create_folder_menu(None, None, 0, clipboard_has_content=True)),
        )
        self.assertNotIn("win.paste-item-root", _menu_actions(menus.create_root_menu()))
        self.assertIn("win.paste-item-root", _menu_actions(menus.create_root_menu(True)))

    def test_terminal_menu_link_and_ai_sections(self):
        terminal = mock.MagicMock()
        terminal.get_char_width.return_value = 10
        terminal.get_char_height.return_value = 10
        terminal.match_check.return_value = ("https://example.org", 0)
        terminal.get_has_selection.return_value = True
        settings = {"ai_assistant_enabled": True}
        manager = SimpleNamespace(get=lambda key, default=None: settings.get(key, default))
        actions = _menu_actions(menus.create_terminal_menu(terminal, 5, 5, manager))
        self.assertIn("win.open-url", actions)
        self.assertIn("win.ask-ai-selection", actions)
        self.assertEqual(terminal._context_menu_url, "https://example.org")

        terminal.match_check.return_value = None
        terminal.get_has_selection.return_value = False
        actions = _menu_actions(menus.create_terminal_menu(terminal, 5, 5, manager))
        self.assertNotIn("win.open-url", actions)
        self.assertNotIn("win.ask-ai-selection", actions)
        self.assertIn("win.split-horizontal", actions)


class PaletteEntryBuildingTests(unittest.TestCase):
    def _window(self, sessions, known_actions=("save-layout", "shortcuts")):
        store = SimpleNamespace(
            get_n_items=lambda: len(sessions), get_item=lambda i: sessions[i]
        )
        app = SimpleNamespace(get_accels_for_action=lambda name: ["<Control>k"])
        return SimpleNamespace(
            session_store=store,
            layouts=[SimpleNamespace(name="dev")],
            tftp_server=None,
            get_application=lambda: app,
            lookup_action=lambda name: name in known_actions,
            activate_action=mock.MagicMock(),
            settings_manager=mock.MagicMock(),
            action_handler=mock.MagicMock(),
            tab_manager=mock.MagicMock(),
            _on_session_activated=mock.MagicMock(),
            execute_toolbar_command=mock.MagicMock(),
        )

    def test_action_entries_skip_unregistered_actions_and_carry_accel(self):
        window = self._window([])
        entries = _action_entries(window)
        self.assertEqual(len(entries), 2)
        self.assertTrue(all(e.accel for e in entries))
        entries[0].activate()
        window.activate_action.assert_called_once()

    def test_preference_page_entries_open_their_page(self):
        window = self._window([])
        with mock.patch(
            "zashterminal.ui.dialogs.PreferencesDialog", side_effect=RuntimeError
        ):
            entries = _preferences_entries(window)
        self.assertEqual(len(entries), 4)
        entries[0].activate()
        window.action_handler.preferences.assert_called_once_with(page="appearance")

    def test_unbuildable_preferences_dialog_yields_no_row_entries(self):
        with mock.patch(
            "zashterminal.ui.dialogs.PreferencesDialog", side_effect=RuntimeError
        ):
            self.assertEqual(_individual_preference_entries(self._window([]), None), [])

    def test_row_entries_come_from_walking_the_preferences_dialog(self):
        def node(spec, children=(), **props):
            widget = mock.MagicMock(spec=spec)
            if "name" in props:
                widget.get_name.return_value = props["name"]
            if "title" in props:
                widget.get_title.return_value = props["title"]
            widget._kids = list(children)
            return widget

        def link(parent):
            kids = parent._kids
            parent.get_first_child.return_value = kids[0] if kids else None
            for a, b in zip(kids, kids[1:] + [None]):
                a.get_next_sibling.return_value = b
            for kid in kids:
                link(kid)

        blur = node(Adw.PreferencesRow, title="Blur")
        untitled = node(Adw.PreferencesRow, title="")
        page = node(
            Adw.PreferencesPage, [blur, untitled], name="appearance", title="Look"
        )
        dialog = node(None, [page])
        link(dialog)
        window = self._window([])
        opened = []
        with mock.patch("zashterminal.ui.dialogs.PreferencesDialog", return_value=dialog):
            entries = _individual_preference_entries(window, opened.append)
        self.assertEqual([e.title for e in entries], ["Blur"])
        self.assertEqual(entries[0].subtitle.rsplit(" ", 1)[-1], "Look")
        entries[0].activate()
        self.assertEqual(opened, ["appearance"])
        window.settings_manager.remove_change_listener.assert_called_once()
        dialog.destroy.assert_called_once()

    def test_build_entries_orders_favorites_first_and_adds_sftp_for_ssh(self):
        plain = SessionItem("plain", host="p", user="u", folder_path="/Prod/")
        fav = SessionItem("fav", host="f", favorite=True)
        local = SessionItem("loc", session_type="local")
        window = self._window([plain, fav, local])
        fake_manager = mock.MagicMock()
        fake_manager.get_all_commands.return_value = [
            SimpleNamespace(
                id="1", name="Shown", command_template="ls", icon_name=""
            ),
            SimpleNamespace(
                id="2", name="Hidden", command_template="rm", icon_name="x"
            ),
        ]
        fake_manager.is_command_hidden.side_effect = lambda cid: cid == "2"
        with mock.patch(
            "zashterminal.data.command_manager_models.get_command_button_manager",
            return_value=fake_manager,
        ), mock.patch(
            "zashterminal.ui.dialogs.PreferencesDialog", side_effect=RuntimeError
        ):
            entries = build_entries(window)

        by_title = {e.title: e for e in entries}
        self.assertIn("★ fav", by_title)
        self.assertEqual(entries[0].title, "★ fav")
        self.assertIn("SFTP: plain", by_title)
        self.assertNotIn("SFTP: loc", by_title)
        self.assertEqual(by_title["plain"].subtitle, "u@p · Prod")
        self.assertEqual(by_title["loc"].icon_name, "computer-symbolic")
        self.assertEqual(by_title["Shown"].icon_name, "utilities-terminal-symbolic")
        self.assertNotIn("Hidden", by_title)

        by_title["plain"].activate()
        window._on_session_activated.assert_called_once_with(plain)
        by_title["SFTP: plain"].activate()
        window.tab_manager.create_sftp_tab.assert_called_once_with(plain)
        by_title["Shown"].activate()
        by_title["dev"].activate()
        window.activate_action.assert_called_once()

    def test_build_entries_tolerates_command_manager_failure(self):
        window = self._window([])
        with mock.patch(
            "zashterminal.data.command_manager_models.get_command_button_manager",
            side_effect=RuntimeError,
        ), mock.patch(
            "zashterminal.ui.dialogs.PreferencesDialog", side_effect=RuntimeError
        ):
            kinds = {e.kind for e in build_entries(window)}
        self.assertNotIn("command", kinds)
        self.assertIn("layout", kinds)


if __name__ == "__main__":
    unittest.main()
