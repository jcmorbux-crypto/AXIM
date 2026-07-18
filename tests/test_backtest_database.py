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


if __name__ == "__main__":
    unittest.main()
