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
    "fund_id": "INTEGER",
    "broker_account_id": "INTEGER",
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
    "fund_id": "INTEGER",
    "broker_account_id": "INTEGER",
    "martingale_disabled": "INTEGER DEFAULT 0",
}

# Billing scaffold (docs/AXIM_APP_PLAN.md Phase 6) - links a user to a
# Stripe customer/subscription once real keys are configured. NULL for
# every existing user until then; core/billing.py is the only writer.
#
# failed_login_count/locked_until: brute-force lockout
# (api/auth_routes.py's login()) - verify_user_credentials's own
# docstring already promised "AND the account isn't locked out" before
# this existed to back it. 0/NULL for every existing user, matching the
# additive-migration pattern every other column here follows.
_NEW_USER_COLUMNS = {
    "stripe_customer_id": "TEXT",
    "stripe_subscription_id": "TEXT",
    "failed_login_count": "INTEGER NOT NULL DEFAULT 0",
    "locked_until": "TEXT",
}

# Multi-broker-account architecture (docs/AXIM_APP_PLAN.md) - a Fund-level
# Live gate, independent of any individual broker_accounts.live_enabled -
# both must be true for a fund to actually place live trades (see
# fund_manager.can_trade_live).
_NEW_FUND_COLUMNS = {
    "live_enabled": "INTEGER DEFAULT 0",
}


