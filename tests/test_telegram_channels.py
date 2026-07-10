import sys
import unittest
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))
sys.path.insert(0, str(PROJECT_ROOT / "parsers"))

import telegram_channels


class MessageToSignalRowTests(unittest.TestCase):
    """_message_to_signal_row() - the pure conversion core of
    fetch_channel_history(), tested without any TelegramClient/network
    dependency. Uses real parsers.signal_parser.parse_signal() (not
    mocked) so this proves the SAME parser the live listener uses
    recognizes historical message text the same way."""

    def test_real_signal_text_produces_a_row(self):
        row = telegram_channels._message_to_signal_row(
            "EUR/USD OTC BUY 1 Minute", datetime(2026, 1, 1, 10, 0, 0), 12345, "Test Channel",
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["asset"], "EUR/USD OTC")
        self.assertEqual(row["direction"], "BUY")
        self.assertEqual(row["source_label"], "Test Channel")
        self.assertIsNone(row["result"])
        self.assertIsNone(row["payout_percent"])
        self.assertIn("12345", row["notes"])

    def test_non_signal_text_returns_none(self):
        row = telegram_channels._message_to_signal_row(
            "Good morning everyone, have a great trading day!",
            datetime(2026, 1, 1, 10, 0, 0), 12346, "Test Channel",
        )
        self.assertIsNone(row)

    def test_empty_text_returns_none(self):
        row = telegram_channels._message_to_signal_row(
            "", datetime(2026, 1, 1, 10, 0, 0), 12347, "Test Channel",
        )
        self.assertIsNone(row)

    def test_missing_date_falls_back_to_now_not_a_crash(self):
        row = telegram_channels._message_to_signal_row(
            "GBP/USD OTC SELL 5 Minutes", None, 12348, "Test Channel",
        )
        self.assertIsNotNone(row)
        self.assertTrue(row["received_at"])  # some ISO timestamp was produced, not blank/error

    def test_expiry_carried_through_when_present(self):
        row = telegram_channels._message_to_signal_row(
            "USD/JPY OTC BUY 5 Minutes", datetime(2026, 1, 1, 10, 0, 0), 12349, "Test Channel",
        )
        self.assertIsNotNone(row)
        self.assertIsNotNone(row["expiry"])


if __name__ == "__main__":
    unittest.main()
