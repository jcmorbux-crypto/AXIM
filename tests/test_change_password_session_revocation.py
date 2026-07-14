import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
API_DIR = PROJECT_ROOT / "api"
CORE_DIR = PROJECT_ROOT / "core"
CONFIG_DIR = PROJECT_ROOT / "config"
sys.path.insert(0, str(API_DIR))
sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(CONFIG_DIR))

import database
import auth_routes


class RevokeOtherSessionsDatabaseTests(unittest.TestCase):
    """core/database.py's revoke_other_sessions - the mechanism behind
    self-service password change kicking out a stolen session without
    also logging out the device actively making the change."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self.user_id = database.create_user("victim@axim.local", "CorrectPass123!", role="user")

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_keeps_only_the_named_session(self):
        keep_token = database.create_session(self.user_id, client_name="This browser")
        database.create_session(self.user_id, client_name="Stolen session")
        database.create_session(self.user_id, client_name="Another device")
        self.assertEqual(len(database.list_user_sessions(self.user_id)), 3)

        database.revoke_other_sessions(self.user_id, keep_token)

        remaining = database.list_user_sessions(self.user_id)
        self.assertEqual(len(remaining), 1)
        self.assertIsNotNone(database.get_session_user(keep_token))

    def test_never_touches_another_users_sessions(self):
        other_user_id = database.create_user("other@axim.local", "CorrectPass123!", role="user")
        keep_token = database.create_session(self.user_id)
        other_token = database.create_session(other_user_id)

        database.revoke_other_sessions(self.user_id, keep_token)

        self.assertIsNotNone(database.get_session_user(other_token))


class ChangePasswordRouteRevocationTests(unittest.TestCase):
    """api/auth_routes.py's change_password() - previously left every
    other active session (e.g. an attacker's stolen session) alive after
    a password change, unlike the forgot-password reset flow which
    already revoked all sessions for the same credential-compromise
    reasoning. Zero test coverage existed on this route before this."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self.user_id = database.create_user("victim@axim.local", "CorrectPass123!", role="user",
                                             access_state="active")
        self.current_token = database.create_session(self.user_id, client_name="This browser")
        self.stolen_token = database.create_session(self.user_id, client_name="Attacker's session")

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def _change_password(self, current_password, new_password):
        body = auth_routes.ChangePasswordRequest(current_password=current_password, new_password=new_password)
        user = database.get_session_user(self.current_token)
        return auth_routes.change_password(body, user=user, authorization=None, axim_session=self.current_token)

    def test_revokes_the_other_session_but_keeps_the_active_one(self):
        self._change_password("CorrectPass123!", "NewPass456!")
        self.assertIsNone(database.get_session_user(self.stolen_token))
        self.assertIsNotNone(database.get_session_user(self.current_token))

    def test_wrong_current_password_does_not_revoke_anything(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            self._change_password("WrongPassword", "NewPass456!")
        self.assertIsNotNone(database.get_session_user(self.stolen_token))
        self.assertIsNotNone(database.get_session_user(self.current_token))


class ChangePasswordLockoutTests(unittest.TestCase):
    """api/auth_routes.py's change_password() previously bypassed
    database.verify_user_credentials's built-in lockout by never even
    reaching a code path that exercises repeated wrong-password guesses
    through it from an authenticated-but-not-password-holding caller.
    On this codebase the lockout (failed_login_attempts/locked_until) is
    enforced centrally inside verify_user_credentials itself - any caller,
    login() or change_password() alike, trips the same counter. This
    confirms change_password really does go through that shared path
    (not a bespoke, unprotected check), so a hijacked/stolen session
    can't brute-force the real password with unlimited attempts."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self.user_id = database.create_user("victim@axim.local", "CorrectPass123!", role="user",
                                             access_state="active")
        self.current_token = database.create_session(self.user_id, client_name="This browser")

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def _change_password(self, current_password, new_password="NewPass456!"):
        body = auth_routes.ChangePasswordRequest(current_password=current_password, new_password=new_password)
        user = database.get_session_user(self.current_token)
        return auth_routes.change_password(body, user=user, authorization=None, axim_session=self.current_token)

    def test_wrong_current_password_counts_toward_lockout(self):
        from fastapi import HTTPException
        for _ in range(database.LOGIN_LOCKOUT_THRESHOLD - 1):
            with self.assertRaises(HTTPException):
                self._change_password("wrong")
        self.assertIsNone(database.get_user_by_id(self.user_id)["locked_until"])
        with self.assertRaises(HTTPException):
            self._change_password("wrong")
        self.assertIsNotNone(database.get_user_by_id(self.user_id)["locked_until"])

    def test_locked_account_rejects_even_the_correct_password(self):
        from fastapi import HTTPException
        for _ in range(database.LOGIN_LOCKOUT_THRESHOLD):
            with self.assertRaises(HTTPException):
                self._change_password("wrong")
        with self.assertRaises(HTTPException) as ctx:
            self._change_password("CorrectPass123!")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_successful_change_clears_the_lockout_counter(self):
        for _ in range(database.LOGIN_LOCKOUT_THRESHOLD - 1):
            with self.assertRaises(Exception):
                self._change_password("wrong")
        self._change_password("CorrectPass123!")
        row = database.get_user_by_id(self.user_id)
        self.assertEqual(row["failed_login_attempts"], 0)


if __name__ == "__main__":
    unittest.main()
