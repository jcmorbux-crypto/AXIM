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

    # UI-managed state (api/, core/telegram_channels.py,
    # core/telegram_listener.py) - deliberately separate from the
    # signals/recovery_events tables above, which are trade history, not
    # configuration.
    conn.execute("""
    CREATE TABLE IF NOT EXISTS ui_channels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id TEXT UNIQUE,
        username TEXT,
        title TEXT,
        kind TEXT,
        enabled INTEGER DEFAULT 0,
        last_signal_at TEXT,
        last_synced_at TEXT,
        created_at TEXT
    );
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS ui_control_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        paused INTEGER DEFAULT 0,
        emergency_stop INTEGER DEFAULT 0,
        updated_at TEXT
    );
    """)
    # Singleton row - every read/write targets id=1, created once here so
    # callers never have to special-case "no row yet".
    conn.execute("INSERT OR IGNORE INTO ui_control_state (id, paused, emergency_stop) VALUES (1, 0, 0)")

    # Key-value store for UI-editable money-management settings - values
    # are JSON-encoded so one table covers numbers/bools/strings alike.
    # risk_manager.py reads these dynamically (falling back to the static
    # .env-derived config/settings.py constant if a key was never set),
    # so a change here takes effect on the very next signal, no restart.
    conn.execute("""
    CREATE TABLE IF NOT EXISTS ui_settings (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at TEXT
    );
    """)

    # Singleton heartbeat row - core/telegram_listener.py writes this
    # periodically so the (separate-process) API/UI has something to show
    # for "is the browser/worker pool actually healthy right now" without
    # sharing memory with the listener process.
    conn.execute("""
    CREATE TABLE IF NOT EXISTS ui_listener_heartbeat (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        generation INTEGER,
        worker_count INTEGER,
        demo_mode_verified INTEGER,
        updated_at TEXT
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
def get_realized_pnl_since(since_iso):
    """Sum of profit_loss across every trade CLOSED (not just placed) since
    since_iso - a win contributes positively, a loss negatively, so this is
    the actual net realized result for the window, not a trade count.
    NULL profit_loss rows (never reached a terminal win/loss/draw) are
    excluded via the SUM's own NULL-skipping behavior. Returns 0.0 (not
    None) when there are no closed trades in the window, so callers can
    compare directly against a threshold without a None check."""
    conn = get_connection()
    row = conn.execute("""
        SELECT SUM(profit_loss) AS total FROM signals
        WHERE closed_at >= ? AND profit_loss IS NOT NULL
    """, (since_iso,)).fetchone()
    conn.close()
    return row["total"] if row["total"] is not None else 0.0


@timed("database")
def get_recent_signals(limit=25):
    """Most recent signals regardless of status - the dashboard's activity
    table. Unlike get_trades_between, not scoped to a time window or
    closed-only, so an in-flight or rejected/ignored signal shows up
    immediately rather than only once it resolves."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, asset, direction, timeframe, channel, execution_status, result, "
        "profit_loss, payout, received_at, opened_at, closed_at FROM signals "
        "ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


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


# ---------------------------------------------------------------------
# UI-managed channel allow-list (api/, core/telegram_channels.py,
# core/telegram_listener.py)
# ---------------------------------------------------------------------

@timed("database")
def upsert_channel(chat_id, username, title, kind):
    """Insert or refresh a dialog's identity (from a real Telethon sync) -
    never touches `enabled`, so re-syncing the dialog list never silently
    re-enables or disables a channel the operator already chose."""
    conn = get_connection()
    now = datetime.now().isoformat()
    conn.execute("""
        INSERT INTO ui_channels (chat_id, username, title, kind, enabled, last_synced_at, created_at)
        VALUES (?, ?, ?, ?, 0, ?, ?)
        ON CONFLICT(chat_id) DO UPDATE SET
            username = excluded.username,
            title = excluded.title,
            kind = excluded.kind,
            last_synced_at = excluded.last_synced_at
    """, (str(chat_id), username, title, kind, now, now))
    conn.commit()
    conn.close()


@timed("database")
def list_channels():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM ui_channels ORDER BY enabled DESC, title ASC").fetchall()
    conn.close()
    return [dict(row) for row in rows]


@timed("database")
def set_channel_enabled(channel_id, enabled):
    conn = get_connection()
    conn.execute("UPDATE ui_channels SET enabled = ? WHERE id = ?", (1 if enabled else 0, channel_id))
    conn.commit()
    conn.close()


@timed("database")
def get_enabled_channels():
    """The real, current allow-list - telegram_listener.py's source of
    truth once at least one row exists (see seed_channels_from_env)."""
    conn = get_connection()
    rows = conn.execute("SELECT * FROM ui_channels WHERE enabled = 1").fetchall()
    conn.close()
    return [dict(row) for row in rows]


