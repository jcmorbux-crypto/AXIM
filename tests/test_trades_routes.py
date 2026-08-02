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
import trades as routes
from trade_lifecycle import TradeStatus

_FAKE_USER = {"id": 1, "email": "owner@axim.local", "role": "owner"}


class ExportTradesTests(unittest.TestCase):
    """2026-08-01: Trade History had no export at all, unlike Strategy
    Lab's backtest runs (api/backtest_routes.py's export_run) - reuses
    that exact csv/io.StringIO/Response convention rather than inventing
    a second one, over the same get_recent_signals data list_trades
    already uses."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def _make_trade(self, asset="EUR/USD OTC", result="win"):
        trade_id = database.record_signal_received(
            {"asset": asset, "direction": "BUY", "expiry": "1 Minute", "raw_message": "test"},
        )
        database.update_trade_status(
            trade_id, TradeStatus.RESULT_WIN, opened_at="2026-08-01T00:00:00",
            closed_at="2026-08-01T00:01:00", result=result, profit_loss=9.0, payout=90,
        )
        return trade_id

    def test_csv_export_contains_a_header_and_one_row_per_trade(self):
        self._make_trade("EUR/USD OTC")
        self._make_trade("GBP/USD OTC")
        response = routes.export_trades(format="csv", user=_FAKE_USER)
        self.assertEqual(response.media_type, "text/csv")
        body = response.body.decode("utf-8")
        lines = [l for l in body.strip().split("\r\n") if l]
        self.assertEqual(len(lines), 3)  # header + 2 trades
        self.assertIn("id,received_at,opened_at,closed_at,channel,asset,direction", lines[0])
        self.assertIn("EUR/USD OTC", body)
        self.assertIn("GBP/USD OTC", body)

    def test_csv_export_sets_download_filename(self):
        response = routes.export_trades(format="csv", user=_FAKE_USER)
        self.assertIn("attachment; filename=axim_trades.csv", response.headers["content-disposition"])

    def test_json_export_round_trips_the_same_fields_as_list_trades(self):
        self._make_trade("EUR/USD OTC")
        response = routes.export_trades(format="json", user=_FAKE_USER)
        self.assertEqual(response.media_type, "application/json")
        import json
        exported = json.loads(response.body)
        listed = routes.list_trades(limit=50, user=_FAKE_USER)
        self.assertEqual(exported, listed)

    def test_unknown_format_is_rejected(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            routes.export_trades(format="xml", user=_FAKE_USER)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_export_with_no_trades_yet_still_returns_just_the_header(self):
        response = routes.export_trades(format="csv", user=_FAKE_USER)
        body = response.body.decode("utf-8")
        lines = [l for l in body.strip().split("\r\n") if l]
        self.assertEqual(len(lines), 1)

    def test_export_respects_the_same_filters_as_list_trades(self):
        # "export what I'm currently looking at" - see api/trades.py's
        # export_trades docstring.
        self._make_trade("EUR/USD OTC")
        self._make_trade("GBP/USD OTC")
        response = routes.export_trades(format="csv", search="EUR", user=_FAKE_USER)
        body = response.body.decode("utf-8")
        self.assertIn("EUR/USD OTC", body)
        self.assertNotIn("GBP/USD OTC", body)


class ListTradesUnfilteredCompatibilityTests(unittest.TestCase):
    """list_trades gained optional filter params 2026-08-01 - a call with
    none of them set (every existing caller: web/trades.html's plain
    refresh, this test file's own JSON-export-round-trip test above) must
    return exactly what get_recent_signals(limit) always did, since
    database.filter_signals is a new function, not a get_recent_signals
    rewrite - this proves the two stay equivalent in the unfiltered case,
    not just asserts it in a docstring."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_unfiltered_call_matches_get_recent_signals_exactly(self):
        for asset in ("EUR/USD OTC", "GBP/USD OTC", "AUD/CAD OTC"):
            trade_id = database.record_signal_received(
                {"asset": asset, "direction": "BUY", "expiry": "1 Minute", "raw_message": "test"},
            )
            database.update_trade_status(trade_id, TradeStatus.RESULT_WIN, result="win")
        self.assertEqual(routes.list_trades(limit=50, user=_FAKE_USER), database.get_recent_signals(50))


class FilterSignalsTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def _make_trade(self, asset="EUR/USD OTC", channel="Test Channel", result="win", received_at=None):
        trade_id = database.record_signal_received(
            {"asset": asset, "direction": "BUY", "expiry": "1 Minute", "raw_message": "test"},
        )
        fields = {"result": result} if result is not None else {}
        if received_at:
            conn = database.get_connection()
            conn.execute("UPDATE signals SET received_at = ?, channel = ? WHERE id = ?",
                         (received_at, channel, trade_id))
            conn.commit()
            conn.close()
        elif channel != "Test Channel":
            conn = database.get_connection()
            conn.execute("UPDATE signals SET channel = ? WHERE id = ?", (channel, trade_id))
            conn.commit()
            conn.close()
        if fields:
            database.update_trade_status(trade_id, TradeStatus.RESULT_WIN, **fields)
        return trade_id

    def test_search_matches_asset_substring(self):
        self._make_trade("EUR/USD OTC")
        self._make_trade("GBP/JPY OTC")
        rows = database.filter_signals(search="EUR")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["asset"], "EUR/USD OTC")

    def test_search_matches_channel_substring(self):
        self._make_trade("EUR/USD OTC", channel="Go+ Signals")
        self._make_trade("GBP/JPY OTC", channel="Martin Trader")
        rows = database.filter_signals(search="Martin")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["channel"], "Martin Trader")

    def test_result_filters_to_exact_value(self):
        self._make_trade("EUR/USD OTC", result="win")
        self._make_trade("GBP/JPY OTC", result="loss")
        rows = database.filter_signals(result="win")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["result"], "win")

    def test_result_open_means_not_yet_resolved(self):
        self._make_trade("EUR/USD OTC", result=None)
        self._make_trade("GBP/JPY OTC", result="win")
        rows = database.filter_signals(result="open")
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["result"])

    def test_since_and_until_bound_the_date_range(self):
        self._make_trade("EUR/USD OTC", received_at="2026-07-01T00:00:00")
        self._make_trade("GBP/JPY OTC", received_at="2026-08-01T00:00:00")
        self._make_trade("AUD/CAD OTC", received_at="2026-09-01T00:00:00")
        rows = database.filter_signals(since="2026-07-15T00:00:00", until="2026-08-15T23:59:59")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["asset"], "GBP/JPY OTC")

    def test_no_filters_returns_everything_newest_first(self):
        self._make_trade("EUR/USD OTC")
        self._make_trade("GBP/JPY OTC")
        rows = database.filter_signals()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["asset"], "GBP/JPY OTC")  # newest (higher id) first

    def test_filters_combine_with_and(self):
        self._make_trade("EUR/USD OTC", result="win")
        self._make_trade("EUR/USD OTC", result="loss")
        self._make_trade("GBP/JPY OTC", result="win")
        rows = database.filter_signals(search="EUR", result="win")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["asset"], "EUR/USD OTC")
        self.assertEqual(rows[0]["result"], "win")


if __name__ == "__main__":
    unittest.main()
