"""Conventional shortcut aliases never override or duplicate user shortcuts."""

from __future__ import annotations

import logging
import unittest
from types import SimpleNamespace

from zashterminal.app import ZashTerminalApp
from zashterminal.helpers import normalize_accelerator


def _apply(shortcuts: dict) -> dict:
    """Run apply_window_shortcuts on a stub and return {action: accels}."""
    applied = {}
    stub = SimpleNamespace(
        settings_manager=SimpleNamespace(get_shortcut=lambda name: shortcuts.get(name, "")),
        SHORTCUT_ALIASES=ZashTerminalApp.SHORTCUT_ALIASES,
        WINDOW_SHORTCUT_ACTIONS=ZashTerminalApp.WINDOW_SHORTCUT_ACTIONS,
        set_accels_for_action=lambda action, accels: applied.__setitem__(action, accels),
        logger=logging.getLogger("test"),
    )
    ZashTerminalApp.apply_window_shortcuts(stub)
    return applied


class ShortcutAliasTests(unittest.TestCase):
    def test_alias_added_after_configured_shortcut(self):
        applied = _apply({"copy": "<Control><Shift>Insert"})
        self.assertEqual(
            applied["win.copy"], ["<Control><Shift>Insert", "<Control><Shift>c"]
        )

    def test_alias_skipped_when_user_bound_it_elsewhere(self):
        # Shortcut editor stores GTK's canonical order (<Shift><Control>).
        applied = _apply({"copy": "<Control><Shift>Insert",
                          "split-horizontal": "<Shift><Control>c"})
        self.assertEqual(applied["win.copy"], ["<Control><Shift>Insert"])
        self.assertEqual(applied["win.split-horizontal"], ["<Shift><Control>c"])

    def test_alias_kept_when_action_has_no_shortcut(self):
        applied = _apply({})
        self.assertEqual(applied["win.next-tab"], ["<Control>Page_Down"])

    def test_normalize_accelerator_ignores_modifier_order(self):
        self.assertEqual(
            normalize_accelerator("<Control><Shift>c"),
            normalize_accelerator("<Shift><Control>c"),
        )


if __name__ == "__main__":
    unittest.main()
