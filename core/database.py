import json
import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent))
from timeline import timed

DB_FILE = Path("data/axim.db")

_NEW_COLUMNS = {
    "execution_status": "TEXT",
    "opened_at": "TEXT",
    "closed_at": "TEXT",
    "profit_loss": "REAL",
    "screenshot_paths": "TEXT",
    "latency_checkpoints_json": "TEXT",
    "outcome_detection_ms": "REAL",
    "trade_timeline_json": "TEXT",
    "category_timings_json": "TEXT",
}


def get_connection():
    DB_FILE.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def _migrate_schema(conn):
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(signals)")}
    for column, sql_type in _NEW_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE signals ADD COLUMN {column} {sql_type}")


def initialize_database():
    conn = get_connection()

    conn.execute("""
    CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message_id INTEGER,
        channel TEXT,
        sender TEXT,
        asset TEXT,
        direction TEXT,
        timeframe TEXT,
        payout INTEGER,
        trade_amount REAL,
        message TEXT,
        received_at TEXT,
        executed INTEGER DEFAULT 0,
        execution_time TEXT,
        result TEXT,
        profit REAL DEFAULT 0,
        execution_status TEXT,
        opened_at TEXT,
        closed_at TEXT,
        profit_loss REAL,
        screenshot_paths TEXT,
        latency_checkpoints_json TEXT,
        outcome_detection_ms REAL,
        trade_timeline_json TEXT,
        category_timings_json TEXT
    );
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS recovery_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT,
        outcome TEXT,
        detail TEXT,
        created_at TEXT
    );
    """)

    _migrate_schema(conn)

    conn.commit()
    conn.close()


@timed("database")
def record_signal_received(signal, source=None, sender=None, message_id=None):
    from trade_lifecycle import TradeStatus

    conn = get_connection()
    cursor = conn.execute("""
    INSERT INTO signals (
        message_id, channel, sender, asset, direction, timeframe,
        trade_amount, message, received_at, executed, execution_status
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
    """, (
        message_id,
        source,
        sender,
        signal["asset"],
        signal["direction"],
        signal["expiry"],
        signal.get("trade_amount"),
        signal["raw_message"],
        datetime.now().isoformat(),
        TradeStatus.SIGNAL_RECEIVED.value,
    ))
    conn.commit()
    trade_id = cursor.lastrowid
    conn.close()
    return trade_id


_UPDATABLE_FIELDS = {
    "trade_amount", "payout", "opened_at", "closed_at", "result",
    "profit_loss", "screenshot_paths", "execution_time", "executed",
}


@timed("database")
def update_trade_status(trade_id, status, **fields):
    from trade_lifecycle import TradeStatus

    status_value = status.value if isinstance(status, TradeStatus) else status

    set_clauses = ["execution_status = ?"]
    params = [status_value]

    for key, value in fields.items():
        if key not in _UPDATABLE_FIELDS:
            raise ValueError(f"Unknown trade field: {key!r}")
        if key == "screenshot_paths" and isinstance(value, (list, tuple)):
            value = json.dumps(list(value))
        set_clauses.append(f"{key} = ?")
        params.append(value)

    params.append(trade_id)

    conn = get_connection()
    conn.execute(
        f"UPDATE signals SET {', '.join(set_clauses)} WHERE id = ?",
        params,
    )
    conn.commit()
    conn.close()


@timed("database")
def record_outcome_latency(trade_id, detection_overhead_ms):
    """Persists outcome-detection overhead: wall-clock time spent in
    wait_for_trade_result beyond the trade's own contractual expiry
    duration. This isolates AXIM's detection/polling lag from the
    deliberate expiry wait, which is not itself a latency cost to optimize."""
    conn = get_connection()
    conn.execute(
        "UPDATE signals SET outcome_detection_ms = ? WHERE id = ?",
        (detection_overhead_ms, trade_id),
    )
    conn.commit()
    conn.close()


@timed("database")
def record_trade_timeline(trade_id, stage_timestamps, category_totals_ms):
    """Merges (does not overwrite) a TradeTimeline's stage timestamps and
    category totals into whatever is already persisted for this trade_id.
    A trade's timeline is typically written in two passes - once from
    prepare_trade, once later from track_outcome, sometimes from a
    different OS process entirely (a trade resumed by core/recovery.py
    after a restart) - so this always reads the existing JSON first and
    combines rather than clobbers it. Category totals are summed (each
    pass measures its own portion of the work), stage timestamps are
    merged key-by-key (each stage is only ever written once, by whichever
    pass reaches it)."""
    conn = get_connection()
    row = conn.execute(
        "SELECT trade_timeline_json, category_timings_json FROM signals WHERE id = ?", (trade_id,)
    ).fetchone()

    existing_stages = json.loads(row["trade_timeline_json"]) if row and row["trade_timeline_json"] else {}
    existing_categories = json.loads(row["category_timings_json"]) if row and row["category_timings_json"] else {}

    merged_stages = {**existing_stages, **stage_timestamps}
    merged_categories = dict(existing_categories)
    for category, ms in category_totals_ms.items():
        merged_categories[category] = merged_categories.get(category, 0.0) + ms

    conn.execute(
        "UPDATE signals SET trade_timeline_json = ?, category_timings_json = ? WHERE id = ?",
        (json.dumps(merged_stages), json.dumps(merged_categories), trade_id),
    )
    conn.commit()
    conn.close()


