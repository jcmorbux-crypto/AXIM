import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))

import database
import risk_engine


class ComputePositionSizeTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self.session_id = database.start_trading_session("Test", [1], "DEMO")

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_no_session_id_falls_through_to_risk_manager(self):
        amount = risk_engine.compute_position_size(None, 5.0)
        self.assertEqual(amount, 5.0)  # risk_manager.compute_trade_amount fixed fallback

    def test_session_without_risk_profile_falls_through(self):
        amount = risk_engine.compute_position_size(self.session_id, 5.0)
        self.assertEqual(amount, 5.0)

    def test_fixed_sizing(self):
        profile_id = database.create_risk_profile("Fixed Test", sizing_mode="fixed", fixed_amount=7.5)
        database.set_session_risk_profile(self.session_id, profile_id)
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 7.5)

    def test_percent_sizing_uses_static_bankroll(self):
        profile_id = database.create_risk_profile("Percent Test", sizing_mode="percent",
                                                    bankroll=1000, percent_of_bankroll=2.0)
        database.set_session_risk_profile(self.session_id, profile_id)
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 20.0)

    def test_dynamic_sizing_grows_with_session_pnl(self):
        profile_id = database.create_risk_profile("Dynamic Test", sizing_mode="dynamic",
                                                    bankroll=1000, percent_of_bankroll=2.0)
        database.set_session_risk_profile(self.session_id, profile_id)
        database.update_session_pnl(self.session_id, 500)  # bankroll now effectively 1500
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 30.0)

    def test_kelly_sizing_positive_edge(self):
        # p=0.6, b=0.85 -> f* = 0.6 - 0.4/0.85 = 0.6 - 0.4706 = 0.1294
        # half-Kelly (default multiplier 0.5) -> 0.0647 * 1000 = 64.7
        profile_id = database.create_risk_profile(
            "Kelly Test", sizing_mode="kelly", bankroll=1000,
            kelly_win_rate_estimate=0.6, kelly_payout_estimate=0.85, kelly_fraction_multiplier=0.5,
        )
        database.set_session_risk_profile(self.session_id, profile_id)
        amount = risk_engine.compute_position_size(self.session_id, 5.0)
        self.assertAlmostEqual(amount, 64.71, places=1)

    def test_kelly_sizing_negative_edge_clamped_to_zero(self):
        # p=0.3 (bad win rate) -> f* is deeply negative, clamped to 0
        profile_id = database.create_risk_profile(
            "Bad Kelly", sizing_mode="kelly", bankroll=1000,
            kelly_win_rate_estimate=0.3, kelly_payout_estimate=0.85, kelly_fraction_multiplier=0.5,
        )
        database.set_session_risk_profile(self.session_id, profile_id)
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 0.0)

    def test_max_trade_amount_caps_final_size(self):
        profile_id = database.create_risk_profile("Capped", sizing_mode="percent",
                                                    bankroll=10000, percent_of_bankroll=5.0, max_trade_amount=50)
        database.set_session_risk_profile(self.session_id, profile_id)
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 50)

    def test_martingale_steps_up_after_loss(self):
        profile_id = database.create_risk_profile("Martingale Test", sizing_mode="fixed", fixed_amount=10)
        database.update_martingale_settings(profile_id, enabled=True, max_steps=5, multiplier=2.0)
        database.set_session_risk_profile(self.session_id, profile_id)

        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 10)  # step 0
        database.advance_martingale_step(self.session_id)
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 20)  # step 1
        database.advance_martingale_step(self.session_id)
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 40)  # step 2

    def test_martingale_disabled_for_session_overrides_profile_setting(self):
        profile_id = database.create_risk_profile("Martingale Test", sizing_mode="fixed", fixed_amount=10)
        database.update_martingale_settings(profile_id, enabled=True, max_steps=5, multiplier=2.0)
        database.set_session_risk_profile(self.session_id, profile_id)
        database.advance_martingale_step(self.session_id)
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 20)  # stepped, martingale on

        database.set_session_martingale_disabled(self.session_id, True)
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 10)  # flat base amount now

    def test_martingale_resets_after_win_when_configured(self):
        profile_id = database.create_risk_profile("Martingale Reset", sizing_mode="fixed", fixed_amount=10)
        database.update_martingale_settings(profile_id, enabled=True, max_steps=5, multiplier=2.0, reset_after_win=True)
        database.set_session_risk_profile(self.session_id, profile_id)
        database.advance_martingale_step(self.session_id)
        database.advance_martingale_step(self.session_id)
        self.assertEqual(database.get_trading_session(self.session_id)["current_martingale_step"], 2)
        database.reset_martingale_step(self.session_id)
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 10)

    def test_martingale_custom_ladder_overrides_multiplier(self):
        profile_id = database.create_risk_profile("Ladder Test", sizing_mode="fixed", fixed_amount=10)
        database.update_martingale_settings(profile_id, enabled=True, max_steps=4,
                                             custom_ladder_json="[10, 22, 48, 105]")
        database.set_session_risk_profile(self.session_id, profile_id)
        database.advance_martingale_step(self.session_id)
        database.advance_martingale_step(self.session_id)
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 48)

    def test_martingale_max_total_exposure_caps_stepped_amount(self):
        profile_id = database.create_risk_profile("Exposure Cap", sizing_mode="fixed", fixed_amount=10)
        database.update_martingale_settings(profile_id, enabled=True, max_steps=10, multiplier=2.0,
                                             max_total_exposure=30)
        database.set_session_risk_profile(self.session_id, profile_id)
        database.advance_martingale_step(self.session_id)
        database.advance_martingale_step(self.session_id)  # would be 40 uncapped
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 30)

    def test_compounding_milestone_step_increases_percent(self):
        profile_id = database.create_risk_profile("Compounding Test", sizing_mode="percent",
                                                    bankroll=1000, percent_of_bankroll=2.0)
        database.update_compounding_settings(
            profile_id, mode="milestone_based",
            steps_json='[{"profit_threshold": 50, "risk_percent": 2.25}, {"profit_threshold": 100, "risk_percent": 2.5}]',
        )
        database.set_session_risk_profile(self.session_id, profile_id)
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 20.0)  # base 2%
        database.update_session_pnl(self.session_id, 60)  # crosses $50 threshold
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 22.5)  # 2.25% of 1000

    def test_compounding_drawdown_resets_to_base(self):
        profile_id = database.create_risk_profile("Drawdown Test", sizing_mode="percent",
                                                    bankroll=1000, percent_of_bankroll=2.0)
        database.update_compounding_settings(
            profile_id, mode="milestone_based", drawdown_reset_percent=8,
            steps_json='[{"profit_threshold": 50, "risk_percent": 3.0}]',
        )
        database.set_session_risk_profile(self.session_id, profile_id)
        database.update_session_pnl(self.session_id, 60)
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 30.0)  # stepped to 3%
        database.update_session_pnl(self.session_id, -150)  # net -90, -9% drawdown of 1000
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 20.0)  # back to base 2%


