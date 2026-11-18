"""The favorite flag round-trips and old sessions.json entries default to False."""

import unittest

from zashterminal.sessions.models import SessionItem


class SessionFavoriteTests(unittest.TestCase):
    def test_old_data_without_field_is_not_favorite(self):
        item = SessionItem.from_dict({"name": "a", "session_type": "ssh", "host": "h"})
        self.assertFalse(item.favorite)

    def test_round_trip(self):
        item = SessionItem(name="a", session_type="ssh", host="h", favorite=True)
        self.assertTrue(SessionItem.from_dict(item.to_dict()).favorite)


if __name__ == "__main__":
    unittest.main()
