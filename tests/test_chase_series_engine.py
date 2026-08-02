import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))
sys.path.insert(0, str(PROJECT_ROOT / "execution"))

import database
import chase_series_engine as engine


def _run(coro):
    return asyncio.run(coro)


def _signal(asset="EUR/USD OTC", direction="BUY", expiry="1 Minute", scheduled_entries=None):
    s = {"asset": asset, "direction": direction, "expiry": expiry, "raw_message": "test"}
    if scheduled_entries is not None:
        s["scheduled_entries"] = scheduled_entries
    return s


class CreateAndFireTests(unittest.TestCase):
    """create_and_fire is the entry point a test-enrolled channel's
    signal goes through - creates the chase_series row, records any
    watch-only overflow, and fires Entry 1 through the real execution
    path (mocked here)."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_creates_a_series_and_fires_entry_1(self):
        route_mock = AsyncMock(return_value={"status": "submitted", "trade_id": 501})
        with patch.object(engine.broker_account_manager, "route_signal", route_mock):
            series_id = _run(engine.create_and_fire(
                _signal(), channel_id=207, coordinator="fake-coordinator",
                source_message_id=1001,
            ))
        series = database.get_chase_series(series_id)
        self.assertEqual(series["status"], "active")
        self.assertEqual(series["current_entry_number"], 1)
        self.assertEqual(series["entry_1_trade_id"], 501)
        self.assertIsNone(series["entry_2_trade_id"])
        route_mock.assert_awaited_once()

    def test_first_ever_signal_opens_a_10_percent_of_100_test_session(self):
        route_mock = AsyncMock(return_value={"status": "submitted", "trade_id": 501})
        with patch.object(engine.broker_account_manager, "route_signal", route_mock):
            _run(engine.create_and_fire(
                _signal(), channel_id=207, coordinator="fake-coordinator", source_message_id=1002,
            ))
        _, kwargs = route_mock.call_args
        self.assertEqual(kwargs["fixed_stake"], 10.0)
        self.assertIsNone(kwargs["session_id"], "must route via the shared connection, not a real trading_session")
        test_session = database.get_open_chase_test_session(207)
        self.assertEqual(test_session["opening_virtual_fund"], 100.0)
        self.assertEqual(test_session["stake"], 10.0)

    def test_stake_stays_flat_across_multiple_signals_in_the_same_open_session(self):
        route_mock = AsyncMock(return_value={"status": "submitted", "trade_id": 501})
        with patch.object(engine.broker_account_manager, "route_signal", route_mock):
            s1 = _run(engine.create_and_fire(_signal(), channel_id=207, coordinator="fake", source_message_id=1))
        database.advance_chase_series(s1, 1, "won", result="win", net_profit_loss=50.0)  # a big win mid-session

        route_mock2 = AsyncMock(return_value={"status": "submitted", "trade_id": 502})
        with patch.object(engine.broker_account_manager, "route_signal", route_mock2):
            _run(engine.create_and_fire(_signal(), channel_id=207, coordinator="fake", source_message_id=2))
        _, kwargs = route_mock2.call_args
        self.assertEqual(kwargs["fixed_stake"], 10.0, "must stay flat within the session despite the win")

    def test_new_session_after_the_old_one_ends_recompounds_from_real_pl(self):
        route_mock = AsyncMock(return_value={"status": "submitted", "trade_id": 501})
        with patch.object(engine.broker_account_manager, "route_signal", route_mock):
            s1 = _run(engine.create_and_fire(_signal(), channel_id=207, coordinator="fake", source_message_id=1))
        database.advance_chase_series(s1, 1, "won", result="win", net_profit_loss=50.0)
        database.end_chase_test_session(207)  # session profit +$50 -> updated virtual fund $150

        route_mock2 = AsyncMock(return_value={"status": "submitted", "trade_id": 502})
        with patch.object(engine.broker_account_manager, "route_signal", route_mock2):
            _run(engine.create_and_fire(_signal(), channel_id=207, coordinator="fake", source_message_id=2))
        _, kwargs = route_mock2.call_args
        self.assertEqual(kwargs["fixed_stake"], 15.0)  # 10% of $150, matching the spec's own worked example

    def test_idempotent_by_channel_and_message_id(self):
        route_mock = AsyncMock(return_value={"status": "submitted", "trade_id": 501})
        with patch.object(engine.broker_account_manager, "route_signal", route_mock):
            first_id = _run(engine.create_and_fire(
                _signal(), channel_id=207, coordinator="fake", source_message_id=2002,
            ))
            second_id = _run(engine.create_and_fire(
                _signal(), channel_id=207, coordinator="fake", source_message_id=2002,
            ))
        self.assertEqual(first_id, second_id)
        route_mock.assert_awaited_once()  # the second call never re-fires entry 1

    def test_overflow_entries_beyond_max_entries_are_recorded_watch_only(self):
        # Martin-Trader-format signal: entries #2, #3, #4 published in the
        # "Martingale:" block (entry #1 is the separate Entry: field, not
        # in scheduled_entries at all - matches signal_parser.py's real shape).
        scheduled = [
            {"entry_number": 2, "time": "09:05"},
            {"entry_number": 3, "time": "09:10"},
            {"entry_number": 4, "time": "09:15"},
        ]
        route_mock = AsyncMock(return_value={"status": "submitted", "trade_id": 700})
        with patch.object(engine.broker_account_manager, "route_signal", route_mock):
            series_id = _run(engine.create_and_fire(
                _signal(scheduled_entries=scheduled), channel_id=163, coordinator="fake",
                source_message_id=3003,
            ))
        watch_only = database.list_chase_watch_only_entries(series_id)
        # entry #2 is within max_entries (2) - only #3 and #4 overflow.
        self.assertEqual(len(watch_only), 2)
        self.assertEqual({w["entry_number"] for w in watch_only}, {3, 4})
        for w in watch_only:
            self.assertIsNone(w["hypothetical_result"], "must never fabricate a hypothetical outcome")

    def test_no_overflow_when_signal_publishes_no_extra_entries(self):
        route_mock = AsyncMock(return_value={"status": "submitted", "trade_id": 701})
        with patch.object(engine.broker_account_manager, "route_signal", route_mock):
            series_id = _run(engine.create_and_fire(
                _signal(), channel_id=207, coordinator="fake", source_message_id=4004,
            ))
        self.assertEqual(database.list_chase_watch_only_entries(series_id), [])

    def test_entry_that_never_opens_does_not_leave_the_series_stuck_active(self):
        route_mock = AsyncMock(return_value={
            "status": "rejected", "trade_id": 800, "rule": "max_trade_amount", "reason": "too large",
        })
        with patch.object(engine.broker_account_manager, "route_signal", route_mock):
            series_id = _run(engine.create_and_fire(
                _signal(), channel_id=207, coordinator="fake", source_message_id=5005,
            ))
        series = database.get_chase_series(series_id)
        self.assertEqual(series["status"], "error")
        self.assertIsNotNone(series["resolved_at"])


class OnTradeClosedTests(unittest.TestCase):
    """The event-triggered chase mechanic itself: a win stops the series,
    a loss/draw on entry 1 fires entry 2 immediately (event-triggered, no
    clock schedule), entry 2's own outcome always ends the series - never
    a 3rd entry, matching 'Maximum executed entries per signal: 2 total.'"""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        engine.register_coordinator("fake-coordinator")

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()
        engine.register_coordinator(None)

    def _create_active_series(self, entry_trade_id=501):
        route_mock = AsyncMock(return_value={"status": "submitted", "trade_id": entry_trade_id})
        with patch.object(engine.broker_account_manager, "route_signal", route_mock):
            series_id = _run(engine.create_and_fire(
                _signal(), channel_id=207, coordinator="fake-coordinator",
                source_message_id=entry_trade_id,
            ))
        return series_id

    def test_entry_1_win_closes_the_series_no_entry_2(self):
        series_id = self._create_active_series(entry_trade_id=501)
        route_mock = AsyncMock()
        with patch.object(engine.broker_account_manager, "route_signal", route_mock):
            _run(engine._on_trade_closed({"trade_id": 501, "result": "win", "profit_loss": 8.5}))
        series = database.get_chase_series(series_id)
        self.assertEqual(series["status"], "won")
        self.assertEqual(series["net_profit_loss"], 8.5)
        self.assertIsNone(series["entry_2_trade_id"])
        route_mock.assert_not_awaited()

    def test_entry_1_loss_fires_entry_2_immediately(self):
        series_id = self._create_active_series(entry_trade_id=501)
        entry2_mock = AsyncMock(return_value={"status": "submitted", "trade_id": 502})
        with patch.object(engine.broker_account_manager, "route_signal", entry2_mock):
            _run(engine._on_trade_closed({"trade_id": 501, "result": "loss", "profit_loss": -10.0}))
        series = database.get_chase_series(series_id)
        self.assertEqual(series["status"], "active")
        self.assertEqual(series["current_entry_number"], 2)
        self.assertEqual(series["entry_2_trade_id"], 502)
        entry2_mock.assert_awaited_once()

    def test_entry_1_draw_also_fires_entry_2(self):
        series_id = self._create_active_series(entry_trade_id=501)
        entry2_mock = AsyncMock(return_value={"status": "submitted", "trade_id": 502})
        with patch.object(engine.broker_account_manager, "route_signal", entry2_mock):
            _run(engine._on_trade_closed({"trade_id": 501, "result": "draw", "profit_loss": 0.0}))
        series = database.get_chase_series(series_id)
        self.assertEqual(series["current_entry_number"], 2)

    def test_entry_2_win_closes_series(self):
        series_id = self._create_active_series(entry_trade_id=501)
        entry2_mock = AsyncMock(return_value={"status": "submitted", "trade_id": 502})
        with patch.object(engine.broker_account_manager, "route_signal", entry2_mock):
            _run(engine._on_trade_closed({"trade_id": 501, "result": "loss", "profit_loss": -10.0}))
        _run(engine._on_trade_closed({"trade_id": 502, "result": "win", "profit_loss": 8.5}))
        series = database.get_chase_series(series_id)
        self.assertEqual(series["status"], "won")
        self.assertEqual(series["current_entry_number"], 2)

    def test_entry_2_loss_exhausts_series_never_fires_entry_3(self):
        series_id = self._create_active_series(entry_trade_id=501)
        entry2_mock = AsyncMock(return_value={"status": "submitted", "trade_id": 502})
        with patch.object(engine.broker_account_manager, "route_signal", entry2_mock):
            _run(engine._on_trade_closed({"trade_id": 501, "result": "loss", "profit_loss": -10.0}))

        entry3_mock = AsyncMock()
        with patch.object(engine.broker_account_manager, "route_signal", entry3_mock):
            _run(engine._on_trade_closed({"trade_id": 502, "result": "loss", "profit_loss": -10.0}))
        series = database.get_chase_series(series_id)
        self.assertEqual(series["status"], "lost_exhausted")
        self.assertEqual(series["current_entry_number"], 2)
        entry3_mock.assert_not_awaited()  # must never fire a 3rd executed entry

    def test_pending_unknown_and_missing_results_never_advance(self):
        series_id = self._create_active_series(entry_trade_id=501)
        for bogus in ("pending", "unknown", None):
            route_mock = AsyncMock()
            with patch.object(engine.broker_account_manager, "route_signal", route_mock):
                _run(engine._on_trade_closed({"trade_id": 501, "result": bogus, "profit_loss": 0.0}))
            series = database.get_chase_series(series_id)
            self.assertEqual(series["status"], "active", f"result={bogus!r} must not advance the series")
            route_mock.assert_not_awaited()

    def test_unrelated_trade_closed_event_is_a_no_op(self):
        self._create_active_series(entry_trade_id=501)
        # No exception, no side effect - trade_id 99999 belongs to no chase_series.
        _run(engine._on_trade_closed({"trade_id": 99999, "result": "win", "profit_loss": 5.0}))

    def test_missing_trade_id_in_payload_is_a_no_op(self):
        _run(engine._on_trade_closed({"result": "win"}))


class RegisterTests(unittest.TestCase):
    def test_register_subscribes_to_trade_closed(self):
        events = []

        class FakeBus:
            def subscribe(self, event_name, handler):
                events.append((event_name, handler))

        bus = FakeBus()
        engine.register(event_bus=bus)
        self.assertEqual(events, [("trade.closed", engine._on_trade_closed)])


class ChaseSeriesSummaryTests(unittest.TestCase):
    """database.get_chase_series_summary - the exact function the final
    provider-by-provider validation report reads from. Executed P/L and
    watch-only entries must never mix: 'the real virtual-fund balance
    must be calculated only from trades AXIM actually submitted.'"""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_summary_scoped_to_one_channel(self):
        won_id = database.create_chase_series(channel_id=207, asset="EUR/USD OTC", direction="BUY", expiry="1 Minute")
        database.advance_chase_series(won_id, 1, "won", entry_trade_id=1, result="win", net_profit_loss=8.5)

        lost_id = database.create_chase_series(channel_id=207, asset="GBP/USD OTC", direction="SELL", expiry="1 Minute")
        database.advance_chase_series(lost_id, 1, "active", entry_trade_id=2)
        database.advance_chase_series(lost_id, 2, "lost_exhausted", entry_trade_id=3, result="loss", net_profit_loss=-20.0)

        other_channel_id = database.create_chase_series(channel_id=163, asset="EUR/USD OTC", direction="BUY", expiry="1 Minute")
        database.advance_chase_series(other_channel_id, 1, "won", entry_trade_id=4, result="win", net_profit_loss=100.0)

        summary = database.get_chase_series_summary(channel_id=207)
        self.assertEqual(summary["signals_received"], 2)
        self.assertEqual(summary["series_completed"], 2)
        self.assertEqual(summary["wins"], 1)
        self.assertEqual(summary["losses"], 1)
        self.assertEqual(summary["win_rate_by_signal"], 50.0)
        self.assertEqual(summary["total_executed_entries"], 3)  # 1 + 2
        self.assertAlmostEqual(summary["net_profit_loss"], -11.5)  # 8.5 - 20.0

    def test_watch_only_entries_reported_separately_never_in_net_pl(self):
        series_id = database.create_chase_series(channel_id=163, asset="EUR/USD OTC", direction="BUY", expiry="1 Minute")
        database.advance_chase_series(series_id, 1, "won", entry_trade_id=1, result="win", net_profit_loss=8.5)
        database.record_chase_watch_only_entries(series_id, [
            {"entry_number": 3, "asset": "EUR/USD OTC", "direction": "BUY", "scheduled_time": "09:10"},
            {"entry_number": 4, "asset": "EUR/USD OTC", "direction": "BUY", "scheduled_time": "09:15"},
        ])

        summary = database.get_chase_series_summary(channel_id=163)
        self.assertEqual(summary["total_watch_only_entries"], 2)
        self.assertAlmostEqual(summary["net_profit_loss"], 8.5, msg="watch-only entries must never affect real P/L")
        self.assertEqual(len(summary["per_signal"][0]["watch_only_entries"]), 2)
        for w in summary["per_signal"][0]["watch_only_entries"]:
            self.assertIsNone(w["hypothetical_result"])

    def test_pending_series_not_counted_as_completed(self):
        database.create_chase_series(channel_id=207, asset="EUR/USD OTC", direction="BUY", expiry="1 Minute")
        summary = database.get_chase_series_summary(channel_id=207)
        self.assertEqual(summary["signals_received"], 1)
        self.assertEqual(summary["series_completed"], 0)
        self.assertIsNone(summary["win_rate_by_signal"])


if __name__ == "__main__":
    unittest.main()
