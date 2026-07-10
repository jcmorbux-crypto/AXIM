import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))

import database


class SessionProfileTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_create_and_list_profile(self):
        database.create_session_profile("Conservative", [1, 2], profit_target=50, loss_limit=20, max_trades=10)
        profiles = database.list_session_profiles()
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]["channel_ids"], [1, 2])
        self.assertEqual(profiles[0]["profit_target"], 50)

    def test_delete_profile(self):
        profile_id = database.create_session_profile("Temp", [1])
        database.delete_session_profile(profile_id)
        self.assertEqual(database.list_session_profiles(), [])


class TradingSessionTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_no_active_session_by_default(self):
        self.assertIsNone(database.get_active_trading_session())

    def test_start_session_becomes_active(self):
        session_id = database.start_trading_session("Test", [1], "DEMO", profit_target=50, loss_limit=20, max_trades=5)
        active = database.get_active_trading_session()
        self.assertEqual(active["id"], session_id)
        self.assertEqual(active["status"], "active")
        self.assertEqual(active["trades_count"], 0)
        self.assertEqual(active["realized_pnl"], 0)

    def test_cannot_start_second_session_while_one_active(self):
        database.start_trading_session("First", [1], "DEMO")
        with self.assertRaises(ValueError):
            database.start_trading_session("Second", [2], "DEMO")

    def test_two_different_broker_accounts_can_each_run_a_concurrent_session(self):
        account_a = database.create_broker_account("Acct A")
        account_b = database.create_broker_account("Acct B")
        session_a = database.start_trading_session("First", [1], "DEMO", broker_account_id=account_a)
        session_b = database.start_trading_session("Second", [2], "DEMO", broker_account_id=account_b)
        self.assertEqual(database.get_trading_session(session_a)["status"], "active")
        self.assertEqual(database.get_trading_session(session_b)["status"], "active")
        self.assertEqual(len(database.list_active_trading_sessions()), 2)

    def test_cannot_start_second_session_on_the_same_broker_account(self):
        account_id = database.create_broker_account("Acct A")
        database.start_trading_session("First", [1], "DEMO", broker_account_id=account_id)
        with self.assertRaises(ValueError):
            database.start_trading_session("Second", [2], "DEMO", broker_account_id=account_id)

    def test_concurrent_starts_on_the_same_broker_account_only_ever_create_one_session(self):
        """The sequential test above only proves the check works when one
        call fully finishes before the next starts. start_trading_session's
        "no active session for this broker account" check and the INSERT
        that follows it are two separate DB connections - without a lock
        serializing them, near-simultaneous calls (an operator
        double-clicking Start, two open browser tabs) could each read "no
        active session" before either had inserted, starting two sessions
        that independently drive trade execution against one physical
        broker login. Proves the fix under real thread concurrency, not
        just sequential calls."""
        import threading
        account_id = database.create_broker_account("Acct A")
        results = []

        def attempt(i):
            try:
                database.start_trading_session(f"Session {i}", [1], "DEMO", broker_account_id=account_id)
                results.append("ok")
            except ValueError:
                results.append("rejected")

        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(results.count("ok"), 1)
        self.assertEqual(results.count("rejected"), 9)
        self.assertEqual(len(database.list_active_trading_sessions()), 1)

    def test_get_active_trading_session_for_broker_account(self):
        account_a = database.create_broker_account("Acct A")
        account_b = database.create_broker_account("Acct B")
        session_a = database.start_trading_session("First", [1], "DEMO", broker_account_id=account_a)
        self.assertEqual(database.get_active_trading_session_for_broker_account(account_a)["id"], session_a)
        self.assertIsNone(database.get_active_trading_session_for_broker_account(account_b))

    def test_get_active_trading_session_for_fund(self):
        fund_a = database.create_fund("Fund A")
        fund_b = database.create_fund("Fund B")
        session_a = database.start_trading_session("First", [1], "DEMO", fund_id=fund_a)
        self.assertEqual(database.get_active_trading_session_for_fund(fund_a)["id"], session_a)
        self.assertIsNone(database.get_active_trading_session_for_fund(fund_b))

    def test_get_active_trading_session_for_channel(self):
        account_a = database.create_broker_account("Acct A")
        account_b = database.create_broker_account("Acct B")
        session_a = database.start_trading_session("First", [1, 2], "DEMO", broker_account_id=account_a)
        session_b = database.start_trading_session("Second", [3], "DEMO", broker_account_id=account_b)
        self.assertEqual(database.get_active_trading_session_for_channel(1)["id"], session_a)
        self.assertEqual(database.get_active_trading_session_for_channel(3)["id"], session_b)
        self.assertIsNone(database.get_active_trading_session_for_channel(999))

    def test_start_session_requires_channels(self):
        with self.assertRaises(ValueError):
            database.start_trading_session("No channels", [], "DEMO")

    def test_broker_account_id_round_trips(self):
        fund_id = database.create_fund("F1")
        account_id = database.create_broker_account("Acc1")
        session_id = database.start_trading_session(
            "Test", [1], "DEMO", fund_id=fund_id, broker_account_id=account_id,
        )
        session = database.get_trading_session(session_id)
        self.assertEqual(session["fund_id"], fund_id)
        self.assertEqual(session["broker_account_id"], account_id)

    def test_broker_account_id_defaults_to_none(self):
        session_id = database.start_trading_session("Test", [1], "DEMO")
        self.assertIsNone(database.get_trading_session(session_id)["broker_account_id"])

    def test_record_trade_increments_count(self):
        session_id = database.start_trading_session("Test", [1], "DEMO")
        database.record_session_trade(session_id)
        database.record_session_trade(session_id)
        self.assertEqual(database.get_trading_session(session_id)["trades_count"], 2)

    def test_update_pnl_accumulates(self):
        session_id = database.start_trading_session("Test", [1], "DEMO")
        database.update_session_pnl(session_id, 10.5)
        database.update_session_pnl(session_id, -3.0)
        self.assertAlmostEqual(database.get_trading_session(session_id)["realized_pnl"], 7.5)

    def test_update_pnl_ignores_none(self):
        session_id = database.start_trading_session("Test", [1], "DEMO")
        database.update_session_pnl(session_id, None)
        self.assertEqual(database.get_trading_session(session_id)["realized_pnl"], 0)

    def test_stop_session_clears_active(self):
        session_id = database.start_trading_session("Test", [1], "DEMO")
        database.stop_trading_session(session_id, "stopped_manual", "user requested")
        self.assertIsNone(database.get_active_trading_session())
        stopped = database.get_trading_session(session_id)
        self.assertEqual(stopped["status"], "stopped_manual")
        self.assertEqual(stopped["stop_reason"], "user requested")
        self.assertIsNotNone(stopped["ended_at"])

    def test_stop_session_rejects_invalid_status(self):
        session_id = database.start_trading_session("Test", [1], "DEMO")
        with self.assertRaises(ValueError):
            database.stop_trading_session(session_id, "not_a_real_status")

    def test_stopping_already_stopped_session_is_a_noop(self):
        session_id = database.start_trading_session("Test", [1], "DEMO")
        database.stop_trading_session(session_id, "stopped_manual")
        database.stop_trading_session(session_id, "stopped_target")  # should not overwrite
        self.assertEqual(database.get_trading_session(session_id)["status"], "stopped_manual")

    def test_list_trading_sessions_ordered_newest_first(self):
        first = database.start_trading_session("First", [1], "DEMO")
        database.stop_trading_session(first, "stopped_manual")
        second = database.start_trading_session("Second", [1], "DEMO")
        sessions = database.list_trading_sessions()
        self.assertEqual(sessions[0]["id"], second)

    def test_get_signal_session_id(self):
        session_id = database.start_trading_session("Test", [1], "DEMO")
        signal = {"asset": "EUR/USD OTC", "direction": "BUY", "expiry": "1 Minute", "raw_message": "test"}
        trade_id = database.record_signal_received(signal, session_id=session_id)
        self.assertEqual(database.get_signal_session_id(trade_id), session_id)

    def test_get_signal_session_id_none_when_not_in_session(self):
        signal = {"asset": "EUR/USD OTC", "direction": "BUY", "expiry": "1 Minute", "raw_message": "test"}
        trade_id = database.record_signal_received(signal)
        self.assertIsNone(database.get_signal_session_id(trade_id))

    def test_martingale_disabled_defaults_false_and_can_be_set(self):
        session_id = database.start_trading_session("Test", [1], "DEMO")
        self.assertFalse(database.get_trading_session(session_id)["martingale_disabled"])
        database.set_session_martingale_disabled(session_id, True)
        self.assertTrue(database.get_trading_session(session_id)["martingale_disabled"])
        database.set_session_martingale_disabled(session_id, False)
        self.assertFalse(database.get_trading_session(session_id)["martingale_disabled"])


if __name__ == "__main__":
    unittest.main()
