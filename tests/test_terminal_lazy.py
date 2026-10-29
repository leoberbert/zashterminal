"""Coverage for terminal/_lazy.py helpers (no live GTK spawn)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from zashterminal.terminal import _lazy


class AnsiEscapePatternTests(unittest.TestCase):
    def test_strips_csi_and_osc(self):
        text = "\x1b[31mred\x1b[0m \x1b]0;title\x07ok"
        cleaned = _lazy.ANSI_ESCAPE_PATTERN.sub("", text)
        self.assertEqual(cleaned, "red ok")

    def test_strips_malformed_bracket_sequences(self):
        text = "[[Khello[[[J"
        cleaned = _lazy.ANSI_ESCAPE_PATTERN.sub("", text)
        self.assertIn("hello", cleaned)
        self.assertNotIn("[[", cleaned)


class LazyGetterTests(unittest.TestCase):
    def setUp(self):
        _lazy._spawner = None
        _lazy._highlight_manager = None
        _lazy._output_highlighter = None
        _lazy._terminal_menu_creator = None

    def tearDown(self):
        _lazy._spawner = None
        _lazy._highlight_manager = None
        _lazy._output_highlighter = None
        _lazy._terminal_menu_creator = None

    def test_lazy_loaders_import_and_cache(self):
        hl = MagicMock(return_value="H")
        oh = MagicMock(return_value="O")
        menu = MagicMock(return_value="M")
        spawn = MagicMock(return_value="S")

        with (
            patch("zashterminal.settings.highlights.get_highlight_manager", hl),
            patch("zashterminal.terminal.highlighter.get_output_highlighter", oh),
            patch("zashterminal.ui.menus.create_terminal_menu", menu),
            patch("zashterminal.terminal.spawner.get_spawner", spawn),
        ):
            self.assertEqual(_lazy.get_highlight_manager(), "H")
            self.assertEqual(_lazy.get_output_highlighter(), "O")
            self.assertEqual(_lazy.create_terminal_menu("a"), "M")
            self.assertEqual(_lazy.get_spawner(), "S")

            self.assertIs(_lazy._highlight_manager, hl)
            self.assertIs(_lazy._output_highlighter, oh)
            self.assertIs(_lazy._terminal_menu_creator, menu)
            self.assertIs(_lazy._spawner, spawn)

            # Second calls hit the cached factories only.
            self.assertEqual(_lazy.get_highlight_manager(), "H")
            self.assertEqual(_lazy.get_output_highlighter(), "O")
            self.assertEqual(_lazy.create_terminal_menu("b"), "M")
            self.assertEqual(_lazy.get_spawner(), "S")
            self.assertEqual(hl.call_count, 2)
            self.assertEqual(oh.call_count, 2)
            self.assertEqual(menu.call_count, 2)
            self.assertEqual(spawn.call_count, 2)


if __name__ == "__main__":
    unittest.main()
