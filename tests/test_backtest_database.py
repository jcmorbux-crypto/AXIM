import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))

import database


class BacktestDbTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()


class ImportedSignalTests(BacktestDbTestCase):
    def test_create_list_grade_delete(self):
        sig_id = database.create_imported_signal(
            "Test Channel", "EUR/USD OTC", "BUY", "1 Minute", "2026-01-01T10:00:00", raw_message="buy eurusd")
        signals = database.list_imported_signals()
        self.assertEqual(len(signals), 1)
        self.assertIsNone(signals[0]["result"])

        database.grade_imported_signal(sig_id, "win", payout_percent=85)
        graded = database.list_imported_signals(graded_only=True)
        self.assertEqual(len(graded), 1)
        self.assertEqual(graded[0]["result"], "win")
        self.assertEqual(graded[0]["payout_percent"], 85)

        database.delete_imported_signal(sig_id)
        self.assertEqual(database.list_imported_signals(), [])

    def test_grade_rejects_invalid_result(self):
        sig_id = database.create_imported_signal("C", "EUR/USD", "BUY", "1m", "2026-01-01T10:00:00")
        with self.assertRaises(ValueError):
            database.grade_imported_signal(sig_id, "not_a_result")

    def test_import_batch_filter(self):
        database.create_imported_signal("C", "A", "BUY", "1m", "2026-01-01T10:00:00", import_batch="batch1")
        database.create_imported_signal("C", "B", "BUY", "1m", "2026-01-01T10:05:00", import_batch="batch2")
        self.assertEqual(len(database.list_imported_signals(import_batch="batch1")), 1)
        self.assertEqual(len(database.list_imported_signals()), 2)


class HistoricalSignalPoolTests(BacktestDbTestCase):
    def test_ungraded_signals_excluded(self):
        database.create_imported_signal("C", "EUR/USD", "BUY", "1m", "2026-01-01T10:00:00")  # no result
        pool = database.get_historical_signal_pool("imported")
        self.assertEqual(pool, [])

    def test_pool_from_imported_only(self):
        sig_id = database.create_imported_signal("C", "EUR/USD", "BUY", "1m", "2026-01-01T10:00:00")
        database.grade_imported_signal(sig_id, "win", payout_percent=80)
        pool = database.get_historical_signal_pool("imported")
        self.assertEqual(len(pool), 1)
        self.assertEqual(pool[0]["source_type"], "imported")
        self.assertEqual(pool[0]["payout_percent"], 80)

    def test_pool_from_live_signals(self):
        signal = {"asset": "EUR/USD OTC", "direction": "BUY", "expiry": "1 Minute", "raw_message": "test",
                  "trade_amount": 10}
        trade_id = database.record_signal_received(signal, source="LiveChannel")
        import trade_lifecycle
        database.update_trade_status(trade_id, trade_lifecycle.TradeStatus.RESULT_WIN,
                                      result="win", profit_loss=8.5, payout=85, closed_at="2026-01-01T10:01:00")
        pool = database.get_historical_signal_pool("live")
        self.assertEqual(len(pool), 1)
        self.assertEqual(pool[0]["source_type"], "live")
        self.assertEqual(pool[0]["payout_percent"], 85)
        self.assertEqual(pool[0]["trade_amount"], 10)

    def test_pool_both_sources_sorted_by_timestamp(self):
        signal = {"asset": "EUR/USD OTC", "direction": "BUY", "expiry": "1 Minute", "raw_message": "test"}
        trade_id = database.record_signal_received(signal, source="LiveChannel")
        import trade_lifecycle
        database.update_trade_status(trade_id, trade_lifecycle.TradeStatus.RESULT_LOSS,
                                      result="loss", profit_loss=-10, closed_at="2026-06-01T10:01:00")
        sig_id = database.create_imported_signal("C", "GBP/USD", "SELL", "1m", "2026-01-01T09:00:00")
        database.grade_imported_signal(sig_id, "win", payout_percent=80)

        pool = database.get_historical_signal_pool("both")
        self.assertEqual(len(pool), 2)
        self.assertEqual(pool[0]["source_type"], "imported")  # earlier timestamp first
        self.assertEqual(pool[1]["source_type"], "live")

    def test_channel_filter(self):
        database.create_imported_signal("Alpha", "A", "BUY", "1m", "2026-01-01T10:00:00")
        sig2 = database.create_imported_signal("Beta", "B", "BUY", "1m", "2026-01-01T10:05:00")
        database.grade_imported_signal(sig2, "win", payout_percent=80)
        sig1 = database.list_imported_signals()[0]["id"]
        database.grade_imported_signal(sig1, "loss")
        pool = database.get_historical_signal_pool("imported", channel_filter=["Beta"])
        self.assertEqual(len(pool), 1)
        self.assertEqual(pool[0]["channel"], "Beta")


