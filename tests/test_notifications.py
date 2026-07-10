import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))

import database


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_create_and_list(self):
        user_id = database.create_user("a@axim.local", "password123")
        database.create_notification(user_id, "Hello", source="rule:Test")
        rows = database.list_notifications(user_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["message"], "Hello")
        self.assertEqual(rows[0]["source"], "rule:Test")
        self.assertIsNone(rows[0]["read_at"])

    def test_unread_count(self):
        user_id = database.create_user("a@axim.local", "password123")
        database.create_notification(user_id, "One")
        database.create_notification(user_id, "Two")
        self.assertEqual(database.count_unread_notifications(user_id), 2)

    def test_mark_read_decrements_unread_count(self):
        user_id = database.create_user("a@axim.local", "password123")
        n_id = database.create_notification(user_id, "One")
        database.mark_notification_read(n_id)
        self.assertEqual(database.count_unread_notifications(user_id), 0)
        rows = database.list_notifications(user_id)
        self.assertIsNotNone(rows[0]["read_at"])

    def test_mark_all_read(self):
        user_id = database.create_user("a@axim.local", "password123")
        database.create_notification(user_id, "One")
        database.create_notification(user_id, "Two")
        database.mark_all_notifications_read(user_id)
        self.assertEqual(database.count_unread_notifications(user_id), 0)

    def test_unread_only_filter(self):
        user_id = database.create_user("a@axim.local", "password123")
        read_id = database.create_notification(user_id, "Read me")
        database.create_notification(user_id, "Still unread")
        database.mark_notification_read(read_id)
        unread = database.list_notifications(user_id, unread_only=True)
        self.assertEqual(len(unread), 1)
        self.assertEqual(unread[0]["message"], "Still unread")

    def test_scoped_per_user(self):
        user_a = database.create_user("a@axim.local", "password123")
        user_b = database.create_user("b@axim.local", "password123")
        database.create_notification(user_a, "For A")
        self.assertEqual(len(database.list_notifications(user_a)), 1)
        self.assertEqual(len(database.list_notifications(user_b)), 0)

    def test_get_owner_user(self):
        database.create_user("admin@axim.local", "password123", role="admin")
        owner_id = database.create_user("owner@axim.local", "password123", role="owner")
        owner = database.get_owner_user()
        self.assertEqual(owner["id"], owner_id)

    def test_get_owner_user_none_when_no_owner(self):
        database.create_user("admin@axim.local", "password123", role="admin")
        self.assertIsNone(database.get_owner_user())


if __name__ == "__main__":
    unittest.main()
