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
    "session_id": "INTEGER",
}

# source_type: "passive" (default - existing behavior) | "bot_command"
# (docs/AXIM_SESSION_ARCHITECTURE.md's interactive-bot workflow) | "group"
# | "manual_review". The bot_command-only fields are simply unused/NULL
# for every other source_type rather than living in a separate table.
_NEW_CHANNEL_COLUMNS = {
    "source_type": "TEXT DEFAULT 'passive'",
    "priority": "INTEGER DEFAULT 0",
    "trigger_command": "TEXT",
    "command_wait_for_result": "INTEGER DEFAULT 1",
    "max_requests_per_session": "INTEGER",
}

_NEW_SESSION_COLUMNS = {
    "risk_profile_id": "INTEGER",
    "current_martingale_step": "INTEGER DEFAULT 0",
    "vaulted_amount": "REAL DEFAULT 0",
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

    control_columns = {row["name"] for row in conn.execute("PRAGMA table_info(ui_control_state)")}
    if "test_mode" not in control_columns:
        conn.execute("ALTER TABLE ui_control_state ADD COLUMN test_mode INTEGER DEFAULT 0")

    channel_columns = {row["name"] for row in conn.execute("PRAGMA table_info(ui_channels)")}
    for column, sql_type in _NEW_CHANNEL_COLUMNS.items():
        if column not in channel_columns:
            conn.execute(f"ALTER TABLE ui_channels ADD COLUMN {column} {sql_type}")

    session_columns = {row["name"] for row in conn.execute("PRAGMA table_info(trading_sessions)")}
    for column, sql_type in _NEW_SESSION_COLUMNS.items():
        if column not in session_columns:
            conn.execute(f"ALTER TABLE trading_sessions ADD COLUMN {column} {sql_type}")


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
        created_at TEXT,
        source_type TEXT DEFAULT 'passive',
        priority INTEGER DEFAULT 0,
        trigger_command TEXT,
        command_wait_for_result INTEGER DEFAULT 1,
        max_requests_per_session INTEGER
    );
    """)

    # Raw incoming Telegram messages, captured regardless of whether they
    # parsed as a tradeable signal - core/telegram_listener.py writes one
    # row per message received. Distinct from `signals` (which only exists
    # for messages that reached record_signal_received) - this is what
    # powers "last message received" and the Signal Inspector's recent-
    # messages viewer, including messages the parser rejected outright.
    conn.execute("""
    CREATE TABLE IF NOT EXISTS channel_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id TEXT,
        username TEXT,
        title TEXT,
        message_text TEXT,
        received_at TEXT
    );
    """)

    # Per-channel custom parsing override - a simple regex substitution
    # applied to the raw message BEFORE it reaches the normal
    # parsers/signal_parser.parse_signal(), rather than a second parser
    # implementation. NULL channel_id means "no per-channel rule saved yet"
    # is simply "no row" - there is no global/default rule concept here.
    conn.execute("""
    CREATE TABLE IF NOT EXISTS signal_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel_id INTEGER NOT NULL,
        rule_name TEXT,
        find_pattern TEXT NOT NULL,
        replace_with TEXT NOT NULL,
        enabled INTEGER DEFAULT 1,
        created_at TEXT
    );
    """)

    # Encrypted-at-rest Telegram API credentials (core/secrets_store.py) -
    # lets the operator enter API ID/Hash/phone through the UI instead of
    # editing .env. Singleton row; falls back to .env (config/settings.py)
    # if this table is empty, so existing installs don't regress.
    conn.execute("""
    CREATE TABLE IF NOT EXISTS telegram_credentials (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        api_id_encrypted TEXT,
        api_hash_encrypted TEXT,
        phone_encrypted TEXT,
        updated_at TEXT
    );
    """)

    # Saved "Start New Session" templates (docs/AXIM_SESSION_ARCHITECTURE.md)
    # - channel_ids_json is a JSON list of ui_channels.id values.
    conn.execute("""
    CREATE TABLE IF NOT EXISTS session_profiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        channel_ids_json TEXT NOT NULL,
        profit_target REAL DEFAULT 0,
        loss_limit REAL DEFAULT 0,
        max_trades INTEGER DEFAULT 0,
        require_confirmation INTEGER DEFAULT 0,
        created_at TEXT
    );
    """)

    # One row per actual session run. Only one row may have status='active'
    # at a time (enforced in start_trading_session, not a DB constraint -
    # SQLite has no partial unique index short of a trigger, and the
    # application is single-process anyway). trades_count/realized_pnl are
    # the session-scoped counters core/session_manager.py checks against
    # profit_target/loss_limit/max_trades before/after every trade.
    conn.execute("""
    CREATE TABLE IF NOT EXISTS trading_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id INTEGER,
        name TEXT,
        channel_ids_json TEXT NOT NULL,
        account_mode TEXT,
        profit_target REAL DEFAULT 0,
        loss_limit REAL DEFAULT 0,
        max_trades INTEGER DEFAULT 0,
        require_confirmation INTEGER DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'active',
        trades_count INTEGER DEFAULT 0,
        realized_pnl REAL DEFAULT 0,
        started_at TEXT,
        ended_at TEXT,
        stop_reason TEXT,
        risk_profile_id INTEGER,
        current_martingale_step INTEGER DEFAULT 0,
        vaulted_amount REAL DEFAULT 0
    );
    """)

    # Risk Engine (docs/AXIM_APP_PLAN.md Phase 4) - risk_profiles is the
    # unlimited, copyable/exportable sizing+limits profile; the three
    # settings tables are 1:1 sub-configs always created alongside a
    # profile (see create_risk_profile) so callers never have to
    # special-case "no martingale row yet". is_template=1 rows are the
    # 28 starter templates - read-only in the API layer, meant to be
    # duplicated before editing.
    conn.execute("""
    CREATE TABLE IF NOT EXISTS risk_profiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT,
        is_template INTEGER DEFAULT 0,
        bankroll REAL DEFAULT 0,
        sizing_mode TEXT DEFAULT 'fixed',
        fixed_amount REAL DEFAULT 1,
        percent_of_bankroll REAL DEFAULT 1,
        kelly_win_rate_estimate REAL,
        kelly_payout_estimate REAL,
        kelly_fraction_multiplier REAL DEFAULT 0.5,
        max_trade_amount REAL DEFAULT 0,
        max_daily_loss REAL DEFAULT 0,
        max_session_loss REAL DEFAULT 0,
        profit_target REAL DEFAULT 0,
        max_trades INTEGER DEFAULT 0,
        live_allowed INTEGER DEFAULT 0,
        created_at TEXT,
        updated_at TEXT
    );
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS martingale_settings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        risk_profile_id INTEGER UNIQUE NOT NULL,
        enabled INTEGER DEFAULT 0,
        max_steps INTEGER DEFAULT 0,
        multiplier REAL DEFAULT 2.0,
        custom_ladder_json TEXT,
        reset_after_win INTEGER DEFAULT 1,
        reset_after_session INTEGER DEFAULT 1,
        max_total_exposure REAL DEFAULT 0,
        confidence_threshold REAL,
        same_asset_only INTEGER DEFAULT 0,
        same_source_only INTEGER DEFAULT 0
    );
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS compounding_settings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        risk_profile_id INTEGER UNIQUE NOT NULL,
        mode TEXT DEFAULT 'disabled',
        base_risk_percent REAL DEFAULT 2.0,
        steps_json TEXT,
        drawdown_reset_percent REAL DEFAULT 0,
        max_risk_percent REAL DEFAULT 0,
        min_risk_percent REAL DEFAULT 0
    );
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS profit_vault_settings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        risk_profile_id INTEGER UNIQUE NOT NULL,
        enabled INTEGER DEFAULT 0,
        vault_percent REAL DEFAULT 0,
        trigger_event TEXT DEFAULT 'every_winning_session',
        milestone_amount REAL DEFAULT 0
    );
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS ui_control_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        paused INTEGER DEFAULT 0,
        emergency_stop INTEGER DEFAULT 0,
        test_mode INTEGER DEFAULT 0,
        updated_at TEXT
    );
    """)
    # Adds any columns missing on a pre-existing table (e.g. test_mode on a
    # DB created before it existed) BEFORE the seed INSERT below references
    # them - a fresh CREATE TABLE IF NOT EXISTS is a no-op against an
    # existing table, so this can't be deferred to the end of the function.
    _migrate_schema(conn)
    # Singleton row - every read/write targets id=1, created once here so
    # callers never have to special-case "no row yet".
    conn.execute("INSERT OR IGNORE INTO ui_control_state (id, paused, emergency_stop, test_mode) VALUES (1, 0, 0, 0)")

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

    # Singleton "Test Trade" request queue (Broker page) - the API process
    # never imports/calls the trading engine directly (see docs/AXIM_APP_PLAN.md's
    # architecture notes), so a manual test trade is requested here and
    # core/telegram_listener.py's own poll loop picks it up and runs it
    # through the SAME real coordinator/worker_pool already running in
    # that process - not a second, API-side execution path.
    conn.execute("""
    CREATE TABLE IF NOT EXISTS pending_test_trade (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        status TEXT DEFAULT 'none',
        requested_by TEXT,
        requested_at TEXT,
        result_json TEXT,
        completed_at TEXT
    );
    """)
    conn.execute("INSERT OR IGNORE INTO pending_test_trade (id, status) VALUES (1, 'none')")

    # Auth/access-control layer (docs/AXIM_APP_PLAN.md) - who may log into
    # the control UI at all, separate from the single shared Telegram/
    # Pocket Option trading connection every user currently sees. role is
    # the in-app permission level (owner/admin/user/free_user/trial_user/
    # disabled_user); access_tier is the plan/tier assignment (owner/
    # internal/free_beta/trial/basic/pro/elite/suspended) - kept ready for
    # a future Stripe integration without being wired to one yet;
    # access_state is the actual current usability of the account (active/
    # free_access/trial/pending_approval/expired/suspended/disabled).
    conn.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'user',
        access_tier TEXT NOT NULL DEFAULT 'trial',
        access_state TEXT NOT NULL DEFAULT 'pending_approval',
        trial_expires_at TEXT,
        demo_only_forced INTEGER DEFAULT 0,
        live_trading_allowed INTEGER DEFAULT 0,
        created_at TEXT,
        last_login_at TEXT
    );
    """)

    # token_hash only - the raw bearer token lives in the browser's cookie
    # and is never persisted, same reasoning as password_hash: a DB read
    # alone can't be replayed as a valid session (see core/auth.py).
    conn.execute("""
    CREATE TABLE IF NOT EXISTS auth_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        token_hash TEXT UNIQUE NOT NULL,
        created_at TEXT,
        last_seen_at TEXT,
        expires_at TEXT
    );
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS admin_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        actor_user_id INTEGER,
        target_user_id INTEGER,
        action TEXT NOT NULL,
        detail TEXT,
        created_at TEXT
    );
    """)

    conn.commit()
    conn.close()


