"""2026-08-01: item 6 of the trading-engine priority list ("Notifications")
- the in-app notifications table (core/database.py's notifications, see
core/database.py's own "deliberately in-app only, no email/SMS/push -
those need an external provider and credentials, out of scope here"
comment) already existed but was only ever written by rule_engine.py's
manual "Notify the Owner" rule action. Nothing surfaced the system's own
real failure states (a trade series blocked or erroring, a series stuck
needing manual reconciliation - exactly what series 105 sat in silently
for weeks before the 2026-07-29 fix) to the operator automatically.
database.notify_owner() reuses the same create_notification() pipeline
these tests confirm now fires from advance_trade_series (blocked/error
only - not every status, which would be noise) and
mark_series_reconciliation_required."""
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))

import database


def _make_series():
    entry_1_utc = datetime.now(timezone.utc) + timedelta(seconds=3)
    return database.create_trade_series(
        channel_id=163, asset="AUD/JPY OTC", direction="SELL", expiry="5 Minute",
        stake=10.0, entry_times=["09:00"], max_entries=4,
        entry_times_utc=[entry_1_utc.isoformat()],
    )


class AdvanceTradeSeriesNotificationTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self.owner_id = database.create_user("owner@axim.local", "password123", role="owner")

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_blocked_status_notifies_the_owner(self):
        series_id = _make_series()
        database.advance_trade_series(series_id, 1, "blocked", result="blocked:consecutive_loss_lock")
        rows = database.list_notifications(self.owner_id)
        self.assertEqual(len(rows), 1)
        self.assertIn("AUD/JPY OTC", rows[0]["message"])
        self.assertIn(f"series #{series_id}", rows[0]["message"])
        self.assertIn("blocked:consecutive_loss_lock", rows[0]["message"])
        self.assertEqual(rows[0]["source"], "trade_series.blocked")

    def test_error_status_notifies_the_owner(self):
        series_id = _make_series()
        database.advance_trade_series(series_id, 1, "error", result="error")
        rows = database.list_notifications(self.owner_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "trade_series.error")

    def test_won_status_does_not_notify(self):
        # A normal winning outcome is not a failure state - would be pure
        # noise, and the operator already sees it on Trade History/Analytics.
        series_id = _make_series()
        database.advance_trade_series(series_id, 1, "won", result="win", net_profit_loss=8.5)
        self.assertEqual(database.list_notifications(self.owner_id), [])

    def test_active_status_does_not_notify(self):
        # Every entry firing goes through 'active' first - notifying here
        # would fire on every single trade, not just real failures.
        series_id = _make_series()
        database.advance_trade_series(series_id, 1, "active")
        self.assertEqual(database.list_notifications(self.owner_id), [])

    def test_no_owner_account_does_not_raise(self):
        database.DB_FILE = Path(self._tmp_dir.name) / "no_owner.db"
        database.initialize_database()
        series_id = _make_series()
        database.advance_trade_series(series_id, 1, "blocked", result="blocked:test")  # must not raise


class ReconciliationRequiredNotificationTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self.owner_id = database.create_user("owner@axim.local", "password123", role="owner")

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_marking_reconciliation_required_notifies_the_owner(self):
        series_id = _make_series()
        database.mark_series_reconciliation_required(series_id, "broker_history_no_match")
        rows = database.list_notifications(self.owner_id)
        self.assertEqual(len(rows), 1)
        self.assertIn("AUD/JPY OTC", rows[0]["message"])
        self.assertIn("broker_history_no_match", rows[0]["message"])
        self.assertEqual(rows[0]["source"], "trade_series.reconciliation_required")

    def test_no_owner_account_does_not_raise(self):
        database.DB_FILE = Path(self._tmp_dir.name) / "no_owner.db"
        database.initialize_database()
        series_id = _make_series()
        database.mark_series_reconciliation_required(series_id, "test reason")  # must not raise


if __name__ == "__main__":
    unittest.main()
