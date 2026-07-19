import sys
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

PROJECT_ROOT = Path(__file__).resolve().parent.parent
API_DIR = PROJECT_ROOT / "api"
CORE_DIR = PROJECT_ROOT / "core"
CONFIG_DIR = PROJECT_ROOT / "config"
sys.path.insert(0, str(API_DIR))
sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(CONFIG_DIR))

import database
import backtest_routes

_FAKE_USER = {"id": 1, "email": "owner@axim.local", "role": "owner"}


def _sample_metrics(**overrides):
    """A complete, realistic metrics dict matching
    core/backtest_engine.py's _compute_metrics() return shape exactly -
    ai_analysis.generate_strategy_narrative reads several fields beyond
    the handful the PDF table itself displays, so a partial dict (like
    an early draft of this test used) fails in a way that has nothing
    to do with PDF generation itself."""
    base = {
        "final_bankroll": 1150.5, "total_profit_loss": 150.5, "roi_percent": 15.05,
        "win_rate": 0.62, "loss_rate": 0.38, "max_drawdown_percent": 8.2, "max_drawdown_amount": 90.0,
        "best_day_pnl": 40.0, "worst_day_pnl": -25.0, "longest_win_streak": 5, "longest_loss_streak": 2,
        "max_martingale_step_used": 1, "sessions_completed": 10, "sessions_stopped_by_target": 2,
        "sessions_stopped_by_loss_limit": 0, "avg_trade_size": 12.5, "largest_trade_size": 25.0,
        "total_protected_profit": 0.0, "risk_score": "Low", "best_for_label": "Steady growth",
        "sharpe_like_score": 1.2, "profit_factor": 1.8, "consistency_percent": 70.0,
        "recovery_factor": 1.6, "volatility": 5.0, "rank_overall": 1,
    }
    base.update(overrides)
    return base


def _sample_report():
    return {
        "run": {
            "name": "Test Run", "created_at": "2026-01-01T10:00:00", "created_by": "owner@axim.local",
            "starting_bankroll": 1000, "session_window": "daily",
        },
        "strategies": [
            {"id": 1, "label": "AutoPilot Conservative", "metrics": _sample_metrics()},
            {
                "id": 2, "label": "AutoPilot Growth",
                "metrics": _sample_metrics(
                    final_bankroll=890.0, total_profit_loss=-110.0, roi_percent=-10.99, win_rate=0.48,
                    loss_rate=0.52, max_drawdown_percent=34.6, risk_score="High",
                    best_for_label="Aggressive upside", rank_overall=2,
                ),
            },
        ],
    }


class BuildBacktestPdfTests(unittest.TestCase):
    """_build_backtest_pdf() - a pure function (report dict in, PDF bytes
    out, no DB/HTTP), tested directly here the same way
    test_auth_routes.py/test_event_stream_routes.py test other pure
    helpers inside api/*.py files."""

    def test_produces_a_real_pdf(self):
        pdf_bytes = backtest_routes._build_backtest_pdf(_sample_report())
        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))
        self.assertGreater(len(pdf_bytes), 1000)  # a trivially-empty PDF would be a red flag

    def test_handles_a_strategy_with_no_metrics_yet(self):
        report = _sample_report()
        report["strategies"].append({"id": 3, "label": "No Metrics Yet", "metrics": None})
        pdf_bytes = backtest_routes._build_backtest_pdf(report)
        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))

    def test_handles_zero_strategies(self):
        report = _sample_report()
        report["strategies"] = []
        pdf_bytes = backtest_routes._build_backtest_pdf(report)
        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))


class EstimateRunRouteTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_rejects_invalid_source(self):
        body = backtest_routes.RunEstimateRequest(source="not_a_source")
        with self.assertRaises(HTTPException) as ctx:
            backtest_routes.estimate_run(body, user=_FAKE_USER)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_rejects_invalid_trust_tier(self):
        body = backtest_routes.RunEstimateRequest(min_trust_tier="super_verified")
        with self.assertRaises(HTTPException) as ctx:
            backtest_routes.estimate_run(body, user=_FAKE_USER)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_estimate_reflects_real_eligible_signals(self):
        sig_id = database.create_imported_signal("C", "EUR/USD", "BUY", "1m", "2026-01-01T10:00:00")
        database.grade_imported_signal(sig_id, "win", payout_percent=85)
        body = backtest_routes.RunEstimateRequest(source="imported", risk_profile_ids=[1, 2])
        result = backtest_routes.estimate_run(body, user=_FAKE_USER)
        self.assertEqual(result["eligible_signal_count"], 1)
        self.assertEqual(result["estimated_total_trades"], 2)

    def test_create_run_rejects_invalid_trust_tier(self):
        body = backtest_routes.RunCreateRequest(
            name="R", starting_bankroll=1000, risk_profile_ids=[1], min_trust_tier="bogus")
        with self.assertRaises(HTTPException) as ctx:
            backtest_routes.create_run(body, user=_FAKE_USER)
        self.assertEqual(ctx.exception.status_code, 400)


