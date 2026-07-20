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
import money_studio
import capital_recommendation_routes as routes
from fastapi import HTTPException

_FAKE_ADMIN = {"id": 1, "email": "owner@axim.local", "role": "owner"}


class CreateDemoFundTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

        self.ladder_profile = money_studio.build_virtual_profile("recovery_ladder")

        run_id = database.create_backtest_run(
            "Auto: Martin Trader", {"source": "imported", "channel_filter": ["Martin Trader"]}, 1000.0,
        )
        strategy_id = database.create_backtest_strategy(
            run_id, self.ladder_profile["id"], self.ladder_profile["name"], self.ladder_profile,
        )
        self.run_id = run_id
        self.strategy_id = strategy_id
        self.recommendation_id = database.save_capital_recommendation(
            source_label="Martin Trader", backtest_run_id=run_id, best_strategy_id=strategy_id,
            best_strategy_key="recovery_ladder", best_strategy_name=self.ladder_profile["name"],
            roi_percent=25.0, win_rate=0.6, max_drawdown_percent=12.0, max_drawdown_amount=100.0,
            minimum_allocation=150.0, conservative_allocation=250.0, suggested_allocation=400.0,
            trades_backtested=1630,
        )

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_creates_a_new_fund_sized_at_the_requested_tier(self):
        body = routes.CreateDemoFundRequest(tier="suggested")
        result = routes.create_demo_fund(self.recommendation_id, body, user=_FAKE_ADMIN)
        self.assertEqual(result["fund_report"]["fund"]["starting_balance"], 400.0)
        self.assertEqual(result["allocation_tier"], "suggested")
        self.assertEqual(result["allocation_amount"], 400.0)

    def test_minimum_tier_uses_the_minimum_allocation(self):
        body = routes.CreateDemoFundRequest(tier="minimum")
        result = routes.create_demo_fund(self.recommendation_id, body, user=_FAKE_ADMIN)
        self.assertEqual(result["fund_report"]["fund"]["starting_balance"], 150.0)

    def test_deploys_the_recommended_strategy_as_a_fresh_profile(self):
        body = routes.CreateDemoFundRequest(tier="suggested")
        result = routes.create_demo_fund(self.recommendation_id, body, user=_FAKE_ADMIN)
        deployed = database.get_risk_profile(result["deployed_profile_id"])
        self.assertEqual(deployed["strategy_key"], "recovery_ladder")
        self.assertFalse(deployed["is_template"])  # a fresh instance, never the shared template itself
        martingale = database.get_martingale_settings(result["deployed_profile_id"])
        self.assertTrue(martingale["enabled"])

    def test_new_fund_is_never_live_enabled(self):
        body = routes.CreateDemoFundRequest(tier="suggested")
        result = routes.create_demo_fund(self.recommendation_id, body, user=_FAKE_ADMIN)
        self.assertFalse(result["fund_report"]["fund"]["live_enabled"])

    def test_no_matching_channel_still_creates_the_fund_with_a_clear_note(self):
        body = routes.CreateDemoFundRequest(tier="suggested")
        result = routes.create_demo_fund(self.recommendation_id, body, user=_FAKE_ADMIN)
        self.assertFalse(result["source_channel_attached"])
        self.assertIsNotNone(result["note"])
        self.assertIn("Martin Trader", result["note"])

    def test_matching_channel_gets_attached_as_a_fund_source(self):
        database.upsert_channel(chat_id="999", username="martintrader", title="Martin Trader", kind="channel")
        channel_id = database.find_channel(title="Martin Trader")["id"]
        body = routes.CreateDemoFundRequest(tier="suggested")
        result = routes.create_demo_fund(self.recommendation_id, body, user=_FAKE_ADMIN)
        self.assertTrue(result["source_channel_attached"])
        self.assertIsNone(result["note"])
        self.assertIn(channel_id, database.list_fund_source_channel_ids(result["fund_report"]["fund"]["id"]))

    def test_invalid_tier_is_rejected(self):
        body = routes.CreateDemoFundRequest(tier="aggressive")
        with self.assertRaises(HTTPException) as ctx:
            routes.create_demo_fund(self.recommendation_id, body, user=_FAKE_ADMIN)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_unknown_recommendation_404s(self):
        body = routes.CreateDemoFundRequest(tier="suggested")
        with self.assertRaises(HTTPException) as ctx:
            routes.create_demo_fund(999999, body, user=_FAKE_ADMIN)
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
