import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))

import database
import email_sender


class PasswordResetTokenTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self.user_id = database.create_user("reset@axim.local", "originalpass123")

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_create_token_is_valid(self):
        raw_token, expires_at = database.create_password_reset_token(self.user_id)
        user = database.get_valid_password_reset_token(raw_token)
        self.assertIsNotNone(user)
        self.assertEqual(user["id"], self.user_id)

    def test_unknown_token_is_invalid(self):
        self.assertIsNone(database.get_valid_password_reset_token("not-a-real-token"))

    def test_creating_a_new_token_invalidates_the_old_one(self):
        first_token, _ = database.create_password_reset_token(self.user_id)
        second_token, _ = database.create_password_reset_token(self.user_id)
        self.assertIsNone(database.get_valid_password_reset_token(first_token))
        self.assertIsNotNone(database.get_valid_password_reset_token(second_token))

    def test_consumed_token_cannot_be_reused(self):
        raw_token, _ = database.create_password_reset_token(self.user_id)
        database.consume_password_reset_token(raw_token)
        self.assertIsNone(database.get_valid_password_reset_token(raw_token))

    def test_expired_token_is_invalid(self):
        raw_token, _ = database.create_password_reset_token(self.user_id)
        # Force the token's expiry into the past directly via SQL - the
        # only way to test expiry without actually sleeping 30 minutes.
        conn = database.get_connection()
        past = (datetime.now() - timedelta(minutes=5)).isoformat()
        conn.execute("UPDATE password_reset_tokens SET expires_at = ? WHERE user_id = ?", (past, self.user_id))
        conn.commit()
        conn.close()
        self.assertIsNone(database.get_valid_password_reset_token(raw_token))

    def test_recently_requested_guard(self):
        self.assertFalse(database.password_reset_recently_requested(self.user_id))
        database.create_password_reset_token(self.user_id)
        self.assertTrue(database.password_reset_recently_requested(self.user_id))

    def test_recently_requested_false_after_interval_elapses(self):
        database.create_password_reset_token(self.user_id)
        with mock.patch("database._PASSWORD_RESET_MIN_REQUEST_INTERVAL_SECONDS", 0):
            self.assertFalse(database.password_reset_recently_requested(self.user_id))

    def test_reset_flow_actually_changes_the_password(self):
        raw_token, _ = database.create_password_reset_token(self.user_id)
        self.assertIsNotNone(database.verify_user_credentials("reset@axim.local", "originalpass123"))
        database.set_user_password(self.user_id, "brandnewpass456")
        database.consume_password_reset_token(raw_token)
        self.assertIsNone(database.verify_user_credentials("reset@axim.local", "originalpass123"))
        self.assertIsNotNone(database.verify_user_credentials("reset@axim.local", "brandnewpass456"))

    def test_reset_revokes_existing_sessions(self):
        raw_session = database.create_session(self.user_id)
        self.assertIsNotNone(database.get_session_user(raw_session))
        database.revoke_all_sessions(self.user_id)
        self.assertIsNone(database.get_session_user(raw_session))


class EmailSenderTests(unittest.TestCase):
    def test_not_configured_when_smtp_host_unset(self):
        with mock.patch("email_sender.SMTP_HOST", None):
            self.assertFalse(email_sender.is_configured())
            result = email_sender.send_password_reset_email("a@b.com", "http://example.com/reset?token=x")
            self.assertEqual(result, {"configured": False, "sent": False})

    def test_configured_when_smtp_host_set(self):
        with mock.patch("email_sender.SMTP_HOST", "smtp.example.com"):
            self.assertTrue(email_sender.is_configured())

    def test_send_failure_does_not_raise(self):
        with mock.patch("email_sender.SMTP_HOST", "smtp.invalid.example"), \
             mock.patch("smtplib.SMTP", side_effect=OSError("connection refused")):
            result = email_sender.send_password_reset_email("a@b.com", "http://example.com/reset?token=x")
            self.assertEqual(result, {"configured": True, "sent": False})


if __name__ == "__main__":
    unittest.main()
