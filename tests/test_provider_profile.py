import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CORE_DIR = PROJECT_ROOT / "core"
CONFIG_DIR = PROJECT_ROOT / "config"
sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(CONFIG_DIR))

import database
import provider_profile as pp


class ProviderProfileTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        channel_id = database.upsert_channel(chat_id=999, username="test", title="Test Source", kind="channel")
        self.channel_id = database.list_channels()[0]["id"]
        self.profile = database.get_or_create_provider_profile(self.channel_id)
        self.profile_id = self.profile["id"]

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()


class GraduationStatusTests(ProviderProfileTestCase):
    def test_fresh_profile_is_not_eligible(self):
        status = pp.graduation_status(self.profile)
        self.assertFalse(status["eligible"])
        self.assertTrue(status["reasons"])

    def test_eligible_once_every_threshold_is_met(self):
        database.update_provider_profile(
            self.profile_id, observed_signal_count=25, parse_success_count=23, coverage=0.9,
        )
        profile = database.get_provider_profile(self.profile_id)
        status = pp.graduation_status(profile)
        self.assertTrue(status["eligible"])
        self.assertEqual(status["reasons"], [])

    def test_low_success_rate_blocks_graduation(self):
        database.update_provider_profile(
            self.profile_id, observed_signal_count=25, parse_success_count=5, coverage=0.9,
        )
        profile = database.get_provider_profile(self.profile_id)
        status = pp.graduation_status(profile)
        self.assertFalse(status["eligible"])
        self.assertTrue(any("success rate" in r for r in status["reasons"]))

    def test_active_drift_blocks_graduation_even_if_otherwise_qualified(self):
        database.update_provider_profile(
            self.profile_id, observed_signal_count=25, parse_success_count=23, coverage=0.9,
            drift_detected_at="2026-07-18T00:00:00",
        )
        profile = database.get_provider_profile(self.profile_id)
        status = pp.graduation_status(profile)
        self.assertFalse(status["eligible"])
        self.assertTrue(any("drift" in r for r in status["reasons"]))


class RecordObservedSignalTests(ProviderProfileTestCase):
    def test_successful_parse_increments_both_counters(self):
        pp.record_observed_signal(self.profile_id, parsed_successfully=True)
        profile = database.get_provider_profile(self.profile_id)
        self.assertEqual(profile["observed_signal_count"], 1)
        self.assertEqual(profile["parse_success_count"], 1)

    def test_failed_parse_increments_only_observed(self):
        pp.record_observed_signal(self.profile_id, parsed_successfully=False)
        profile = database.get_provider_profile(self.profile_id)
        self.assertEqual(profile["observed_signal_count"], 1)
        self.assertEqual(profile["parse_success_count"], 0)

    def test_counters_accumulate_across_calls(self):
        for success in (True, True, False, True):
            pp.record_observed_signal(self.profile_id, parsed_successfully=success)
        profile = database.get_provider_profile(self.profile_id)
        self.assertEqual(profile["observed_signal_count"], 4)
        self.assertEqual(profile["parse_success_count"], 3)


class TradingModeTransitionTests(ProviderProfileTestCase):
    def _make_eligible(self):
        database.update_provider_profile(
            self.profile_id, observed_signal_count=25, parse_success_count=23, coverage=0.9,
        )

    def test_new_profile_starts_in_observation(self):
        self.assertEqual(self.profile["trading_mode"], "observation")

    def test_graduate_requires_meeting_criteria(self):
        with self.assertRaises(pp.ProviderProfileError):
            pp.graduate_to_demo_ready(self.profile_id)

    def test_graduate_succeeds_once_eligible(self):
        self._make_eligible()
        pp.graduate_to_demo_ready(self.profile_id, changed_by="system")
        profile = database.get_provider_profile(self.profile_id)
        self.assertEqual(profile["trading_mode"], "demo_ready")

    def test_cannot_graduate_twice(self):
        self._make_eligible()
        pp.graduate_to_demo_ready(self.profile_id)
        with self.assertRaises(pp.ProviderProfileError):
            pp.graduate_to_demo_ready(self.profile_id)

    def test_approve_demo_requires_demo_ready_state(self):
        with self.assertRaises(pp.ProviderProfileError):
            pp.approve_demo(self.profile_id, approved_by="owner@axim.local")

    def test_approve_demo_requires_an_approver(self):
        self._make_eligible()
        pp.graduate_to_demo_ready(self.profile_id)
        with self.assertRaises(pp.ProviderProfileError):
            pp.approve_demo(self.profile_id, approved_by=None)

    def test_full_happy_path_to_demo(self):
        self._make_eligible()
        pp.graduate_to_demo_ready(self.profile_id)
        pp.approve_demo(self.profile_id, approved_by="owner@axim.local")
        profile = database.get_provider_profile(self.profile_id)
        self.assertEqual(profile["trading_mode"], "demo")
        self.assertEqual(profile["demo_approved_by"], "owner@axim.local")
        self.assertIsNotNone(profile["demo_approved_at"])

    def test_cannot_skip_straight_to_live(self):
        self._make_eligible()
        pp.graduate_to_demo_ready(self.profile_id)
        with self.assertRaises(pp.ProviderProfileError):
            pp.approve_live(self.profile_id, approved_by="owner@axim.local")

    def test_live_requires_a_real_prior_demo_approval_not_just_the_state_string(self):
        self._make_eligible()
        pp.graduate_to_demo_ready(self.profile_id)
        pp.approve_demo(self.profile_id, approved_by="owner@axim.local")
        pp.approve_live(self.profile_id, approved_by="owner@axim.local")
        profile = database.get_provider_profile(self.profile_id)
        self.assertEqual(profile["trading_mode"], "live")
        self.assertIsNotNone(profile["live_approved_at"])

    def test_revert_to_observation_works_from_any_state(self):
        self._make_eligible()
        pp.graduate_to_demo_ready(self.profile_id)
        pp.approve_demo(self.profile_id, approved_by="owner@axim.local")
        pp.revert_to_observation(self.profile_id, reason="format drift detected", changed_by="system")
        profile = database.get_provider_profile(self.profile_id)
        self.assertEqual(profile["trading_mode"], "observation")

    def test_revert_preserves_prior_approval_history(self):
        self._make_eligible()
        pp.graduate_to_demo_ready(self.profile_id)
        pp.approve_demo(self.profile_id, approved_by="owner@axim.local")
        pp.revert_to_observation(self.profile_id, reason="drift")
        profile = database.get_provider_profile(self.profile_id)
        self.assertIsNotNone(profile["demo_approved_at"])  # history preserved, not erased


class AuditTrailTests(ProviderProfileTestCase):
    def test_every_transition_is_logged_in_history(self):
        database.update_provider_profile(
            self.profile_id, observed_signal_count=25, parse_success_count=23, coverage=0.9,
        )
        pp.graduate_to_demo_ready(self.profile_id, changed_by="system")
        pp.approve_demo(self.profile_id, approved_by="owner@axim.local")
        history = database.list_provider_profile_history(self.profile_id)
        reasons = [h["reason"] for h in history]
        self.assertIn("graduated to demo_ready", reasons)
        self.assertIn("demo trading approved", reasons)


if __name__ == "__main__":
    unittest.main()