class BacktestRunLifecycleTests(BacktestDbTestCase):
    def test_full_lifecycle(self):
        run_id = database.create_backtest_run("My Run", {"source": "imported"}, 1000, default_payout_percent=85)
        run = database.get_backtest_run(run_id)
        self.assertEqual(run["name"], "My Run")
        self.assertEqual(run["status"], "pending")
        self.assertEqual(run["signal_pool"], {"source": "imported"})

        database.update_backtest_run_status(run_id, "running")
        self.assertEqual(database.get_backtest_run(run_id)["status"], "running")

        strategy_id = database.create_backtest_strategy(run_id, None, "Capital Shield", {"sizing_mode": "fixed"})
        strategies = database.list_backtest_strategies(run_id)
        self.assertEqual(len(strategies), 1)
        self.assertEqual(strategies[0]["profile_snapshot"]["sizing_mode"], "fixed")

        session_id = database.create_backtest_session(strategy_id, 0, "2026-01-01T00:00:00", "completed", 1000)
        database.create_backtest_trade(session_id, "imported", 1, 0, "EUR/USD", "BUY",
                                        "2026-01-01T10:00:00", 10, 0, "win", 8.5, 1008.5)
        trades = database.list_backtest_trades(session_id)
        self.assertEqual(len(trades), 1)
        strategy_trades = database.list_backtest_trades_for_strategy(strategy_id)
        self.assertEqual(len(strategy_trades), 1)

        database.save_backtest_metrics(strategy_id, {"final_bankroll": 1008.5, "roi_percent": 0.85})
        metrics = database.get_backtest_metrics(strategy_id)
        self.assertEqual(metrics["final_bankroll"], 1008.5)

        # re-save (upsert) updates in place, not a duplicate row
        database.save_backtest_metrics(strategy_id, {"final_bankroll": 1200.0, "roi_percent": 20.0})
        self.assertEqual(database.get_backtest_metrics(strategy_id)["final_bankroll"], 1200.0)

        database.update_backtest_run_status(run_id, "completed")
        report = database.get_backtest_report(run_id)
        self.assertEqual(report["run"]["status"], "completed")
        self.assertEqual(len(report["strategies"]), 1)
        self.assertEqual(report["strategies"][0]["metrics"]["final_bankroll"], 1200.0)

    def test_create_backtest_trades_bulk_matches_individual_inserts(self):
        # Discovered as a real performance bug (~45ms/trade individual
        # commits made a real-data backtest take minutes instead of
        # seconds) while verifying Daily Compounding's backtest reporting -
        # core/backtest_engine.py's run_backtest now calls this instead of
        # looping create_backtest_trade. Proves the batched insert produces
        # the identical rows a caller would get from individual calls.
        run_id = database.create_backtest_run("Bulk Test", {"source": "imported"}, 1000)
        strategy_id = database.create_backtest_strategy(run_id, None, "S", {"sizing_mode": "fixed"})
        session_id = database.create_backtest_session(strategy_id, 0, "2026-01-01T00:00:00", "completed", 1000)

        trades = [
            {
                "signal_source_type": "imported", "signal_id": i, "sequence_in_session": i,
                "asset": "EUR/USD", "direction": "BUY", "occurred_at": f"2026-01-01T{10 + i}:00:00",
                "trade_amount": 10, "martingale_step": 0, "result": "win" if i % 2 == 0 else "loss",
                "profit_loss": 8.5 if i % 2 == 0 else -10, "running_balance": 1000 + i,
            }
            for i in range(5)
        ]
        database.create_backtest_trades_bulk(session_id, trades)

        stored = database.list_backtest_trades(session_id)
        self.assertEqual(len(stored), 5)
        self.assertEqual({t["signal_id"] for t in stored}, {0, 1, 2, 3, 4})
        self.assertEqual(sorted(t["result"] for t in stored), ["loss", "loss", "win", "win", "win"])

    def test_create_backtest_trades_bulk_with_empty_list_is_a_no_op(self):
        run_id = database.create_backtest_run("Bulk Empty Test", {"source": "imported"}, 1000)
        strategy_id = database.create_backtest_strategy(run_id, None, "S", {"sizing_mode": "fixed"})
        session_id = database.create_backtest_session(strategy_id, 0, "2026-01-01T00:00:00", "completed", 1000)
        database.create_backtest_trades_bulk(session_id, [])
        self.assertEqual(database.list_backtest_trades(session_id), [])

    def test_update_status_rejects_invalid(self):
        run_id = database.create_backtest_run("R", {}, 1000)
        with self.assertRaises(ValueError):
            database.update_backtest_run_status(run_id, "not_a_status")

    def test_delete_run_cascades(self):
        run_id = database.create_backtest_run("R", {}, 1000)
        strategy_id = database.create_backtest_strategy(run_id, None, "S", {})
        session_id = database.create_backtest_session(strategy_id, 0, "2026-01-01T00:00:00", "completed", 1000)
        database.create_backtest_trade(session_id, "imported", 1, 0, "A", "BUY", "2026-01-01T10:00:00", 10, 0, "win", 8.5, 1008.5)
        database.save_backtest_metrics(strategy_id, {"final_bankroll": 1008.5})

        database.delete_backtest_run(run_id)

        self.assertIsNone(database.get_backtest_run(run_id))
        self.assertEqual(database.list_backtest_strategies(run_id), [])
        self.assertEqual(database.list_backtest_sessions(strategy_id), [])
        self.assertEqual(database.list_backtest_trades(session_id), [])
        self.assertIsNone(database.get_backtest_metrics(strategy_id))

    def test_get_backtest_report_missing_run(self):
        self.assertIsNone(database.get_backtest_report(999999))


