import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))

import database
import provider_reanalysis as reanalysis


def _run(coro):
    return asyncio.run(coro)


def _rec(strategy_key="capital_preservation", strategy_name="Capital Preservation", win_rate=0.55, roi_percent=20.0):
    return {"best_strategy_key": strategy_key, "best_strategy_name": strategy_name, "win_rate": win_rate, "roi_percent": roi_percent}


class ClassifyChangeTests(unittest.TestCase):
    def test_no_change_produces_no_notes(self):
        old = _rec()
        new = _rec()
        self.assertEqual(reanalysis.classify_change(old, new), [])

    def test_strategy_change_is_noted(self):
        old = _rec(strategy_key="capital_preservation", strategy_name="Capital Preservation")
        new = _rec(strategy_key="recovery_ladder", strategy_name="Recovery Ladder")
        notes = reanalysis.classify_change(old, new)
        self.assertEqual(len(notes), 1)
        self.assertIn("Capital Preservation", notes[0])
        self.assertIn("Recovery Ladder", notes[0])

    def test_small_win_rate_drift_is_not_flagged(self):
        old = _rec(win_rate=0.55)
        new = _rec(win_rate=0.53)  # 2 points - below the 5-point threshold
        self.assertEqual(reanalysis.classify_change(old, new), [])

    def test_real_win_rate_deterioration_is_flagged(self):
        old = _rec(win_rate=0.60)
        new = _rec(win_rate=0.50)  # 10 points - above threshold
        notes = reanalysis.classify_change(old, new)
        self.assertEqual(len(notes), 1)
        self.assertIn("Win rate dropped", notes[0])

    def test_real_roi_deterioration_is_flagged(self):
        old = _rec(roi_percent=50.0)
        new = _rec(roi_percent=30.0)  # 20 points - above the 10-point threshold
        notes = reanalysis.classify_change(old, new)
        self.assertTrue(any("ROI dropped" in n for n in notes))

    def test_no_prior_recommendation_is_never_a_change(self):
        self.assertEqual(reanalysis.classify_change(None, _rec()), [])

    def test_losing_the_recommendation_entirely_is_flagged(self):
        old = _rec()
        new = {"_no_recommendation": True}
        notes = reanalysis.classify_change(old, new)
        self.assertTrue(any("No strategy is recommended" in n for n in notes))


class ReanalyzeAllKnownProvidersTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self.owner_id = database.create_user("owner@test.local", "password123", role="owner", access_state="active")

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_provider_without_a_synced_channel_is_skipped_not_reanalyzed(self):
        database.save_capital_recommendation(
            source_label="Research-Only Provider", backtest_run_id=1, best_strategy_id=1,
            best_strategy_key="capital_preservation", best_strategy_name="Capital Preservation",
            roi_percent=20.0, win_rate=0.55, max_drawdown_percent=10.0, max_drawdown_amount=100.0,
            minimum_allocation=150.0, conservative_allocation=250.0, suggested_allocation=400.0,
            trades_backtested=100,
        )
        summary = _run(reanalysis.reanalyze_all_known_providers())
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["status"], "skipped_no_live_channel")

    def test_a_synced_provider_gets_reanalyzed_and_owner_notified_on_real_change(self):
        database.upsert_channel(chat_id="999", username=None, title="Live Provider", kind="channel")
        database.save_capital_recommendation(
            source_label="Live Provider", backtest_run_id=1, best_strategy_id=1,
            best_strategy_key="capital_preservation", best_strategy_name="Capital Preservation",
            roi_percent=20.0, win_rate=0.55, max_drawdown_percent=10.0, max_drawdown_amount=100.0,
            minimum_allocation=150.0, conservative_allocation=250.0, suggested_allocation=400.0,
            trades_backtested=100,
        )

        async def fake_analyze(chat_id, source_label=None, created_by=None):
            # Simulate a re-analysis that changed the recommended strategy.
            database.save_capital_recommendation(
                source_label="Live Provider", backtest_run_id=2, best_strategy_id=2,
                best_strategy_key="recovery_ladder", best_strategy_name="Recovery Ladder",
                roi_percent=25.0, win_rate=0.58, max_drawdown_percent=12.0, max_drawdown_amount=110.0,
                minimum_allocation=165.0, conservative_allocation=275.0, suggested_allocation=440.0,
                trades_backtested=120,
            )
            return {"status": "complete", "title": "Live Provider"}

        with patch("provider_onboarding.analyze_and_onboard_provider", AsyncMock(side_effect=fake_analyze)):
            summary = _run(reanalysis.reanalyze_all_known_providers())

        self.assertEqual(summary[0]["status"], "reanalyzed")
        self.assertTrue(any("Recovery Ladder" in n for n in summary[0]["changes"]))

        notifications = database.list_notifications(self.owner_id)
        self.assertEqual(len(notifications), 1)
        self.assertIn("Live Provider", notifications[0]["message"])

    def test_no_meaningful_change_does_not_notify(self):
        database.upsert_channel(chat_id="998", username=None, title="Stable Provider", kind="channel")
        database.save_capital_recommendation(
            source_label="Stable Provider", backtest_run_id=1, best_strategy_id=1,
            best_strategy_key="capital_preservation", best_strategy_name="Capital Preservation",
            roi_percent=20.0, win_rate=0.55, max_drawdown_percent=10.0, max_drawdown_amount=100.0,
            minimum_allocation=150.0, conservative_allocation=250.0, suggested_allocation=400.0,
            trades_backtested=100,
        )

        async def fake_analyze(chat_id, source_label=None, created_by=None):
            return {"status": "complete", "title": "Stable Provider"}  # recommendation row untouched -> same as before

        with patch("provider_onboarding.analyze_and_onboard_provider", AsyncMock(side_effect=fake_analyze)):
            summary = _run(reanalysis.reanalyze_all_known_providers())

        self.assertEqual(summary[0]["changes"], [])
        self.assertEqual(database.list_notifications(self.owner_id), [])


if __name__ == "__main__":
    unittest.main()
