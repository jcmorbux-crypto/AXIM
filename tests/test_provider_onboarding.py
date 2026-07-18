"""Tests core/provider_onboarding.py - the real orchestration behind
the automatic "add a new provider" workflow. telegram_channels.
fetch_channel_raw_history requires a live authenticated Telegram
session (same documented limitation class as execution/pocket_dom.py's
DOM functions and core/telegram_bot_trigger.py's send/receive) - not
live-testable here, so it's mocked with realistic data shaped exactly
like core/provider_language_learner.py's own test fixtures, and every
DB-touching step downstream of that mock runs for real against a
temp database."""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))

import database
import provider_onboarding


def _run(coro):
    return asyncio.run(coro)


def _daniel_fx_trade_like_messages():
    pairs = [
        (1, "GBP/CAD HIGH ⬆️ 15 MIN", "2026-01-01T00:00:00"), (2, "✅✅✅", "2026-01-01T00:15:00"),
        (3, "GBP/CHF LOWER ⬇️ 15 MIN", "2026-01-01T01:00:00"), (4, "❎❎❎", "2026-01-01T01:15:00"),
        (5, "USD/JPY HIGH ⬆️ 10 MIN", "2026-01-02T00:00:00"), (6, "✅✅✅", "2026-01-02T00:10:00"),
    ]
    return [{"message_id": mid, "text": text, "date_utc": date} for mid, text, date in pairs]


class ProviderOnboardingTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_full_pipeline_with_a_recognizable_provider(self):
        fake_fetch = AsyncMock(return_value=(_daniel_fx_trade_like_messages(), "Test Provider"))
        with patch("telegram_channels.fetch_channel_raw_history", fake_fetch):
            result = _run(provider_onboarding.analyze_and_onboard_provider(chat_id=123))

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["title"], "Test Provider")
        self.assertEqual(result["pattern"], "compact_dirfirst")
        self.assertEqual(result["imported_trades"], 3)
        self.assertIsNotNone(result["backtest_run_id"])

        imported = database.list_imported_signals(import_batch="auto_onboard_123", graded_only=True, limit=100)
        self.assertEqual(len(imported), 3)
        self.assertIn("provider_language_learner", imported[0]["notes"])
        self.assertIn("not independently verified against a broker", imported[0]["notes"])

    def test_no_history_returns_a_status_not_an_error(self):
        fake_fetch = AsyncMock(return_value=([], "Empty Channel"))
        with patch("telegram_channels.fetch_channel_raw_history", fake_fetch):
            result = _run(provider_onboarding.analyze_and_onboard_provider(chat_id=456))
        self.assertEqual(result["status"], "no_history")

    def test_unrecognized_format_returns_a_status_not_an_error(self):
        messages = [
            {"message_id": 1, "text": "Welcome to our channel!", "date_utc": "2026-01-01T00:00:00"},
            {"message_id": 2, "text": "Great session today everyone", "date_utc": "2026-01-01T01:00:00"},
        ]
        fake_fetch = AsyncMock(return_value=(messages, "Chatty Channel"))
        with patch("telegram_channels.fetch_channel_raw_history", fake_fetch):
            result = _run(provider_onboarding.analyze_and_onboard_provider(chat_id=789))
        self.assertEqual(result["status"], "pattern_not_detected")
        self.assertIn("hand-built adapter", result["note"])

    def test_re_running_is_idempotent_not_accumulating_duplicate_signals(self):
        fake_fetch = AsyncMock(return_value=(_daniel_fx_trade_like_messages(), "Test Provider"))
        with patch("telegram_channels.fetch_channel_raw_history", fake_fetch):
            _run(provider_onboarding.analyze_and_onboard_provider(chat_id=123))
            _run(provider_onboarding.analyze_and_onboard_provider(chat_id=123))
        imported = database.list_imported_signals(import_batch="auto_onboard_123", graded_only=True, limit=100)
        self.assertEqual(len(imported), 3)

    def test_excluding_a_signal_message_id_drops_it_from_the_committed_import(self):
        fake_fetch = AsyncMock(return_value=(_daniel_fx_trade_like_messages(), "Test Provider"))
        with patch("telegram_channels.fetch_channel_raw_history", fake_fetch):
            # message_id 1 is the first signal (GBP/CAD) - excluding it should
            # leave only the other 2 decided trades committed.
            result = _run(provider_onboarding.analyze_and_onboard_provider(chat_id=123, excluded_message_ids={1}))
        self.assertEqual(result["imported_trades"], 2)
        imported = database.list_imported_signals(import_batch="auto_onboard_123", graded_only=True, limit=100)
        self.assertEqual(len(imported), 2)
        self.assertNotIn("GBP/CAD", [s["asset"] for s in imported])


