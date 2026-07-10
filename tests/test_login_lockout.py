import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
API_DIR = PROJECT_ROOT / "api"
CORE_DIR = PROJECT_ROOT / "core"
CONFIG_DIR = PROJECT_ROOT / "config"
sys.path.insert(0, str(API_DIR))
sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(CONFIG_DIR))

from fastapi import HTTPException, Response
from starlette.requests import Request

import database
import auth_routes


def _make_request():
    scope = {
        "type": "http", "scheme": "http", "headers": [], "method": "POST",
        "path": "/api/auth/login", "query_string": b"", "server": ("testserver", 80),
    }
    return Request(scope)


class LockoutDatabaseTests(unittest.TestCase):
    """core/database.py: is_account_locked / record_failed_login /
    reset_failed_login, and verify_user_credentials's own lockout check -
    the mechanism behind api/auth_routes.py's login() 429 response."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self.user_id = database.create_user("victim@axim.local", "CorrectPass123!", role="user")

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_not_locked_before_threshold(self):
        for _ in range(database.MAX_FAILED_LOGIN_ATTEMPTS - 1):
            database.record_failed_login("victim@axim.local")
        self.assertIsNone(database.is_account_locked("victim@axim.local"))

    def test_locked_at_threshold(self):
        for _ in range(database.MAX_FAILED_LOGIN_ATTEMPTS):
            database.record_failed_login("victim@axim.local")
        self.assertIsNotNone(database.is_account_locked("victim@axim.local"))

    def test_correct_password_rejected_while_locked(self):
        for _ in range(database.MAX_FAILED_LOGIN_ATTEMPTS):
            database.record_failed_login("victim@axim.local")
        self.assertIsNone(database.verify_user_credentials("victim@axim.local", "CorrectPass123!"))

    def test_unlocks_after_window_elapses(self):
        for _ in range(database.MAX_FAILED_LOGIN_ATTEMPTS):
            database.record_failed_login("victim@axim.local")
        conn = database.get_connection()
        past = (datetime.now() - timedelta(minutes=1)).isoformat()
        conn.execute("UPDATE users SET locked_until = ? WHERE id = ?", (past, self.user_id))
        conn.commit()
        conn.close()
        self.assertIsNone(database.is_account_locked("victim@axim.local"))
        self.assertIsNotNone(database.verify_user_credentials("victim@axim.local", "CorrectPass123!"))

    def test_reset_failed_login_clears_both_columns(self):
        for _ in range(database.MAX_FAILED_LOGIN_ATTEMPTS):
            database.record_failed_login("victim@axim.local")
        database.reset_failed_login(self.user_id)
        row = database.get_user_by_id(self.user_id)
        self.assertEqual(row["failed_login_count"], 0)
        self.assertIsNone(row["locked_until"])
        self.assertIsNone(database.is_account_locked("victim@axim.local"))

    def test_nonexistent_email_never_locks(self):
        for _ in range(database.MAX_FAILED_LOGIN_ATTEMPTS + 5):
            database.record_failed_login("nobody@axim.local")
        self.assertIsNone(database.is_account_locked("nobody@axim.local"))

    def test_set_user_password_also_clears_lockout(self):
        for _ in range(database.MAX_FAILED_LOGIN_ATTEMPTS):
            database.record_failed_login("victim@axim.local")
        self.assertIsNotNone(database.is_account_locked("victim@axim.local"))
        database.set_user_password(self.user_id, "NewPass123!")
        self.assertIsNone(database.is_account_locked("victim@axim.local"))


class LoginRouteLockoutTests(unittest.TestCase):
    """api/auth_routes.py's login() - the actual HTTP-facing behavior:
    wrong password increments the counter, and a locked account gets a
    distinct 429 even with the correct password."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self.user_id = database.create_user("victim@axim.local", "CorrectPass123!", role="user",
                                             access_state="active")

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def _login(self, password):
        body = auth_routes.LoginRequest(email="victim@axim.local", password=password)
        return auth_routes.login(body, Response(), _make_request())

    def test_wrong_password_raises_401(self):
        with self.assertRaises(HTTPException) as ctx:
            self._login("wrong")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_five_failures_then_locked_even_with_correct_password(self):
        for _ in range(database.MAX_FAILED_LOGIN_ATTEMPTS):
            with self.assertRaises(HTTPException):
                self._login("wrong")
        with self.assertRaises(HTTPException) as ctx:
            self._login("CorrectPass123!")
        self.assertEqual(ctx.exception.status_code, 429)

    def test_correct_password_before_lockout_succeeds_and_resets_counter(self):
        for _ in range(database.MAX_FAILED_LOGIN_ATTEMPTS - 1):
            with self.assertRaises(HTTPException):
                self._login("wrong")
        result = self._login("CorrectPass123!")
        self.assertEqual(result["email"], "victim@axim.local")
        row = database.get_user_by_id(self.user_id)
        self.assertEqual(row["failed_login_count"], 0)


if __name__ == "__main__":
    unittest.main()
