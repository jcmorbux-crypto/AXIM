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


class DriftDetectionTests(ProviderProfileTestCase):
    """core/provider_profile.py's check_for_drift + record_observed_signal
    wiring - a real format change should be flagged, but a source that
    was always somewhat imperfect (or one that just started) must not
    be."""

    def _seed_good_history(self, count=30, successes=29):
        for i in range(count):
            pp.record_observed_signal(self.profile_id, parsed_successfully=(i < successes))

    def test_a_short_history_of_failures_is_not_drift(self):
        for _ in range(5):
            pp.record_observed_signal(self.profile_id, parsed_successfully=False)
        profile = database.get_provider_profile(self.profile_id)
        self.assertIsNone(profile["drift_detected_at"])

    def test_a_source_that_was_always_imperfect_is_not_flagged(self):
        # Consistently ~60% - never drifted, just not great.
        for i in range(30):
            pp.record_observed_signal(self.profile_id, parsed_successfully=(i % 5 != 0))
        profile = database.get_provider_profile(self.profile_id)
        self.assertIsNone(profile["drift_detected_at"])

    def test_a_real_format_change_is_flagged(self):
        self._seed_good_history(count=30, successes=29)  # ~97% lifetime rate
        for _ in range(16):  # then a real break - almost everything fails now
            pp.record_observed_signal(self.profile_id, parsed_successfully=False)
        profile = database.get_provider_profile(self.profile_id)
        self.assertIsNotNone(profile["drift_detected_at"])
        self.assertIn("dropped", profile["drift_reason"])

    def test_a_live_source_is_automatically_reverted_to_observation_on_drift(self):
        self._seed_good_history(count=30, successes=29)
        database.update_provider_profile(self.profile_id, coverage=0.9)
        pp.graduate_to_demo_ready(self.profile_id)
        pp.approve_demo(self.profile_id, approved_by="owner@axim.local")
        pp.approve_live(self.profile_id, approved_by="owner@axim.local")
        self.assertEqual(database.get_provider_profile(self.profile_id)["trading_mode"], "live")

        for _ in range(16):
            pp.record_observed_signal(self.profile_id, parsed_successfully=False)

        profile = database.get_provider_profile(self.profile_id)
        self.assertEqual(profile["trading_mode"], "observation")
        self.assertIsNotNone(profile["drift_detected_at"])
        # Live/demo approval history is preserved, not erased by the revert.
        self.assertIsNotNone(profile["live_approved_at"])

    def test_drift_is_only_flagged_once_not_repeatedly(self):
        self._seed_good_history(count=30, successes=29)
        for _ in range(16):
            pp.record_observed_signal(self.profile_id, parsed_successfully=False)
        first_flagged_at = database.get_provider_profile(self.profile_id)["drift_detected_at"]

        pp.record_observed_signal(self.profile_id, parsed_successfully=False)
        self.assertEqual(database.get_provider_profile(self.profile_id)["drift_detected_at"], first_flagged_at)

    def test_reanalysis_clears_a_flagged_drift(self):
        import provider_onboarding
        self._seed_good_history(count=30, successes=29)
        for _ in range(16):
            pp.record_observed_signal(self.profile_id, parsed_successfully=False)
        self.assertIsNotNone(database.get_provider_profile(self.profile_id)["drift_detected_at"])

        provider_onboarding._update_provider_profile_from_analysis(
            999, {"pattern": "custom", "coverage": 0.9}, message_count=30,
        )
        profile = database.get_provider_profile(self.profile_id)
        self.assertIsNone(profile["drift_detected_at"])
        self.assertIsNone(profile["recent_outcomes_json"])


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