def get_connection():
    """WAL mode + a busy_timeout are required now that two OS processes
    (the API and the Telegram listener) - and, per
    docs/AXIM_REMOTE_ACCESS.md, potentially several remote clients on top
    of that - all open short-lived connections against the same SQLite
    file. Without WAL, a writer briefly locks the whole file against
    other writers under the default rollback-journal mode; busy_timeout
    makes a connection that arrives mid-lock wait and retry rather than
    immediately raising "database is locked". journal_mode is a
    persistent property of the DB file itself (set once, sticks), so this
    PRAGMA is cheap on every subsequent connection - SQLite no-ops if
    already in WAL mode."""
    DB_FILE.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
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

    fund_columns = {row["name"] for row in conn.execute("PRAGMA table_info(funds)")}
    for column, sql_type in _NEW_FUND_COLUMNS.items():
        if column not in fund_columns:
            conn.execute(f"ALTER TABLE funds ADD COLUMN {column} {sql_type}")


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

    # Funds / Portfolios (docs/AXIM_APP_PLAN.md) - AXIM does not assume a
    # single bankroll. A fund is an organizing/attribution layer: trading
    # sessions, trades (via their session), and backtest runs each belong
    # to at most one fund (a plain fund_id FK added to trading_sessions/
    # backtest_runs below, NOT separate fund_sessions/fund_trades/
    # fund_backtests join tables - a session/trade/backtest run can only
    # ever belong to one fund, so a direct FK is the correct normalized
    # design and avoids a duplicate-data-sync problem a join table would
    # create for no benefit). current/trading/protected balance are
    # deliberately NOT stored columns here - core/fund_manager.py computes
    # them from the fund's real sessions (starting_balance + cumulative
    # realized_pnl - cumulative vaulted_amount), the same
    # compute-don't-cache approach core/backtest_engine.py already uses,
    # so a fund's balance can never drift out of sync with its actual
    # trade history.
    #
    # assigned_broker_label is a free-text description from before real
    # multi-broker-account support existed - kept for backward
    # compatibility with existing rows, superseded by the real
    # broker_accounts/fund_broker_accounts relationship below. A fund's
    # actual attached account is now a real, independently-connected
    # Pocket Option session, not a shared label.
    conn.execute("""
    CREATE TABLE IF NOT EXISTS funds (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        starting_balance REAL DEFAULT 0,
        assigned_broker_label TEXT,
        default_risk_profile_id INTEGER,
        default_session_profile_id INTEGER,
        profit_target REAL DEFAULT 0,
        loss_limit REAL DEFAULT 0,
        max_trades INTEGER DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'active',
        created_at TEXT,
        updated_at TEXT,
        archived_at TEXT
    );
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS fund_sources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fund_id INTEGER NOT NULL,
        channel_id INTEGER NOT NULL,
        created_at TEXT,
        UNIQUE(fund_id, channel_id)
    );
    """)

    # Broker Accounts (docs/AXIM_APP_PLAN.md) - a real, independently-
    # connected Pocket Option login, not a label. Each account gets its
    # own persistent browser profile directory (user_data_dir - see
    # execution/browser_session.py's PocketBrowserSession, which already
    # accepted a user_data_dir parameter before this feature existed) so
    # its cookies/login session can never bleed into another account's.
    # `mode` describes what the account IS ("demo"/"live"/"both" - which
    # cabinet URL(s) it can be pointed at); `live_enabled` is a SEPARATE,
    # explicit safety gate on top of that (a "both" account can still be
    # demo-only in practice until this is flipped) - see fund_broker_
    # accounts and funds.live_enabled below for the matching Fund-level
    # gate. Real balance/connection facts (last_balance,
    # connection_status) are observed facts written by the execution
    # layer, not user input - never fabricated if unobserved (see
    # api/pocket-option/status's existing "balance: None, not yet
    # implemented" precedent).
    conn.execute("""
    CREATE TABLE IF NOT EXISTS broker_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        name TEXT NOT NULL,
        mode TEXT NOT NULL DEFAULT 'demo',
        live_enabled INTEGER NOT NULL DEFAULT 0,
        user_data_dir TEXT,
        connection_status TEXT NOT NULL DEFAULT 'disconnected',
        last_connected_at TEXT,
        last_balance REAL,
        last_balance_checked_at TEXT,
        status TEXT NOT NULL DEFAULT 'active',
        created_at TEXT,
        updated_at TEXT
    );
    """)

    # A fund's attachment to a real broker_accounts row. Modeled as a join
    # table (not a plain broker_account_id FK on funds) because the
    # product spec explicitly calls for future "distribute trades across
    # multiple broker accounts" support - is_primary marks which one is
    # authoritative today (exactly one primary per fund, enforced in
    # assign_broker_account_to_fund) without a later schema migration
    # being needed to add the rest.
    conn.execute("""
    CREATE TABLE IF NOT EXISTS fund_broker_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fund_id INTEGER NOT NULL,
        broker_account_id INTEGER NOT NULL,
        is_primary INTEGER NOT NULL DEFAULT 1,
        created_at TEXT,
        UNIQUE(fund_id, broker_account_id)
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
    # Process-health columns for scripts/soak_snapshot.py (docs/
    # AXIM_RELEASE_CHECKLIST.md) - added after the table above already
    # shipped, so migrated inline here (same reason auth_sessions'
    # client_name/client_type get the same treatment). Deliberately
    # SELF-reported by the listener process itself (see
    # telegram_listener.py's _heartbeat_loop) rather than discovered
    # externally via WMI process enumeration: a process reading its OWN
    # session's CommandLine/Path is unrestricted, but Windows blocks
    # another process (e.g. a Task-Scheduler-spawned one, a different
    # logon session even for the same user) from reading those same
    # properties on a process outside its session - confirmed by live
    # testing, not assumed. Self-reporting sidesteps that boundary
    # entirely instead of working around it.
    _NEW_HEARTBEAT_COLUMNS = {
        "listener_pid": "INTEGER",
        "listener_uptime_min": "REAL",
        "listener_mem_mb": "REAL",
        "chrome_count": "INTEGER",
        "chrome_mem_mb": "REAL",
    }
    heartbeat_columns = {row["name"] for row in conn.execute("PRAGMA table_info(ui_listener_heartbeat)")}
    for column, sql_type in _NEW_HEARTBEAT_COLUMNS.items():
        if column not in heartbeat_columns:
            conn.execute(f"ALTER TABLE ui_listener_heartbeat ADD COLUMN {column} {sql_type}")

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

    # Live-mode trade confirmation gate (docs/AXIM_APP_PLAN.md) - when a
    # session has require_confirmation set AND its account_mode is LIVE,
    # core/trade_coordinator.py writes a row here and BLOCKS (polling,
    # never a direct call into the UI) until an operator confirms/rejects
    # from any page (see web/shell.js) or the request times out. Not a
    # singleton like pending_test_trade - the worker pool can process
    # more than one signal at once, so more than one confirmation can be
    # in flight at a time.
    conn.execute("""
    CREATE TABLE IF NOT EXISTS pending_trade_confirmations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_id INTEGER NOT NULL UNIQUE,
        session_id INTEGER,
        asset TEXT,
        direction TEXT,
        expiry TEXT,
        amount REAL,
        status TEXT NOT NULL DEFAULT 'pending',
        requested_at TEXT,
        decided_at TEXT,
        decided_by TEXT
    );
    """)

    # Cross-process real-time event outbox (docs/AXIM_REMOTE_ACCESS.md) -
    # the API process's SSE endpoint (GET /api/events/stream) and the
    # listener process's core/event_bus.EventBus don't share memory (two
    # separate OS processes, same as everywhere else in this codebase -
    # see ui_control_state/ui_listener_heartbeat/pending_trade_confirmations
    # above for the same "coupled only via SQLite, one side writes, the
    # other polls" pattern already in production use). core/event_stream.py
    # subscribes to the listener's event_bus and writes one row here per
    # meaningful event; the API process tails this table on a short
    # internal poll and pushes new rows to connected SSE clients. id is
    # the resume cursor a reconnecting client sends back as Last-Event-ID -
    # a monotonic autoincrement id, not created_at, since SQLite guarantees
    # strictly increasing ids even when multiple events land in the same
    # millisecond (concurrent workers can do exactly that), which a
    # timestamp cursor can't guarantee. Pruned periodically (see
    # prune_server_events) so this never grows unbounded.
    conn.execute("""
    CREATE TABLE IF NOT EXISTS server_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT NOT NULL,
        payload_json TEXT,
        created_at TEXT NOT NULL
    );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_server_events_created_at ON server_events (created_at)")

    # Rule Builder (docs/AXIM_APP_PLAN.md) - visual IF/THEN automation.
    # Every condition evaluator is a pure function returning (bool, ...)
    # and every action executor calls an existing real mutation function
    # (session_manager.end_session, database.update_risk_profile, etc.) -
    # see core/rule_engine.py. last_condition_state implements edge
    # triggering (fire only on false->true transitions) so a rule whose
    # condition stays true across many trades (e.g. daily_profit_gte
    # after the target is hit) fires once, not on every subsequent trade.
    #
    # Rules belong to a Fund, not a session - a Fund is the permanent
    # trading object, sessions are disposable (docs/AXIM_APP_PLAN.md).
    # scope='fund' rules are evaluated against whichever session is
    # currently active for that fund (or not at all, if none is); these
    # persist across sessions exactly like the rest of a fund's config.
    # scope='session' rules are a temporary override tied to ONE specific
    # trading_sessions row (session_id) - session_manager.end_session
    # deletes them when that session ends, so they never outlive it.
    # fund_id is nullable only for pre-this-feature legacy rows; the API
    # layer requires it on every new rule.
    conn.execute("""
    CREATE TABLE IF NOT EXISTS rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        enabled INTEGER DEFAULT 1,
        condition_type TEXT NOT NULL,
        condition_params_json TEXT DEFAULT '{}',
        action_type TEXT NOT NULL,
        action_params_json TEXT DEFAULT '{}',
        last_condition_state INTEGER DEFAULT 0,
        last_triggered_at TEXT,
        trigger_count INTEGER DEFAULT 0,
        created_at TEXT,
        updated_at TEXT
    );
    """)
    _NEW_RULE_COLUMNS = {
        "fund_id": "INTEGER",
        "scope": "TEXT NOT NULL DEFAULT 'fund'",
        "session_id": "INTEGER",
    }
    rule_columns = {row["name"] for row in conn.execute("PRAGMA table_info(rules)")}
    for column, sql_type in _NEW_RULE_COLUMNS.items():
        if column not in rule_columns:
            conn.execute(f"ALTER TABLE rules ADD COLUMN {column} {sql_type}")

    # Automation Studio's per-rule activity trail - one row per actual
    # fire (edge-triggered, same as rules.trigger_count), so "Fired 12
    # times" on the list view can expand into an honest, real history
    # instead of just a counter. outcome_message is the exact string the
    # action executor returned (core/rule_engine.py's evaluate_rule) -
    # never a separately-composed summary that could drift from what
    # actually happened.
    conn.execute("""
    CREATE TABLE IF NOT EXISTS rule_firings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rule_id INTEGER NOT NULL,
        fired_at TEXT NOT NULL,
        outcome_message TEXT
    );
    """)

    # In-app notifications - a rule's "notify Owner" action (and future
    # non-email/push alerts) writes here; web/shell.js polls the unread
    # count. Deliberately in-app only, no email/SMS/push - those need an
    # external provider and credentials, out of scope here.
    conn.execute("""
    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        message TEXT NOT NULL,
        source TEXT,
        created_at TEXT NOT NULL,
        read_at TEXT
    );
    """)

    # Backtest Engine / Strategy Lab (docs/AXIM_APP_PLAN.md) - lets a user
    # replay historical signals through one or more Risk Engine profiles
    # to compare how each would have performed, before risking real money.
    # imported_signals holds signals that did NOT come through the live
    # Telegram pipeline (CSV/manual entry) - normalized to the same shape
    # as `signals` so core/backtest_engine.py can treat both pools
    # uniformly. result/payout_percent are nullable: an imported signal
    # with no graded result yet simply can't be used in a backtest until
    # graded (see database.grade_imported_signal).
    conn.execute("""
    CREATE TABLE IF NOT EXISTS imported_signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_label TEXT,
        raw_message TEXT,
        asset TEXT,
        direction TEXT,
        expiry TEXT,
        received_at TEXT NOT NULL,
        result TEXT,
        payout_percent REAL,
        profit_loss REAL,
        notes TEXT,
        import_batch TEXT,
        created_at TEXT
    );
    """)

    # One row per "Run Backtest" click. signal_pool_json captures the
    # filter used (source: live/imported/both, channel filter, date
    # range) so a completed run's report stays reproducible even if the
    # underlying signal pool changes later.
    conn.execute("""
    CREATE TABLE IF NOT EXISTS backtest_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        signal_pool_json TEXT NOT NULL,
        starting_bankroll REAL NOT NULL,
        default_payout_percent REAL DEFAULT 85,
        session_window TEXT DEFAULT 'daily',
        status TEXT DEFAULT 'pending',
        error_message TEXT,
        created_by TEXT,
        created_at TEXT,
        completed_at TEXT,
        fund_id INTEGER
    );
    """)
    backtest_run_columns = {row["name"] for row in conn.execute("PRAGMA table_info(backtest_runs)")}
    if "fund_id" not in backtest_run_columns:
        conn.execute("ALTER TABLE backtest_runs ADD COLUMN fund_id INTEGER")

    # One row per risk_profile being compared within a run.
    # profile_snapshot_json freezes the profile+martingale+compounding+
    # vault settings at run time - profiles are mutable and a backtest
    # report must stay reproducible even if the user edits the profile
    # (or deletes it) afterward.
    conn.execute("""
    CREATE TABLE IF NOT EXISTS backtest_strategies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        backtest_run_id INTEGER NOT NULL,
        risk_profile_id INTEGER,
        label TEXT NOT NULL,
        profile_snapshot_json TEXT NOT NULL,
        created_at TEXT
    );
    """)

    # One row per simulated session within one strategy's simulation -
    # sessions are grouped from the signal pool per backtest_runs.
    # session_window (default: one calendar day per session, mirroring
    # how a real operator would run AXIM day to day).
    conn.execute("""
    CREATE TABLE IF NOT EXISTS backtest_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        backtest_strategy_id INTEGER NOT NULL,
        session_index INTEGER NOT NULL,
        started_at TEXT,
        ended_at TEXT,
        status TEXT NOT NULL,
        starting_balance REAL,
        realized_pnl REAL DEFAULT 0,
        trades_count INTEGER DEFAULT 0,
        ending_martingale_step INTEGER DEFAULT 0,
        ending_vaulted_amount REAL DEFAULT 0
    );
    """)

    # One row per simulated trade. signal_source_type + signal_id point
    # back at the real `signals` row or `imported_signals` row this trade
    # was simulated from, so a trade log can always be traced to its
    # source signal rather than being opaque numbers.
    conn.execute("""
    CREATE TABLE IF NOT EXISTS backtest_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        backtest_session_id INTEGER NOT NULL,
        signal_source_type TEXT NOT NULL,
        signal_id INTEGER,
        sequence_in_session INTEGER NOT NULL,
        asset TEXT,
        direction TEXT,
        occurred_at TEXT,
        trade_amount REAL NOT NULL,
        martingale_step INTEGER DEFAULT 0,
        result TEXT NOT NULL,
        profit_loss REAL NOT NULL,
        running_balance REAL NOT NULL
    );
    """)

    # One row per strategy per run - the aggregated comparison-card
    # numbers. A 1:1 relationship with backtest_strategies, kept as its
    # own table (rather than extra columns on backtest_strategies) so the
    # "what happened" (strategies/sessions/trades) and "what it means"
    # (metrics) halves of a report stay clearly separated.
    conn.execute("""
    CREATE TABLE IF NOT EXISTS backtest_metrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        backtest_strategy_id INTEGER NOT NULL UNIQUE,
        final_bankroll REAL,
        total_profit_loss REAL,
        roi_percent REAL,
        win_rate REAL,
        loss_rate REAL,
        max_drawdown_percent REAL,
        best_day_pnl REAL,
        worst_day_pnl REAL,
        longest_win_streak INTEGER,
        longest_loss_streak INTEGER,
        max_martingale_step_used INTEGER,
        sessions_completed INTEGER,
        sessions_stopped_by_target INTEGER,
        sessions_stopped_by_loss_limit INTEGER,
        avg_trade_size REAL,
        largest_trade_size REAL,
        total_protected_profit REAL,
        risk_score TEXT,
        best_for_label TEXT,
        rank_overall INTEGER,
        rank_safest INTEGER,
        rank_highest_growth INTEGER,
        rank_lowest_drawdown INTEGER,
        rank_risk_adjusted INTEGER
    );
    """)
    # AI Strategy Lab analyst metrics (docs/AXIM_APP_PLAN.md) - added
    # after the table above already shipped, so migrated inline here
    # (this table is created AFTER _migrate_schema()'s call site, same
    # reason backtest_runs' fund_id gets the same inline treatment).
    _NEW_BACKTEST_METRICS_COLUMNS = {
        "sharpe_like_score": "REAL",
        "profit_factor": "REAL",
        "consistency_percent": "REAL",
        "recovery_factor": "REAL",
        "max_drawdown_amount": "REAL",
        "volatility": "REAL",
    }
    backtest_metrics_columns = {row["name"] for row in conn.execute("PRAGMA table_info(backtest_metrics)")}
    for column, sql_type in _NEW_BACKTEST_METRICS_COLUMNS.items():
        if column not in backtest_metrics_columns:
            conn.execute(f"ALTER TABLE backtest_metrics ADD COLUMN {column} {sql_type}")

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
    user_columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
    for column, sql_type in _NEW_USER_COLUMNS.items():
        if column not in user_columns:
            conn.execute(f"ALTER TABLE users ADD COLUMN {column} {sql_type}")

    # token_hash only - the raw bearer token lives in the browser's cookie
    # (or, for a Remote Client, an Authorization: Bearer header - see
    # docs/AXIM_REMOTE_ACCESS.md) and is never persisted, same reasoning
    # as password_hash: a DB read alone can't be replayed as a valid
    # session (see core/auth.py).
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
    # Remote-client identification - lets the "Connected Devices" view
    # show which physical device/app a session belongs to (this browser
    # vs. a laptop's Remote Client vs. a future mobile app), not just an
    # opaque token. client_type defaults to 'web' since every session
    # created before this column existed (and every plain browser login
    # going forward) is exactly that. Added after auth_sessions already
    # shipped, so migrated inline here (this table is created AFTER
    # _migrate_schema()'s call site), same as backtest_metrics/users above.
    _NEW_AUTH_SESSION_COLUMNS = {
        "client_name": "TEXT",
        "client_type": "TEXT DEFAULT 'web'",
    }
    auth_session_columns = {row["name"] for row in conn.execute("PRAGMA table_info(auth_sessions)")}
    for column, sql_type in _NEW_AUTH_SESSION_COLUMNS.items():
        if column not in auth_session_columns:
            conn.execute(f"ALTER TABLE auth_sessions ADD COLUMN {column} {sql_type}")

    # Password reset (web/login.html "Forgot password?" -> core/
    # email_sender.py). token_hash only, same reasoning as auth_sessions -
    # the raw token lives in the emailed link, never persisted. One
    # outstanding token per user in practice (requesting a new one
    # invalidates prior ones - see invalidate_password_reset_tokens_for_user)
    # so an old, possibly-leaked reset link can't still be redeemed later.
    conn.execute("""
    CREATE TABLE IF NOT EXISTS password_reset_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        token_hash TEXT UNIQUE NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        used_at TEXT
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
def record_signal_received(signal, source=None, sender=None, message_id=None, session_id=None,
                            fund_id=None, broker_account_id=None):
    from trade_lifecycle import TradeStatus

    conn = get_connection()
    cursor = conn.execute("""
    INSERT INTO signals (
        message_id, channel, sender, asset, direction, timeframe,
        trade_amount, message, received_at, executed, execution_status, session_id,
        fund_id, broker_account_id
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
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
        fund_id,
        broker_account_id,
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
def get_recent_results(limit, fund_id=None):
    conn = get_connection()
    if fund_id is not None:
        rows = conn.execute("""
            SELECT result FROM signals
            WHERE result IS NOT NULL AND fund_id = ?
            ORDER BY closed_at DESC
            LIMIT ?
        """, (fund_id, limit)).fetchall()
    else:
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
def get_open_trades(broker_account_id=None):
    """broker_account_id scopes recovery to one account's own open
    positions - resuming a trade under the WRONG account's browser
    context would check the wrong Pocket Option account's Closed-trades
    list entirely, silently reporting a missing/wrong outcome. None
    (the default) is the pre-multi-account behavior, unscoped - still
    correct for a single legacy connection with no broker_account_id on
    any of its rows."""
    conn = get_connection()
    if broker_account_id is not None:
        rows = conn.execute("""
            SELECT * FROM signals
            WHERE execution_status IN ('trade_clicked', 'trade_opened') AND broker_account_id = ?
            ORDER BY opened_at ASC
        """, (broker_account_id,)).fetchall()
    else:
        rows = conn.execute("""
            SELECT * FROM signals
            WHERE execution_status IN ('trade_clicked', 'trade_opened')
            ORDER BY opened_at ASC
        """).fetchall()
    conn.close()
    return [dict(row) for row in rows]


@timed("database")
def get_trades_between(start_iso, end_iso, closed_only=False, fund_id=None):
    conn = get_connection()
    fund_clause = " AND fund_id = ?" if fund_id is not None else ""
    fund_params = (fund_id,) if fund_id is not None else ()
    if closed_only:
        rows = conn.execute(f"""
            SELECT * FROM signals
            WHERE closed_at IS NOT NULL AND closed_at >= ? AND closed_at <= ?{fund_clause}
            ORDER BY closed_at ASC
        """, (start_iso, end_iso) + fund_params).fetchall()
    else:
        rows = conn.execute(f"""
            SELECT * FROM signals
            WHERE received_at >= ? AND received_at <= ?{fund_clause}
            ORDER BY received_at ASC
        """, (start_iso, end_iso) + fund_params).fetchall()
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
    # id DESC as a tiebreaker, not just received_at DESC - found live via a
    # real intermittent test failure: datetime.now().isoformat() can tie
    # between two inserts under load (Windows clock resolution), and
    # SQLite gives no ordering guarantee among tied rows without a second
    # sort key, so the "most recent" message could come back wrong even
    # though it was correctly inserted last (id is autoincrement, so it's
    # a genuine insertion-order tiebreaker, unlike received_at).
    conn = get_connection()
    if chat_id is not None:
        row = conn.execute(
            "SELECT * FROM channel_messages WHERE chat_id = ? ORDER BY received_at DESC, id DESC LIMIT 1",
            (str(chat_id),),
        ).fetchone()
    elif username:
        row = conn.execute(
            "SELECT * FROM channel_messages WHERE LOWER(username) = LOWER(?) ORDER BY received_at DESC, id DESC LIMIT 1",
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
            "SELECT * FROM channel_messages WHERE chat_id = ? ORDER BY received_at DESC, id DESC LIMIT ?",
            (str(chat_id), limit),
        ).fetchall()
    elif username:
        rows = conn.execute(
            "SELECT * FROM channel_messages WHERE LOWER(username) = LOWER(?) ORDER BY received_at DESC, id DESC LIMIT ?",
            (username, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM channel_messages ORDER BY received_at DESC, id DESC LIMIT ?", (limit,)
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
# Funds / Portfolios (docs/AXIM_APP_PLAN.md) - core/fund_manager.py owns
# balance/performance aggregation; this is CRUD + row-shaping only,
# matching every other feature table in this module.
# ---------------------------------------------------------------------

_FUND_FIELDS = {
    "name", "starting_balance", "assigned_broker_label", "default_risk_profile_id",
    "default_session_profile_id", "profit_target", "loss_limit", "max_trades", "status",
    "live_enabled",
}
_VALID_FUND_STATUSES = {"active", "paused", "archived"}


@timed("database")
def create_fund(name, starting_balance=0, **fields):
    for key in fields:
        if key not in _FUND_FIELDS:
            raise ValueError(f"Unknown fund field: {key!r}")
    status = fields.pop("status", "active")
    if status not in _VALID_FUND_STATUSES:
        raise ValueError(f"invalid fund status: {status!r}")
    conn = get_connection()
    now = datetime.now().isoformat()
    columns = ["name", "starting_balance", "status", "created_at", "updated_at"] + list(fields.keys())
    placeholders = ", ".join("?" for _ in columns)
    values = [name, starting_balance, status, now, now] + list(fields.values())
    cursor = conn.execute(f"INSERT INTO funds ({', '.join(columns)}) VALUES ({placeholders})", values)
    fund_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return fund_id


@timed("database")
def get_fund(fund_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM funds WHERE id = ?", (fund_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


@timed("database")
def list_funds(status=None):
    conn = get_connection()
    if status:
        rows = conn.execute("SELECT * FROM funds WHERE status = ? ORDER BY name", (status,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM funds ORDER BY status, name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


@timed("database")
def update_fund(fund_id, **fields):
    for key in fields:
        if key not in _FUND_FIELDS:
            raise ValueError(f"Unknown fund field: {key!r}")
    if "status" in fields and fields["status"] not in _VALID_FUND_STATUSES:
        raise ValueError(f"invalid fund status: {fields['status']!r}")
    if not fields:
        return
    set_clauses = [f"{key} = ?" for key in fields] + ["updated_at = ?"]
    params = list(fields.values()) + [datetime.now().isoformat()]
    if fields.get("status") == "archived":
        set_clauses.append("archived_at = ?")
        params.append(datetime.now().isoformat())
    params.append(fund_id)
    conn = get_connection()
    conn.execute(f"UPDATE funds SET {', '.join(set_clauses)} WHERE id = ?", params)
    conn.commit()
    conn.close()


@timed("database")
def duplicate_fund(fund_id, new_name):
    source = get_fund(fund_id)
    if source is None:
        raise ValueError(f"no fund with id {fund_id}")
    new_id = create_fund(
        new_name, starting_balance=source["starting_balance"],
        assigned_broker_label=source["assigned_broker_label"],
        default_risk_profile_id=source["default_risk_profile_id"],
        default_session_profile_id=source["default_session_profile_id"],
        profit_target=source["profit_target"], loss_limit=source["loss_limit"],
        max_trades=source["max_trades"],
    )
    for channel_id in list_fund_source_channel_ids(fund_id):
        add_fund_source(new_id, channel_id)
    return new_id


@timed("database")
def add_fund_source(fund_id, channel_id):
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO fund_sources (fund_id, channel_id, created_at) VALUES (?, ?, ?)",
        (fund_id, channel_id, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


@timed("database")
def remove_fund_source(fund_id, channel_id):
    conn = get_connection()
    conn.execute("DELETE FROM fund_sources WHERE fund_id = ? AND channel_id = ?", (fund_id, channel_id))
    conn.commit()
    conn.close()


@timed("database")
def list_fund_source_channel_ids(fund_id):
    conn = get_connection()
    rows = conn.execute("SELECT channel_id FROM fund_sources WHERE fund_id = ?", (fund_id,)).fetchall()
    conn.close()
    return [r["channel_id"] for r in rows]


@timed("database")
def list_fund_sessions(fund_id, limit=50):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM trading_sessions WHERE fund_id = ? ORDER BY id DESC LIMIT ?", (fund_id, limit)
    ).fetchall()
    conn.close()
    return [_session_row_to_dict(r) for r in rows]


@timed("database")
def list_fund_backtest_runs(fund_id, limit=50):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM backtest_runs WHERE fund_id = ? ORDER BY id DESC LIMIT ?", (fund_id, limit)
    ).fetchall()
    conn.close()
    return [_backtest_run_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------
# Broker Accounts (docs/AXIM_APP_PLAN.md) - real, independently-connected
# Pocket Option logins. api/broker_accounts_routes.py is the HTTP surface;
# execution/browser_warmup.py is what actually opens each account's
# persistent browser context using user_data_dir.
# ---------------------------------------------------------------------

_BROKER_ACCOUNT_FIELDS = {
    "name", "mode", "live_enabled", "user_data_dir", "connection_status",
    "last_connected_at", "last_balance", "last_balance_checked_at", "status",
}
_VALID_BROKER_ACCOUNT_MODES = {"demo", "live", "both"}
_VALID_BROKER_ACCOUNT_STATUSES = {"active", "disabled", "archived"}
_VALID_CONNECTION_STATUSES = {"disconnected", "connecting", "connected", "error"}


@timed("database")
def create_broker_account(name, mode="demo", user_id=None):
    if mode not in _VALID_BROKER_ACCOUNT_MODES:
        raise ValueError(f"invalid broker account mode: {mode!r}")
    conn = get_connection()
    now = datetime.now().isoformat()
    cursor = conn.execute(
        "INSERT INTO broker_accounts (user_id, name, mode, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, name, mode, now, now),
    )
    account_id = cursor.lastrowid
    # user_data_dir depends on the id, so it's set right after insert
    # rather than pre-computed - matches the same "known after INSERT"
    # ordering used elsewhere in this module (e.g. trade_id-derived rows).
    user_data_dir = f"sessions/pocket_broker_{account_id}"
    conn.execute("UPDATE broker_accounts SET user_data_dir = ? WHERE id = ?", (user_data_dir, account_id))
    conn.commit()
    conn.close()
    return account_id


@timed("database")
def get_broker_account(account_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM broker_accounts WHERE id = ?", (account_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


@timed("database")
def list_broker_accounts(user_id=None, status=None):
    conn = get_connection()
    query = "SELECT * FROM broker_accounts WHERE 1=1"
    params = []
    if user_id is not None:
        query += " AND user_id = ?"
        params.append(user_id)
    if status is not None:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY name"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@timed("database")
def update_broker_account(account_id, **fields):
    for key in fields:
        if key not in _BROKER_ACCOUNT_FIELDS:
            raise ValueError(f"Unknown broker account field: {key!r}")
    if "mode" in fields and fields["mode"] not in _VALID_BROKER_ACCOUNT_MODES:
        raise ValueError(f"invalid broker account mode: {fields['mode']!r}")
    if "status" in fields and fields["status"] not in _VALID_BROKER_ACCOUNT_STATUSES:
        raise ValueError(f"invalid broker account status: {fields['status']!r}")
    if "connection_status" in fields and fields["connection_status"] not in _VALID_CONNECTION_STATUSES:
        raise ValueError(f"invalid connection status: {fields['connection_status']!r}")
    if not fields:
        return
    set_clauses = [f"{key} = ?" for key in fields] + ["updated_at = ?"]
    params = list(fields.values()) + [datetime.now().isoformat()]
    params.append(account_id)
    conn = get_connection()
    conn.execute(f"UPDATE broker_accounts SET {', '.join(set_clauses)} WHERE id = ?", params)
    conn.commit()
    conn.close()


@timed("database")
def assign_broker_account_to_fund(fund_id, broker_account_id, is_primary=True):
    """Attaches a broker account to a fund. Only one broker account may be
    primary per fund today (a fund "distributing trades across multiple
    broker accounts" is explicitly future scope) - assigning a new primary
    demotes any existing one rather than allowing two."""
    conn = get_connection()
    now = datetime.now().isoformat()
    if is_primary:
        conn.execute("UPDATE fund_broker_accounts SET is_primary = 0 WHERE fund_id = ?", (fund_id,))
    conn.execute(
        """
        INSERT INTO fund_broker_accounts (fund_id, broker_account_id, is_primary, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(fund_id, broker_account_id) DO UPDATE SET is_primary = excluded.is_primary
        """,
        (fund_id, broker_account_id, 1 if is_primary else 0, now),
    )
    conn.commit()
    conn.close()


@timed("database")
def unassign_broker_account_from_fund(fund_id, broker_account_id):
    conn = get_connection()
    conn.execute(
        "DELETE FROM fund_broker_accounts WHERE fund_id = ? AND broker_account_id = ?",
        (fund_id, broker_account_id),
    )
    conn.commit()
    conn.close()


@timed("database")
def get_fund_primary_broker_account(fund_id):
    conn = get_connection()
    row = conn.execute(
        """
        SELECT ba.* FROM broker_accounts ba
        JOIN fund_broker_accounts fba ON fba.broker_account_id = ba.id
        WHERE fba.fund_id = ? AND fba.is_primary = 1
        """,
        (fund_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


@timed("database")
def list_fund_broker_accounts(fund_id):
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT ba.*, fba.is_primary FROM broker_accounts ba
        JOIN fund_broker_accounts fba ON fba.broker_account_id = ba.id
        WHERE fba.fund_id = ?
        ORDER BY fba.is_primary DESC, ba.name
        """,
        (fund_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@timed("database")
def list_broker_account_funds(broker_account_id):
    """Which funds currently point at this account - needed both for the
    Broker Accounts UI ("used by: Fund A, Fund B") and to warn before an
    operator disconnects/archives an account still in active use."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT f.* FROM funds f
        JOIN fund_broker_accounts fba ON fba.fund_id = f.id
        WHERE fba.broker_account_id = ?
        ORDER BY f.name
        """,
        (broker_account_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


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
    """Newest active session across the WHOLE app, regardless of fund/
    broker account - kept for legacy/test call sites and rule_engine's
    unscoped fallback. Prefer get_active_trading_session_for_fund/
    _for_broker_account/_for_channel below, which are concurrency-safe;
    this one silently picks only the most-recently-started session once
    more than one is active."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM trading_sessions WHERE status = 'active' ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return _session_row_to_dict(row) if row else None


@timed("database")
def list_active_trading_sessions():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM trading_sessions WHERE status = 'active' ORDER BY id DESC").fetchall()
    conn.close()
    return [_session_row_to_dict(r) for r in rows]


@timed("database")
def get_active_trading_session_for_broker_account(broker_account_id):
    if broker_account_id is None:
        return None
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM trading_sessions WHERE status = 'active' AND broker_account_id = ? ORDER BY id DESC LIMIT 1",
        (broker_account_id,),
    ).fetchone()
    conn.close()
    return _session_row_to_dict(row) if row else None


@timed("database")
def get_active_trading_session_for_fund(fund_id):
    if fund_id is None:
        return None
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM trading_sessions WHERE status = 'active' AND fund_id = ? ORDER BY id DESC LIMIT 1",
        (fund_id,),
    ).fetchone()
    conn.close()
    return _session_row_to_dict(row) if row else None


@timed("database")
def get_active_trading_session_for_channel(channel_id):
    """The one active session (if any) whose channel_ids_json covers this
    channel - the correct scoping for signal routing once more than one
    session can be active at a time. Concurrently active sessions are
    never expected to share a channel (a Signal Source belongs to one
    Fund), but if that ever happened this deterministically returns the
    newest rather than guessing."""
    if channel_id is None:
        return None
    for session in list_active_trading_sessions():
        if channel_id in session["channel_ids"]:
            return session
    return None


@timed("database")
def start_trading_session(name, channel_ids, account_mode, profit_target=0, loss_limit=0, max_trades=0,
                           require_confirmation=False, profile_id=None, risk_profile_id=None, fund_id=None,
                           broker_account_id=None):
    """Raises ValueError if a session is already active ON THE SAME
    BROKER ACCOUNT - that's the real concurrency boundary (one physical
    Pocket Option login can only run one session at a time; different
    Funds on different broker accounts can each run their own). When
    broker_account_id is None (legacy/test call sites that predate the
    multi-broker-account architecture, or a session started with no Fund
    attached), falls back to the old whole-app exclusivity check, since
    there's no account to scope it to.

    profile_id is the Phase 3 session_profiles start-config template;
    risk_profile_id is the Risk Engine sizing/martingale/compounding/
    vault profile; fund_id is the Fund this session's P&L should be
    attributed to - three independent, optional attachments, not the
    same concept. fund_id defaults to None (not required at the database
    layer) so every existing test/call site keeps working unchanged -
    "a session must have a fund" is a UI/API-layer rule (api/sessions.py),
    the same soft-enforcement pattern this codebase already uses for
    other evolving requirements.

    broker_account_id is also a point-in-time snapshot of the fund's
    primary broker account at session-start, for display/audit only -
    actual trade routing (core/broker_account_manager.py) always
    resolves the fund's CURRENT primary account dynamically per signal,
    not this stored value, so a mid-session account reassignment can't
    leave trades silently routed by a stale snapshot."""
    if broker_account_id is not None:
        if get_active_trading_session_for_broker_account(broker_account_id) is not None:
            raise ValueError("this broker account already has an active session - stop it before starting another")
    elif get_active_trading_session() is not None:
        raise ValueError("a session is already active - stop it before starting another")
    if not channel_ids:
        raise ValueError("a session must have at least one channel")
    conn = get_connection()
    now = datetime.now().isoformat()
    cursor = conn.execute("""
        INSERT INTO trading_sessions (
            profile_id, name, channel_ids_json, account_mode, profit_target, loss_limit,
            max_trades, require_confirmation, status, trades_count, realized_pnl, started_at,
            risk_profile_id, fund_id, broker_account_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', 0, 0, ?, ?, ?, ?)
    """, (profile_id, name, json.dumps(channel_ids), account_mode, profit_target, loss_limit,
          max_trades, 1 if require_confirmation else 0, now, risk_profile_id, fund_id, broker_account_id))
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
    "stopped_emergency", "stopped_connection_lost", "stopped_parse_failures", "stopped_rule",
    "stopped_fund_target", "stopped_fund_loss_limit", "stopped_fund_max_trades",
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
    return create_risk_profile_from_snapshot(new_name, source)


@timed("database")
def create_risk_profile_from_snapshot(new_name, snapshot):
    """Same copy logic as duplicate_risk_profile, but sourced from an
    arbitrary snapshot dict (get_risk_profile()'s exact shape) rather
    than re-fetching a live profile by id - used to deploy a Strategy
    Lab backtest_strategy's point-in-time profile_snapshot to a Fund
    (docs/AXIM_APP_PLAN.md "Deploy to Fund"), which may reflect a
    template or profile that's since been edited or deleted. Always a
    fresh, independent, non-template profile - deploying to two Funds
    never leaves them silently sharing (and able to drift) the same
    row."""
    new_id = create_risk_profile(
        new_name, is_template=False, description=snapshot.get("description"),
        **{k: snapshot[k] for k in _RISK_PROFILE_FIELDS if k not in ("name", "description")},
    )
    update_martingale_settings(new_id, **{k: snapshot["martingale"][k] for k in _MARTINGALE_FIELDS})
    update_compounding_settings(new_id, **{k: snapshot["compounding"][k] for k in _COMPOUNDING_FIELDS})
    update_profit_vault_settings(new_id, **{k: snapshot["profit_vault"][k] for k in _VAULT_FIELDS})
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
def set_session_martingale_disabled(session_id, disabled=True):
    """A per-session override, not a mutation of the shared risk profile
    (which other sessions/funds may also be using) - see
    risk_engine.compute_position_size, which checks this before applying
    the profile's martingale ladder at all."""
    conn = get_connection()
    conn.execute(
        "UPDATE trading_sessions SET martingale_disabled = ? WHERE id = ?",
        (1 if disabled else 0, session_id),
    )
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
def update_listener_heartbeat(generation, worker_count, demo_mode_verified,
                               listener_pid=None, listener_uptime_min=None, listener_mem_mb=None,
                               chrome_count=None, chrome_mem_mb=None):
    conn = get_connection()
    now = datetime.now().isoformat()
    conn.execute("""
        UPDATE ui_listener_heartbeat
        SET generation = ?, worker_count = ?, demo_mode_verified = ?, updated_at = ?,
            listener_pid = ?, listener_uptime_min = ?, listener_mem_mb = ?,
            chrome_count = ?, chrome_mem_mb = ?
        WHERE id = 1
    """, (generation, worker_count, 1 if demo_mode_verified else 0, now,
          listener_pid, listener_uptime_min, listener_mem_mb, chrome_count, chrome_mem_mb))
    if conn.total_changes == 0:
        conn.execute("""
            INSERT INTO ui_listener_heartbeat (
                id, generation, worker_count, demo_mode_verified, updated_at,
                listener_pid, listener_uptime_min, listener_mem_mb, chrome_count, chrome_mem_mb
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (generation, worker_count, 1 if demo_mode_verified else 0, now,
              listener_pid, listener_uptime_min, listener_mem_mb, chrome_count, chrome_mem_mb))
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
# Live-mode trade confirmation gate (docs/AXIM_APP_PLAN.md) -
# core/session_manager.py owns the actual wait/timeout logic; this is
# just the CRUD + row shaping layer.
# ---------------------------------------------------------------------

@timed("database")
def create_pending_trade_confirmation(trade_id, session_id, asset, direction, expiry, amount):
    conn = get_connection()
    now = datetime.now().isoformat()
    conn.execute("""
        INSERT INTO pending_trade_confirmations (
            trade_id, session_id, asset, direction, expiry, amount, status, requested_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
    """, (trade_id, session_id, asset, direction, expiry, amount, now))
    conn.commit()
    conn.close()


@timed("database")
def get_pending_trade_confirmation(trade_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM pending_trade_confirmations WHERE trade_id = ?", (trade_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


@timed("database")
def list_pending_trade_confirmations():
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM pending_trade_confirmations WHERE status = 'pending' ORDER BY requested_at ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@timed("database")
def decide_trade_confirmation(trade_id, decision, decided_by=None):
    """decision is 'confirmed' or 'rejected'. Only updates a row that is
    still 'pending' (WHERE-guarded) - a late double-click or a decision
    arriving after the coordinator already timed it out must not
    silently overwrite the real outcome. Returns True if this call is
    the one that actually recorded the decision."""
    if decision not in ("confirmed", "rejected"):
        raise ValueError(f"invalid decision: {decision!r}")
    conn = get_connection()
    cursor = conn.execute(
        "UPDATE pending_trade_confirmations SET status = ?, decided_at = ?, decided_by = ? "
        "WHERE trade_id = ? AND status = 'pending'",
        (decision, datetime.now().isoformat(), decided_by, trade_id),
    )
    conn.commit()
    updated = cursor.rowcount > 0
    conn.close()
    return updated


@timed("database")
def expire_trade_confirmation(trade_id):
    """Called by the coordinator itself when its wait times out - fails
    closed. WHERE-guarded the same way, so a decision that arrives in
    the same instant the timeout fires doesn't get clobbered."""
    conn = get_connection()
    conn.execute(
        "UPDATE pending_trade_confirmations SET status = 'expired', decided_at = ? WHERE trade_id = ? AND status = 'pending'",
        (datetime.now().isoformat(), trade_id),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------
# Rule Builder (docs/AXIM_APP_PLAN.md) - core/rule_engine.py owns the
# actual condition/action logic; this is just the CRUD + row shaping
# layer, matching every other feature table in this module.
# ---------------------------------------------------------------------

def _rule_row_to_dict(row):
    d = dict(row)
    d["condition_params"] = json.loads(d["condition_params_json"]) if d["condition_params_json"] else {}
    d["action_params"] = json.loads(d["action_params_json"]) if d["action_params_json"] else {}
    d["last_condition_state"] = bool(d["last_condition_state"])
    return d


_VALID_RULE_SCOPES = {"fund", "session"}


@timed("database")
def create_rule(name, condition_type, condition_params, action_type, action_params, enabled=True,
                 fund_id=None, scope="fund", session_id=None):
    if scope not in _VALID_RULE_SCOPES:
        raise ValueError(f"invalid rule scope: {scope!r}")
    if scope == "session" and session_id is None:
        raise ValueError("a session-scoped rule needs a session_id")
    conn = get_connection()
    now = datetime.now().isoformat()
    cursor = conn.execute("""
        INSERT INTO rules (
            name, enabled, condition_type, condition_params_json,
            action_type, action_params_json, last_condition_state, created_at, updated_at,
            fund_id, scope, session_id
        ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)
    """, (name, 1 if enabled else 0, condition_type, json.dumps(condition_params),
          action_type, json.dumps(action_params), now, now, fund_id, scope, session_id))
    conn.commit()
    rule_id = cursor.lastrowid
    conn.close()
    return rule_id


@timed("database")
def get_rule(rule_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()
    conn.close()
    return _rule_row_to_dict(row) if row else None


@timed("database")
def list_rules(fund_id=None):
    conn = get_connection()
    if fund_id is not None:
        rows = conn.execute("SELECT * FROM rules WHERE fund_id = ? ORDER BY id DESC", (fund_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM rules ORDER BY id DESC").fetchall()
    conn.close()
    return [_rule_row_to_dict(r) for r in rows]


_RULE_UPDATABLE_FIELDS = {"name", "enabled", "condition_type", "action_type", "fund_id", "scope", "session_id"}


@timed("database")
def update_rule(rule_id, condition_params=None, action_params=None, **fields):
    for key in fields:
        if key not in _RULE_UPDATABLE_FIELDS:
            raise ValueError(f"Unknown rule field: {key!r}")
    if "scope" in fields and fields["scope"] not in _VALID_RULE_SCOPES:
        raise ValueError(f"invalid rule scope: {fields['scope']!r}")
    set_clauses = [f"{key} = ?" for key in fields]
    params = list(fields.values())
    if condition_params is not None:
        set_clauses.append("condition_params_json = ?")
        params.append(json.dumps(condition_params))
    if action_params is not None:
        set_clauses.append("action_params_json = ?")
        params.append(json.dumps(action_params))
    if not set_clauses:
        return
    set_clauses.append("updated_at = ?")
    params.append(datetime.now().isoformat())
    params.append(rule_id)
    conn = get_connection()
    conn.execute(f"UPDATE rules SET {', '.join(set_clauses)} WHERE id = ?", params)
    conn.commit()
    conn.close()


@timed("database")
def delete_rule(rule_id):
    conn = get_connection()
    conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
    conn.execute("DELETE FROM rule_firings WHERE rule_id = ?", (rule_id,))
    conn.commit()
    conn.close()


@timed("database")
def delete_session_rules(session_id):
    """Session-scoped rules are a temporary override for exactly one
    trading_sessions row - called from session_manager.end_session so
    they never outlive the session they were created for."""
    conn = get_connection()
    conn.execute("DELETE FROM rules WHERE scope = 'session' AND session_id = ?", (session_id,))
    conn.commit()
    conn.close()


@timed("database")
def record_rule_evaluation(rule_id, condition_state, fired):
    """Always updates last_condition_state (the edge-trigger memory);
    only bumps trigger_count/last_triggered_at when the action actually
    fired this evaluation."""
    conn = get_connection()
    if fired:
        conn.execute(
            "UPDATE rules SET last_condition_state = ?, trigger_count = trigger_count + 1, "
            "last_triggered_at = ? WHERE id = ?",
            (1 if condition_state else 0, datetime.now().isoformat(), rule_id),
        )
    else:
        conn.execute(
            "UPDATE rules SET last_condition_state = ? WHERE id = ?",
            (1 if condition_state else 0, rule_id),
        )
    conn.commit()
    conn.close()


@timed("database")
def record_rule_firing(rule_id, outcome_message):
    conn = get_connection()
    conn.execute(
        "INSERT INTO rule_firings (rule_id, fired_at, outcome_message) VALUES (?, ?, ?)",
        (rule_id, datetime.now().isoformat(), outcome_message),
    )
    conn.commit()
    conn.close()


@timed("database")
def list_rule_firings(rule_id, limit=20):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM rule_firings WHERE rule_id = ? ORDER BY id DESC LIMIT ?", (rule_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------
# Cross-process real-time event outbox - core/event_stream.py (listener
# process) writes, api/event_stream_routes.py's SSE endpoint (API
# process) reads. See server_events' own CREATE TABLE comment above for
# the full reasoning.
# ---------------------------------------------------------------------

@timed("database")
def record_server_event(event_type, payload=None):
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO server_events (event_type, payload_json, created_at) VALUES (?, ?, ?)",
        (event_type, json.dumps(payload) if payload is not None else None, datetime.now().isoformat()),
    )
    conn.commit()
    event_id = cursor.lastrowid
    conn.close()
    return event_id


@timed("database")
def list_server_events_since(last_id, limit=200):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM server_events WHERE id > ? ORDER BY id ASC LIMIT ?", (last_id, limit)
    ).fetchall()
    conn.close()
    results = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d["payload_json"]) if d["payload_json"] else None
        results.append(d)
    return results


@timed("database")
def oldest_server_event_id():
    """Used by the SSE tailer to detect a stale Last-Event-ID (one older
    than anything still in the table, because prune_server_events already
    removed it) so it can emit an explicit resync signal instead of
    silently skipping the gap. None if the table is empty."""
    conn = get_connection()
    row = conn.execute("SELECT MIN(id) AS min_id FROM server_events").fetchone()
    conn.close()
    return row["min_id"]


@timed("database")
def prune_server_events(older_than_hours=72):
    conn = get_connection()
    cutoff = (datetime.now() - timedelta(hours=older_than_hours)).isoformat()
    conn.execute("DELETE FROM server_events WHERE created_at < ?", (cutoff,))
    conn.commit()
    conn.close()


@timed("database")
def latest_server_event_id():
    """The SSE poller's starting cursor when it first starts up - a fresh
    poller begins from "now" (only new events going forward), not from
    the start of history. None if the table is empty."""
    conn = get_connection()
    row = conn.execute("SELECT MAX(id) AS max_id FROM server_events").fetchone()
    conn.close()
    return row["max_id"]


# ---------------------------------------------------------------------
# In-app notifications - api/notifications.py, core/rule_engine.py's
# notify_owner action.
# ---------------------------------------------------------------------

@timed("database")
def create_notification(user_id, message, source=None):
    conn = get_connection()
    now = datetime.now().isoformat()
    cursor = conn.execute(
        "INSERT INTO notifications (user_id, message, source, created_at) VALUES (?, ?, ?, ?)",
        (user_id, message, source, now),
    )
    conn.commit()
    notification_id = cursor.lastrowid
    conn.close()
    record_server_event("notification.created", {"user_id": user_id, "message": message, "source": source})
    return notification_id


@timed("database")
def list_notifications(user_id, unread_only=False, limit=50):
    conn = get_connection()
    if unread_only:
        rows = conn.execute(
            "SELECT * FROM notifications WHERE user_id = ? AND read_at IS NULL ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@timed("database")
def count_unread_notifications(user_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM notifications WHERE user_id = ? AND read_at IS NULL", (user_id,)
    ).fetchone()
    conn.close()
    return row["n"]


@timed("database")
def mark_notification_read(notification_id):
    conn = get_connection()
    conn.execute(
        "UPDATE notifications SET read_at = ? WHERE id = ? AND read_at IS NULL",
        (datetime.now().isoformat(), notification_id),
    )
    conn.commit()
    conn.close()


@timed("database")
def mark_all_notifications_read(user_id):
    conn = get_connection()
    conn.execute(
        "UPDATE notifications SET read_at = ? WHERE user_id = ? AND read_at IS NULL",
        (datetime.now().isoformat(), user_id),
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
    "stripe_customer_id", "stripe_subscription_id",
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
def get_owner_user():
    """The account created via /bootstrap-owner - there is exactly one,
    by construction (see auth_routes.bootstrap_owner). Used to resolve
    who "notify Owner" rule actions actually notify."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM users WHERE role = 'owner' ORDER BY id LIMIT 1").fetchone()
    conn.close()
    return dict(row) if row else None


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
def get_user_by_stripe_customer_id(stripe_customer_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM users WHERE stripe_customer_id = ?", (stripe_customer_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


@timed("database")
def check_and_expire_trial(user):
    """Real trial-expiration enforcement (docs/AXIM_APP_PLAN.md Phase 6) -
    called on every login and every authenticated request. If a
    trial-tier user's trial_expires_at has passed and they aren't
    already expired/disabled/suspended, flips access_state to 'expired'
    (already one of auth_routes._BLOCKED_ACCESS_STATES, so this alone is
    enough to lock the account out - no separate cron process needed).
    Returns the (possibly updated) user dict. Does not touch access_tier
    itself - Owner can still see what plan a now-expired user was on."""
    if user["access_tier"] != "trial" or not user["trial_expires_at"]:
        return user
    if user["access_state"] in ("expired", "disabled", "suspended"):
        return user
    if datetime.now().isoformat() < user["trial_expires_at"]:
        return user
    update_user(user["id"], access_state="expired")
    return get_user_by_id(user["id"])


@timed("database")
def set_user_password(user_id, new_password):
    """Also clears any brute-force lockout (failed_login_count/
    locked_until) - a password change (self-service or admin reset) is a
    stronger signal than just waiting out LOCKOUT_MINUTES, and there's no
    reason to leave a legitimately-reset account still locked."""
    import auth
    conn = get_connection()
    conn.execute(
        "UPDATE users SET password_hash = ?, failed_login_count = 0, locked_until = NULL WHERE id = ?",
        (auth.hash_password(new_password), user_id),
    )
    conn.commit()
    conn.close()


@timed("database")
def verify_user_credentials(email, password):
    """Returns the user dict if email+password are correct AND the
    account isn't locked out, else None. Does not itself check
    access_state beyond that - callers (api/auth.py) decide what each
    access_state is allowed to do (e.g. 'pending_approval' can log in
    but sees a waiting screen, 'disabled' can't log in at all).

    Lockout is checked here (not just by the caller separately) so a
    locked-but-otherwise-correct password still returns None - there is
    no path where the lockout can be bypassed by calling this directly."""
    import auth
    user = get_user_by_email(email)
    if user is None:
        return None
    if is_account_locked(email):
        return None
    if not auth.verify_password(password, user["password_hash"]):
        return None
    return user


MAX_FAILED_LOGIN_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


@timed("database")
def is_account_locked(email):
    """Returns the ISO lockout-expiry timestamp if this account is
    currently locked out, else None. Self-healing: an expired lock is
    cleared here on read rather than needing a separate cron/cleanup
    job - the next login attempt after the window naturally gets a clean
    slate. Nonexistent email -> never locked (nothing to lock; also
    avoids leaking account existence through lockout behavior)."""
    conn = get_connection()
    row = conn.execute(
        "SELECT id, locked_until FROM users WHERE email = ?", (email,)
    ).fetchone()
    if row is None or row["locked_until"] is None:
        conn.close()
        return None
    if datetime.now().isoformat() < row["locked_until"]:
        conn.close()
        return row["locked_until"]
    conn.execute(
        "UPDATE users SET failed_login_count = 0, locked_until = NULL WHERE id = ?", (row["id"],)
    )
    conn.commit()
    conn.close()
    return None


@timed("database")
def record_failed_login(email):
    """Increments the per-account failed-attempt counter and locks the
    account for LOCKOUT_MINUTES once MAX_FAILED_LOGIN_ATTEMPTS is
    reached. No-op for a nonexistent email - same reasoning as
    is_account_locked, there's no real account to protect and doing
    nothing here doesn't reveal whether the email matched anything (the
    API layer already returns the identical generic error either way)."""
    conn = get_connection()
    row = conn.execute("SELECT id, failed_login_count FROM users WHERE email = ?", (email,)).fetchone()
    if row is None:
        conn.close()
        return
    new_count = row["failed_login_count"] + 1
    if new_count >= MAX_FAILED_LOGIN_ATTEMPTS:
        locked_until = (datetime.now() + timedelta(minutes=LOCKOUT_MINUTES)).isoformat()
        conn.execute(
            "UPDATE users SET failed_login_count = ?, locked_until = ? WHERE id = ?",
            (new_count, locked_until, row["id"]),
        )
    else:
        conn.execute("UPDATE users SET failed_login_count = ? WHERE id = ?", (new_count, row["id"]))
    conn.commit()
    conn.close()


@timed("database")
def reset_failed_login(user_id):
    conn = get_connection()
    conn.execute("UPDATE users SET failed_login_count = 0, locked_until = NULL WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()


@timed("database")
def record_login(user_id):
    conn = get_connection()
    conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (datetime.now().isoformat(), user_id))
    conn.commit()
    conn.close()


@timed("database")
def create_session(user_id, expires_hours=720, client_name=None, client_type="web"):
    """Default 30-day expiry (720h) - a local single-operator-class tool
    with a 'remember me' checkbox on login, not a bank session timeout.
    client_name/client_type identify a Remote Client device (see
    docs/AXIM_REMOTE_ACCESS.md) for the "Connected Devices" view - both
    optional, default to an unnamed 'web' session (today's plain browser
    login), so every existing call site keeps working unchanged."""
    import auth
    raw_token, token_hash = auth.generate_session_token()
    now = datetime.now()
    expires_at = (now + timedelta(hours=expires_hours)).isoformat()
    conn = get_connection()
    conn.execute(
        """INSERT INTO auth_sessions (user_id, token_hash, created_at, last_seen_at, expires_at, client_name, client_type)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (user_id, token_hash, now.isoformat(), now.isoformat(), expires_at, client_name, client_type),
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
        """SELECT id, created_at, last_seen_at, expires_at, client_name, client_type
           FROM auth_sessions WHERE user_id = ? ORDER BY last_seen_at DESC""",
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
def revoke_other_sessions(user_id, keep_raw_token):
    """Used after a self-service password change - the same
    credential-compromise-recovery reasoning as revoke_all_sessions (an
    attacker holding a stolen session must be kicked out), but scoped to
    exclude the session actively making the change so the user isn't
    immediately logged out of their own device."""
    import auth
    keep_hash = auth.hash_token(keep_raw_token)
    conn = get_connection()
    conn.execute("DELETE FROM auth_sessions WHERE user_id = ? AND token_hash != ?", (user_id, keep_hash))
    conn.commit()
    conn.close()


@timed("database")
def revoke_session(session_id, user_id=None):
    """Revoke ONE device/session (the 'Connected Devices' panel's per-row
    action) rather than every session for a user. user_id, if given,
    scopes the delete to that user too - the self-service "log out this
    device of mine" call site passes the caller's own id so a user can
    never revoke someone else's session by guessing an id; the admin
    'revoke any user's session' call site omits it."""
    conn = get_connection()
    if user_id is not None:
        conn.execute("DELETE FROM auth_sessions WHERE id = ? AND user_id = ?", (session_id, user_id))
    else:
        conn.execute("DELETE FROM auth_sessions WHERE id = ?", (session_id,))
    conn.commit()
    conn.close()


_PASSWORD_RESET_TOKEN_TTL_MINUTES = 30
_PASSWORD_RESET_MIN_REQUEST_INTERVAL_SECONDS = 60


@timed("database")
def password_reset_recently_requested(user_id):
    """True if a reset was requested within the last
    _PASSWORD_RESET_MIN_REQUEST_INTERVAL_SECONDS - a basic guard against
    using the forgot-password endpoint to spam a real user's inbox
    (repeated requests can't be told apart from a genuine retry by the
    caller alone, since the HTTP response is deliberately the same
    either way - see api/auth_routes.py's forgot_password)."""
    conn = get_connection()
    row = conn.execute(
        "SELECT created_at FROM password_reset_tokens WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    conn.close()
    if row is None:
        return False
    elapsed = (datetime.now() - datetime.fromisoformat(row["created_at"])).total_seconds()
    return elapsed < _PASSWORD_RESET_MIN_REQUEST_INTERVAL_SECONDS


@timed("database")
def invalidate_password_reset_tokens_for_user(user_id):
    """Deletes every not-yet-used token for this user - called before
    issuing a new one (a fresh 'forgot password' request supersedes any
    prior link) and after a successful reset (the token that was just
    redeemed, plus any other still-outstanding ones, must all stop
    working)."""
    conn = get_connection()
    conn.execute("DELETE FROM password_reset_tokens WHERE user_id = ? AND used_at IS NULL", (user_id,))
    conn.commit()
    conn.close()


@timed("database")
def create_password_reset_token(user_id):
    """Returns (raw_token, expires_at) - only token_hash is persisted,
    same reasoning as auth_sessions. Invalidates any prior outstanding
    token for this user first, so at most one reset link is ever valid
    at a time."""
    import auth
    invalidate_password_reset_tokens_for_user(user_id)
    raw_token, token_hash = auth.generate_session_token()
    now = datetime.now()
    expires_at = (now + timedelta(minutes=_PASSWORD_RESET_TOKEN_TTL_MINUTES)).isoformat()
    conn = get_connection()
    conn.execute(
        "INSERT INTO password_reset_tokens (user_id, token_hash, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (user_id, token_hash, now.isoformat(), expires_at),
    )
    conn.commit()
    conn.close()
    return raw_token, expires_at


@timed("database")
def get_valid_password_reset_token(raw_token):
    """Returns the owning user's row if raw_token hashes to a token that
    exists, hasn't been used, and hasn't expired - else None. Never
    distinguishes "wrong token" from "expired token" from "already used"
    in what it returns, so a caller can't be tricked into leaking which
    case applies."""
    import auth
    token_hash = auth.hash_token(raw_token)
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM password_reset_tokens WHERE token_hash = ? AND used_at IS NULL", (token_hash,)
    ).fetchone()
    if row is None:
        conn.close()
        return None
    if row["expires_at"] < datetime.now().isoformat():
        conn.close()
        return None
    user = conn.execute("SELECT * FROM users WHERE id = ?", (row["user_id"],)).fetchone()
    conn.close()
    return dict(user) if user else None


@timed("database")
def consume_password_reset_token(raw_token):
    """Marks the token used - called only after the password has already
    been successfully updated, so a failure partway through never leaves
    a token burned with no password change to show for it."""
    import auth
    token_hash = auth.hash_token(raw_token)
    conn = get_connection()
    conn.execute(
        "UPDATE password_reset_tokens SET used_at = ? WHERE token_hash = ? AND used_at IS NULL",
        (datetime.now().isoformat(), token_hash),
    )
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


# ---------------------------------------------------------------------
# Backtest Engine / Strategy Lab (docs/AXIM_APP_PLAN.md) -
# core/backtest_engine.py owns the actual simulation math; this is the
# CRUD + row-shaping layer, matching every other feature table in this
# module.
# ---------------------------------------------------------------------

@timed("database")
def create_imported_signal(source_label, asset, direction, expiry, received_at, raw_message=None,
                            result=None, payout_percent=None, profit_loss=None, notes=None, import_batch=None):
    conn = get_connection()
    now = datetime.now().isoformat()
    cursor = conn.execute("""
        INSERT INTO imported_signals (
            source_label, raw_message, asset, direction, expiry, received_at,
            result, payout_percent, profit_loss, notes, import_batch, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (source_label, raw_message, asset, direction, expiry, received_at,
          result, payout_percent, profit_loss, notes, import_batch, now))
    conn.commit()
    signal_id = cursor.lastrowid
    conn.close()
    return signal_id


@timed("database")
def list_imported_signals(import_batch=None, graded_only=False, limit=500):
    conn = get_connection()
    query = "SELECT * FROM imported_signals"
    conditions = []
    params = []
    if import_batch is not None:
        conditions.append("import_batch = ?")
        params.append(import_batch)
    if graded_only:
        conditions.append("result IS NOT NULL")
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY received_at ASC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@timed("database")
def grade_imported_signal(signal_id, result, payout_percent=None, profit_loss=None):
    if result not in ("win", "loss", "draw"):
        raise ValueError(f"invalid result: {result!r}")
    conn = get_connection()
    conn.execute(
        "UPDATE imported_signals SET result = ?, payout_percent = ?, profit_loss = ? WHERE id = ?",
        (result, payout_percent, profit_loss, signal_id),
    )
    conn.commit()
    conn.close()


@timed("database")
def delete_imported_signal(signal_id):
    conn = get_connection()
    conn.execute("DELETE FROM imported_signals WHERE id = ?", (signal_id,))
    conn.commit()
    conn.close()


@timed("database")
def list_historical_signal_sources():
    """Distinct channel/source labels actually present across both real
    (`signals`) and imported (`imported_signals`) history - powers the
    Strategy Lab's source filter dropdown without hardcoding a list."""
    conn = get_connection()
    live = [r["channel"] for r in conn.execute(
        "SELECT DISTINCT channel FROM signals WHERE channel IS NOT NULL AND result IN ('win','loss','draw')"
    ).fetchall()]
    imported = [r["source_label"] for r in conn.execute(
        "SELECT DISTINCT source_label FROM imported_signals WHERE source_label IS NOT NULL AND result IS NOT NULL"
    ).fetchall()]
    conn.close()
    return sorted(set(live) | set(imported))


@timed("database")
def get_historical_signal_pool(source, channel_filter=None, date_from=None, date_to=None):
    """Normalizes real `signals` rows and `imported_signals` rows into one
    common shape for core/backtest_engine.py: {source_type, signal_id,
    channel, asset, direction, expiry, timestamp, result, payout_percent,
    profit_loss, trade_amount}. source is "live", "imported", or "both".
    Only rows with a known win/loss/draw result are returned - an
    ungraded/unresolved signal can't be simulated."""
    pool = []
    conn = get_connection()

    if source in ("live", "both"):
        query = "SELECT * FROM signals WHERE result IN ('win', 'loss', 'draw')"
        params = []
        if date_from:
            query += " AND received_at >= ?"
            params.append(date_from)
        if date_to:
            query += " AND received_at <= ?"
            params.append(date_to)
        if channel_filter:
            placeholders = ", ".join("?" for _ in channel_filter)
            query += f" AND channel IN ({placeholders})"
            params.extend(channel_filter)
        query += " ORDER BY received_at ASC"
        for row in conn.execute(query, params).fetchall():
            r = dict(row)
            payout_percent = (r["payout"] if r["payout"] is not None else
                               (round((r["profit_loss"] / r["trade_amount"]) * 100, 2)
                                if r["result"] == "win" and r["trade_amount"] else None))
            pool.append({
                "source_type": "live", "signal_id": r["id"], "channel": r["channel"],
                "asset": r["asset"], "direction": r["direction"], "expiry": r["timeframe"],
                "timestamp": r["received_at"], "result": r["result"],
                "payout_percent": payout_percent, "profit_loss": r["profit_loss"],
                "trade_amount": r["trade_amount"],
            })

    if source in ("imported", "both"):
        query = "SELECT * FROM imported_signals WHERE result IN ('win', 'loss', 'draw')"
        params = []
        if date_from:
            query += " AND received_at >= ?"
            params.append(date_from)
        if date_to:
            query += " AND received_at <= ?"
            params.append(date_to)
        if channel_filter:
            placeholders = ", ".join("?" for _ in channel_filter)
            query += f" AND source_label IN ({placeholders})"
            params.extend(channel_filter)
        query += " ORDER BY received_at ASC"
        for row in conn.execute(query, params).fetchall():
            r = dict(row)
            pool.append({
                "source_type": "imported", "signal_id": r["id"], "channel": r["source_label"],
                "asset": r["asset"], "direction": r["direction"], "expiry": r["expiry"],
                "timestamp": r["received_at"], "result": r["result"],
                "payout_percent": r["payout_percent"], "profit_loss": r["profit_loss"],
                "trade_amount": None,
            })

    conn.close()
    pool.sort(key=lambda s: s["timestamp"])
    return pool


@timed("database")
def create_backtest_run(name, signal_pool, starting_bankroll, default_payout_percent=85,
                         session_window="daily", created_by=None):
    conn = get_connection()
    now = datetime.now().isoformat()
    cursor = conn.execute("""
        INSERT INTO backtest_runs (
            name, signal_pool_json, starting_bankroll, default_payout_percent,
            session_window, status, created_by, created_at
        ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
    """, (name, json.dumps(signal_pool), starting_bankroll, default_payout_percent,
          session_window, created_by, now))
    conn.commit()
    run_id = cursor.lastrowid
    conn.close()
    return run_id


def _backtest_run_row_to_dict(row):
    d = dict(row)
    d["signal_pool"] = json.loads(d["signal_pool_json"]) if d["signal_pool_json"] else {}
    return d


@timed("database")
def get_backtest_run(run_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM backtest_runs WHERE id = ?", (run_id,)).fetchone()
    conn.close()
    return _backtest_run_row_to_dict(row) if row else None


@timed("database")
def list_backtest_runs(limit=50):
    conn = get_connection()
    rows = conn.execute("SELECT * FROM backtest_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [_backtest_run_row_to_dict(r) for r in rows]


@timed("database")
def update_backtest_run_status(run_id, status, error_message=None):
    if status not in ("pending", "running", "completed", "failed"):
        raise ValueError(f"invalid backtest run status: {status!r}")
    conn = get_connection()
    completed_at = datetime.now().isoformat() if status in ("completed", "failed") else None
    conn.execute(
        "UPDATE backtest_runs SET status = ?, error_message = ?, completed_at = COALESCE(?, completed_at) WHERE id = ?",
        (status, error_message, completed_at, run_id),
    )
    conn.commit()
    conn.close()


@timed("database")
def create_backtest_strategy(backtest_run_id, risk_profile_id, label, profile_snapshot):
    conn = get_connection()
    now = datetime.now().isoformat()
    cursor = conn.execute("""
        INSERT INTO backtest_strategies (backtest_run_id, risk_profile_id, label, profile_snapshot_json, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (backtest_run_id, risk_profile_id, label, json.dumps(profile_snapshot), now))
    conn.commit()
    strategy_id = cursor.lastrowid
    conn.close()
    return strategy_id


def _backtest_strategy_row_to_dict(row):
    d = dict(row)
    d["profile_snapshot"] = json.loads(d["profile_snapshot_json"]) if d["profile_snapshot_json"] else {}
    return d


@timed("database")
def list_backtest_strategies(backtest_run_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM backtest_strategies WHERE backtest_run_id = ? ORDER BY id ASC", (backtest_run_id,)
    ).fetchall()
    conn.close()
    return [_backtest_strategy_row_to_dict(r) for r in rows]


@timed("database")
def get_backtest_strategy(strategy_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM backtest_strategies WHERE id = ?", (strategy_id,)).fetchone()
    conn.close()
    return _backtest_strategy_row_to_dict(row) if row else None


@timed("database")
def create_backtest_session(backtest_strategy_id, session_index, started_at, status,
                             starting_balance, realized_pnl=0, trades_count=0,
                             ending_martingale_step=0, ending_vaulted_amount=0, ended_at=None):
    conn = get_connection()
    cursor = conn.execute("""
        INSERT INTO backtest_sessions (
            backtest_strategy_id, session_index, started_at, ended_at, status,
            starting_balance, realized_pnl, trades_count, ending_martingale_step, ending_vaulted_amount
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (backtest_strategy_id, session_index, started_at, ended_at, status,
          starting_balance, realized_pnl, trades_count, ending_martingale_step, ending_vaulted_amount))
    conn.commit()
    session_id = cursor.lastrowid
    conn.close()
    return session_id


@timed("database")
def list_backtest_sessions(backtest_strategy_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM backtest_sessions WHERE backtest_strategy_id = ? ORDER BY session_index ASC",
        (backtest_strategy_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@timed("database")
def create_backtest_trade(backtest_session_id, signal_source_type, signal_id, sequence_in_session,
                           asset, direction, occurred_at, trade_amount, martingale_step, result,
                           profit_loss, running_balance):
    conn = get_connection()
    cursor = conn.execute("""
        INSERT INTO backtest_trades (
            backtest_session_id, signal_source_type, signal_id, sequence_in_session,
            asset, direction, occurred_at, trade_amount, martingale_step, result,
            profit_loss, running_balance
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (backtest_session_id, signal_source_type, signal_id, sequence_in_session,
          asset, direction, occurred_at, trade_amount, martingale_step, result,
          profit_loss, running_balance))
    conn.commit()
    trade_id = cursor.lastrowid
    conn.close()
    return trade_id


@timed("database")
def list_backtest_trades(backtest_session_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM backtest_trades WHERE backtest_session_id = ? ORDER BY sequence_in_session ASC",
        (backtest_session_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@timed("database")
def list_backtest_trades_for_strategy(backtest_strategy_id):
    conn = get_connection()
    rows = conn.execute("""
        SELECT bt.* FROM backtest_trades bt
        JOIN backtest_sessions bs ON bt.backtest_session_id = bs.id
        WHERE bs.backtest_strategy_id = ?
        ORDER BY bt.occurred_at ASC, bt.id ASC
    """, (backtest_strategy_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@timed("database")
def save_backtest_metrics(backtest_strategy_id, metrics):
    conn = get_connection()
    columns = list(metrics.keys())
    placeholders = ", ".join("?" for _ in columns)
    values = [metrics[c] for c in columns]
    conn.execute(f"""
        INSERT INTO backtest_metrics (backtest_strategy_id, {', '.join(columns)})
        VALUES (?, {placeholders})
        ON CONFLICT(backtest_strategy_id) DO UPDATE SET
        {', '.join(f'{c} = excluded.{c}' for c in columns)}
    """, [backtest_strategy_id] + values)
    conn.commit()
    conn.close()


@timed("database")
def get_backtest_metrics(backtest_strategy_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM backtest_metrics WHERE backtest_strategy_id = ?", (backtest_strategy_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


@timed("database")
def get_backtest_report(run_id):
    """One call that assembles everything a report/comparison view needs:
    the run, every strategy in it, and each strategy's metrics - the
    session/trade-level detail is deliberately NOT inlined here (fetched
    separately, on demand, via list_backtest_sessions/list_backtest_trades)
    since a report with many strategies x many sessions x many trades
    would otherwise be a very large single payload."""
    run = get_backtest_run(run_id)
    if run is None:
        return None
    strategies = list_backtest_strategies(run_id)
    for s in strategies:
        s["metrics"] = get_backtest_metrics(s["id"])
    return {"run": run, "strategies": strategies}


@timed("database")
def delete_backtest_run(run_id):
    conn = get_connection()
    strategy_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM backtest_strategies WHERE backtest_run_id = ?", (run_id,)
    ).fetchall()]
    for strategy_id in strategy_ids:
        session_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM backtest_sessions WHERE backtest_strategy_id = ?", (strategy_id,)
        ).fetchall()]
        for session_id in session_ids:
            conn.execute("DELETE FROM backtest_trades WHERE backtest_session_id = ?", (session_id,))
        conn.execute("DELETE FROM backtest_sessions WHERE backtest_strategy_id = ?", (strategy_id,))
        conn.execute("DELETE FROM backtest_metrics WHERE backtest_strategy_id = ?", (strategy_id,))
    conn.execute("DELETE FROM backtest_strategies WHERE backtest_run_id = ?", (run_id,))
    conn.execute("DELETE FROM backtest_runs WHERE id = ?", (run_id,))
    conn.commit()
    conn.close()


if __name__ == "__main__":
    initialize_database()
    print("Database Ready")
