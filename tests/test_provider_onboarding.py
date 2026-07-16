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


if __name__ == "__main__":
    unittest.main()
