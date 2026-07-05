"""
AXIM Timeline Report - Full Observability

Reads every trade's persisted TradeTimeline (core/timeline.py:
signals.trade_timeline_json for the 10 named stages, signals.
category_timings_json for measured waiting/browser/database/logging
totals) and produces:

1. A per-trade timeline view (stage-to-stage deltas in ms).
2. Aggregate statistics (count, min, max, avg, P50, P95, P99) across every
   trade with data, for each stage-to-stage transition and for each of 5
   time categories - waiting/browser/database/logging are measured
   directly; "active" is the residual (total duration minus the sum of the
   other four), not separately instrumented line-by-line - see
   core/timeline.py's module docstring for why that's the right way to do
   this kind of breakdown, not a shortcut.

Percentile methodology, spelled out because "P95" can silently mean several
different things: linear interpolation between closest ranks (the same
method numpy/Excel default to). For N sorted values, P_k's position is
index = (k/100) * (N-1); if that isn't a whole number, interpolate between
its two neighboring values.

Read-only. Does not execute trades, does not import trade_coordinator/
pocket_executor/risk_manager.

Run: python core/timeline_report.py [--limit N]
"""
import json
import sys
from datetime import datetime
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CORE_DIR))

import database
from timeline import STAGES, MEASURED_CATEGORIES

CATEGORY_DISPLAY_ORDER = (*MEASURED_CATEGORIES, "active", "total")


def _percentile(sorted_values, pct):
    """Linear interpolation between closest ranks (the R-7/numpy/Excel
    default method). `sorted_values` must already be sorted ascending."""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (pct / 100) * (len(sorted_values) - 1)
    lo = int(k)
    hi = min(lo + 1, len(sorted_values) - 1)
    if lo == hi:
        return sorted_values[lo]
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (k - lo)


def _stats(values):
    if not values:
        return {"n": 0}
    s = sorted(values)
    return {
        "n": len(s),
        "min": s[0],
        "max": s[-1],
        "avg": sum(s) / len(s),
        "p50": _percentile(s, 50),
        "p95": _percentile(s, 95),
        "p99": _percentile(s, 99),
    }


def _fetch_trades_with_timeline(limit=None):
    conn = database.get_connection()
    query = (
        "SELECT id, asset, direction, execution_status, result, "
        "trade_timeline_json, category_timings_json FROM signals "
        "WHERE trade_timeline_json IS NOT NULL ORDER BY id DESC"
    )
    if limit:
        query += f" LIMIT {int(limit)}"
    rows = conn.execute(query).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def parse_trade(row):
    stages_raw = json.loads(row["trade_timeline_json"]) if row["trade_timeline_json"] else {}
    categories = json.loads(row["category_timings_json"]) if row["category_timings_json"] else {}
    stages = {}
    for stage in STAGES:
        if stage in stages_raw:
            stages[stage] = datetime.fromisoformat(stages_raw[stage])
    return {
        "trade_id": row["id"], "asset": row["asset"], "direction": row["direction"],
        "execution_status": row["execution_status"], "result": row["result"],
        "stages": stages, "categories": categories,
    }


def stage_deltas(trade):
    """ms between each consecutive pair of PRESENT stages, in canonical
    STAGES order - not just adjacent list-index pairs, since a trade can
    skip stages entirely (e.g. rejected before ever reaching
    asset_selected)."""
    present = [(s, trade["stages"][s]) for s in STAGES if s in trade["stages"]]
    deltas = {}
    for (s1, t1), (s2, t2) in zip(present, present[1:]):
        deltas[f"{s1}->{s2}"] = (t2 - t1).total_seconds() * 1000
    return deltas


def total_duration_ms(trade):
    present = [trade["stages"][s] for s in STAGES if s in trade["stages"]]
    if len(present) < 2:
        return None
    return (present[-1] - present[0]).total_seconds() * 1000


def category_breakdown(trade):
    """The 4 measured categories plus "active" (the residual) and "total".
    None if total duration isn't known (fewer than 2 stages present)."""
    total = total_duration_ms(trade)
    if total is None:
        return None
    cats = {c: trade["categories"].get(c, 0.0) for c in MEASURED_CATEGORIES}
    cats["active"] = max(0.0, total - sum(cats.values()))
    cats["total"] = total
    return cats


def print_per_trade_timeline(trades, limit=10):
    print(f"=== Per-trade timeline (most recent {min(limit, len(trades))} of {len(trades)}) ===\n")
    for trade in trades[:limit]:
        print(f"Trade {trade['trade_id']} | {trade['asset']} {trade['direction']} | "
              f"status={trade['execution_status']} result={trade['result']}")
        for name, ms in stage_deltas(trade).items():
            print(f"    {name:40s} {ms:9.1f} ms")
        cats = category_breakdown(trade)
        if cats:
            print("    -- categories --")
            for name in CATEGORY_DISPLAY_ORDER:
                print(f"    {name:40s} {cats[name]:9.1f} ms")
        print()


def aggregate_report(trades):
    transition_values = {}
    category_values = {c: [] for c in CATEGORY_DISPLAY_ORDER}

    for trade in trades:
        for name, ms in stage_deltas(trade).items():
            transition_values.setdefault(name, []).append(ms)
        cats = category_breakdown(trade)
        if cats:
            for name, ms in cats.items():
                category_values[name].append(ms)

    return {
        "sample_size": len(trades),
        "stage_transitions": {name: _stats(vals) for name, vals in transition_values.items()},
        "categories": {name: _stats(vals) for name, vals in category_values.items()},
    }


def generate_report(limit=None):
    rows = _fetch_trades_with_timeline(limit=limit)
    trades = [parse_trade(r) for r in rows]
    return trades, aggregate_report(trades)


def _print_stats_line(label, stats, width):
    if stats["n"] == 0:
        return
    print(
        f"{label:{width}s} n={stats['n']:4d} avg={stats['avg']:9.1f} "
        f"p50={stats['p50']:9.1f} p95={stats['p95']:9.1f} p99={stats['p99']:9.1f} "
        f"min={stats['min']:9.1f} max={stats['max']:9.1f}"
    )


if __name__ == "__main__":
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    all_trades, report = generate_report(limit=limit)
    print_per_trade_timeline(all_trades, limit=10)

    print("=== Aggregate statistics (ms) ===\n")
    print(f"Sample size: {report['sample_size']} trade(s) with timeline data\n")

    print("-- Stage transitions --")
    for name, stats in report["stage_transitions"].items():
        _print_stats_line(name, stats, 40)

    print("\n-- Time categories (waiting/browser/database/logging measured "
          "directly; active = total - sum(others)) --")
    for name in CATEGORY_DISPLAY_ORDER:
        _print_stats_line(name, report["categories"][name], 12)
