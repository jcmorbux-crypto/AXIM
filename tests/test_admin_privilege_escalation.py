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

from fastapi import HTTPException

import database
import admin


class OwnerRoleEscalationTests(unittest.TestCase):
    """A plain 'admin' account (a lesser role by design - 'owner' is
    meant to be assigned exactly once, automatically, at
    POST /api/auth/bootstrap-owner) could previously grant itself (or
    anyone) the 'owner' role, or strip an existing owner's role, through
    the ordinary user-management endpoints - both only ever gated by
    require_admin (owner OR admin), with no additional check on the
    'owner' role specifically. See _forbid_owner_grant_by_non_owner and
    docs/AXIM_ROADMAP.md's own writeup of this fix."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self.owner_id = database.create_user("owner@axim.local", "pw12345678", role="owner", access_state="active")
        self.admin_id = database.create_user("admin@axim.local", "pw12345678", role="admin", access_state="active")
        self.owner = database.get_user_by_id(self.owner_id)
        self.plain_admin = database.get_user_by_id(self.admin_id)

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_plain_admin_cannot_self_promote_to_owner(self):
        with self.assertRaises(HTTPException) as ctx:
            admin.edit_user(self.admin_id, admin.EditUserRequest(role="owner"), admin_user=self.plain_admin)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(database.get_user_by_id(self.admin_id)["role"], "admin")

    def test_plain_admin_cannot_demote_the_real_owner(self):
        with self.assertRaises(HTTPException) as ctx:
            admin.edit_user(self.owner_id, admin.EditUserRequest(role="admin"), admin_user=self.plain_admin)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(database.get_user_by_id(self.owner_id)["role"], "owner")

    def test_plain_admin_cannot_create_a_new_owner_account(self):
        body = admin.CreateUserRequest(email="sneaky@axim.local", password="pw12345678", role="owner")
        with self.assertRaises(HTTPException) as ctx:
            admin.create_user(body, admin_user=self.plain_admin)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIsNone(database.get_user_by_email("sneaky@axim.local"))

    def test_real_owner_can_still_grant_ownership_to_a_successor(self):
        result = admin.edit_user(self.admin_id, admin.EditUserRequest(role="owner"), admin_user=self.owner)
        self.assertEqual(result["role"], "owner")
        self.assertEqual(database.get_user_by_id(self.admin_id)["role"], "owner")

    def test_plain_admin_can_still_edit_ordinary_non_owner_roles(self):
        user_id = database.create_user("plain@axim.local", "pw12345678", role="user", access_state="active")
        result = admin.edit_user(user_id, admin.EditUserRequest(role="admin"), admin_user=self.plain_admin)
        self.assertEqual(result["role"], "admin")


class OwnerTierEscalationTests(unittest.TestCase):
    """Same shape of bug, one axis over: VALID_TIERS also allows
    access_tier='owner' (a separate field from role - reserved for a
    future Stripe integration, per core/database.py's own comment) with
    no restriction at all before this fix. Not currently read by any
    enforcement path (unlike role), so not a live exploit today, but the
    exact same unrestricted-'owner'-value shape - closed the same way so
    a future billing integration doesn't inherit the same mistake."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self.owner_id = database.create_user("owner@axim.local", "pw12345678", role="owner", access_state="active")
        self.admin_id = database.create_user("admin@axim.local", "pw12345678", role="admin", access_state="active")
        self.owner = database.get_user_by_id(self.owner_id)
        self.plain_admin = database.get_user_by_id(self.admin_id)

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_plain_admin_cannot_grant_owner_tier_via_edit_user(self):
        with self.assertRaises(HTTPException) as ctx:
            admin.edit_user(self.admin_id, admin.EditUserRequest(access_tier="owner"), admin_user=self.plain_admin)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(database.get_user_by_id(self.admin_id)["access_tier"], "trial")

    def test_plain_admin_cannot_grant_owner_tier_via_set_tier(self):
        with self.assertRaises(HTTPException) as ctx:
            admin.set_tier(self.admin_id, admin.SetTierRequest(access_tier="owner"), admin_user=self.plain_admin)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_plain_admin_cannot_create_user_with_owner_tier(self):
        body = admin.CreateUserRequest(email="sneaky2@axim.local", password="pw12345678", access_tier="owner")
        with self.assertRaises(HTTPException) as ctx:
            admin.create_user(body, admin_user=self.plain_admin)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_real_owner_can_still_grant_owner_tier(self):
        result = admin.set_tier(self.admin_id, admin.SetTierRequest(access_tier="owner"), admin_user=self.owner)
        self.assertEqual(result["access_tier"], "owner")

    def test_plain_admin_can_still_set_ordinary_tiers(self):
        result = admin.set_tier(self.admin_id, admin.SetTierRequest(access_tier="pro"), admin_user=self.plain_admin)
        self.assertEqual(result["access_tier"], "pro")


if __name__ == "__main__":
    unittest.main()
