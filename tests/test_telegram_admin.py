import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock
import unittest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))
sys.path.insert(0, str(PROJECT_ROOT / "api"))

import telegram_admin


def _run(coro):
    return asyncio.run(coro)


class ClearPendingLoginsTests(unittest.TestCase):
    """An operator who calls send-code and never follows through with
    verify-code left that pending login's TelegramClient connected
    forever - no timeout, no reaper, nothing else ever cleaned it up.
    _clear_pending_logins is send_code's own fix: disconnect and drop
    whatever's left over before starting a fresh attempt, bounding this
    to at most one leaked connection instead of unbounded growth."""

    def tearDown(self):
        telegram_admin._pending_logins.clear()

    def test_disconnects_and_clears_every_pending_entry(self):
        client_a = AsyncMock()
        client_b = AsyncMock()
        telegram_admin._pending_logins["a"] = {"client": client_a, "phone": "+1", "phone_code_hash": "h1"}
        telegram_admin._pending_logins["b"] = {"client": client_b, "phone": "+2", "phone_code_hash": "h2"}

        _run(telegram_admin._clear_pending_logins())

        client_a.disconnect.assert_awaited_once()
        client_b.disconnect.assert_awaited_once()
        self.assertEqual(telegram_admin._pending_logins, {})

    def test_noop_when_nothing_pending(self):
        _run(telegram_admin._clear_pending_logins())  # must not raise
        self.assertEqual(telegram_admin._pending_logins, {})

    def test_one_client_failing_to_disconnect_does_not_block_the_others(self):
        client_a = AsyncMock()
        client_a.disconnect.side_effect = Exception("connection already dead")
        client_b = AsyncMock()
        telegram_admin._pending_logins["a"] = {"client": client_a, "phone": "+1", "phone_code_hash": "h1"}
        telegram_admin._pending_logins["b"] = {"client": client_b, "phone": "+2", "phone_code_hash": "h2"}

        _run(telegram_admin._clear_pending_logins())  # must not raise

        client_b.disconnect.assert_awaited_once()
        self.assertEqual(telegram_admin._pending_logins, {})


if __name__ == "__main__":
    unittest.main()
