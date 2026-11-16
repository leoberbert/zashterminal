"""Command palette matching: word filters, kind prefixes and grouping."""

from __future__ import annotations

import unittest

from zashterminal.ui.command_palette import PaletteEntry, filter_entries


def _entry(kind: str, title: str, subtitle: str = "") -> PaletteEntry:
    return PaletteEntry(kind, title, subtitle, "icon", lambda: None)


ENTRIES = [
    _entry("action", "Split Left/Right"),
    _entry("session", "lab-db", "dba@db.lab.local"),
    _entry("command", "Restart nginx", "sudo systemctl restart nginx"),
    _entry("session", "prod-api", "deploy@api.prod.example · Production"),
    _entry("session", "web-prod", "ops@web.example"),
]


def titles(query: str) -> list:
    return [e.title for e in filter_entries(ENTRIES, query)]


class CommandPaletteFilterTests(unittest.TestCase):
    def test_every_word_must_match_title_or_subtitle(self):
        self.assertEqual(titles("prod deploy"), ["prod-api"])

    def test_results_grouped_by_kind_with_title_prefix_first(self):
        self.assertEqual(titles("prod"), ["prod-api", "web-prod"])
        self.assertEqual(titles("")[0:3], ["lab-db", "prod-api", "web-prod"])
        self.assertEqual(titles("")[-1], "Split Left/Right")

    def test_prefixes_restrict_kind(self):
        self.assertEqual(titles("> split"), ["Split Left/Right"])
        self.assertNotIn("Restart nginx", titles("@"))

    def test_match_is_case_insensitive(self):
        self.assertEqual(titles("NGINX"), ["Restart nginx"])


if __name__ == "__main__":
    unittest.main()
