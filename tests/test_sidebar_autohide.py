"""Unpinned sessions panel closes after a session is activated."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from zashterminal.sessions.models import SessionItem
from zashterminal.sessions.tree import SessionTreeView
import zashterminal.sessions.tree as tree_mod


class _FilterModel:
    def __init__(self, row):
        self._row = row

    def get_item(self, position):
        return self._row if position == 0 else None


class SidebarAutoHideTests(unittest.TestCase):
    def test_activation_closes_unpinned_panel(self):
        manager = SimpleNamespace(closed=0)

        def close_if_unpinned():
            manager.closed += 1

        manager.close_if_unpinned = close_if_unpinned
        settings = SimpleNamespace(get=lambda key, default=False: True)
        view = SimpleNamespace(
            filter_model=_FilterModel(SimpleNamespace(get_item=lambda: SessionItem("alma"))),
            settings_manager=settings,
            parent_window=SimpleNamespace(sidebar_manager=manager),
            on_session_activated=lambda _item: None,
        )
        scheduled = {}

        def timeout_add(_delay, callback):
            scheduled["callback"] = callback
            return 1

        with patch.object(tree_mod.GLib, "timeout_add", timeout_add):
            SessionTreeView._on_row_activated(view, None, 0)

        self.assertIn("callback", scheduled)
        self.assertFalse(scheduled["callback"]())
        self.assertEqual(manager.closed, 1)

    def test_activation_skips_close_without_manager(self):
        settings = SimpleNamespace(get=lambda key, default=False: True)
        view = SimpleNamespace(
            filter_model=_FilterModel(SimpleNamespace(get_item=lambda: SessionItem("alma"))),
            settings_manager=settings,
            parent_window=SimpleNamespace(),
            on_session_activated=lambda _item: None,
        )

        def timeout_add(_delay, callback):
            self.assertFalse(callback())
            return 1

        with patch.object(tree_mod.GLib, "timeout_add", timeout_add):
            SessionTreeView._on_row_activated(view, None, 0)
