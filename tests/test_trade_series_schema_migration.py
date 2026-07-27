import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))

import database


class TradeSeriesStaleSchemaMigrationTests(unittest.TestCase):
    """Verified production defect (Martin Trader forensic investigation,
    2026-07-27): trade_series's CREATE TABLE IF NOT EXISTS was edited from
    `expiry_seconds INTEGER` to `expiry TEXT` mid-development, but
    "IF NOT EXISTS" is a no-op against a table a PRIOR
    initialize_database() call already created with the old column name -
    production silently stayed on the stale schema, and every real Martin
    Trader signal's create_trade_series call failed with
    sqlite3.OperationalError: table trade_series has no column named
    expiry, invisible in any log because it happened inside an
    uninstrumented Telethon event handler.

    This is exactly the class of bug ordinary tests can't catch: every
    other test in this suite builds a brand-new temp DB via
    initialize_database() itself, so the table is always created fresh
    with whatever CREATE TABLE currently says - there was never a
    PRE-EXISTING stale table for those tests to collide with. This test
    deliberately recreates that exact production condition: a table that
    already exists with the OLD schema, migrated by calling
    initialize_database() a SECOND time (the real restart-time codepath),
    exactly as happens when a redeploy runs against the real, persistent
    axim.db file."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def _table_columns(self, table):
        conn = sqlite3.connect(database.DB_FILE)
        try:
            return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        finally:
            conn.close()

    def test_a_stale_expiry_seconds_table_gets_the_new_expiry_column_added(self):
        # Simulate a table created before the expiry_seconds -> expiry
        # rename - the exact real production condition.
        conn = sqlite3.connect(database.DB_FILE)
        conn.execute("""
            CREATE TABLE trade_series (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER,
                fund_id INTEGER,
                broker_account_id INTEGER,
                session_id INTEGER,
                asset TEXT,
                direction TEXT,
                expiry_seconds INTEGER,
                stake REAL,
                entry_times_json TEXT NOT NULL,
                max_entries INTEGER NOT NULL DEFAULT 4,
                current_entry_number INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                result TEXT,
                net_profit_loss REAL,
                source_message_id INTEGER,
                raw_message TEXT,
                created_at TEXT NOT NULL,
                resolved_at TEXT
            );
        """)
        conn.commit()
        conn.close()

        columns_before = self._table_columns("trade_series")
        self.assertNotIn("expiry", columns_before)
        self.assertIn("expiry_seconds", columns_before)

        # The real restart-time codepath - initialize_database() runs
        # unconditionally at every process startup.
        database.initialize_database()

        columns_after = self._table_columns("trade_series")
        self.assertIn("expiry", columns_after, "migration must add the missing column to a pre-existing stale table")
        self.assertIn("expiry_seconds", columns_after, "the old column must be left in place, never dropped")

        # The real regression: create_trade_series must actually succeed
        # against the migrated table, not just have the right column exist.
        series_id = database.create_trade_series(
            channel_id=163, asset="CAD/JPY OTC", direction="BUY", expiry="5 Minute",
            stake=10.0, entry_times=["18:25", "18:30", "18:35", "18:40"], max_entries=4,
        )
        series = database.get_trade_series(series_id)
        self.assertEqual(series["expiry"], "5 Minute")

    def test_a_fresh_install_already_has_the_column_migration_is_a_no_op(self):
        database.initialize_database()
        columns = self._table_columns("trade_series")
        self.assertIn("expiry", columns)
        # Calling it again (as every real process restart does) must not raise.
        database.initialize_database()


if __name__ == "__main__":
    unittest.main()
