"""Tests scripts/import_provider_research.py's pure filtering logic
(_extract_backtestable_trades) against a fake adapter module - no live
DB, no dependency on the research worktree actually being present on
disk for this specific logic (a separate, environment-dependent smoke
run of the real thing is documented in
docs/AXIM_ENGINEERING_JOURNAL.md, not repeated here as an automated
test)."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import import_provider_research as bridge


def _record(message_id, asset, direction, expiry="15 Minutes"):
    return {
        "source_message_id": message_id, "normalized_asset": asset,
        "direction": direction, "expiry": expiry,
    }


def _link(signal_message_id, result_message_id, result):
    return {"signal_message_id": signal_message_id, "result_message_id": result_message_id, "result": result}


class FakeAdapter:
    def __init__(self, records, links):
        self._records = records
        self._links = links

    def parse_source(self, messages):
        return self._records, self._links


def _messages(*id_date_pairs):
    return [{"message_id": mid, "date_utc": date, "text": ""} for mid, date in id_date_pairs]


class ExtractBacktestableTradesTests(unittest.TestCase):
    def test_cleanly_linked_win_is_kept(self):
        records = [_record(1, "EUR/USD", "BUY")]
        links = [_link(1, 2, "win")]
        adapter = FakeAdapter(records, links)
        messages = _messages((1, "2026-01-01T00:00:00"), (2, "2026-01-01T00:01:00"))
        trades = bridge._extract_backtestable_trades(adapter, messages)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["asset"], "EUR/USD")
        self.assertEqual(trades[0]["direction"], "BUY")
        self.assertEqual(trades[0]["result"], "win")

    def test_unresolved_result_is_excluded(self):
        records = [_record(1, "EUR/USD", "BUY")]
        links = [_link(1, None, "unresolved")]
        adapter = FakeAdapter(records, links)
        messages = _messages((1, "2026-01-01T00:00:00"))
        trades = bridge._extract_backtestable_trades(adapter, messages)
        self.assertEqual(trades, [])

    def test_orphan_result_with_no_signal_message_id_is_excluded(self):
        # Real finding from VIP | Signals: a "win" result referencing a
        # trade that was never announced as its own signal message -
        # nothing to attach asset/direction to, must not be forced in.
        records = []
        links = [_link(None, 5, "win")]
        adapter = FakeAdapter(records, links)
        messages = _messages((5, "2026-01-01T00:00:00"))
        trades = bridge._extract_backtestable_trades(adapter, messages)
        self.assertEqual(trades, [])

    def test_record_with_unresolved_asset_is_excluded(self):
        records = [_record(1, None, "BUY")]
        links = [_link(1, 2, "win")]
        adapter = FakeAdapter(records, links)
        messages = _messages((1, "2026-01-01T00:00:00"), (2, "2026-01-01T00:01:00"))
        trades = bridge._extract_backtestable_trades(adapter, messages)
        self.assertEqual(trades, [])

    def test_draw_is_kept_as_a_decided_outcome(self):
        records = [_record(1, "GBP/JPY", "SELL")]
        links = [_link(1, 2, "draw")]
        adapter = FakeAdapter(records, links)
        messages = _messages((1, "2026-01-01T00:00:00"), (2, "2026-01-01T00:01:00"))
        trades = bridge._extract_backtestable_trades(adapter, messages)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["result"], "draw")

    def test_trades_are_sorted_by_received_at(self):
        records = [_record(1, "EUR/USD", "BUY"), _record(3, "GBP/USD", "SELL")]
        links = [_link(3, 4, "loss"), _link(1, 2, "win")]
        adapter = FakeAdapter(records, links)
        messages = _messages(
            (1, "2026-01-02T00:00:00"), (2, "2026-01-02T00:01:00"),
            (3, "2026-01-01T00:00:00"), (4, "2026-01-01T00:01:00"),
        )
        trades = bridge._extract_backtestable_trades(adapter, messages)
        self.assertEqual([t["asset"] for t in trades], ["GBP/USD", "EUR/USD"])

    def test_against_real_daniel_fx_trade_adapter(self):
        # Uses the actual research adapter (present in this environment)
        # rather than a fake, to confirm the bridge's field names really
        # do match what a real Layer 3 adapter returns.
        research_repo = bridge.RESEARCH_REPO
        if not research_repo.exists():
            self.skipTest("research worktree not present in this environment")
        sys.path.insert(0, str(research_repo))
        sys.path.insert(0, str(research_repo / "research"))
        sys.path.insert(0, str(research_repo / "research" / "parser"))
        from adapters import daniel_fx_trade
        messages = _messages(
            (1, "2026-01-01T00:00:00"), (2, "2026-01-01T00:01:00"),
        )
        messages[0]["text"] = "GBP/CAD HIGH ⬆️ 15 MIN"
        messages[1]["text"] = "✅✅✅"
        trades = bridge._extract_backtestable_trades(daniel_fx_trade, messages)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["asset"], "GBP/CAD")
        self.assertEqual(trades[0]["direction"], "BUY")
        self.assertEqual(trades[0]["result"], "win")


if __name__ == "__main__":
    unittest.main()
