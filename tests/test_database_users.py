import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))

import database


class UserAccountTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_create_and_fetch_user(self):
        user_id = database.create_user("owner@example.com", "supersecret1", role="owner",
                                         access_tier="owner", access_state="active")
        user = database.get_user_by_id(user_id)
        self.assertEqual(user["email"], "owner@example.com")
        self.assertEqual(user["role"], "owner")
        self.assertNotEqual(user["password_hash"], "supersecret1")  # never stored raw

    def test_email_lookup_is_case_insensitive(self):
        database.create_user("Owner@Example.com", "supersecret1")
        user = database.get_user_by_email("owner@example.com")
        self.assertIsNotNone(user)

    def test_count_users(self):
        self.assertEqual(database.count_users(), 0)
        database.create_user("a@example.com", "supersecret1")
        self.assertEqual(database.count_users(), 1)

    def test_verify_user_credentials_correct_and_incorrect(self):
        database.create_user("a@example.com", "supersecret1")
        self.assertIsNotNone(database.verify_user_credentials("a@example.com", "supersecret1"))
        self.assertIsNone(database.verify_user_credentials("a@example.com", "wrongpassword"))
        self.assertIsNone(database.verify_user_credentials("nobody@example.com", "supersecret1"))

    def test_login_lockout_after_threshold_failed_attempts(self):
        # Security-audit follow-up: verify_user_credentials's own
        # docstring always claimed to check lockout, with nothing behind
        # it until this session - regression-test the real behavior.
        database.create_user("a@example.com", "supersecret1")
        for _ in range(database.LOGIN_LOCKOUT_THRESHOLD):
            self.assertIsNone(database.verify_user_credentials("a@example.com", "wrongpassword"))
        # The account is now locked - even the CORRECT password must be
        # rejected until the lockout window passes, not just further wrong
        # guesses.
        self.assertIsNone(database.verify_user_credentials("a@example.com", "supersecret1"))
        user = database.get_user_by_email("a@example.com")
        self.assertIsNotNone(user["locked_until"])

    def test_successful_login_resets_failed_attempt_counter(self):
        database.create_user("a@example.com", "supersecret1")
        for _ in range(database.LOGIN_LOCKOUT_THRESHOLD - 1):
            database.verify_user_credentials("a@example.com", "wrongpassword")
        self.assertIsNotNone(database.verify_user_credentials("a@example.com", "supersecret1"))
        user = database.get_user_by_email("a@example.com")
        self.assertEqual(user["failed_login_attempts"], 0)
        self.assertIsNone(user["locked_until"])

    def test_lockout_expires_after_the_window(self):
        from datetime import datetime, timedelta
        database.create_user("a@example.com", "supersecret1")
        for _ in range(database.LOGIN_LOCKOUT_THRESHOLD):
            database.verify_user_credentials("a@example.com", "wrongpassword")
        self.assertIsNone(database.verify_user_credentials("a@example.com", "supersecret1"))  # still locked
        # Simulate the lockout window having already passed.
        conn = database.get_connection()
        conn.execute(
            "UPDATE users SET locked_until = ? WHERE email = 'a@example.com'",
            ((datetime.now() - timedelta(minutes=1)).isoformat(),),
        )
        conn.commit()
        conn.close()
        self.assertIsNotNone(database.verify_user_credentials("a@example.com", "supersecret1"))

    def test_update_user_rejects_unknown_field(self):
        user_id = database.create_user("a@example.com", "supersecret1")
        with self.assertRaises(ValueError):
            database.update_user(user_id, password_hash="hijacked")

    def test_update_user_partial_update(self):
        user_id = database.create_user("a@example.com", "supersecret1")
        database.update_user(user_id, access_state="active")
        user = database.get_user_by_id(user_id)
        self.assertEqual(user["access_state"], "active")
        self.assertEqual(user["access_tier"], "trial")  # untouched default

    def test_set_user_password_changes_credential(self):
        user_id = database.create_user("a@example.com", "supersecret1")
        database.set_user_password(user_id, "newpassword2")
        self.assertIsNone(database.verify_user_credentials("a@example.com", "supersecret1"))
        self.assertIsNotNone(database.verify_user_credentials("a@example.com", "newpassword2"))

    def test_record_login_sets_last_login_at(self):
        user_id = database.create_user("a@example.com", "supersecret1")
        self.assertIsNone(database.get_user_by_id(user_id)["last_login_at"])
        database.record_login(user_id)
        self.assertIsNotNone(database.get_user_by_id(user_id)["last_login_at"])


class SessionTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self.user_id = database.create_user("a@example.com", "supersecret1")

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_create_session_and_resolve_user(self):
        raw_token = database.create_session(self.user_id)
        user = database.get_session_user(raw_token)
        self.assertIsNotNone(user)
        self.assertEqual(user["id"], self.user_id)

    def test_invalid_token_resolves_to_none(self):
        self.assertIsNone(database.get_session_user("not-a-real-token"))

    def test_expired_session_resolves_to_none(self):
        raw_token = database.create_session(self.user_id, expires_hours=-1)
        self.assertIsNone(database.get_session_user(raw_token))

    def test_delete_session_invalidates_token(self):
        raw_token = database.create_session(self.user_id)
        database.delete_session(raw_token)
        self.assertIsNone(database.get_session_user(raw_token))

    def test_revoke_all_sessions(self):
        token1 = database.create_session(self.user_id)
        token2 = database.create_session(self.user_id)
        database.revoke_all_sessions(self.user_id)
        self.assertIsNone(database.get_session_user(token1))
        self.assertIsNone(database.get_session_user(token2))

    def test_list_user_sessions(self):
        database.create_session(self.user_id)
        database.create_session(self.user_id)
        self.assertEqual(len(database.list_user_sessions(self.user_id)), 2)

    def test_client_name_and_type_round_trip(self):
        database.create_session(self.user_id, client_name="Jay's Laptop", client_type="desktop")
        sessions = database.list_user_sessions(self.user_id)
        self.assertEqual(sessions[0]["client_name"], "Jay's Laptop")
        self.assertEqual(sessions[0]["client_type"], "desktop")

    def test_client_type_defaults_to_web(self):
        database.create_session(self.user_id)
        sessions = database.list_user_sessions(self.user_id)
        self.assertEqual(sessions[0]["client_type"], "web")
        self.assertIsNone(sessions[0]["client_name"])

    def test_revoke_session_removes_only_that_one(self):
        database.create_session(self.user_id)
        database.create_session(self.user_id)
        session_rows = database.list_user_sessions(self.user_id)
        self.assertEqual(len(session_rows), 2)
        database.revoke_session(session_rows[0]["id"])
        remaining = database.list_user_sessions(self.user_id)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["id"], session_rows[1]["id"])

    def test_revoke_session_scoped_to_user_id_ignores_other_users(self):
        other_user_id = database.create_user("b@example.com", "supersecret2")
        database.create_session(self.user_id)
        session_row = database.list_user_sessions(self.user_id)[0]
        # Attempting to revoke user A's session while scoped as user B must be a no-op.
        database.revoke_session(session_row["id"], user_id=other_user_id)
        self.assertEqual(len(database.list_user_sessions(self.user_id)), 1)


class AdminActionTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_record_and_list_admin_actions(self):
        admin_id = database.create_user("admin@example.com", "supersecret1", role="admin")
        target_id = database.create_user("user@example.com", "supersecret1")
        database.record_admin_action(admin_id, target_id, "disable", "reason: nonpayment")
        actions = database.list_admin_actions()
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["action"], "disable")
        self.assertEqual(actions[0]["target_user_id"], target_id)


if __name__ == "__main__":
    unittest.main()