class ProviderProfileWritingTestCase(unittest.TestCase):
    """Universal Signal Intelligence Engine directive: historical
    analysis must write a real, database-driven provider_profiles row,
    not just return an in-memory result."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self.channel_id = database.upsert_channel(chat_id=123, username="testprov", title="Test Provider", kind="channel")
        self.channel_id = database.list_channels()[0]["id"]

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_analyze_and_onboard_creates_a_real_profile_row(self):
        fake_fetch = AsyncMock(return_value=(_daniel_fx_trade_like_messages(), "Test Provider"))
        with patch("telegram_channels.fetch_channel_raw_history", fake_fetch):
            _run(provider_onboarding.analyze_and_onboard_provider(chat_id=123))
        profile = database.get_provider_profile_by_channel_id(self.channel_id)
        self.assertIsNotNone(profile)
        self.assertEqual(profile["pattern_name"], "compact_dirfirst")
        self.assertIsNotNone(profile["coverage"])
        self.assertEqual(profile["trading_mode"], "observation")  # never auto-graduated
        self.assertIsNotNone(profile["last_analyzed_at"])

    def test_preview_also_writes_the_profile_not_just_the_commit_step(self):
        fake_fetch = AsyncMock(return_value=(_daniel_fx_trade_like_messages(), "Test Provider"))
        with patch("telegram_channels.fetch_channel_raw_history", fake_fetch):
            _run(provider_onboarding.preview_provider(chat_id=123))
        profile = database.get_provider_profile_by_channel_id(self.channel_id)
        self.assertIsNotNone(profile)
        self.assertEqual(profile["pattern_name"], "compact_dirfirst")

    def test_single_message_pattern_gets_a_single_step_expected_sequence(self):
        import json as json_module
        fake_fetch = AsyncMock(return_value=(_daniel_fx_trade_like_messages(), "Test Provider"))
        with patch("telegram_channels.fetch_channel_raw_history", fake_fetch):
            _run(provider_onboarding.preview_provider(chat_id=123))
        profile = database.get_provider_profile_by_channel_id(self.channel_id)
        self.assertEqual(json_module.loads(profile["expected_sequence_json"]), ["entry"])

    def test_reanalysis_updates_the_same_profile_row_not_a_new_one(self):
        fake_fetch = AsyncMock(return_value=(_daniel_fx_trade_like_messages(), "Test Provider"))
        with patch("telegram_channels.fetch_channel_raw_history", fake_fetch):
            _run(provider_onboarding.preview_provider(chat_id=123))
            first = database.get_provider_profile_by_channel_id(self.channel_id)
            _run(provider_onboarding.preview_provider(chat_id=123))
            second = database.get_provider_profile_by_channel_id(self.channel_id)
        self.assertEqual(first["id"], second["id"])

    def test_no_ui_channels_row_yet_does_not_crash(self):
        # A raw chat_id that's never been synced into ui_channels - the
        # profile write is a documented no-op, not a crash.
        fake_fetch = AsyncMock(return_value=(_daniel_fx_trade_like_messages(), "Never Synced"))
        with patch("telegram_channels.fetch_channel_raw_history", fake_fetch):
            result = _run(provider_onboarding.preview_provider(chat_id=99999))
        self.assertEqual(result["status"], "complete")

    def test_profile_change_is_auditable(self):
        fake_fetch = AsyncMock(return_value=(_daniel_fx_trade_like_messages(), "Test Provider"))
        with patch("telegram_channels.fetch_channel_raw_history", fake_fetch):
            _run(provider_onboarding.preview_provider(chat_id=123))
        profile = database.get_provider_profile_by_channel_id(self.channel_id)
        history = database.list_provider_profile_history(profile["id"])
        self.assertTrue(any(h["reason"] == "historical analysis" for h in history))


class PreviewProviderTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_preview_never_writes_to_the_database(self):
        fake_fetch = AsyncMock(return_value=(_daniel_fx_trade_like_messages(), "Test Provider"))
        with patch("telegram_channels.fetch_channel_raw_history", fake_fetch):
            result = _run(provider_onboarding.preview_provider(chat_id=123))
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["sample_count"], 3)
        self.assertEqual(result["total_decided_trades"], 3)
        # No imported_signals, no backtest_run - this is a preview, not a commit.
        imported = database.list_imported_signals(import_batch="auto_onboard_123", graded_only=True, limit=100)
        self.assertEqual(len(imported), 0)

    def test_preview_sample_includes_original_text_and_parsed_fields(self):
        fake_fetch = AsyncMock(return_value=(_daniel_fx_trade_like_messages(), "Test Provider"))
        with patch("telegram_channels.fetch_channel_raw_history", fake_fetch):
            result = _run(provider_onboarding.preview_provider(chat_id=123))
        first = result["sample"][0]
        self.assertEqual(first["raw_signal_text"], "GBP/CAD HIGH ⬆️ 15 MIN")
        self.assertEqual(first["raw_result_text"], "✅✅✅")
        self.assertEqual(first["parsed_asset"], "GBP/CAD")
        self.assertEqual(first["parsed_direction"], "BUY")
        self.assertEqual(first["matched_result"], "win")
        self.assertEqual(first["warnings"], [])

    def test_preview_passes_through_the_days_window(self):
        fake_fetch = AsyncMock(return_value=(_daniel_fx_trade_like_messages(), "Test Provider"))
        with patch("telegram_channels.fetch_channel_raw_history", fake_fetch):
            _run(provider_onboarding.preview_provider(chat_id=123, days=7))
        fake_fetch.assert_awaited_once()
        self.assertEqual(fake_fetch.call_args.kwargs.get("days"), 7)

    def test_preview_no_history(self):
        fake_fetch = AsyncMock(return_value=([], "Empty Channel"))
        with patch("telegram_channels.fetch_channel_raw_history", fake_fetch):
            result = _run(provider_onboarding.preview_provider(chat_id=456))
        self.assertEqual(result["status"], "no_history")

    def test_preview_unrecognized_format(self):
        messages = [
            {"message_id": 1, "text": "Welcome to our channel!", "date_utc": "2026-01-01T00:00:00"},
        ]
        fake_fetch = AsyncMock(return_value=(messages, "Chatty Channel"))
        with patch("telegram_channels.fetch_channel_raw_history", fake_fetch):
            result = _run(provider_onboarding.preview_provider(chat_id=789))
        self.assertEqual(result["status"], "pattern_not_detected")


if __name__ == "__main__":
    unittest.main()