def _run(coro):
    import asyncio
    return asyncio.run(coro)


class AsyncRunRouteTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def _graded_signal(self, i=0):
        sig_id = database.create_imported_signal(
            "C", "EUR/USD OTC", "BUY", "1 Minute", f"2026-01-01T10:0{i}:00")
        database.grade_imported_signal(sig_id, "win", payout_percent=85)

    def test_create_run_async_returns_immediately_pending_or_running(self):
        self._graded_signal()
        profile_id = database.create_risk_profile("S1", sizing_mode="fixed", fixed_amount=10)
        body = backtest_routes.RunCreateRequest(
            name="R", source="imported", starting_bankroll=1000, risk_profile_ids=[profile_id])
        run = _run(backtest_routes.create_run_async(body, user=_FAKE_USER))
        self.assertEqual(run["run_mode"], "async")
        self.assertIn(run["status"], ("pending", "running", "completed"))

    def test_async_run_eventually_completes(self):
        self._graded_signal()
        profile_id = database.create_risk_profile("S1", sizing_mode="fixed", fixed_amount=10)
        body = backtest_routes.RunCreateRequest(
            name="R", source="imported", starting_bankroll=1000, risk_profile_ids=[profile_id])

        async def scenario():
            run = await backtest_routes.create_run_async(body, user=_FAKE_USER)
            task = backtest_routes.backtest_engine._ACTIVE_BACKTEST_TASKS.get(run["id"])
            if task is not None:
                await task
            return run["id"]

        run_id = _run(scenario())
        progress = backtest_routes.get_run_progress(run_id, user=_FAKE_USER)
        self.assertEqual(progress["status"], "completed")
        self.assertEqual(progress["progress_percent"], 100.0)

    def test_get_progress_404_for_unknown_run(self):
        with self.assertRaises(HTTPException) as ctx:
            backtest_routes.get_run_progress(999999, user=_FAKE_USER)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_cancel_before_start_prevents_any_simulation(self):
        self._graded_signal()
        profile_id = database.create_risk_profile("S1", sizing_mode="fixed", fixed_amount=10)
        body = backtest_routes.RunCreateRequest(
            name="R", source="imported", starting_bankroll=1000, risk_profile_ids=[profile_id])
        run_id = database.create_backtest_run(
            "R", {"source": "imported"}, 1000, run_mode="async")
        database.create_backtest_strategy(run_id, profile_id, "S1", database.get_risk_profile(profile_id))

        cancel_result = backtest_routes.cancel_run(run_id, user=_FAKE_USER)
        self.assertTrue(cancel_result["cancelled"])

        _run(backtest_routes.backtest_engine.run_backtest_async(run_id))
        run = database.get_backtest_run(run_id)
        self.assertEqual(run["status"], "cancelled")

    def test_cancel_404_for_unknown_run(self):
        with self.assertRaises(HTTPException) as ctx:
            backtest_routes.cancel_run(999999, user=_FAKE_USER)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_cancel_is_a_clean_no_op_once_completed(self):
        run_id = database.create_backtest_run("R", {}, 1000)
        database.update_backtest_run_status(run_id, "running")
        database.update_backtest_run_status(run_id, "completed")
        result = backtest_routes.cancel_run(run_id, user=_FAKE_USER)
        self.assertFalse(result["cancelled"])
        self.assertEqual(result["status"], "completed")

    def test_audit_history_includes_creation_and_completion(self):
        self._graded_signal()
        profile_id = database.create_risk_profile("S1", sizing_mode="fixed", fixed_amount=10)
        body = backtest_routes.RunCreateRequest(
            name="R", source="imported", starting_bankroll=1000, risk_profile_ids=[profile_id])
        run = backtest_routes.create_run(body, user=_FAKE_USER)
        history = backtest_routes.get_run_audit_history(run["run"]["id"], user=_FAKE_USER)
        actions = [entry["action"] for entry in history]
        self.assertIn("created", actions)
        self.assertIn("status_completed", actions)

    def test_audit_history_404_for_unknown_run(self):
        with self.assertRaises(HTTPException) as ctx:
            backtest_routes.get_run_audit_history(999999, user=_FAKE_USER)
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
