"""
AXIM Research Module - Source Profiler

Analyzes data collected by core/source_observer.py: signal cadence, asset
mix, expiry distribution, lead-time/timing behavior, duplicate patterns,
and OTC usage - then compares this against AXIM's own measured execution
latency to suggest what level of integration (warm browser, preselected
asset, multiple tabs, websocket/API) the source's timing actually demands.

Read-only analysis. Does not execute trades, does not import
trade_coordinator/pocket_executor/risk_manager.

Run: python core/source_profiler.py
"""
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CORE_DIR.parent
sys.path.insert(0, str(CORE_DIR))

from source_observer import get_connection, SOURCE_USERNAME

_IMMEDIATE_RE = re.compile(r"\bnow\b|\bimmediate", re.IGNORECASE)
_DELAYED_RE = re.compile(r"\bwait\b|\bnext candle\b|\d+\s*(?:sec|min)", re.IGNORECASE)


def _fetch_all(source=None):
    conn = get_connection()
    if source:
        rows = conn.execute(
            "SELECT * FROM source_observations WHERE source = ? ORDER BY received_at ASC",
            (source,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM source_observations ORDER BY received_at ASC"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def signals_per_hour(rows):
    if len(rows) < 2:
        return None
    first = datetime.fromisoformat(rows[0]["received_at"])
    last = datetime.fromisoformat(rows[-1]["received_at"])
    span_hours = max((last - first).total_seconds() / 3600, 1e-9)
    return len(rows) / span_hours


def common_assets(rows, top=10):
    counts = Counter(r["asset"] for r in rows if r["asset"])
    return counts.most_common(top)


def expiry_distribution(rows):
    return dict(Counter(r["expiry"] for r in rows if r["expiry"]))


def otc_ratio(rows):
    assets = [r["asset"] for r in rows if r["asset"]]
    if not assets:
        return None
    otc = sum(1 for a in assets if "OTC" in a.upper())
    return {"otc": otc, "non_otc": len(assets) - otc, "otc_fraction": otc / len(assets)}


def entry_timing_classification(rows):
    """Best-effort: classifies each observation's flagged timing language as
    immediate vs delayed vs unspecified. Accuracy depends entirely on how
    much temporal information the source's own message text contains - this
    is not a precise lead-time measurement."""
    immediate, delayed, unknown = 0, 0, 0
    for r in rows:
        tl = r.get("timing_language") or ""
        if _IMMEDIATE_RE.search(tl):
            immediate += 1
        elif _DELAYED_RE.search(tl):
            delayed += 1
        else:
            unknown += 1
    return {"immediate": immediate, "delayed": delayed, "unspecified": unknown}


def duplicate_patterns(rows, window_seconds=120):
    """Counts how often the same (asset, direction, expiry) repeats within
    window_seconds of an earlier observation. Descriptive only - this
    module never rejects or deduplicates anything.

    Only considers rows with an actually-parsed asset: unparsed status
    messages (e.g. "Scanning Market...", "Opening position...") all share
    the same (None, None, None) key, which would otherwise look like
    massive duplication when it's really just unrelated housekeeping text."""
    signal_rows = [r for r in rows if r["asset"]]
    seen = []
    duplicate_count = 0
    for r in signal_rows:
        key = (r["asset"], r["direction"], r["expiry"])
        ts = datetime.fromisoformat(r["received_at"])
        for prev_key, prev_ts in seen:
            if prev_key == key and (ts - prev_ts).total_seconds() <= window_seconds:
                duplicate_count += 1
                break
        seen.append((key, ts))
    return {
        "duplicate_count": duplicate_count,
        "total_signals_considered": len(signal_rows),
        "total_observations": len(rows),
        "window_seconds": window_seconds,
    }


def parse_success_rate(rows):
    if not rows:
        return None
    parsed = sum(1 for r in rows if r["parsed_successfully"])
    return parsed / len(rows)


def generate_report(source=None):
    rows = _fetch_all(source or SOURCE_USERNAME)
    return {
        "source": source or SOURCE_USERNAME,
        "total_observations": len(rows),
        "signals_per_hour": signals_per_hour(rows),
        "common_assets": common_assets(rows),
        "expiry_distribution": expiry_distribution(rows),
        "otc_ratio": otc_ratio(rows),
        "entry_timing": entry_timing_classification(rows),
        "duplicate_patterns": duplicate_patterns(rows),
        "parse_success_rate": parse_success_rate(rows),
    }


def axim_execution_latency():
    """AXIM's own measured signal-received -> trade-opened latency, from
    real trades in the main signals table (Phase 3/4 data) - not from this
    observer, which never executes anything."""
    sys.path.insert(0, str(PROJECT_ROOT / "core"))
    import database

    conn = database.get_connection()
    rows = conn.execute(
        "SELECT received_at, opened_at FROM signals WHERE opened_at IS NOT NULL"
    ).fetchall()
    conn.close()

    deltas = []
    for r in rows:
        try:
            received = datetime.fromisoformat(r["received_at"])
            opened = datetime.fromisoformat(r["opened_at"])
            deltas.append((opened - received).total_seconds())
        except (ValueError, TypeError):
            continue

    if not deltas:
        return None

    return {
        "sample_count": len(deltas),
        "min_seconds": min(deltas),
        "max_seconds": max(deltas),
        "avg_seconds": sum(deltas) / len(deltas),
    }


def latency_recommendation(source=None):
    rows = _fetch_all(source or SOURCE_USERNAME)
    timing = entry_timing_classification(rows)
    axim_latency = axim_execution_latency()

    notes = []
    if axim_latency is None:
        notes.append(
            "No AXIM execution latency samples yet (no trade has opened_at "
            "recorded) - run some ARMED demo trades to get real data here."
        )
    else:
        notes.append(
            f"AXIM measured execution latency: avg {axim_latency['avg_seconds']:.2f}s "
            f"(min {axim_latency['min_seconds']:.2f}s, max {axim_latency['max_seconds']:.2f}s) "
            f"across {axim_latency['sample_count']} trade(s)."
        )

    classified = timing["immediate"] + timing["delayed"]
    if classified == 0:
        notes.append(
            f"This source's messages don't contain enough explicit timing "
            f"language to estimate lead time ({timing['unspecified']} unclassified "
            f"of {len(rows)} total) - the recommendation below is a cautious "
            f"default, not a measurement."
        )
        recommendation = (
            "Insufficient timing data from this source to justify anything beyond "
            "the current warm-browser architecture (Phase 4). Keep observing and "
            "re-run this report once more observations accumulate."
        )
    elif timing["immediate"] > timing["delayed"]:
        recommendation = (
            "This source appears to favor immediate ('NOW'-style) entries with "
            "little lead time. A warm browser alone (current AXIM architecture, "
            "measured ~1-2s warm / ~2-5s on an asset change) may not reliably win "
            "the race. Consider: confirming the asset cache pre-selection is "
            "actually hitting for this source's assets, and evaluate whether "
            "multiple warm tabs or websocket/API-level integration are needed."
        )
    else:
        recommendation = (
            "This source appears to give some lead time before entries (delayed/"
            "countdown language present). AXIM's current warm-browser execution "
            "(~1-2s warm, ~2-5s on an asset change) is likely sufficient, provided "
            "the source's actual lead time exceeds those figures - re-check as "
            "more data accumulates."
        )

    return {
        "axim_latency": axim_latency,
        "source_timing_classification": timing,
        "recommendation": recommendation,
        "notes": notes,
    }


if __name__ == "__main__":
    print("=== Source Behavior Report ===")
    print(json.dumps(generate_report(), indent=2, default=str))
    print("\n=== Latency Comparison & Recommendation ===")
    print(json.dumps(latency_recommendation(), indent=2, default=str))
