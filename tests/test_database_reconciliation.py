"""2026-08-01: get_unresolved_submitted_trades() is a status-string-
independent "not yet authoritatively resolved" scan (opened_at IS NOT
NULL AND result NOT IN ('win','loss','draw')) used by core/recovery.py's
startup reconciliation. Confirmed live: 8 trades whose broker-history
reconciliation already failed as no_match at a restart (weeks-old
history, unrecoverable) kept matching this scan on every subsequent
restart forever, since 'error:reconciliation_required' isn't a genuine
terminal result either. mark_trade_reconciliation_abandoned() is the
explicit, operator-only escape hatch - these tests prove the query
excludes its result value, and that the function itself does what it
claims (updates the trade AND logs an audited admin action)."""
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))

import database
from trade_lifecycle import TradeStatus


class ReconciliationQueryTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def _insert_trade(self, opened_at="2026-07-01T00:00:00", result=None):
        trade_id = database.record_signal_received(
            {"asset": "EUR/USD OTC", "direction": "BUY", "expiry": "1 Minute", "raw_message": "test"},
        )
        fields = {"opened_at": opened_at}
        if result is not None:
            fields["result"] = result
        database.update_trade_status(trade_id, TradeStatus.ERROR, **fields)
        return trade_id

    def test_reconciliation_required_still_counts_as_unresolved(self):
        # Unchanged pre-existing behavior - recovery.py must keep retrying
        # a trade it hasn't given up on yet.
        trade_id = self._insert_trade(result="error:reconciliation_required")
        unresolved_ids = [row["id"] for row in database.get_unresolved_submitted_trades()]
        self.assertIn(trade_id, unresolved_ids)

    def test_reconciliation_abandoned_is_excluded(self):
        trade_id = self._insert_trade(result="error:reconciliation_abandoned")
        unresolved_ids = [row["id"] for row in database.get_unresolved_submitted_trades()]
        self.assertNotIn(trade_id, unresolved_ids)

    def test_reconciliation_abandoned_excluded_in_broker_scoped_query_too(self):
        trade_id = database.record_signal_received(
            {"asset": "EUR/USD OTC", "direction": "BUY", "expiry": "1 Minute", "raw_message": "test"},
            broker_account_id=7,
        )
        database.update_trade_status(
            trade_id, TradeStatus.ERROR,
            opened_at="2026-07-01T00:00:00", result="error:reconciliation_abandoned",
        )
        unresolved_ids = [
            row["id"] for row in database.get_unresolved_submitted_trades(broker_account_id=7)
        ]
        self.assertNotIn(trade_id, unresolved_ids)


class MarkTradeReconciliationAbandonedTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_marks_trade_and_removes_it_from_unresolved_scan(self):
        trade_id = database.record_signal_received(
            {"asset": "EUR/USD OTC", "direction": "BUY", "expiry": "1 Minute", "raw_message": "test"},
        )
        database.update_trade_status(
            trade_id, TradeStatus.ERROR,
            opened_at="2026-07-01T00:00:00", result="error:reconciliation_required",
        )
        self.assertIn(trade_id, [r["id"] for r in database.get_unresolved_submitted_trades()])

        database.mark_trade_reconciliation_abandoned(trade_id, "too old to reconcile - test")

        self.assertNotIn(trade_id, [r["id"] for r in database.get_unresolved_submitted_trades()])
        conn = database.get_connection()
        row = conn.execute("SELECT result FROM signals WHERE id = ?", (trade_id,)).fetchone()
        conn.close()
        self.assertEqual(row["result"], "error:reconciliation_abandoned")

    def test_logs_an_admin_action_with_the_reason(self):
        trade_id = database.record_signal_received(
            {"asset": "EUR/USD OTC", "direction": "BUY", "expiry": "1 Minute", "raw_message": "test"},
        )
        database.update_trade_status(
            trade_id, TradeStatus.ERROR,
            opened_at="2026-07-01T00:00:00", result="error:reconciliation_required",
        )
        database.mark_trade_reconciliation_abandoned(trade_id, "too old to reconcile - test")

        actions = database.list_admin_actions()
        matching = [a for a in actions if a["action"] == "reconciliation_abandoned"]
        self.assertEqual(len(matching), 1)
        self.assertIn(f"trade_id={trade_id}", matching[0]["detail"])
        self.assertIn("too old to reconcile - test", matching[0]["detail"])


if __name__ == "__main__":
    unittest.main()
