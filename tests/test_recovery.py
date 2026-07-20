import asyncio
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "execution"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))

import database
import recovery
from trade_lifecycle import TradeStatus


def _run(coro):
    """Runs `coro` to completion AND drains any task it scheduled via
    asyncio.create_task() (recovery._resume_one does this for
    pocket_executor.track_outcome) - without this, asyncio.run() would
    cancel that still-pending task the instant the coroutine returns,
    which both loses the ability to assert on it and prints a spurious
    "Task was destroyed but it is pending" warning."""
    async def runner():
        result = await coro
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*pending)
        return result
    return asyncio.run(runner())


class RecoveryTestsBase(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def _insert_trade(self, status, asset="EUR/USD OTC", direction="BUY", expiry="1 Minute",
                       opened_at=None, broker_account_id=None):
        trade_id = database.record_signal_received(
            {"asset": asset, "direction": direction, "expiry": expiry, "raw_message": "test"},
            broker_account_id=broker_account_id,
        )
        fields = {}
        if opened_at is not None:
            fields["opened_at"] = opened_at
        database.update_trade_status(trade_id, status, **fields)
        return trade_id

    def _recovery_event_counts(self, event_type):
        stats = database.get_recovery_event_stats()
        return {row["outcome"]: row["n"] for row in stats if row["event_type"] == event_type}


class MarkAbandonedPreparationsTests(RecoveryTestsBase):
    def test_trade_prepared_is_marked_abandoned(self):
        trade_id = self._insert_trade(TradeStatus.TRADE_PREPARED)
        recovery.mark_abandoned_preparations()
        conn = database.get_connection()
        row = conn.execute("SELECT * FROM signals WHERE id = ?", (trade_id,)).fetchone()
        conn.close()
        self.assertEqual(row["execution_status"], TradeStatus.ERROR.value)
        self.assertEqual(row["result"], "error:abandoned_on_restart")

    def test_non_prepared_trades_are_untouched(self):
        clicked_id = self._insert_trade(TradeStatus.TRADE_CLICKED, opened_at=datetime.now().isoformat())
        opened_id = self._insert_trade(TradeStatus.TRADE_OPENED, opened_at=datetime.now().isoformat())

        recovery.mark_abandoned_preparations()

        conn = database.get_connection()
        clicked_row = conn.execute("SELECT execution_status FROM signals WHERE id = ?", (clicked_id,)).fetchone()
        opened_row = conn.execute("SELECT execution_status FROM signals WHERE id = ?", (opened_id,)).fetchone()
        conn.close()
        self.assertEqual(clicked_row["execution_status"], TradeStatus.TRADE_CLICKED.value)
        self.assertEqual(opened_row["execution_status"], TradeStatus.TRADE_OPENED.value)

    def test_no_prepared_trades_is_a_noop(self):
        # Should not raise even with nothing to do.
        recovery.mark_abandoned_preparations()


class ResumePendingTradesTests(RecoveryTestsBase):
    def setUp(self):
        super().setUp()
        self._patcher = patch.object(recovery.pocket_executor, "track_outcome", new=AsyncMock())
        self.mock_track_outcome = self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        super().tearDown()

    def test_no_open_trades_does_not_call_track_outcome(self):
        _run(recovery.resume_pending_trades(warmup_service="fake-warmup"))
        self.mock_track_outcome.assert_not_called()

    def test_open_trade_with_opened_at_is_resumed_and_records_succeeded_event(self):
        trade_id = self._insert_trade(
            TradeStatus.TRADE_CLICKED,
            opened_at=(datetime.now() - timedelta(seconds=10)).isoformat(),
        )

        _run(recovery.resume_pending_trades(warmup_service="fake-warmup"))

        self.mock_track_outcome.assert_awaited_once()
        call_kwargs = self.mock_track_outcome.call_args
        self.assertEqual(call_kwargs.args[0], "fake-warmup")
        self.assertEqual(call_kwargs.args[1], trade_id)
        self.assertGreater(call_kwargs.args[2], 0)  # remaining seconds
        self.assertEqual(self._recovery_event_counts("resume_open_trade"), {"succeeded": 1})

    def test_trade_opened_status_is_also_resumed(self):
        # get_open_trades() covers BOTH trade_clicked and trade_opened -
        # only trade_prepared (no real position yet) is excluded.
        self._insert_trade(
            TradeStatus.TRADE_OPENED,
            opened_at=(datetime.now() - timedelta(seconds=5)).isoformat(),
        )
        _run(recovery.resume_pending_trades(warmup_service="fake-warmup"))
        self.mock_track_outcome.assert_awaited_once()

    def test_missing_opened_at_marks_abandoned_and_records_failed_event(self):
        trade_id = self._insert_trade(TradeStatus.TRADE_CLICKED, opened_at=None)

        _run(recovery.resume_pending_trades(warmup_service="fake-warmup"))

        self.mock_track_outcome.assert_not_called()
        conn = database.get_connection()
        row = conn.execute("SELECT * FROM signals WHERE id = ?", (trade_id,)).fetchone()
        conn.close()
        self.assertEqual(row["execution_status"], TradeStatus.ERROR.value)
        self.assertEqual(row["result"], "error:abandoned_on_restart")
        self.assertEqual(self._recovery_event_counts("resume_open_trade"), {"failed": 1})

    def test_exception_during_resume_marks_error_and_records_failed_event(self):
        self._insert_trade(
            TradeStatus.TRADE_CLICKED,
            opened_at=(datetime.now() - timedelta(seconds=10)).isoformat(),
        )
        with patch.object(recovery, "_resume_one", new=AsyncMock(side_effect=RuntimeError("boom"))):
            _run(recovery.resume_pending_trades(warmup_service="fake-warmup"))

        conn = database.get_connection()
        row = conn.execute("SELECT * FROM signals").fetchone()
        conn.close()
        self.assertEqual(row["execution_status"], TradeStatus.ERROR.value)
        self.assertIn("recovery_failed", row["result"])
        self.assertEqual(self._recovery_event_counts("resume_open_trade"), {"failed": 1})

    def test_broker_account_id_scopes_which_trades_are_resumed(self):
        account_a_trade = self._insert_trade(
            TradeStatus.TRADE_CLICKED, broker_account_id=1,
            opened_at=(datetime.now() - timedelta(seconds=10)).isoformat(),
        )
        self._insert_trade(
            TradeStatus.TRADE_CLICKED, broker_account_id=2,
            opened_at=(datetime.now() - timedelta(seconds=10)).isoformat(),
        )

        _run(recovery.resume_pending_trades(warmup_service="fake-warmup", broker_account_id=1))

        self.mock_track_outcome.assert_awaited_once()
        self.assertEqual(self.mock_track_outcome.call_args.args[1], account_a_trade)


class RunRecoveryTests(RecoveryTestsBase):
    def setUp(self):
        super().setUp()
        self._patcher = patch.object(recovery.pocket_executor, "track_outcome", new=AsyncMock())
        self.mock_track_outcome = self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        super().tearDown()

    def test_default_call_marks_abandoned_and_resumes_open_trades(self):
        prepared_id = self._insert_trade(TradeStatus.TRADE_PREPARED)
        self._insert_trade(
            TradeStatus.TRADE_CLICKED,
            opened_at=(datetime.now() - timedelta(seconds=10)).isoformat(),
        )

        _run(recovery.run_recovery(warmup_service="fake-warmup"))

        conn = database.get_connection()
        prepared_row = conn.execute("SELECT execution_status FROM signals WHERE id = ?", (prepared_id,)).fetchone()
        conn.close()
        self.assertEqual(prepared_row["execution_status"], TradeStatus.ERROR.value)
        self.mock_track_outcome.assert_awaited_once()

    def test_skip_abandoned_pass_leaves_prepared_trades_alone(self):
        prepared_id = self._insert_trade(TradeStatus.TRADE_PREPARED)

        _run(recovery.run_recovery(warmup_service="fake-warmup", skip_abandoned_pass=True))

        conn = database.get_connection()
        prepared_row = conn.execute("SELECT execution_status FROM signals WHERE id = ?", (prepared_id,)).fetchone()
        conn.close()
        # Unchanged - a per-account lazy-build call must not sweep every
        # trade_prepared row process-wide (see run_recovery's docstring).
        self.assertEqual(prepared_row["execution_status"], TradeStatus.TRADE_PREPARED.value)

    def test_broker_account_id_passed_through_to_resume(self):
        target_trade = self._insert_trade(
            TradeStatus.TRADE_CLICKED, broker_account_id=7,
            opened_at=(datetime.now() - timedelta(seconds=10)).isoformat(),
        )
        self._insert_trade(
            TradeStatus.TRADE_CLICKED, broker_account_id=9,
            opened_at=(datetime.now() - timedelta(seconds=10)).isoformat(),
        )

        _run(recovery.run_recovery(warmup_service="fake-warmup", broker_account_id=7, skip_abandoned_pass=True))

        self.mock_track_outcome.assert_awaited_once()
        self.assertEqual(self.mock_track_outcome.call_args.args[1], target_trade)


if __name__ == "__main__":
    unittest.main()