@timed("database")
def seed_channels_from_env(watch_channels):
    """Populates ui_channels from the static .env WATCH_CHANNELS list, but
    ONLY if the table is completely empty - a one-time migration so
    switching to DB-backed channel control doesn't silently change which
    channels are followed on first upgrade. Each entry is seeded with
    chat_id=NULL (identity unknown until a real dialog sync matches it by
    username/title) - enabled=1 so behavior is preserved immediately."""
    conn = get_connection()
    count = conn.execute("SELECT COUNT(*) AS n FROM ui_channels").fetchone()["n"]
    if count > 0:
        conn.close()
        return
    now = datetime.now().isoformat()
    for entry in watch_channels:
        conn.execute("""
            INSERT INTO ui_channels (chat_id, username, title, kind, enabled, created_at)
            VALUES (NULL, ?, ?, 'unknown', 1, ?)
        """, (entry, entry, now))
    conn.commit()
    conn.close()


@timed("database")
def record_channel_signal_seen(chat_id=None, username=None, title=None):
    """Updates last_signal_at for whichever ui_channels row matches - by
    chat_id if known, else by username, else by title substring (mirrors
    telegram_listener.source_allowed's own matching logic, since a
    seeded-from-env row may not have a real chat_id yet)."""
    conn = get_connection()
    now = datetime.now().isoformat()
    if chat_id is not None:
        conn.execute("UPDATE ui_channels SET last_signal_at = ? WHERE chat_id = ?", (now, str(chat_id)))
    if username:
        conn.execute(
            "UPDATE ui_channels SET last_signal_at = ? WHERE username IS NOT NULL AND LOWER(username) = LOWER(?)",
            (now, username),
        )
    if title:
        conn.execute(
            "UPDATE ui_channels SET last_signal_at = ? WHERE title IS NOT NULL AND INSTR(LOWER(?), LOWER(title)) > 0",
            (now, title),
        )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------
# UI control state (pause/resume/emergency-stop) - api/, telegram_listener.py
# ---------------------------------------------------------------------

@timed("database")
def get_control_state():
    conn = get_connection()
    row = conn.execute("SELECT paused, emergency_stop, updated_at FROM ui_control_state WHERE id = 1").fetchone()
    conn.close()
    return {
        "paused": bool(row["paused"]),
        "emergency_stop": bool(row["emergency_stop"]),
        "updated_at": row["updated_at"],
    }


@timed("database")
def set_control_state(paused=None, emergency_stop=None):
    conn = get_connection()
    current = conn.execute("SELECT paused, emergency_stop FROM ui_control_state WHERE id = 1").fetchone()
    new_paused = current["paused"] if paused is None else (1 if paused else 0)
    new_emergency = current["emergency_stop"] if emergency_stop is None else (1 if emergency_stop else 0)
    conn.execute(
        "UPDATE ui_control_state SET paused = ?, emergency_stop = ?, updated_at = ? WHERE id = 1",
        (new_paused, new_emergency, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------
# UI-editable money-management settings - api/, risk_manager.py, trade_coordinator.py
# ---------------------------------------------------------------------

@timed("database")
def get_setting(key, default=None):
    """Returns the decoded value if this key was ever explicitly set via
    the UI, else `default` (the caller passes its own static config/
    settings.py constant as that default, so an unconfigured setting
    behaves exactly as it did before the UI existed)."""
    conn = get_connection()
    row = conn.execute("SELECT value FROM ui_settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    if row is None:
        return default
    return json.loads(row["value"])


@timed("database")
def set_setting(key, value):
    conn = get_connection()
    conn.execute("""
        INSERT INTO ui_settings (key, value, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
    """, (key, json.dumps(value), datetime.now().isoformat()))
    conn.commit()
    conn.close()


@timed("database")
def get_all_settings():
    conn = get_connection()
    rows = conn.execute("SELECT key, value, updated_at FROM ui_settings").fetchall()
    conn.close()
    return {row["key"]: json.loads(row["value"]) for row in rows}


@timed("database")
def get_lifetime_realized_pnl():
    """Sum of profit_loss across every closed trade, ever - used to
    compute "current bankroll" (starting_bankroll setting + this) for
    percentage-of-bankroll position sizing."""
    conn = get_connection()
    row = conn.execute("SELECT SUM(profit_loss) AS total FROM signals WHERE profit_loss IS NOT NULL").fetchone()
    conn.close()
    return row["total"] if row["total"] is not None else 0.0


# ---------------------------------------------------------------------
# Listener heartbeat - telegram_listener.py writes, api/ reads
# ---------------------------------------------------------------------

@timed("database")
def update_listener_heartbeat(generation, worker_count, demo_mode_verified):
    conn = get_connection()
    conn.execute("""
        UPDATE ui_listener_heartbeat
        SET generation = ?, worker_count = ?, demo_mode_verified = ?, updated_at = ?
        WHERE id = 1
    """, (generation, worker_count, 1 if demo_mode_verified else 0, datetime.now().isoformat()))
    if conn.total_changes == 0:
        conn.execute("""
            INSERT INTO ui_listener_heartbeat (id, generation, worker_count, demo_mode_verified, updated_at)
            VALUES (1, ?, ?, ?, ?)
        """, (generation, worker_count, 1 if demo_mode_verified else 0, datetime.now().isoformat()))
    conn.commit()
    conn.close()


@timed("database")
def get_listener_heartbeat():
    conn = get_connection()
    row = conn.execute("SELECT * FROM ui_listener_heartbeat WHERE id = 1").fetchone()
    conn.close()
    return dict(row) if row else None


if __name__ == "__main__":
    initialize_database()
    print("Database Ready")
