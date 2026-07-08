import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))

import database
import fund_manager
import trade_lifecycle


def _make_closed_trade(session_id, result="win", profit_loss=8.5, trade_amount=10):
    signal = {"asset": "EUR/USD OTC", "direction": "BUY", "expiry": "1 Minute", "raw_message": "test",
              "trade_amount": trade_amount}
    trade_id = database.record_signal_received(signal, session_id=session_id)
    status = trade_lifecycle.TradeStatus.RESULT_WIN if result == "win" else trade_lifecycle.TradeStatus.RESULT_LOSS
    database.update_trade_status(trade_id, status, result=result, profit_loss=profit_loss,
                                  closed_at=datetime.now().isoformat())
    return trade_id


class FundManagerTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()


class BalanceComputationTests(FundManagerTestCase):
    def test_no_sessions_returns_starting_balance_only(self):
        fund_id = database.create_fund("F", starting_balance=1000)
        balances = fund_manager.get_fund_balances(fund_id)
        self.assertEqual(balances["starting_balance"], 1000)
        self.assertEqual(balances["trading_balance"], 1000)
        self.assertEqual(balances["protected_balance"], 0)
        self.assertEqual(balances["total_account_value"], 1000)

    def test_missing_fund_returns_none(self):
        self.assertIsNone(fund_manager.get_fund_balances(999999))

    def test_realized_pnl_flows_into_trading_balance(self):
        fund_id = database.create_fund("F", starting_balance=1000)
        session_id = database.start_trading_session("S", [1], "DEMO", fund_id=fund_id)
        database.update_session_pnl(session_id, 50)
        balances = fund_manager.get_fund_balances(fund_id)
        self.assertEqual(balances["trading_balance"], 1050)
        self.assertEqual(balances["total_account_value"], 1050)

    def test_vaulted_amount_moves_from_trading_to_protected(self):
        fund_id = database.create_fund("F", starting_balance=1000)
        session_id = database.start_trading_session("S", [1], "DEMO", fund_id=fund_id)
        database.update_session_pnl(session_id, 100)
        database.add_to_vault(session_id, 20)
        balances = fund_manager.get_fund_balances(fund_id)
        self.assertEqual(balances["protected_balance"], 20)
        self.assertEqual(balances["trading_balance"], 1080)  # 1000 + 100 - 20
        self.assertEqual(balances["total_account_value"], 1100)  # vaulting doesn't destroy value

    def test_multiple_sessions_accumulate(self):
        fund_id = database.create_fund("F", starting_balance=1000)
        s1 = database.start_trading_session("S1", [1], "DEMO", fund_id=fund_id)
        database.update_session_pnl(s1, 50)
        database.stop_trading_session(s1, "stopped_manual")
        s2 = database.start_trading_session("S2", [1], "DEMO", fund_id=fund_id)
        database.update_session_pnl(s2, 30)
        balances = fund_manager.get_fund_balances(fund_id)
        self.assertEqual(balances["trading_balance"], 1080)

    def test_unrelated_sessions_not_counted(self):
        fund_id = database.create_fund("F", starting_balance=1000)
        database.start_trading_session("No fund", [1], "DEMO")  # not attributed to any fund
        balances = fund_manager.get_fund_balances(fund_id)
        self.assertEqual(balances["trading_balance"], 1000)


class PerformanceTests(FundManagerTestCase):
    def test_no_trades_returns_empty_summary(self):
        fund_id = database.create_fund("F")
        perf = fund_manager.get_fund_performance(fund_id)
        self.assertEqual(perf["total_closed"], 0)
        self.assertIsNone(perf["win_rate"])

    def test_performance_reflects_real_trades(self):
        fund_id = database.create_fund("F")
        session_id = database.start_trading_session("S", [1], "DEMO", fund_id=fund_id)
        _make_closed_trade(session_id, result="win", profit_loss=8.5)
        _make_closed_trade(session_id, result="loss", profit_loss=-10)
        perf = fund_manager.get_fund_performance(fund_id)
        self.assertEqual(perf["total_closed"], 2)
        self.assertEqual(perf["wins"], 1)
        self.assertEqual(perf["losses"], 1)
        self.assertAlmostEqual(perf["profit_loss"], -1.5)

    def test_trades_from_other_fund_excluded(self):
        fund_a = database.create_fund("A")
        fund_b = database.create_fund("B")
        session_a = database.start_trading_session("SA", [1], "DEMO", fund_id=fund_a)
        _make_closed_trade(session_a, result="win", profit_loss=8.5)
        perf_b = fund_manager.get_fund_performance(fund_b)
        self.assertEqual(perf_b["total_closed"], 0)


class FundReportTests(FundManagerTestCase):
    def test_full_report_shape(self):
        fund_id = database.create_fund("F", starting_balance=1000)
        database.add_fund_source(fund_id, 1)
        session_id = database.start_trading_session("S", [1], "DEMO", fund_id=fund_id)
        _make_closed_trade(session_id, result="win", profit_loss=8.5)

        report = fund_manager.get_fund_report(fund_id)
        self.assertEqual(report["fund"]["name"], "F")
        self.assertIn("balances", report)
        self.assertIn("performance", report)
        self.assertEqual(report["sources"], [1])
        self.assertEqual(len(report["recent_sessions"]), 1)

    def test_missing_fund_returns_none(self):
        self.assertIsNone(fund_manager.get_fund_report(999999))


class ListFundsWithBalancesTests(FundManagerTestCase):
    def test_lists_all_with_balances_attached(self):
        f1 = database.create_fund("F1", starting_balance=500)
        f2 = database.create_fund("F2", starting_balance=1000, status="paused")
        results = fund_manager.list_funds_with_balances()
        self.assertEqual(len(results), 2)
        for r in results:
            self.assertIn("balances", r)

    def test_filters_by_status(self):
        database.create_fund("Active", status="active")
        database.create_fund("Paused", status="paused")
        results = fund_manager.list_funds_with_balances(status="active")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "Active")


if __name__ == "__main__":
    unittest.main()
