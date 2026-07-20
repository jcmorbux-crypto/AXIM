import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))
sys.path.insert(0, str(PROJECT_ROOT / "parsers"))
sys.path.insert(0, str(PROJECT_ROOT / "execution"))

import database
import telegram_listener


def _run_until_complete_sequence(*outcomes):
    """A side_effect function for client.loop.run_until_complete mocks.
    run_forever() calls this with real coroutine objects (_startup()/
    _shutdown()) - closing each one (rather than just returning a value
    and leaving it for the GC) avoids a spurious "coroutine was never
    awaited" RuntimeWarning, without actually running it."""
    outcomes = iter(outcomes)

    def _side_effect(coro):
        coro.close()
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return _side_effect


class RunForeverTests(unittest.TestCase):
    """run_forever() is the top-level 24/7 supervisor - per the Phase 2
    fault-injection audit, real and well-structured (exponential backoff,
    process_restart recovery events, clean-vs-unexpected-disconnect
    discrimination) but entirely untested. The real module-level `client`
    (a Telethon TelegramClient) is swapped for a fully-controlled
    MagicMock via patch.object(telegram_listener, "client", ...) - since
    run_forever() looks up `client` as a module global on every access,
    this substitution is exactly what the real function sees, without
    needing to know Telethon's own internals. Each test terminates the
    otherwise-infinite while loop deterministically via a scripted
    KeyboardInterrupt, the same signal a real Ctrl+C delivers."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self._sleep_patcher = patch.object(telegram_listener.time, "sleep")
        self.mock_sleep = self._sleep_patcher.start()

    def tearDown(self):
        self._sleep_patcher.stop()
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def _event_counts(self, event_type):
        stats = database.get_recovery_event_stats()
        return {row["outcome"]: row["n"] for row in stats if row["event_type"] == event_type}

    def test_clean_ctrl_c_on_first_run_exits_with_no_restart_event(self):
        fake_client = MagicMock()
        fake_client.loop.run_until_complete = MagicMock(side_effect=_run_until_complete_sequence(None, None))
        fake_client.run_until_disconnected = MagicMock(side_effect=KeyboardInterrupt())

        with patch.object(telegram_listener, "client", fake_client):
            with self.assertRaises(KeyboardInterrupt):
                telegram_listener.run_forever()

        fake_client.start.assert_called_once_with(phone=telegram_listener.phone)
        # _startup() then _shutdown() - both via run_until_complete.
        self.assertEqual(fake_client.loop.run_until_complete.call_count, 2)
        self.assertEqual(self._event_counts("process_restart"), {})
        self.mock_sleep.assert_not_called()

    def test_unexpected_disconnect_then_successful_restart_records_succeeded(self):
        fake_client = MagicMock()
        # 3 calls total: attempt 1's _startup(), attempt 2's _startup(),
        # and the final _shutdown() after Ctrl+C - attempt 1 never calls
        # _shutdown() since a normal (non-exception) disconnect isn't
        # caught by either except clause.
        fake_client.loop.run_until_complete = MagicMock(side_effect=_run_until_complete_sequence(None, None, None))
        # First call: client just returns (unexpected disconnect, no
        # exception) - the exact case run_forever's own docstring
        # describes as "not itself a recovery outcome, the failure the
        # NEXT attempt reports on." Second call: clean Ctrl+C to stop.
        fake_client.run_until_disconnected = MagicMock(side_effect=[None, KeyboardInterrupt()])

        with patch.object(telegram_listener, "client", fake_client):
            with self.assertRaises(KeyboardInterrupt):
                telegram_listener.run_forever()

        self.assertEqual(fake_client.start.call_count, 2)
        self.assertEqual(self._event_counts("process_restart"), {"succeeded": 1})
        self.mock_sleep.assert_called_once_with(1)  # first backoff value

    def test_startup_failure_then_successful_retry_records_both_outcomes(self):
        fake_client = MagicMock()
        # Call order: [0] _startup() for attempt 1 -> raises, [1] _shutdown()
        # after that failure -> succeeds, [2] _startup() for attempt 2 ->
        # succeeds, [3] _shutdown() after the final clean Ctrl+C -> succeeds.
        fake_client.loop.run_until_complete = MagicMock(
            side_effect=_run_until_complete_sequence(
                RuntimeError("browser stack failed to start"), None, None, None,
            )
        )
        fake_client.run_until_disconnected = MagicMock(side_effect=[KeyboardInterrupt()])

        with patch.object(telegram_listener, "client", fake_client):
            with self.assertRaises(KeyboardInterrupt):
                telegram_listener.run_forever()

        self.assertEqual(fake_client.loop.run_until_complete.call_count, 4)
        self.assertEqual(fake_client.start.call_count, 1)  # only attempt 2 got past _startup()
        self.assertEqual(self._event_counts("process_restart"), {"failed": 1, "succeeded": 1})
        self.mock_sleep.assert_called_once_with(1)

    def test_backoff_doubles_across_consecutive_failures(self):
        fake_client = MagicMock()
        fake_client.loop.run_until_complete = MagicMock(
            side_effect=_run_until_complete_sequence(
                RuntimeError("fail 1"), None,  # attempt 1 startup fails, its shutdown succeeds
                RuntimeError("fail 2"), None,  # attempt 2 startup fails, its shutdown succeeds
                None, None,                    # attempt 3 startup succeeds, final shutdown succeeds
            )
        )
        fake_client.run_until_disconnected = MagicMock(side_effect=[KeyboardInterrupt()])

        with patch.object(telegram_listener, "client", fake_client):
            with self.assertRaises(KeyboardInterrupt):
                telegram_listener.run_forever()

        self.assertEqual(self.mock_sleep.call_args_list, [((1,),), ((2,),)])
        self.assertEqual(self._event_counts("process_restart"), {"failed": 2, "succeeded": 1})

    def test_shutdown_error_during_failure_handling_does_not_prevent_retry(self):
        # A failure in the CLEANUP itself (shutdown erroring out while
        # handling the original failure) must not crash run_forever or
        # skip the retry - it's caught and logged separately (see the
        # nested try/except around client.loop.run_until_complete(_shutdown())).
        fake_client = MagicMock()
        fake_client.loop.run_until_complete = MagicMock(
            side_effect=_run_until_complete_sequence(
                RuntimeError("startup failed"),
                RuntimeError("shutdown ALSO failed"),
                None,  # attempt 2 startup succeeds
                None,  # final shutdown succeeds
            )
        )
        fake_client.run_until_disconnected = MagicMock(side_effect=[KeyboardInterrupt()])

        with patch.object(telegram_listener, "client", fake_client):
            with self.assertRaises(KeyboardInterrupt):
                telegram_listener.run_forever()

        self.assertEqual(fake_client.start.call_count, 1)
        self.assertEqual(self._event_counts("process_restart"), {"failed": 1, "succeeded": 1})


if __name__ == "__main__":
    unittest.main()
