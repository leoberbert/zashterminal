import json
import tempfile
import unittest
from pathlib import Path

from zashterminal.data.connection_history import MAX_ENTRIES, ConnectionHistory


class ConnectionHistoryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "sub" / "history.json"

    def test_order_and_persistence(self):
        h = ConnectionHistory(self.path)
        h.record("a", "h1", "ssh")
        h.record("b", "h2", "local")
        self.assertEqual([e.session_name for e in h.recent()], ["b", "a"])
        h2 = ConnectionHistory(self.path)
        self.assertEqual([e.session_name for e in h2.recent()], ["b", "a"])

    def test_dedupe_and_count(self):
        h = ConnectionHistory(self.path)
        h.record("a", "h1", "ssh")
        h.record("b", "h2", "ssh")
        h.record("a", "h1", "sftp")
        entries = h.recent()
        self.assertEqual([e.session_name for e in entries], ["a", "b"])
        self.assertEqual(entries[0].count, 2)
        self.assertEqual(entries[0].kind, "sftp")

    def test_max_entries(self):
        h = ConnectionHistory(self.path)
        for i in range(MAX_ENTRIES + 10):
            h.record(f"s{i}", "h", "ssh")
        self.assertEqual(len(h.recent(limit=1000)), MAX_ENTRIES)
        self.assertEqual(h.recent()[0].session_name, f"s{MAX_ENTRIES + 9}")

    def test_corrupt_file(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not json")
        h = ConnectionHistory(self.path)
        self.assertEqual(h.recent(), [])
        h.record("a", "h", "ssh")
        self.assertEqual(len(ConnectionHistory(self.path).recent()), 1)

    def test_bad_items_skipped(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps([{"x": 1}, "str", {"session_name": "ok"}]))
        self.assertEqual([e.session_name for e in ConnectionHistory(self.path).recent()], ["ok"])

    def test_remove_and_clear(self):
        h = ConnectionHistory(self.path)
        h.record("a", "h", "ssh")
        h.record("b", "h", "ssh")
        h.remove("a")
        self.assertEqual([e.session_name for e in h.recent()], ["b"])
        h.clear()
        self.assertEqual(ConnectionHistory(self.path).recent(), [])


if __name__ == "__main__":
    unittest.main()