@timed("database")
def record_signal_received(signal, source=None, sender=None, message_id=None, session_id=None):
    from trade_lifecycle import TradeStatus

    conn = get_connection()
    cursor = conn.execute("""
    INSERT INTO signals (
        message_id, channel, sender, asset, direction, timeframe,
        trade_amount, message, received_at, executed, execution_status, session_id
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
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
        session_id,
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
        "profit_loss, payout, received_at, opened_at, closed_at, trade_amount, session_id FROM signals "
        "ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


@timed("database")
def get_signal_detail(trade_id):
    """Full detail for the Trade Center's trade-detail view: raw message,
    parsed fields, the real execution timeline (stage -> timestamp, from
    core/timeline.py's TradeTimeline.persist), category timings, result,
    and - if this trade belonged to a session - the session's name/risk
    profile. Money-management/martingale-step display is honestly scoped:
    trade_amount is the real dollar figure actually used, but the
    martingale STEP at the time of THIS specific trade isn't separately
    recorded, only the session's current step - the caller should label
    it as such, not claim per-trade historical precision that doesn't
    exist."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM signals WHERE id = ?", (trade_id,)).fetchone()
    conn.close()
    if row is None:
        return None
    detail = dict(row)
    detail["trade_timeline"] = json.loads(detail["trade_timeline_json"]) if detail["trade_timeline_json"] else None
    detail["category_timings"] = json.loads(detail["category_timings_json"]) if detail["category_timings_json"] else None
    detail["screenshots"] = json.loads(detail["screenshot_paths"]) if detail["screenshot_paths"] else []
    if detail["session_id"] is not None:
        detail["session"] = get_trading_session(detail["session_id"])
    else:
        detail["session"] = None
    return detail


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


@timed("database")
def get_channel_performance(title):
    """Win rate / P&L for one channel, matched by title - signals.channel
    stores the chat_title trade_coordinator.handle_signal() was called
    with (source=chat_title), not a foreign key to ui_channels.id, so
    matching by title is the same join every other channel-scoped query
    in this codebase already relies on."""
    conn = get_connection()
    row = conn.execute("""
        SELECT
            COUNT(*) AS total_closed,
            SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN result = 'loss' THEN 1 ELSE 0 END) AS losses,
            SUM(profit_loss) AS profit_loss
        FROM signals
        WHERE channel = ? AND execution_status IN ('result_win', 'result_loss', 'result_draw')
    """, (title,)).fetchone()
    conn.close()
    total = row["total_closed"] or 0
    wins = row["wins"] or 0
    return {
        "total_closed": total,
        "wins": wins,
        "losses": row["losses"] or 0,
        "win_rate": (wins / total) if total > 0 else None,
        "profit_loss": row["profit_loss"] if row["profit_loss"] is not None else 0.0,
    }


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


@timed("database")
def find_channel(chat_id=None, username=None, title=None):
    """Same match precedence as record_channel_signal_seen (chat_id, then
    username, then title substring) - returns the first matching
    ui_channels row as a dict, or None."""
    conn = get_connection()
    row = None
    if chat_id is not None:
        row = conn.execute("SELECT * FROM ui_channels WHERE chat_id = ?", (str(chat_id),)).fetchone()
    if row is None and username:
        row = conn.execute(
            "SELECT * FROM ui_channels WHERE username IS NOT NULL AND LOWER(username) = LOWER(?)", (username,)
        ).fetchone()
    if row is None and title:
        row = conn.execute(
            "SELECT * FROM ui_channels WHERE title IS NOT NULL AND INSTR(LOWER(?), LOWER(title)) > 0", (title,)
        ).fetchone()
    conn.close()
    return dict(row) if row else None


_CHANNEL_CONFIG_FIELDS = {
    "source_type", "priority", "trigger_command",
    "command_wait_for_result", "max_requests_per_session",
}
_VALID_SOURCE_TYPES = {"passive", "bot_command", "group", "manual_review"}


@timed("database")
def set_channel_config(channel_id, **fields):
    for key in fields:
        if key not in _CHANNEL_CONFIG_FIELDS:
            raise ValueError(f"Unknown channel config field: {key!r}")
    if "source_type" in fields and fields["source_type"] not in _VALID_SOURCE_TYPES:
        raise ValueError(f"Invalid source_type: {fields['source_type']!r}")
    if not fields:
        return
    set_clauses = [f"{key} = ?" for key in fields]
    params = list(fields.values()) + [channel_id]
    conn = get_connection()
    conn.execute(f"UPDATE ui_channels SET {', '.join(set_clauses)} WHERE id = ?", params)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------
# Raw incoming message capture (per channel) - core/telegram_listener.py
# writes, api/ + Signal Inspector read
# ---------------------------------------------------------------------

@timed("database")
def record_channel_message(chat_id=None, username=None, title=None, message_text=""):
    conn = get_connection()
    conn.execute(
        """INSERT INTO channel_messages (chat_id, username, title, message_text, received_at)
           VALUES (?, ?, ?, ?, ?)""",
        (str(chat_id) if chat_id is not None else None, username, title, message_text, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


@timed("database")
def get_last_channel_message(chat_id=None, username=None):
    conn = get_connection()
    if chat_id is not None:
        row = conn.execute(
            "SELECT * FROM channel_messages WHERE chat_id = ? ORDER BY received_at DESC LIMIT 1", (str(chat_id),)
        ).fetchone()
    elif username:
        row = conn.execute(
            "SELECT * FROM channel_messages WHERE LOWER(username) = LOWER(?) ORDER BY received_at DESC LIMIT 1",
            (username,),
        ).fetchone()
    else:
        row = None
    conn.close()
    return dict(row) if row else None


@timed("database")
def list_recent_channel_messages(chat_id=None, username=None, limit=25):
    conn = get_connection()
    if chat_id is not None:
        rows = conn.execute(
            "SELECT * FROM channel_messages WHERE chat_id = ? ORDER BY received_at DESC LIMIT ?",
            (str(chat_id), limit),
        ).fetchall()
    elif username:
        rows = conn.execute(
            "SELECT * FROM channel_messages WHERE LOWER(username) = LOWER(?) ORDER BY received_at DESC LIMIT ?",
            (username, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM channel_messages ORDER BY received_at DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------
# Per-channel custom parsing rules (signal_rules) - api/, telegram_listener.py
# A rule is a simple find/replace applied to the raw message text BEFORE
# parsers/signal_parser.parse_signal() sees it - not a second parser.
# ---------------------------------------------------------------------

@timed("database")
def create_signal_rule(channel_id, find_pattern, replace_with, rule_name=None):
    conn = get_connection()
    cursor = conn.execute(
        """INSERT INTO signal_rules (channel_id, rule_name, find_pattern, replace_with, enabled, created_at)
           VALUES (?, ?, ?, ?, 1, ?)""",
        (channel_id, rule_name, find_pattern, replace_with, datetime.now().isoformat()),
    )
    conn.commit()
    rule_id = cursor.lastrowid
    conn.close()
    return rule_id


@timed("database")
def list_signal_rules(channel_id=None):
    conn = get_connection()
    if channel_id is not None:
        rows = conn.execute("SELECT * FROM signal_rules WHERE channel_id = ? ORDER BY id", (channel_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM signal_rules ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


@timed("database")
def get_enabled_rules_for_channel(channel_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM signal_rules WHERE channel_id = ? AND enabled = 1 ORDER BY id", (channel_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@timed("database")
def delete_signal_rule(rule_id):
    conn = get_connection()
    conn.execute("DELETE FROM signal_rules WHERE id = ?", (rule_id,))
    conn.commit()
    conn.close()


@timed("database")
def set_signal_rule_enabled(rule_id, enabled):
    conn = get_connection()
    conn.execute("UPDATE signal_rules SET enabled = ? WHERE id = ?", (1 if enabled else 0, rule_id))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------
# Encrypted Telegram credentials - core/secrets_store.py does the actual
# encryption; this module only ever stores/returns ciphertext, except
# get_decrypted_telegram_credentials() which is for internal use by the
# Telegram client code itself, never exposed over HTTP as plaintext.
# ---------------------------------------------------------------------

@timed("database")
def set_telegram_credentials(api_id, api_hash, phone):
    import secrets_store
    conn = get_connection()
    conn.execute("""
        INSERT INTO telegram_credentials (id, api_id_encrypted, api_hash_encrypted, phone_encrypted, updated_at)
        VALUES (1, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            api_id_encrypted = excluded.api_id_encrypted,
            api_hash_encrypted = excluded.api_hash_encrypted,
            phone_encrypted = excluded.phone_encrypted,
            updated_at = excluded.updated_at
    """, (
        secrets_store.encrypt(str(api_id)), secrets_store.encrypt(api_hash), secrets_store.encrypt(phone),
        datetime.now().isoformat(),
    ))
    conn.commit()
    conn.close()


@timed("database")
def get_telegram_credentials_status():
    """Masked view for the UI - never the real values."""
    import secrets_store
    conn = get_connection()
    row = conn.execute("SELECT * FROM telegram_credentials WHERE id = 1").fetchone()
    conn.close()
    if row is None or not row["api_id_encrypted"]:
        return {"configured": False, "phone_masked": None, "updated_at": None}
    phone = secrets_store.decrypt(row["phone_encrypted"])
    return {
        "configured": True,
        "phone_masked": secrets_store.mask(phone),
        "updated_at": row["updated_at"],
    }


@timed("database")
def get_decrypted_telegram_credentials():
    """Internal use only (core/telegram_channels.py, core/telegram_listener.py)
    - returns (api_id, api_hash, phone) or None if nothing is stored yet,
    in which case callers fall back to the .env-derived config/settings.py
    constants. Never call this from an HTTP-facing function."""
    import secrets_store
    conn = get_connection()
    row = conn.execute("SELECT * FROM telegram_credentials WHERE id = 1").fetchone()
    conn.close()
    if row is None or not row["api_id_encrypted"]:
        return None
    return (
        int(secrets_store.decrypt(row["api_id_encrypted"])),
        secrets_store.decrypt(row["api_hash_encrypted"]),
        secrets_store.decrypt(row["phone_encrypted"]),
    )


# ---------------------------------------------------------------------
# Trading Sessions (docs/AXIM_SESSION_ARCHITECTURE.md) - api/sessions.py,
# core/session_manager.py, core/telegram_listener.py
# ---------------------------------------------------------------------

def _session_row_to_dict(row):
    d = dict(row)
    d["channel_ids"] = json.loads(d.pop("channel_ids_json"))
    return d


@timed("database")
def create_session_profile(name, channel_ids, profit_target=0, loss_limit=0, max_trades=0, require_confirmation=False):
    conn = get_connection()
    cursor = conn.execute("""
        INSERT INTO session_profiles (name, channel_ids_json, profit_target, loss_limit, max_trades, require_confirmation, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (name, json.dumps(channel_ids), profit_target, loss_limit, max_trades, 1 if require_confirmation else 0, datetime.now().isoformat()))
    conn.commit()
    profile_id = cursor.lastrowid
    conn.close()
    return profile_id


@timed("database")
def list_session_profiles():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM session_profiles ORDER BY created_at DESC").fetchall()
    conn.close()
    return [_session_row_to_dict(r) for r in rows]


@timed("database")
def delete_session_profile(profile_id):
    conn = get_connection()
    conn.execute("DELETE FROM session_profiles WHERE id = ?", (profile_id,))
    conn.commit()
    conn.close()


@timed("database")
def get_active_trading_session():
    conn = get_connection()
    row = conn.execute("SELECT * FROM trading_sessions WHERE status = 'active' ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return _session_row_to_dict(row) if row else None


@timed("database")
def start_trading_session(name, channel_ids, account_mode, profit_target=0, loss_limit=0, max_trades=0,
                           require_confirmation=False, profile_id=None, risk_profile_id=None):
    """Raises ValueError if a session is already active - only one active
    session at a time (single shared trading connection, see
    docs/AXIM_APP_PLAN.md's "known gaps"). profile_id is the Phase 3
    session_profiles start-config template; risk_profile_id is the Risk
    Engine sizing/martingale/compounding/vault profile - two independent,
    optional attachments, not the same concept."""
    if get_active_trading_session() is not None:
        raise ValueError("a session is already active - stop it before starting another")
    if not channel_ids:
        raise ValueError("a session must have at least one channel")
    conn = get_connection()
    now = datetime.now().isoformat()
    cursor = conn.execute("""
        INSERT INTO trading_sessions (
            profile_id, name, channel_ids_json, account_mode, profit_target, loss_limit,
            max_trades, require_confirmation, status, trades_count, realized_pnl, started_at,
            risk_profile_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', 0, 0, ?, ?)
    """, (profile_id, name, json.dumps(channel_ids), account_mode, profit_target, loss_limit,
          max_trades, 1 if require_confirmation else 0, now, risk_profile_id))
    conn.commit()
    session_id = cursor.lastrowid
    conn.close()
    return session_id


@timed("database")
def get_trading_session(session_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM trading_sessions WHERE id = ?", (session_id,)).fetchone()
    conn.close()
    return _session_row_to_dict(row) if row else None


@timed("database")
def list_trading_sessions(limit=50):
    conn = get_connection()
    rows = conn.execute("SELECT * FROM trading_sessions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [_session_row_to_dict(r) for r in rows]


_VALID_SESSION_STOP_STATUSES = {
    "stopped_target", "stopped_loss_limit", "stopped_max_trades", "stopped_manual",
    "stopped_emergency", "stopped_connection_lost", "stopped_parse_failures",
}


@timed("database")
def stop_trading_session(session_id, status, stop_reason=None):
    if status not in _VALID_SESSION_STOP_STATUSES:
        raise ValueError(f"invalid session stop status: {status!r}")
    conn = get_connection()
    conn.execute(
        "UPDATE trading_sessions SET status = ?, stop_reason = ?, ended_at = ? WHERE id = ? AND status = 'active'",
        (status, stop_reason, datetime.now().isoformat(), session_id),
    )
    conn.commit()
    conn.close()


@timed("database")
def record_session_trade(session_id):
    """Increments trades_count - called once a signal actually reaches
    execution (not on reject/ignore), matching "trades completed" as
    shown in the Trading Sessions UI."""
    conn = get_connection()
    conn.execute("UPDATE trading_sessions SET trades_count = trades_count + 1 WHERE id = ?", (session_id,))
    conn.commit()
    conn.close()


@timed("database")
def update_session_pnl(session_id, profit_loss):
    if profit_loss is None:
        return
    conn = get_connection()
    conn.execute("UPDATE trading_sessions SET realized_pnl = realized_pnl + ? WHERE id = ?", (profit_loss, session_id))
    conn.commit()
    conn.close()


@timed("database")
def get_signal_session_id(trade_id):
    conn = get_connection()
    row = conn.execute("SELECT session_id FROM signals WHERE id = ?", (trade_id,)).fetchone()
    conn.close()
    return row["session_id"] if row else None


# ---------------------------------------------------------------------
# Risk Engine (docs/AXIM_APP_PLAN.md Phase 4) - api/risk_engine.py,
# core/risk_engine.py. Every risk_profiles row always has exactly one
# martingale_settings/compounding_settings/profit_vault_settings row,
# created alongside it with safe (disabled) defaults - callers never have
# to special-case "no sub-config yet".
# ---------------------------------------------------------------------

_RISK_PROFILE_FIELDS = {
    "name", "description", "bankroll", "sizing_mode", "fixed_amount", "percent_of_bankroll",
    "kelly_win_rate_estimate", "kelly_payout_estimate", "kelly_fraction_multiplier",
    "max_trade_amount", "max_daily_loss", "max_session_loss", "profit_target", "max_trades",
    "live_allowed",
}
_MARTINGALE_FIELDS = {
    "enabled", "max_steps", "multiplier", "custom_ladder_json", "reset_after_win",
    "reset_after_session", "max_total_exposure", "confidence_threshold",
    "same_asset_only", "same_source_only",
}
_COMPOUNDING_FIELDS = {
    "mode", "base_risk_percent", "steps_json", "drawdown_reset_percent",
    "max_risk_percent", "min_risk_percent",
}
_VAULT_FIELDS = {"enabled", "vault_percent", "trigger_event", "milestone_amount"}


def _risk_profile_row_to_dict(row):
    d = dict(row)
    d["martingale"] = get_martingale_settings(d["id"])
    d["compounding"] = get_compounding_settings(d["id"])
    d["profit_vault"] = get_profit_vault_settings(d["id"])
    return d


@timed("database")
def create_risk_profile(name, is_template=False, description=None, **fields):
    for key in fields:
        if key not in _RISK_PROFILE_FIELDS:
            raise ValueError(f"Unknown risk profile field: {key!r}")
    conn = get_connection()
    now = datetime.now().isoformat()
    columns = ["name", "description", "is_template", "created_at", "updated_at"] + list(fields.keys())
    placeholders = ", ".join("?" for _ in columns)
    values = [name, description, 1 if is_template else 0, now, now] + list(fields.values())
    cursor = conn.execute(f"INSERT INTO risk_profiles ({', '.join(columns)}) VALUES ({placeholders})", values)
    profile_id = cursor.lastrowid
    conn.execute("INSERT INTO martingale_settings (risk_profile_id) VALUES (?)", (profile_id,))
    conn.execute("INSERT INTO compounding_settings (risk_profile_id) VALUES (?)", (profile_id,))
    conn.execute("INSERT INTO profit_vault_settings (risk_profile_id) VALUES (?)", (profile_id,))
    conn.commit()
    conn.close()
    return profile_id


@timed("database")
def get_risk_profile(profile_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM risk_profiles WHERE id = ?", (profile_id,)).fetchone()
    conn.close()
    return _risk_profile_row_to_dict(row) if row else None


@timed("database")
def list_risk_profiles(include_templates=True):
    conn = get_connection()
    if include_templates:
        rows = conn.execute("SELECT * FROM risk_profiles ORDER BY is_template, name").fetchall()
    else:
        rows = conn.execute("SELECT * FROM risk_profiles WHERE is_template = 0 ORDER BY name").fetchall()
    conn.close()
    return [_risk_profile_row_to_dict(r) for r in rows]


@timed("database")
def update_risk_profile(profile_id, **fields):
    for key in fields:
        if key not in _RISK_PROFILE_FIELDS:
            raise ValueError(f"Unknown risk profile field: {key!r}")
    if not fields:
        return
    set_clauses = [f"{key} = ?" for key in fields] + ["updated_at = ?"]
    params = list(fields.values()) + [datetime.now().isoformat(), profile_id]
    conn = get_connection()
    conn.execute(f"UPDATE risk_profiles SET {', '.join(set_clauses)} WHERE id = ?", params)
    conn.commit()
    conn.close()


@timed("database")
def delete_risk_profile(profile_id):
    conn = get_connection()
    conn.execute("DELETE FROM martingale_settings WHERE risk_profile_id = ?", (profile_id,))
    conn.execute("DELETE FROM compounding_settings WHERE risk_profile_id = ?", (profile_id,))
    conn.execute("DELETE FROM profit_vault_settings WHERE risk_profile_id = ?", (profile_id,))
    conn.execute("DELETE FROM risk_profiles WHERE id = ?", (profile_id,))
    conn.commit()
    conn.close()


@timed("database")
def duplicate_risk_profile(profile_id, new_name):
    """Always creates a non-template, independent copy - duplicating a
    starter template is how a user is meant to start customizing one."""
    source = get_risk_profile(profile_id)
    if source is None:
        raise ValueError(f"no risk profile with id {profile_id}")
    new_id = create_risk_profile(
        new_name, is_template=False, description=source["description"],
        **{k: source[k] for k in _RISK_PROFILE_FIELDS if k not in ("name", "description")},
    )
    update_martingale_settings(new_id, **{k: source["martingale"][k] for k in _MARTINGALE_FIELDS})
    update_compounding_settings(new_id, **{k: source["compounding"][k] for k in _COMPOUNDING_FIELDS})
    update_profit_vault_settings(new_id, **{k: source["profit_vault"][k] for k in _VAULT_FIELDS})
    return new_id


@timed("database")
def export_risk_profile(profile_id):
    """Portable JSON-serializable dict - no id/timestamps, so it can be
    imported into any AXIM instance as a brand-new profile."""
    profile = get_risk_profile(profile_id)
    if profile is None:
        raise ValueError(f"no risk profile with id {profile_id}")
    return {
        "name": profile["name"],
        "description": profile["description"],
        "profile": {k: profile[k] for k in _RISK_PROFILE_FIELDS if k not in ("name", "description")},
        "martingale": {k: profile["martingale"][k] for k in _MARTINGALE_FIELDS},
        "compounding": {k: profile["compounding"][k] for k in _COMPOUNDING_FIELDS},
        "profit_vault": {k: profile["profit_vault"][k] for k in _VAULT_FIELDS},
    }


@timed("database")
def import_risk_profile(data, name=None):
    profile_name = name or data.get("name") or "Imported Profile"
    new_id = create_risk_profile(profile_name, is_template=False, description=data.get("description"),
                                  **data.get("profile", {}))
    if data.get("martingale"):
        update_martingale_settings(new_id, **data["martingale"])
    if data.get("compounding"):
        update_compounding_settings(new_id, **data["compounding"])
    if data.get("profit_vault"):
        update_profit_vault_settings(new_id, **data["profit_vault"])
    return new_id


@timed("database")
def get_martingale_settings(risk_profile_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM martingale_settings WHERE risk_profile_id = ?", (risk_profile_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


@timed("database")
def update_martingale_settings(risk_profile_id, **fields):
    for key in fields:
        if key not in _MARTINGALE_FIELDS:
            raise ValueError(f"Unknown martingale field: {key!r}")
    if not fields:
        return
    set_clauses = [f"{key} = ?" for key in fields]
    params = list(fields.values()) + [risk_profile_id]
    conn = get_connection()
    conn.execute(f"UPDATE martingale_settings SET {', '.join(set_clauses)} WHERE risk_profile_id = ?", params)
    conn.commit()
    conn.close()


@timed("database")
def get_compounding_settings(risk_profile_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM compounding_settings WHERE risk_profile_id = ?", (risk_profile_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


@timed("database")
def update_compounding_settings(risk_profile_id, **fields):
    for key in fields:
        if key not in _COMPOUNDING_FIELDS:
            raise ValueError(f"Unknown compounding field: {key!r}")
    if not fields:
        return
    set_clauses = [f"{key} = ?" for key in fields]
    params = list(fields.values()) + [risk_profile_id]
    conn = get_connection()
    conn.execute(f"UPDATE compounding_settings SET {', '.join(set_clauses)} WHERE risk_profile_id = ?", params)
    conn.commit()
    conn.close()


@timed("database")
def get_profit_vault_settings(risk_profile_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM profit_vault_settings WHERE risk_profile_id = ?", (risk_profile_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


@timed("database")
def update_profit_vault_settings(risk_profile_id, **fields):
    for key in fields:
        if key not in _VAULT_FIELDS:
            raise ValueError(f"Unknown profit vault field: {key!r}")
    if not fields:
        return
    set_clauses = [f"{key} = ?" for key in fields]
    params = list(fields.values()) + [risk_profile_id]
    conn = get_connection()
    conn.execute(f"UPDATE profit_vault_settings SET {', '.join(set_clauses)} WHERE risk_profile_id = ?", params)
    conn.commit()
    conn.close()


@timed("database")
def set_session_risk_profile(session_id, risk_profile_id):
    conn = get_connection()
    conn.execute("UPDATE trading_sessions SET risk_profile_id = ? WHERE id = ?", (risk_profile_id, session_id))
    conn.commit()
    conn.close()


@timed("database")
def advance_martingale_step(session_id):
    conn = get_connection()
    conn.execute(
        "UPDATE trading_sessions SET current_martingale_step = current_martingale_step + 1 WHERE id = ?",
        (session_id,),
    )
    conn.commit()
    conn.close()


@timed("database")
def reset_martingale_step(session_id):
    conn = get_connection()
    conn.execute("UPDATE trading_sessions SET current_martingale_step = 0 WHERE id = ?", (session_id,))
    conn.commit()
    conn.close()


@timed("database")
def add_to_vault(session_id, amount):
    conn = get_connection()
    conn.execute("UPDATE trading_sessions SET vaulted_amount = vaulted_amount + ? WHERE id = ?", (amount, session_id))
    conn.commit()
    conn.close()


# name, description, percent_of_bankroll, martingale(enabled, steps, multiplier),
# compounding(mode, base_risk_percent), vault(enabled, percent, trigger_event).
# bankroll is deliberately 0 on every template (read-only, meant to be
# duplicated - the user sets their own bankroll on the copy). Grouped by
# risk archetype implied by each name rather than hand-tuned individually
# - real differentiation (conservative/balanced/aggressive/martingale/
# vault-focused/compounding-focused) rather than 27 copies of one config.
_RISK_PROFILE_TEMPLATES = [
    ("Capital Shield", "Capital preservation first - small stakes, no martingale, vaults every win.", 1.0, (False, 0, 2.0), ("disabled", 1.0), (True, 25, "every_winning_session")),
    ("Vault Builder", "Aggressively protects profit with a large vault skim at every milestone.", 1.5, (False, 0, 2.0), ("disabled", 1.5), (True, 35, "milestone_based")),
    ("Balanced Builder", "Even mix of growth and protection - the default starting point.", 2.0, (True, 2, 1.8), ("target_based", 2.0), (True, 10, "daily_target")),
    ("Momentum Rider", "Rides winning streaks - compounds after every win, no brakes.", 2.5, (False, 0, 2.0), ("every_win", 2.0), (False, 0, "every_winning_session")),
    ("Target Hunter", "Fixed daily profit target, stops the moment it's hit.", 2.0, (False, 0, 2.0), ("disabled", 2.0), (False, 0, "daily_target")),
    ("Session Closer", "Tight session profit/loss limits - built for short, decisive sessions.", 2.0, (False, 0, 2.0), ("disabled", 2.0), (False, 0, "every_winning_session")),
    ("Growth Engine", "Milestone-based compounding for steady bankroll growth.", 2.5, (False, 0, 2.0), ("milestone_based", 2.0), (False, 0, "milestone_based")),
    ("Recovery Guard", "Conservative martingale strictly capped - built to recover, not chase.", 1.0, (True, 3, 1.7), ("disabled", 1.0), (True, 15, "every_winning_session")),
    ("Precision Compounding", "Kelly-criterion sizing for mathematically precise position sizes.", 0, (False, 0, 2.0), ("disabled", 2.0), (False, 0, "every_winning_session")),
    ("Greenline Builder", "Smart compounding that only ever risks realized profit.", 2.0, (False, 0, 2.0), ("smart", 2.0), (True, 20, "every_winning_session")),
    ("SafeStack", "Stacks small, steady wins - minimal risk per trade.", 0.75, (False, 0, 2.0), ("disabled", 0.75), (True, 30, "every_winning_session")),
    ("Mission Control", "Full-visibility balanced profile for hands-on operators.", 2.0, (False, 0, 2.0), ("daily", 2.0), (False, 0, "daily_target")),
    ("Signal Sprint", "Fast, frequent small trades - built for high-signal-volume sources.", 1.5, (False, 0, 2.0), ("disabled", 1.5), (False, 0, "every_winning_session")),
    ("Daily Climb", "Daily compounding - risk steps up once per day, resets each morning.", 2.0, (False, 0, 2.0), ("daily", 2.0), (False, 0, "daily_target")),
    ("Lock & Grow", "Vaults profit weekly while letting the trading balance compound.", 2.0, (False, 0, 2.0), ("weekly", 2.0), (True, 20, "weekly_target")),
    ("RiskBound", "Hard risk ceiling - risk % never exceeds a strict cap regardless of streak.", 2.0, (False, 0, 2.0), ("target_based", 2.0), (False, 0, "every_winning_session")),
    ("Base Camp", "Minimal-risk starting profile for new, unproven signal sources.", 1.0, (False, 0, 2.0), ("disabled", 1.0), (False, 0, "every_winning_session")),
    ("StepUp", "Fixed-ladder risk increases after each profit milestone.", 2.0, (False, 0, 2.0), ("milestone_based", 2.0), (False, 0, "milestone_based")),
    ("Snowball", "Classic compounding snowball - every win increases the next stake.", 2.0, (False, 0, 2.0), ("every_win", 2.0), (False, 0, "every_winning_session")),
    ("TargetRun", "Sprints toward a fixed profit target then stops cold.", 2.5, (False, 0, 2.0), ("disabled", 2.5), (False, 0, "daily_target")),
    ("Shielded Martingale", "Martingale with a hard exposure cap and same-asset-only guard.", 1.0, (True, 4, 2.0), ("disabled", 1.0), (False, 0, "every_winning_session")),
    ("Controlled Recovery", "Short martingale ladder that resets after every session regardless of outcome.", 1.0, (True, 3, 1.9), ("disabled", 1.0), (False, 0, "every_winning_session")),
    ("Profit Staircase", "Risk climbs one step at a time as profit milestones are reached.", 1.5, (False, 0, 2.0), ("milestone_based", 1.5), (False, 0, "milestone_based")),
    ("Bankroll Architect", "Structures the bankroll into trading and protected balances from day one.", 1.5, (False, 0, 2.0), ("disabled", 1.5), (True, 25, "milestone_based")),
    ("AutoPilot Conservative", "Set-and-forget low-risk defaults for unattended sessions.", 1.0, (False, 0, 2.0), ("disabled", 1.0), (True, 20, "every_winning_session")),
    ("AutoPilot Growth", "Set-and-forget growth defaults - smart compounding, no martingale.", 2.5, (False, 0, 2.0), ("smart", 2.5), (False, 0, "every_winning_session")),
    ("Elite Session", "Premium high-conviction profile for the most trusted signal sources.", 3.0, (False, 0, 2.0), ("smart", 3.0), (True, 15, "every_winning_session")),
]


@timed("database")
def seed_risk_profile_templates():
    """Populates the 27 starter templates - a no-op if any template
    already exists, same one-time-migration pattern as
    seed_channels_from_env."""
    conn = get_connection()
    count = conn.execute("SELECT COUNT(*) AS n FROM risk_profiles WHERE is_template = 1").fetchone()["n"]
    conn.close()
    if count > 0:
        return

    for name, description, percent, martingale, compounding, vault in _RISK_PROFILE_TEMPLATES:
        sizing_mode = "kelly" if name == "Precision Compounding" else "percent"
        kwargs = dict(sizing_mode=sizing_mode, percent_of_bankroll=percent)
        if sizing_mode == "kelly":
            kwargs.update(kelly_win_rate_estimate=0.55, kelly_payout_estimate=0.85, kelly_fraction_multiplier=0.5)
        profile_id = create_risk_profile(name, is_template=True, description=description, **kwargs)
        m_enabled, m_steps, m_mult = martingale
        update_martingale_settings(profile_id, enabled=m_enabled, max_steps=m_steps, multiplier=m_mult)
        c_mode, c_base = compounding
        update_compounding_settings(profile_id, mode=c_mode, base_risk_percent=c_base)
        v_enabled, v_pct, v_trigger = vault
        update_profit_vault_settings(profile_id, enabled=v_enabled, vault_percent=v_pct, trigger_event=v_trigger)


# ---------------------------------------------------------------------
# UI control state (pause/resume/emergency-stop/test-mode) - api/, telegram_listener.py
# ---------------------------------------------------------------------

@timed("database")
def get_control_state():
    conn = get_connection()
    row = conn.execute(
        "SELECT paused, emergency_stop, test_mode, updated_at FROM ui_control_state WHERE id = 1"
    ).fetchone()
    conn.close()
    return {
        "paused": bool(row["paused"]),
        "emergency_stop": bool(row["emergency_stop"]),
        "test_mode": bool(row["test_mode"]),
        "updated_at": row["updated_at"],
    }


@timed("database")
def set_control_state(paused=None, emergency_stop=None, test_mode=None):
    conn = get_connection()
    current = conn.execute(
        "SELECT paused, emergency_stop, test_mode FROM ui_control_state WHERE id = 1"
    ).fetchone()
    new_paused = current["paused"] if paused is None else (1 if paused else 0)
    new_emergency = current["emergency_stop"] if emergency_stop is None else (1 if emergency_stop else 0)
    new_test_mode = current["test_mode"] if test_mode is None else (1 if test_mode else 0)
    conn.execute(
        "UPDATE ui_control_state SET paused = ?, emergency_stop = ?, test_mode = ?, updated_at = ? WHERE id = 1",
        (new_paused, new_emergency, new_test_mode, datetime.now().isoformat()),
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


@timed("database")
def request_test_trade(requested_by):
    """Raises ValueError if one is already pending - only one test trade
    in flight at a time, same single-worker-pool reasoning as trading
    sessions."""
    conn = get_connection()
    current = conn.execute("SELECT status FROM pending_test_trade WHERE id = 1").fetchone()
    if current and current["status"] == "pending":
        conn.close()
        raise ValueError("a test trade is already pending")
    conn.execute(
        "UPDATE pending_test_trade SET status = 'pending', requested_by = ?, requested_at = ?, "
        "result_json = NULL, completed_at = NULL WHERE id = 1",
        (requested_by, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


@timed("database")
def get_pending_test_trade():
    conn = get_connection()
    row = conn.execute("SELECT * FROM pending_test_trade WHERE id = 1").fetchone()
    conn.close()
    if row is None:
        return None
    d = dict(row)
    d["result"] = json.loads(d["result_json"]) if d["result_json"] else None
    return d


@timed("database")
def complete_test_trade(result):
    conn = get_connection()
    conn.execute(
        "UPDATE pending_test_trade SET status = 'completed', result_json = ?, completed_at = ? WHERE id = 1",
        (json.dumps(result), datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


@timed("database")
def fail_test_trade(error_message):
    conn = get_connection()
    conn.execute(
        "UPDATE pending_test_trade SET status = 'error', result_json = ?, completed_at = ? WHERE id = 1",
        (json.dumps({"error": error_message}), datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------
# Users / auth / admin actions - api/auth.py, api/admin.py
#
# get_user_by_email/get_user_by_id/list_users return every column
# including password_hash - callers that expose this over HTTP (api/)
# are responsible for whitelisting fields on the way out. Kept this way
# (rather than a DB-layer redaction) because password verification
# itself needs the hash, so the DB layer can't strip it unconditionally.
# ---------------------------------------------------------------------

_USER_UPDATABLE_FIELDS = {
    "role", "access_tier", "access_state", "trial_expires_at",
    "demo_only_forced", "live_trading_allowed",
}


@timed("database")
def create_user(email, password, role="user", access_tier="trial", access_state="pending_approval"):
    import auth
    conn = get_connection()
    cursor = conn.execute(
        """INSERT INTO users (email, password_hash, role, access_tier, access_state, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (email.lower().strip(), auth.hash_password(password), role, access_tier, access_state, datetime.now().isoformat()),
    )
    conn.commit()
    user_id = cursor.lastrowid
    conn.close()
    return user_id


@timed("database")
def get_user_by_email(email):
    conn = get_connection()
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email.lower().strip(),)).fetchone()
    conn.close()
    return dict(row) if row else None


@timed("database")
def get_user_by_id(user_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


@timed("database")
def list_users():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM users ORDER BY created_at").fetchall()
    conn.close()
    return [dict(r) for r in rows]


@timed("database")
def count_users():
    conn = get_connection()
    row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
    conn.close()
    return row["n"]


@timed("database")
def update_user(user_id, **fields):
    for key in fields:
        if key not in _USER_UPDATABLE_FIELDS:
            raise ValueError(f"Unknown user field: {key!r}")
    if not fields:
        return
    set_clauses = [f"{key} = ?" for key in fields]
    params = list(fields.values()) + [user_id]
    conn = get_connection()
    conn.execute(f"UPDATE users SET {', '.join(set_clauses)} WHERE id = ?", params)
    conn.commit()
    conn.close()


@timed("database")
def set_user_password(user_id, new_password):
    import auth
    conn = get_connection()
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (auth.hash_password(new_password), user_id))
    conn.commit()
    conn.close()


@timed("database")
def verify_user_credentials(email, password):
    """Returns the user dict if email+password are correct AND the
    account isn't locked out, else None. Does not itself check
    access_state beyond that - callers (api/auth.py) decide what each
    access_state is allowed to do (e.g. 'pending_approval' can log in
    but sees a waiting screen, 'disabled' can't log in at all)."""
    import auth
    user = get_user_by_email(email)
    if user is None:
        return None
    if not auth.verify_password(password, user["password_hash"]):
        return None
    return user


@timed("database")
def record_login(user_id):
    conn = get_connection()
    conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (datetime.now().isoformat(), user_id))
    conn.commit()
    conn.close()


@timed("database")
def create_session(user_id, expires_hours=720):
    """Default 30-day expiry (720h) - a local single-operator-class tool
    with a 'remember me' checkbox on login, not a bank session timeout."""
    import auth
    raw_token, token_hash = auth.generate_session_token()
    now = datetime.now()
    expires_at = (now + timedelta(hours=expires_hours)).isoformat()
    conn = get_connection()
    conn.execute(
        """INSERT INTO auth_sessions (user_id, token_hash, created_at, last_seen_at, expires_at)
           VALUES (?, ?, ?, ?, ?)""",
        (user_id, token_hash, now.isoformat(), now.isoformat(), expires_at),
    )
    conn.commit()
    conn.close()
    return raw_token


@timed("database")
def get_session_user(raw_token):
    """Returns the user dict for a valid, non-expired session token, and
    bumps last_seen_at - or None if the token is missing/expired/revoked.
    Callers must treat None as 'not authenticated', not distinguish why."""
    import auth
    token_hash = auth.hash_token(raw_token)
    conn = get_connection()
    row = conn.execute(
        """SELECT s.expires_at, u.* FROM auth_sessions s
           JOIN users u ON u.id = s.user_id
           WHERE s.token_hash = ?""",
        (token_hash,),
    ).fetchone()
    if row is None:
        conn.close()
        return None
    if datetime.fromisoformat(row["expires_at"]) < datetime.now():
        conn.close()
        return None
    conn.execute("UPDATE auth_sessions SET last_seen_at = ? WHERE token_hash = ?",
                 (datetime.now().isoformat(), token_hash))
    conn.commit()
    user = dict(row)
    del user["expires_at"]
    conn.close()
    return user


@timed("database")
def delete_session(raw_token):
    import auth
    token_hash = auth.hash_token(raw_token)
    conn = get_connection()
    conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))
    conn.commit()
    conn.close()


@timed("database")
def list_user_sessions(user_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, created_at, last_seen_at, expires_at FROM auth_sessions WHERE user_id = ? ORDER BY last_seen_at DESC",
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@timed("database")
def revoke_all_sessions(user_id):
    """Used by the admin 'revoke access immediately' action - deletes
    every active session for this user, forcing re-login everywhere."""
    conn = get_connection()
    conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


@timed("database")
def record_admin_action(actor_user_id, target_user_id, action, detail=None):
    conn = get_connection()
    conn.execute(
        """INSERT INTO admin_actions (actor_user_id, target_user_id, action, detail, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (actor_user_id, target_user_id, action, detail, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


@timed("database")
def list_admin_actions(limit=200):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM admin_actions ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    initialize_database()
    print("Database Ready")