@timed("database")
def record_recovery_event(event_type, outcome, detail=None):
    """Structured log of an automatic-recovery attempt (browser reconnect,
    worker pool rebuild, process-level restart, abandoned-trade resume) and
    whether it succeeded - the basis for a real "recovery rate" metric,
    rather than inferring it from unstructured log text."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO recovery_events (event_type, outcome, detail, created_at) VALUES (?, ?, ?, ?)",
        (event_type, outcome, detail, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


@timed("database")
def get_recovery_event_stats():
    conn = get_connection()
    rows = conn.execute("SELECT event_type, outcome, COUNT(*) AS n FROM recovery_events GROUP BY event_type, outcome").fetchall()
    conn.close()
    return [dict(r) for r in rows]


@timed("database")
def append_screenshot_path(trade_id, path):
    conn = get_connection()
    row = conn.execute("SELECT screenshot_paths FROM signals WHERE id = ?", (trade_id,)).fetchone()
    existing = json.loads(row["screenshot_paths"]) if row and row["screenshot_paths"] else []
    existing.append(str(path))
    conn.execute(
        "UPDATE signals SET screenshot_paths = ? WHERE id = ?",
        (json.dumps(existing), trade_id),
    )
    conn.commit()
    conn.close()


@timed("database")
def find_recent_duplicate(asset, direction, expiry, window_seconds, exclude_id=None):
    cutoff = (datetime.now() - timedelta(seconds=window_seconds)).isoformat()
    conn = get_connection()
    row = conn.execute("""
        SELECT id FROM signals
        WHERE asset = ? AND direction = ? AND timeframe = ? AND received_at >= ?
          AND (? IS NULL OR id != ?)
        ORDER BY received_at DESC
        LIMIT 1
    """, (asset, direction, expiry, cutoff, exclude_id, exclude_id)).fetchone()
    conn.close()
    return row["id"] if row else None


@timed("database")
def count_trades_since(since_iso):
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM signals WHERE received_at >= ?", (since_iso,)
    ).fetchone()
    conn.close()
    return row["n"]


@timed("database")
def get_recent_results(limit):
    conn = get_connection()
    rows = conn.execute("""
        SELECT result FROM signals
        WHERE result IS NOT NULL
        ORDER BY closed_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [row["result"] for row in rows]


@timed("database")
def get_last_loss_time():
    conn = get_connection()
    row = conn.execute("""
        SELECT closed_at FROM signals
        WHERE result = 'loss'
        ORDER BY closed_at DESC
        LIMIT 1
    """).fetchone()
    conn.close()
    return row["closed_at"] if row else None


@timed("database")
def get_open_trades():
    conn = get_connection()
    rows = conn.execute("""
        SELECT * FROM signals
        WHERE execution_status IN ('trade_clicked', 'trade_opened')
        ORDER BY opened_at ASC
    """).fetchall()
    conn.close()
    return [dict(row) for row in rows]


@timed("database")
def get_trades_between(start_iso, end_iso, closed_only=False):
    conn = get_connection()
    if closed_only:
        rows = conn.execute("""
            SELECT * FROM signals
            WHERE closed_at IS NOT NULL AND closed_at >= ? AND closed_at <= ?
            ORDER BY closed_at ASC
        """, (start_iso, end_iso)).fetchall()
    else:
        rows = conn.execute("""
            SELECT * FROM signals
            WHERE received_at >= ? AND received_at <= ?
            ORDER BY received_at ASC
        """, (start_iso, end_iso)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


@timed("database")
def count_with_result_prefix(prefix, since_iso=None):
    conn = get_connection()
    if since_iso:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM signals WHERE result LIKE ? AND received_at >= ?",
            (f"{prefix}%", since_iso),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM signals WHERE result LIKE ?", (f"{prefix}%",)
        ).fetchone()
    conn.close()
    return row["n"]


if __name__ == "__main__":
    initialize_database()
    print("Database Ready")
