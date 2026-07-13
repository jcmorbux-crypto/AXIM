"""
ONE-TIME seed script for the isolated UI Vision preview database
(C:\\AXIM-ui-vision\\data\\axim.db) ONLY - never touches production's
C:\\AXIM\\data\\axim.db, which is a physically separate file (verified
before running this).

Adds 3 additional demo Funds (on top of the 1 real "Primary Fund"
already in this snapshot) so the Portfolio Command Dashboard hybrid
can be evaluated at realistic multi-fund density, per the explicit
user instruction that preview data may be "read-only or seeded."

Every seeded row uses mode='demo' (never live_enabled=1) and reuses
REAL existing risk_profile templates and REAL existing signal-source
channels already in this snapshot, so the dashboard's strategy-name
and provider-name resolution runs through the same real code paths
it would for genuine data - only the Fund/session/trade rows
themselves are synthetic.

Run once: python seed_preview_funds.py
Safe to re-run - checks for existing seeded funds by name first.
"""
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "axim.db"
assert DB_PATH.exists(), f"expected isolated preview DB at {DB_PATH}"

SEED_TAG = "[preview-seed]"  # marker in broker account names so these are easy to find/remove later


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    existing = conn.execute(
        "SELECT COUNT(*) FROM broker_accounts WHERE name LIKE ?", (f"%{SEED_TAG}%",)
    ).fetchone()[0]
    if existing:
        print(f"Already seeded ({existing} seed broker accounts found) - skipping. "
              f"Delete rows matching '{SEED_TAG}' first to reseed.")
        conn.close()
        return

    now = datetime.now()

    # (fund_name, strategy_risk_profile_id, channel_id, starting_balance,
    #  today_pnl, fund_status, session_status)
    # risk_profile_id 16=Balanced Growth, 22=Controlled Recovery,
    # 1=Capital Preservation - real starter templates already in this DB.
    # channel_id 16=Go+, 9=TYLER VIP CLUB, 76=Pocket Option Quant Algorithm
    # - real channels already enabled in this snapshot.
    plan = [
        ("Momentum Fund", 16, 16, 13640.00, 680.50, "active", "active"),
        ("Conservative Fund", 1, 9, 8954.12, 0.00, "active", "paused"),
        ("Recovery Test Fund", 22, 76, 3111.02, -111.02, "active", "stopped_risk"),
    ]

    for name, risk_profile_id, channel_id, starting_balance, today_pnl, fund_status, session_status in plan:
        broker_name = f"{name} Broker {SEED_TAG}"
        cur = conn.execute("""
            INSERT INTO broker_accounts (name, mode, live_enabled, connection_status, last_connected_at,
                last_balance, last_balance_checked_at, status, created_at, updated_at)
            VALUES (?, 'demo', 0, 'connected', ?, ?, ?, 'active', ?, ?)
        """, (broker_name, now.isoformat(), starting_balance + today_pnl, now.isoformat(),
              now.isoformat(), now.isoformat()))
        broker_account_id = cur.lastrowid

        cur = conn.execute("""
            INSERT INTO funds (name, starting_balance, default_risk_profile_id, profit_target, loss_limit,
                max_trades, status, created_at, updated_at)
            VALUES (?, ?, ?, 0, 0, 0, ?, ?, ?)
        """, (name, starting_balance, risk_profile_id, fund_status, now.isoformat(), now.isoformat()))
        fund_id = cur.lastrowid

        conn.execute("""
            INSERT INTO fund_broker_accounts (fund_id, broker_account_id, is_primary, created_at)
            VALUES (?, ?, 1, ?)
        """, (fund_id, broker_account_id, now.isoformat()))

        conn.execute("""
            INSERT INTO fund_sources (fund_id, channel_id, created_at) VALUES (?, ?, ?)
        """, (fund_id, channel_id, now.isoformat()))

        session_stop_reason = None
        if session_status == "stopped_risk":
            session_stop_reason = "stopped_fund_loss_limit"
        started_at = (now - timedelta(hours=3)).isoformat()
        ended_at = None if session_status == "active" else now.isoformat()
        db_session_status = "active" if session_status == "active" else "stopped_manual"
        conn.execute("""
            INSERT INTO trading_sessions (name, channel_ids_json, account_mode, profit_target, loss_limit,
                max_trades, status, trades_count, realized_pnl, started_at, ended_at, stop_reason,
                risk_profile_id, fund_id)
            VALUES (?, ?, 'demo', 0, 0, 0, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (f"{name} Session", f"[{channel_id}]", db_session_status,
              12 if session_status != "paused" else 4, today_pnl, started_at, ended_at,
              session_stop_reason, risk_profile_id, fund_id))

        print(f"Seeded fund_id={fund_id} {name!r} (broker_account_id={broker_account_id})")

    conn.commit()
    conn.close()
    print("Done. This only modified the isolated preview DB at:", DB_PATH)


if __name__ == "__main__":
    main()