class TrustedDataPolicyTests(BacktestDbTestCase):
    """2026-07-19 Backtesting trust-data policy: every imported signal is
    provider-claimed, never independently confirmed by AXIM - defaults
    must stay honest (never silently upgraded)."""

    def test_new_imported_signal_defaults_to_legacy_unverified(self):
        sig_id = database.create_imported_signal("C", "EUR/USD", "BUY", "1m", "2026-01-01T10:00:00")
        row = database.list_imported_signals()[0]
        self.assertEqual(row["id"], sig_id)
        self.assertEqual(row["data_trust_tier"], "legacy_unverified")

    def test_create_rejects_invalid_trust_tier(self):
        with self.assertRaises(ValueError):
            database.create_imported_signal("C", "EUR/USD", "BUY", "1m", "2026-01-01T10:00:00",
                                              data_trust_tier="made_up_tier")

    def test_grading_does_not_change_trust_tier(self):
        sig_id = database.create_imported_signal("C", "EUR/USD", "BUY", "1m", "2026-01-01T10:00:00")
        database.grade_imported_signal(sig_id, "win", payout_percent=85)
        row = database.list_imported_signals()[0]
        self.assertEqual(row["data_trust_tier"], "legacy_unverified")

    def test_grade_accepts_cancelled(self):
        sig_id = database.create_imported_signal("C", "EUR/USD", "BUY", "1m", "2026-01-01T10:00:00")
        database.grade_imported_signal(sig_id, "cancelled")
        row = database.list_imported_signals()[0]
        self.assertEqual(row["result"], "cancelled")

    def test_edit_bumps_trust_tier_to_reprocessed_and_audits(self):
        sig_id = database.create_imported_signal("C", "EUR/USD", "SELL", "1m", "2026-01-01T10:00:00")
        database.edit_imported_signal(sig_id, changed_by="ops@axim", reason="parser had wrong direction",
                                       direction="BUY")
        row = database.list_imported_signals()[0]
        self.assertEqual(row["direction"], "BUY")
        self.assertEqual(row["data_trust_tier"], "reprocessed_source")
        self.assertIsNotNone(row["reprocessed_at"])
        self.assertEqual(row["reprocessed_by"], "ops@axim")

        log = database.list_backtest_data_audit_log(entity_type="imported_signal", entity_id=sig_id)
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["action"], "edited")
        self.assertEqual(log[0]["reason"], "parser had wrong direction")

    def test_edit_never_downgrades_an_already_verified_signal(self):
        sig_id = database.create_imported_signal("C", "EUR/USD", "SELL", "1m", "2026-01-01T10:00:00")
        database.set_imported_signal_trust_tier(sig_id, "verified_only", changed_by="ops", reason="cross-checked broker log")
        database.edit_imported_signal(sig_id, changed_by="ops", reason="typo fix", asset="EUR/GBP")
        row = database.list_imported_signals()[0]
        self.assertEqual(row["data_trust_tier"], "verified_only")

    def test_edit_requires_a_reason(self):
        sig_id = database.create_imported_signal("C", "EUR/USD", "SELL", "1m", "2026-01-01T10:00:00")
        with self.assertRaises(ValueError):
            database.edit_imported_signal(sig_id, changed_by="ops", reason="", asset="EUR/GBP")

    def test_edit_rejects_unknown_fields(self):
        sig_id = database.create_imported_signal("C", "EUR/USD", "SELL", "1m", "2026-01-01T10:00:00")
        with self.assertRaises(ValueError):
            database.edit_imported_signal(sig_id, changed_by="ops", reason="x", result="win")

    def test_set_trust_tier_requires_a_reason(self):
        sig_id = database.create_imported_signal("C", "EUR/USD", "SELL", "1m", "2026-01-01T10:00:00")
        with self.assertRaises(ValueError):
            database.set_imported_signal_trust_tier(sig_id, "verified_only", changed_by="ops", reason="")

    def test_set_trust_tier_rejects_invalid_value(self):
        sig_id = database.create_imported_signal("C", "EUR/USD", "SELL", "1m", "2026-01-01T10:00:00")
        with self.assertRaises(ValueError):
            database.set_imported_signal_trust_tier(sig_id, "super_verified", changed_by="ops", reason="x")

    def test_set_trust_tier_is_audited(self):
        sig_id = database.create_imported_signal("C", "EUR/USD", "SELL", "1m", "2026-01-01T10:00:00")
        database.set_imported_signal_trust_tier(sig_id, "verified_only", changed_by="ops", reason="cross-checked")
        log = database.list_backtest_data_audit_log(entity_type="imported_signal", entity_id=sig_id)
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["action"], "trust_tier_changed")