class MartingaleProjectionTests(unittest.TestCase):
    def test_project_exposure_with_multiplier(self):
        martingale = {"enabled": True, "max_steps": 4, "multiplier": 2.0,
                      "custom_ladder_json": None, "max_total_exposure": 0}
        result = risk_engine.project_martingale_exposure(martingale, 10)
        self.assertEqual(result["steps"], [10, 20, 40, 80])
        self.assertEqual(result["total_exposure"], 150)

    def test_project_exposure_disabled_returns_empty(self):
        result = risk_engine.project_martingale_exposure({"enabled": False}, 10)
        self.assertEqual(result["steps"], [])
        self.assertEqual(result["total_exposure"], 0)

    def test_project_exposure_respects_cap(self):
        martingale = {"enabled": True, "max_steps": 3, "multiplier": 2.0,
                      "custom_ladder_json": None, "max_total_exposure": 25}
        result = risk_engine.project_martingale_exposure(martingale, 10)
        self.assertEqual(result["steps"], [10, 20, 25])


class VaultTriggerTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self.session_id = database.start_trading_session("Vault Test", [1], "DEMO")

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_every_winning_session_vaults_on_session_end(self):
        profile_id = database.create_risk_profile("Vault Profile", sizing_mode="fixed", fixed_amount=10)
        database.update_profit_vault_settings(profile_id, enabled=True, vault_percent=20,
                                               trigger_event="every_winning_session")
        database.set_session_risk_profile(self.session_id, profile_id)
        database.update_session_pnl(self.session_id, 100)
        risk_engine.on_session_ended(self.session_id)
        self.assertEqual(database.get_trading_session(self.session_id)["vaulted_amount"], 20.0)

    def test_no_vault_on_losing_session(self):
        profile_id = database.create_risk_profile("Vault Profile", sizing_mode="fixed", fixed_amount=10)
        database.update_profit_vault_settings(profile_id, enabled=True, vault_percent=20,
                                               trigger_event="every_winning_session")
        database.set_session_risk_profile(self.session_id, profile_id)
        database.update_session_pnl(self.session_id, -50)
        risk_engine.on_session_ended(self.session_id)
        self.assertEqual(database.get_trading_session(self.session_id)["vaulted_amount"], 0.0)

    def test_milestone_based_vault_skims_at_each_milestone(self):
        profile_id = database.create_risk_profile("Milestone Vault", sizing_mode="fixed", fixed_amount=10)
        database.update_profit_vault_settings(profile_id, enabled=True, vault_percent=10,
                                               trigger_event="milestone_based", milestone_amount=50)
        database.set_session_risk_profile(self.session_id, profile_id)
        database.update_session_pnl(self.session_id, 60)  # crosses one $50 milestone
        risk_engine.on_trade_closed(self.session_id, won=True, profit_loss=60)
        self.assertEqual(database.get_trading_session(self.session_id)["vaulted_amount"], 5.0)

        database.update_session_pnl(self.session_id, 50)  # now at 110, crosses second milestone
        risk_engine.on_trade_closed(self.session_id, won=True, profit_loss=50)
        self.assertEqual(database.get_trading_session(self.session_id)["vaulted_amount"], 10.0)


if __name__ == "__main__":
    unittest.main()
