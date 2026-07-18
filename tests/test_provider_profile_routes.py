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
import provider_profile_routes as routes

_FAKE_ADMIN = {"id": 1, "email": "owner@axim.local", "role": "owner"}


class ProviderProfileRoutesTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        database.upsert_channel(chat_id=42, username="test", title="Test Source", kind="channel")
        self.channel_id = database.list_channels()[0]["id"]

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def _eligible_profile(self):
        profile = routes.get_or_create_profile_for_channel(self.channel_id, user=_FAKE_ADMIN)
        database.update_provider_profile(
            profile["id"], observed_signal_count=25, parse_success_count=23, coverage=0.9,
        )
        return profile["id"]


class ListProfilesTests(ProviderProfileRoutesTestCase):
    def test_empty_by_default(self):
        self.assertEqual(routes.list_profiles(user=_FAKE_ADMIN), [])

    def test_lists_created_profiles(self):
        routes.get_or_create_profile_for_channel(self.channel_id, user=_FAKE_ADMIN)
        result = routes.list_profiles(user=_FAKE_ADMIN)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["channel_id"], self.channel_id)

    def test_filters_by_trading_mode(self):
        profile_id = self._eligible_profile()
        routes.graduate_profile(profile_id, user=_FAKE_ADMIN)
        self.assertEqual(len(routes.list_profiles(trading_mode="demo_ready", user=_FAKE_ADMIN)), 1)
        self.assertEqual(len(routes.list_profiles(trading_mode="live", user=_FAKE_ADMIN)), 0)


class GetOrCreateTests(ProviderProfileRoutesTestCase):
    def test_creates_a_profile_on_first_view(self):
        result = routes.get_or_create_profile_for_channel(self.channel_id, user=_FAKE_ADMIN)
        self.assertEqual(result["trading_mode"], "observation")
        self.assertIn("graduation", result)

    def test_unknown_channel_404s(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            routes.get_or_create_profile_for_channel(999999, user=_FAKE_ADMIN)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_repeated_calls_return_the_same_profile(self):
        first = routes.get_or_create_profile_for_channel(self.channel_id, user=_FAKE_ADMIN)
        second = routes.get_or_create_profile_for_channel(self.channel_id, user=_FAKE_ADMIN)
        self.assertEqual(first["id"], second["id"])


class UpdateProfileTests(ProviderProfileRoutesTestCase):
    def test_update_changes_editable_fields_and_is_audited(self):
        profile = routes.get_or_create_profile_for_channel(self.channel_id, user=_FAKE_ADMIN)
        updated = routes.update_profile(
            profile["id"], routes.ProfileUpdate(timezone="America/New_York", assembly_timeout_seconds=120),
            user=_FAKE_ADMIN,
        )
        self.assertEqual(updated["timezone"], "America/New_York")
        self.assertEqual(updated["assembly_timeout_seconds"], 120)
        history = database.list_provider_profile_history(profile["id"])
        self.assertTrue(any(h["reason"] == "edited via browser" for h in history))

    def test_unknown_profile_404s(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            routes.update_profile(999999, routes.ProfileUpdate(timezone="UTC"), user=_FAKE_ADMIN)
        self.assertEqual(ctx.exception.status_code, 404)


class LifecycleTransitionRouteTests(ProviderProfileRoutesTestCase):
    def test_graduate_fails_with_a_clear_400_when_not_eligible(self):
        from fastapi import HTTPException
        profile = routes.get_or_create_profile_for_channel(self.channel_id, user=_FAKE_ADMIN)
        with self.assertRaises(HTTPException) as ctx:
            routes.graduate_profile(profile["id"], user=_FAKE_ADMIN)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_full_lifecycle_through_the_routes(self):
        profile_id = self._eligible_profile()
        graduated = routes.graduate_profile(profile_id, user=_FAKE_ADMIN)
        self.assertEqual(graduated["trading_mode"], "demo_ready")

        demo = routes.approve_demo_profile(profile_id, user=_FAKE_ADMIN)
        self.assertEqual(demo["trading_mode"], "demo")
        self.assertEqual(demo["demo_approved_by"], "owner@axim.local")

        live = routes.approve_live_profile(profile_id, user=_FAKE_ADMIN)
        self.assertEqual(live["trading_mode"], "live")
        self.assertEqual(live["live_approved_by"], "owner@axim.local")

    def test_approve_live_before_demo_is_rejected(self):
        from fastapi import HTTPException
        profile_id = self._eligible_profile()
        routes.graduate_profile(profile_id, user=_FAKE_ADMIN)
        with self.assertRaises(HTTPException) as ctx:
            routes.approve_live_profile(profile_id, user=_FAKE_ADMIN)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_revert_to_observation_requires_a_reason(self):
        profile_id = self._eligible_profile()
        routes.graduate_profile(profile_id, user=_FAKE_ADMIN)
        routes.approve_demo_profile(profile_id, user=_FAKE_ADMIN)
        reverted = routes.revert_profile(
            profile_id, routes.RevertRequest(reason="format changed"), user=_FAKE_ADMIN,
        )
        self.assertEqual(reverted["trading_mode"], "observation")


class HistoryRouteTests(ProviderProfileRoutesTestCase):
    def test_history_includes_every_transition(self):
        profile_id = self._eligible_profile()
        routes.graduate_profile(profile_id, user=_FAKE_ADMIN)
        history = routes.get_profile_history(profile_id, user=_FAKE_ADMIN)
        reasons = [h["reason"] for h in history]
        self.assertIn("graduated to demo_ready", reasons)


if __name__ == "__main__":
    unittest.main()
