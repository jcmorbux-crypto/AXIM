"""
End-to-end integration test for the exact series-105 production incident
(2026-07-29 reconciliation-gap fix). Unit tests elsewhere in this suite
cover each module in isolation (test_pocket_dom.py's transient-retry and
broker-matcher tests, test_recovery.py's broadened-scan tests,
test_trade_series_engine.py's ReconciliationGapTests) - this file instead
drives the REAL functions across all of them together, in the same order
core/telegram_listener.py's real startup does, so the interaction between
independently-developed pieces is actually exercised, not just each
piece's own contract in isolation.

Reproduces the full sequence: a trade is submitted -> the browser context
closes while pocket_executor.track_outcome is waiting for the result (via
the REAL pocket_dom.wait_for_trade_result, not a stand-in for it) -> that
call cannot finish even after its own internal retry, so track_outcome's
own exception handler leaves the signals row exactly as series 105's did
(execution_status='error', opened_at set, no terminal result) -> the
process "restarts" (a fresh recovery/reconciliation pass against the same
on-disk database - no in-memory state carried over from submission) ->
core/recovery.py's broadened resume_pending_trades runs -> broker-history
reconciliation executes -> the series is either uniquely resolved or
explicitly marked reconciliation_required -> core/trade_series_engine.py's
_apply_entry_outcome runs exactly once even though BOTH recovery.py's own
event-publishing resume path and trade_series_engine.reconcile_stuck_series
independently look at the same series in the real startup order -> the
series never remains permanently 'active'.
"""
import asyncio
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "execution"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))

import database
import recovery
import trade_series_engine as engine
import pocket_dom
import pocket_executor
from event_bus import get_event_bus
from trade_lifecycle import TradeStatus


def _run(coro):
    return asyncio.run(coro)


_TRANSIENT_ERROR_TEXT = "Target page, context or browser has been closed"


class _FakeExpectation:
    """Stands in for playwright.async_api.expect(locator) - the real
    version chokes on a plain MagicMock locator (tries to serialize it
    for a real CDP round-trip); only to_be_visible() is ever awaited by
    the code under test here."""
    async def to_be_visible(self, timeout=None):
        return None


class ReconciliationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

        # Matches production wiring exactly (core/telegram_listener.py's
        # _startup(): trade_series_engine.register(get_event_bus()) runs
        # before recovery.run_recovery()). core/recovery.py's own publish
        # call does a local `from event_bus import get_event_bus` at call
        # time - always the process-wide singleton, not injectable per
        # test - so this test registers onto that same singleton to
        # faithfully exercise the real cross-module event chain, and
        # removes it again in tearDown.
        engine.register()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()
        subscribers = get_event_bus()._subscribers.get("trade.closed", [])
        if engine._on_trade_closed in subscribers:
            subscribers.remove(engine._on_trade_closed)

    def _submit_then_crash_during_result_read(self, series_id, entry_number, asset, direction, amount,
                                               expiry="5 Minute", minutes_ago=10):
        """Step 1+2+3 of the incident: a real signals row is created (a
        real click already happened - opened_at is set), then the REAL
        pocket_executor.track_outcome is run against a page double whose
        Closed-tab click always raises the exact transient-crash error -
        on BOTH the initial attempt and wait_for_trade_result's own
        internal retry, so it genuinely cannot finish (matches "the
        browser context closes... wait_for_trade_result() cannot
        finish", not just a mocked-away function that never really ran)."""
        trade_id = database.record_signal_received(
            {"asset": asset, "direction": direction, "expiry": expiry, "raw_message": "x",
             "trade_amount": amount},
            series_id=series_id, entry_number=entry_number,
        )
        opened_at = datetime.now() - timedelta(minutes=minutes_ago)
        database.update_trade_status(trade_id, TradeStatus.TRADE_OPENED, opened_at=opened_at.isoformat())

        def _crashing_page():
            page = MagicMock()
            page.locator.return_value.get_by_text.return_value.first.click = AsyncMock(
                side_effect=Exception(_TRANSIENT_ERROR_TEXT))
            page.locator.return_value.first = MagicMock()
            return page

        warmup = MagicMock()
        warmup.get_page = AsyncMock(side_effect=[_crashing_page(), _crashing_page()])
        warmup.ensure_alive = AsyncMock()
        warmup.outcome_lock = asyncio.Lock()

        with patch.object(pocket_dom, "_ensure_opened_tab_active", new=AsyncMock(return_value=True)), \
             patch.object(pocket_dom, "_probe_state", new=AsyncMock(return_value=(True, True, True))):
            _run(pocket_executor.track_outcome(warmup, trade_id, expiry_seconds=0, asset=asset, direction=direction))

        return trade_id

    def _restart_warmup_with_closed_history(self, closed_items):
        """Step 4: "the process restarts" - a completely fresh
        warmup_service/page double (nothing carried over from
        submission), whose Closed-trades history is exactly what a real
        restart would read from Pocket Option at that later point in
        time."""
        page = MagicMock()
        page.locator.return_value.get_by_text.return_value.first.click = AsyncMock()
        page.locator.return_value.first = MagicMock()
        page.evaluate = AsyncMock(return_value=closed_items)
        warmup = MagicMock()
        warmup.get_page = AsyncMock(return_value=page)
        warmup.ensure_alive = AsyncMock()
        warmup.outcome_lock = asyncio.Lock()
        return warmup

    def test_unique_broker_match_resolves_the_series_and_updates_outcome_exactly_once(self):
        series_id = database.create_trade_series(
            channel_id=163, asset="AUD/CAD OTC", direction="SELL", expiry="5 Minute",
            stake=10.0, entry_times=["09:00"], max_entries=1,
        )
        database.advance_trade_series(series_id, 1, "active")

        self._submit_then_crash_during_result_read(series_id, 1, "AUD/CAD OTC", "SELL", 10.0)

        # Confirm the crash left EXACTLY series 105's real shape:
        # possibly_submitted (opened_at set) AND not_authoritatively_
        # resolved (no win/loss/draw) - invisible to the OLD narrow
        # "error:abandoned_on_restart"-only recovery.
        entry = database.get_series_entry(series_id, 1)
        self.assertEqual(entry["execution_status"], TradeStatus.ERROR.value)
        self.assertIn(_TRANSIENT_ERROR_TEXT, entry["result"])
        self.assertIsNotNone(entry["opened_at"])
        self.assertEqual(database.get_trade_series(series_id)["status"], "active")

        # Step 5/6/7: restart -> recovery runs -> broker-history
        # reconciliation executes, in the exact real order
        # core/telegram_listener.py's startup uses (run_recovery, then
        # reconcile_stuck_series).
        restart_warmup = self._restart_warmup_with_closed_history([
            {"asset": "AUD/CAD OTC", "direction": "SELL",
             "time_text": datetime.now().strftime("%H:%M"), "values": ["$10.00", "$0"]},
        ])

        apply_spy = AsyncMock(wraps=engine._apply_entry_outcome)
        with patch.object(pocket_dom, "_ensure_opened_tab_active", new=AsyncMock(return_value=True)), \
             patch.object(pocket_dom, "expect", new=lambda locator: _FakeExpectation()), \
             patch.object(engine, "_apply_entry_outcome", new=apply_spy):
            _run(recovery.run_recovery(restart_warmup, skip_abandoned_pass=True))
            _run(engine.reconcile_stuck_series(restart_warmup))

        # Step 8: risk counters/P&L updated through the one shared
        # _apply_entry_outcome path exactly once - even though BOTH
        # recovery.py's own event-publishing resume path AND
        # reconcile_stuck_series independently look at this same series,
        # in the real startup order.
        apply_spy.assert_awaited_once()

        # Step 9: the series never remains permanently active.
        series_after = database.get_trade_series(series_id)
        self.assertEqual(series_after["status"], "lost_exhausted")
        self.assertEqual(series_after["net_profit_loss"], -10.0)

        signal_row = database.get_series_entry(series_id, 1)
        self.assertEqual(signal_row["execution_status"], TradeStatus.RESULT_LOSS.value)
        self.assertEqual(signal_row["result"], "loss")
        self.assertEqual(signal_row["profit_loss"], -10.0)

        # Confirmed via get_recent_results, not just the raw column - this
        # is the exact function risk_manager's consecutive-loss counter
        # reads, so a double-application would have shown up here too.
        self.assertEqual(database.get_recent_results(10), ["loss"])

    def test_no_unique_broker_match_marks_reconciliation_required_and_never_leaves_it_active(self):
        series_id = database.create_trade_series(
            channel_id=163, asset="AUD/CAD OTC", direction="SELL", expiry="5 Minute",
            stake=10.0, entry_times=["09:00"], max_entries=1,
        )
        database.advance_trade_series(series_id, 1, "active")

        self._submit_then_crash_during_result_read(series_id, 1, "AUD/CAD OTC", "SELL", 10.0)

        # This time the broker's own Closed-trades history has nothing
        # matching at all (the realistic case for an old enough trade -
        # exactly what happened to the pre-existing legacy rows found
        # alongside series 105 in production).
        restart_warmup = self._restart_warmup_with_closed_history([])

        apply_spy = AsyncMock(wraps=engine._apply_entry_outcome)
        with patch.object(pocket_dom, "_ensure_opened_tab_active", new=AsyncMock(return_value=True)), \
             patch.object(pocket_dom, "expect", new=lambda locator: _FakeExpectation()), \
             patch.object(engine, "_apply_entry_outcome", new=apply_spy):
            _run(recovery.run_recovery(restart_warmup, skip_abandoned_pass=True))
            _run(engine.reconcile_stuck_series(restart_warmup))

        # Never guesses an outcome for an ambiguous/no-match case.
        apply_spy.assert_not_awaited()

        series_after = database.get_trade_series(series_id)
        self.assertNotEqual(series_after["status"], "active")
        self.assertEqual(series_after["status"], "reconciliation_required")
        self.assertIsNotNone(series_after["reconciliation_required_at"])

        # Explicitly excluded from the normal due-entry scan - never
        # silently resumes on its own.
        pending_ids = [s["id"] for s in database.list_pending_trade_series()]
        self.assertNotIn(series_id, pending_ids)

        # A subsequent operator action still resolves it correctly and
        # exactly once, proving the fail-closed state is genuinely
        # recoverable, not a dead end.
        result = _run(engine.reconcile_series_manually(
            series_id, operator="csominq", reconciliation_source="pocket_option_trade_history",
            reconciliation_reason="operator_reviewed_broker_history_manually",
            authoritative_result="loss", authoritative_pnl=-10.0,
        ))
        self.assertTrue(result["applied"])
        self.assertEqual(database.get_trade_series(series_id)["status"], "lost_exhausted")

        second = _run(engine.reconcile_series_manually(
            series_id, operator="csominq", reconciliation_source="pocket_option_trade_history",
            reconciliation_reason="duplicate_attempt", authoritative_result="win", authoritative_pnl=9.2,
        ))
        self.assertFalse(second["applied"])
        self.assertEqual(database.get_trade_series(series_id)["net_profit_loss"], -10.0)


if __name__ == "__main__":
    unittest.main()
