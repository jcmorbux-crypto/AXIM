import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))

import database


class SearchChannelsTestCase(unittest.TestCase):
    """Smart Channel Search's backend (core/database.py's search_channels) -
    ranking, status classification, and the historical-sources merge for
    Strategy Lab's broader backtest-filter domain."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_exact_title_match_ranks_above_partial_match(self):
        database.upsert_channel(chat_id="1", username="tylervip", title="Tyler VIP Club", kind="channel")
        database.upsert_channel(chat_id="2", username="tylerother", title="Tyler VIP Club Reviews", kind="channel")
        results = database.search_channels("Tyler VIP Club")
        self.assertEqual(results[0]["title"], "Tyler VIP Club")

    def test_username_match_is_found(self):
        database.upsert_channel(chat_id="1", username="go_plusbot", title="Go+ | Trading Bot", kind="channel")
        results = database.search_channels("go_plusbot")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Go+ | Trading Bot")

    def test_partial_word_match(self):
        database.upsert_channel(chat_id="1", username=None, title="Pocket Option Quant Algorithm", kind="channel")
        results = database.search_channels("quant")
        self.assertEqual(len(results), 1)

    def test_tolerates_a_common_typo_via_subsequence_matching(self):
        database.upsert_channel(chat_id="1", username=None, title="Tyler VIP Club", kind="channel")
        results = database.search_channels("tylr vip")  # missing the 'e'
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Tyler VIP Club")

    def test_no_match_returns_empty(self):
        database.upsert_channel(chat_id="1", username=None, title="Tyler VIP Club", kind="channel")
        results = database.search_channels("completely unrelated query xyz")
        self.assertEqual(results, [])

    def test_empty_query_returns_a_default_list_not_everything_alphabetically(self):
        database.upsert_channel(chat_id="1", username=None, title="Zebra Channel", kind="channel")
        database.set_channel_enabled(1, True)
        database.upsert_channel(chat_id="2", username=None, title="Apple Channel", kind="channel")
        results = database.search_channels("")
        # Enabled ("Zebra") should rank ahead of disabled ("Apple") in the
        # default listing, even though "Apple" is alphabetically first.
        self.assertEqual(results[0]["title"], "Zebra Channel")

    def test_status_available_for_a_disabled_channel(self):
        database.upsert_channel(chat_id="1", username=None, title="Not Yet Added", kind="channel")
        results = database.search_channels("Not Yet Added")
        self.assertEqual(results[0]["status"], "available")

    def test_status_needs_setup_for_an_enabled_channel_with_no_recommendation(self):
        database.upsert_channel(chat_id="1", username=None, title="Added But Unanalyzed", kind="channel")
        database.set_channel_enabled(1, True)
        results = database.search_channels("Added But Unanalyzed")
        self.assertEqual(results[0]["status"], "needs_setup")

    def test_status_connected_for_an_enabled_channel_with_a_real_recommendation(self):
        database.upsert_channel(chat_id="1", username=None, title="Fully Onboarded", kind="channel")
        database.set_channel_enabled(1, True)
        database.save_capital_recommendation(
            source_label="Fully Onboarded", backtest_run_id=1, best_strategy_id=1,
            best_strategy_key="capital_preservation", best_strategy_name="Capital Preservation",
            roi_percent=20.0, win_rate=0.55, max_drawdown_percent=10.0, max_drawdown_amount=100.0,
            minimum_allocation=150.0, conservative_allocation=250.0, suggested_allocation=400.0,
            trades_backtested=100,
        )
        results = database.search_channels("Fully Onboarded")
        self.assertEqual(results[0]["status"], "connected")

    def test_limit_caps_the_result_count(self):
        for i in range(30):
            database.upsert_channel(chat_id=str(i), username=None, title=f"Test Channel {i}", kind="channel")
        results = database.search_channels("Test Channel", limit=10)
        self.assertEqual(len(results), 10)

    def test_historical_sources_excluded_by_default(self):
        conn = database.get_connection()
        conn.execute(
            "INSERT INTO imported_signals (source_label, asset, direction, expiry, received_at, result, import_batch) "
            "VALUES (?, 'EUR/USD', 'BUY', '1 Minute', ?, 'win', 'test')",
            ("Research-Only Provider", "2026-01-01T00:00:00"),
        )
        conn.commit()
        conn.close()
        results = database.search_channels("Research-Only Provider")
        self.assertEqual(results, [])

    def test_historical_sources_included_when_requested(self):
        conn = database.get_connection()
        conn.execute(
            "INSERT INTO imported_signals (source_label, asset, direction, expiry, received_at, result, import_batch) "
            "VALUES (?, 'EUR/USD', 'BUY', '1 Minute', ?, 'win', 'test')",
            ("Research-Only Provider", "2026-01-01T00:00:00"),
        )
        conn.commit()
        conn.close()
        results = database.search_channels("Research-Only Provider", include_historical_sources=True)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Research-Only Provider")
        self.assertIsNone(results[0]["id"])
        self.assertEqual(results[0]["status"], "connected")

    def test_a_historical_source_already_covered_by_a_real_channel_is_not_duplicated(self):
        database.upsert_channel(chat_id="1", username=None, title="Shared Name Provider", kind="channel")
        conn = database.get_connection()
        conn.execute(
            "INSERT INTO imported_signals (source_label, asset, direction, expiry, received_at, result, import_batch) "
            "VALUES (?, 'EUR/USD', 'BUY', '1 Minute', ?, 'win', 'test')",
            ("Shared Name Provider", "2026-01-01T00:00:00"),
        )
        conn.commit()
        conn.close()
        results = database.search_channels("Shared Name Provider", include_historical_sources=True)
        self.assertEqual(len(results), 1)  # not duplicated


if __name__ == "__main__":
    unittest.main()
