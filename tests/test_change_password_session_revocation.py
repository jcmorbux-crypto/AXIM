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


if __name__ == "__main__":
    unittest.main()
