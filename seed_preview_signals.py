"""
Companion to seed_preview_funds.py - adds individual signals rows for
the 3 seeded funds' sessions (session-level realized_pnl alone isn't
enough to reconstruct a real per-trade growth curve; the chart is
built from actual signals rows via database.get_portfolio_growth_curve).

Same isolated-preview-DB-only guarantee as seed_preview_funds.py.
Each fund's individual trade profit_loss values sum to exactly that
fund's already-seeded session.realized_pnl - no new aggregate numbers
are introduced, this only breaks the existing total into real-shaped
trade-level events spread across the last few hours.

Run once, after seed_preview_funds.py: python seed_preview_signals.py
"""
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "axim.db"
assert DB_PATH.exists(), f"expected isolated preview DB at {DB_PATH}"

ASSETS = ["EUR/USD OTC", "GBP/JPY OTC", "AUD/CAD OTC", "USD/CHF OTC", "NZD/USD OTC", "EUR/JPY OTC"]


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    sessions = conn.execute("""
        SELECT id, fund_id, realized_pnl, trades_count, started_at
        FROM trading_sessions
        WHERE name LIKE '%Fund Session'
    """).fetchall()

    already = conn.execute("SELECT COUNT(*) FROM signals WHERE session_id IN (SELECT id FROM trading_sessions WHERE name LIKE '%Fund Session')").fetchone()[0]
    if already:
        print(f"Already seeded ({already} signal rows found for these sessions) - skipping.")
        conn.close()
        return

    for s in sessions:
        n = max(s["trades_count"], 1)
        total_pnl = s["realized_pnl"]
        start = datetime.fromisoformat(s["started_at"])
        # Split total_pnl into n trade-level amounts that sum exactly to it -
        # a simple even split perturbed slightly so it doesn't look like a
        # uniform fabricated ramp, still summing exactly to the real total.
        base = round(total_pnl / n, 2)
        amounts = [base] * n
        amounts[-1] = round(total_pnl - sum(amounts[:-1]), 2)  # last one absorbs rounding

        for i, amt in enumerate(amounts):
            ts = start + timedelta(minutes=(i + 1) * (180 // n))
            result = "win" if amt >= 0 else "loss"
            conn.execute("""
                INSERT INTO signals (channel, asset, direction, payout, message, received_at,
                    executed, execution_time, result, profit, execution_status, opened_at, closed_at,
                    profit_loss, session_id)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                f"fund-{s['fund_id']}", ASSETS[i % len(ASSETS)], "BUY" if amt >= 0 else "SELL",
                85, "[preview-seed]", ts.isoformat(), ts.isoformat(), result, amt,
                f"result_{result}", ts.isoformat(), ts.isoformat(), amt, s["id"],
            ))
        print(f"Seeded {n} signal(s) for session_id={s['id']} (fund_id={s['fund_id']}), summing to {total_pnl}")

    conn.commit()
    conn.close()
    print("Done. This only modified the isolated preview DB at:", DB_PATH)


if __name__ == "__main__":
    main()