class MinTrustTierPoolFilterTests(BacktestDbTestCase):
    def _make_signal(self, tier):
        sig_id = database.create_imported_signal("C", "EUR/USD", "BUY", "1m", "2026-01-01T10:00:00")
        database.grade_imported_signal(sig_id, "win", payout_percent=85)
        if tier != "legacy_unverified":
            database.set_imported_signal_trust_tier(sig_id, tier, changed_by="ops", reason="test setup")
        return sig_id

    def test_no_filter_returns_every_tier(self):
        self._make_signal("legacy_unverified")
        self._make_signal("reprocessed_source")
        self._make_signal("verified_only")
        pool = database.get_historical_signal_pool("imported")
        self.assertEqual(len(pool), 3)

    def test_min_reprocessed_source_excludes_legacy(self):
        self._make_signal("legacy_unverified")
        self._make_signal("reprocessed_source")
        self._make_signal("verified_only")
        pool = database.get_historical_signal_pool("imported", min_trust_tier="reprocessed_source")
        self.assertEqual(len(pool), 2)
        self.assertNotIn("legacy_unverified", {s["data_trust_tier"] for s in pool})

    def test_min_verified_only_is_strictest(self):
        self._make_signal("legacy_unverified")
        self._make_signal("reprocessed_source")
        self._make_signal("verified_only")
        pool = database.get_historical_signal_pool("imported", min_trust_tier="verified_only")
        self.assertEqual(len(pool), 1)
        self.assertEqual(pool[0]["data_trust_tier"], "verified_only")

    def test_live_signals_always_pass_regardless_of_threshold(self):
        signal = {"asset": "EUR/USD OTC", "direction": "BUY", "expiry": "1 Minute", "raw_message": "test"}
        trade_id = database.record_signal_received(signal, source="LiveChannel")
        import trade_lifecycle
        database.update_trade_status(trade_id, trade_lifecycle.TradeStatus.RESULT_WIN,
                                      result="win", profit_loss=8.5, payout=85, closed_at="2026-01-01T10:01:00")
        pool = database.get_historical_signal_pool("live", min_trust_tier="verified_only")
        self.assertEqual(len(pool), 1)
        self.assertEqual(pool[0]["data_trust_tier"], "verified_only")


