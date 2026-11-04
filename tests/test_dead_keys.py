"""Dead-key compose decisions (ABNT2 ~/^ and Ctrl+C)."""

from __future__ import annotations

import unittest

import gi

gi.require_version("Gdk", "4.0")
from gi.repository import Gdk

from zashterminal.utils.dead_keys import resolve_dead_key


class DeadKeyResolveTests(unittest.TestCase):
    def test_tilde_then_letter_composes(self):
        _, pending, stop = resolve_dead_key(None, Gdk.KEY_dead_tilde, Gdk.ModifierType(0))
        self.assertTrue(stop)
        self.assertEqual(pending, Gdk.KEY_dead_tilde)
        text, pending, stop = resolve_dead_key(pending, Gdk.KEY_a, Gdk.ModifierType(0))
        self.assertEqual((text, pending, stop), ("ã", None, True))

    def test_circumflex_and_space_literal(self):
        _, pending, _ = resolve_dead_key(
            None, Gdk.KEY_dead_circumflex, Gdk.ModifierType(0)
        )
        text, pending, stop = resolve_dead_key(pending, Gdk.KEY_space, Gdk.ModifierType(0))
        self.assertEqual((text, pending, stop), ("^", None, True))

    def test_double_dead_tilde_commits_literal(self):
        text, pending, stop = resolve_dead_key(
            Gdk.KEY_dead_tilde, Gdk.KEY_dead_tilde, Gdk.ModifierType(0)
        )
        self.assertEqual((text, pending, stop), ("~", None, True))

    def test_ctrl_c_cancels_pending_and_propagates(self):
        text, pending, stop = resolve_dead_key(
            Gdk.KEY_dead_tilde,
            Gdk.KEY_c,
            Gdk.ModifierType.CONTROL_MASK,
        )
        self.assertEqual((text, pending, stop), (None, None, False))

    def test_ctrl_c_after_circumflex_propagates(self):
        text, pending, stop = resolve_dead_key(
            Gdk.KEY_dead_circumflex,
            Gdk.KEY_c,
            Gdk.ModifierType.CONTROL_MASK,
        )
        self.assertFalse(stop)
        self.assertIsNone(pending)
        self.assertIsNone(text)

    def test_acute_c_is_cedilla(self):
        text, pending, stop = resolve_dead_key(
            Gdk.KEY_dead_acute, Gdk.KEY_c, Gdk.ModifierType(0)
        )
        self.assertEqual((text, pending, stop), ("ç", None, True))

    def test_unmapped_char_commits_accent_plus_char(self):
        text, pending, stop = resolve_dead_key(
            Gdk.KEY_dead_tilde, Gdk.KEY_b, Gdk.ModifierType(0)
        )
        self.assertEqual((text, pending, stop), ("~b", None, True))

    def test_plain_key_is_ignored(self):
        self.assertEqual(
            resolve_dead_key(None, Gdk.KEY_a, Gdk.ModifierType(0)),
            (None, None, False),
        )

    def test_backspace_cancels_dead_key_only(self):
        text, pending, stop = resolve_dead_key(
            Gdk.KEY_dead_grave, Gdk.KEY_BackSpace, Gdk.ModifierType(0)
        )
        self.assertEqual((text, pending, stop), (None, None, True))


if __name__ == "__main__":
    unittest.main()