class SignalEligibilityTests(BacktestDbTestCase):
    """2026-07-19 directive: every signal considered for a backtest run
    lands in exactly one attributable eligibility bucket."""

    def test_every_category_present_even_when_zero(self):
        result = database.analyze_signal_eligibility("imported")
        self.assertEqual(set(result["breakdown"].keys()), set(database._ELIGIBILITY_CATEGORIES))
        self.assertEqual(result["total"], 0)

    def test_ungraded_signal_is_counted_and_excluded(self):
        database.create_imported_signal("C", "EUR/USD", "BUY", "1m", "2026-01-01T10:00:00")
        result = database.analyze_signal_eligibility("imported")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["breakdown"]["excluded_ungraded"], 1)
        self.assertEqual(result["breakdown"]["eligible"], 0)
        self.assertEqual(result["pool"], [])

    def test_cancelled_signal_gets_its_own_bucket_not_lumped_with_ungraded(self):
        sig_id = database.create_imported_signal("C", "EUR/USD", "BUY", "1m", "2026-01-01T10:00:00")
        database.grade_imported_signal(sig_id, "cancelled")
        result = database.analyze_signal_eligibility("imported")
        self.assertEqual(result["breakdown"]["excluded_cancelled"], 1)
        self.assertEqual(result["breakdown"]["excluded_ungraded"], 0)

    def test_ambiguous_signal_missing_asset_is_excluded_and_counted(self):
        sig_id = database.create_imported_signal("C", None, "BUY", "1m", "2026-01-01T10:00:00")
        database.grade_imported_signal(sig_id, "win", payout_percent=85)
        result = database.analyze_signal_eligibility("imported")
        self.assertEqual(result["breakdown"]["excluded_ambiguous"], 1)
        self.assertEqual(result["breakdown"]["eligible"], 0)

    def test_below_trust_tier_signal_is_excluded_and_counted(self):
        sig_id = database.create_imported_signal("C", "EUR/USD", "BUY", "1m", "2026-01-01T10:00:00")
        database.grade_imported_signal(sig_id, "win", payout_percent=85)
        result = database.analyze_signal_eligibility("imported", min_trust_tier="verified_only")
        self.assertEqual(result["breakdown"]["excluded_below_trust_tier"], 1)
        self.assertEqual(result["breakdown"]["eligible"], 0)

    def test_clean_eligible_signal_is_counted_and_included_in_pool(self):
        sig_id = database.create_imported_signal("C", "EUR/USD", "BUY", "1m", "2026-01-01T10:00:00")
        database.grade_imported_signal(sig_id, "win", payout_percent=85)
        result = database.analyze_signal_eligibility("imported")
        self.assertEqual(result["breakdown"]["eligible"], 1)
        self.assertEqual(len(result["pool"]), 1)
        self.assertEqual(result["pool"][0]["signal_id"], sig_id)

    def test_pool_matches_get_historical_signal_pool_for_same_args(self):
        sig_id = database.create_imported_signal("C", "EUR/USD", "BUY", "1m", "2026-01-01T10:00:00")
        database.grade_imported_signal(sig_id, "win", payout_percent=85)
        eligibility = database.analyze_signal_eligibility("imported")
        plain_pool = database.get_historical_signal_pool("imported")
        self.assertEqual(eligibility["pool"], plain_pool)

    def test_live_non_trade_outcome_is_its_own_bucket(self):
        signal = {"asset": "EUR/USD OTC", "direction": "BUY", "expiry": "1 Minute", "raw_message": "test"}
        trade_id = database.record_signal_received(signal, source="LiveChannel")
        import trade_lifecycle
        database.update_trade_status(trade_id, trade_lifecycle.TradeStatus.ERROR, result="rejected:all_workers_busy")
        result = database.analyze_signal_eligibility("live")
        self.assertEqual(result["breakdown"]["excluded_non_trade_outcome"], 1)
        self.assertEqual(result["breakdown"]["eligible"], 0)

    def test_total_sums_to_breakdown_sum(self):
        s1 = database.create_imported_signal("C", "EUR/USD", "BUY", "1m", "2026-01-01T10:00:00")
        database.grade_imported_signal(s1, "win", payout_percent=85)
        s2 = database.create_imported_signal("C", None, "BUY", "1m", "2026-01-01T10:01:00")
        database.grade_imported_signal(s2, "loss")
        database.create_imported_signal("C", "EUR/USD", "BUY", "1m", "2026-01-01T10:02:00")  # ungraded
        result = database.analyze_signal_eligibility("imported")
        self.assertEqual(result["total"], 3)
        self.assertEqual(sum(result["breakdown"].values()), 3)


if __name__ == "__main__":
    unittest.main()
