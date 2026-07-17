"""Backtest Engine / Strategy Lab (docs/AXIM_APP_PLAN.md) - replays a
pool of historical, already-resolved signals through one or more Risk
Engine profiles to see how each would have performed.

Deliberately reuses core/risk_engine.py's and core/capital_strategies.py's
PURE sizing/martingale/vault/Capital-Strategies functions (_base_amount,
_apply_martingale, milestone_vault_skim, every_winning_session_vault_skim,
momentum_deployment, cashflow_adjusted_amount, sentinel_adjusted_amount,
fortress_adjusted_amount, empire_advance, per_trade_vault_skim) rather
than re-implementing the same math a second time - a backtest whose
sizing logic silently drifts from what AXIM actually does live would be
worse than useless. Every AXIM Capital Strategies (tm) layer a profile
can have enabled (Momentum/Cashflow/Sentinel/Fortress/Empire, on top of
sizing_mode='apex_ascension') is now applied here exactly as
compute_position_size/on_trade_closed apply it live - a profile with
these features enabled backtests as it would actually trade, not as if
they silently didn't exist. `_base_amount` is called with
record_events=False here specifically, so a simulated apex_ascension
tier crossing never writes a real capital_tier_events row (a backtest
replays a profile SNAPSHOT, not the live profile). Only the parts that
are genuinely different between live trading and a backtest (no live DB
session row, a whole pool of signals instead of one at a time,
cross-session bankroll bookkeeping) live here.

Honesty notes:
- Trading balance is carried forward realistically across simulated
  sessions (starting_bankroll + cumulative realized P&L - vaulted
  amount) for EVERY profile, regardless of its sizing_mode or
  compounding setting - this is what a serious backtest should do. Live
  AXIM now does this too (core/risk_engine.py's compute_position_size
  reads fund_manager.get_fund_balances as a live override for any
  Fund-attached session, without mutating the shared risk_profiles.
  bankroll column - see that function's own comment) - this backtest
  engine's carry-forward and live sizing's carry-forward are two
  independently-written but now behaviorally consistent
  implementations of the same idea, not one covering a gap the other
  has.
- Martingale's same_asset_only/same_source_only fields are still not
  enforced here either, for the same reason risk_engine.py doesn't
  enforce them live - no fabricated behavior beyond what's real.
- risk_score and best_for_label are explicit, documented heuristics
  (see _risk_score/_best_for_label below), not a scientific
  classification - labeled as such in the UI.
"""
import csv
import io
import sys
from datetime import datetime
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CORE_DIR))

from collections import defaultdict

import database
import risk_engine
import capital_strategies
from logger import get_logger

logger = get_logger("axim.lifecycle", filename="lifecycle.log")

_VALID_SESSION_WINDOWS = {"daily", "all"}
_VALID_RESULTS = {"win", "loss", "draw"}

# Historical signal import (docs/AXIM_APP_PLAN.md) - CSV and Excel
# (.xlsx). Live Telegram-history scraping is a genuinely separate piece
# of work (a Telethon iter_messages integration) - still explicitly
# deferred. Column names are matched case-insensitively and accept a
# couple of common aliases so a reasonably-shaped export just works
# without the user needing to rename headers by hand - shared between
# both formats, since both ultimately produce the same "header row +
# data rows" shape.
_CSV_COLUMN_ALIASES = {
    "source_label": {"source_label", "source", "channel", "signal source"},
    "asset": {"asset", "symbol", "pair"},
    "direction": {"direction", "action", "side"},
    "expiry": {"expiry", "expiration", "timeframe", "duration"},
    "received_at": {"received_at", "timestamp", "date", "time", "datetime"},
    "result": {"result", "outcome"},
    "payout_percent": {"payout_percent", "payout", "payout %"},
    "notes": {"notes", "note", "comment"},
}


def _resolve_csv_columns(fieldnames):
    """Maps whatever headers the CSV actually has onto our canonical
    column names, case-insensitively. Returns {canonical: actual} for
    whichever canonical columns were found - missing ones are simply
    absent from the result, not an error (only asset/direction/
    received_at are actually required, checked by the caller)."""
    lowered = {(f or "").strip().lower(): f for f in fieldnames}
    resolved = {}
    for canonical, aliases in _CSV_COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in lowered:
                resolved[canonical] = lowered[alias]
                break
    return resolved


def _parse_signal_row(raw_row, columns):
    """Format-agnostic: raw_row is a plain {header: value} dict (values
    already stringified by the caller - CSV's DictReader gives strings
    natively, Excel cells need str()/'' conversion first, see
    parse_signal_excel). Returns the normalized row dict ready for
    database.create_imported_signal(**row); raises ValueError on any
    validation failure, caught by the caller and turned into an
    {"line": n, "message": ...} entry."""
    received_at = (raw_row.get(columns["received_at"]) or "").strip()
    asset = (raw_row.get(columns["asset"]) or "").strip()
    direction = (raw_row.get(columns["direction"]) or "").strip().upper()
    if not received_at or not asset or not direction:
        raise ValueError("asset/direction/received_at cannot be blank")

    result = None
    if "result" in columns:
        raw_result = (raw_row.get(columns["result"]) or "").strip().lower()
        if raw_result:
            if raw_result not in _VALID_RESULTS:
                raise ValueError(f"invalid result {raw_result!r}, must be win/loss/draw")
            result = raw_result

    payout_percent = None
    if "payout_percent" in columns:
        raw_payout = (raw_row.get(columns["payout_percent"]) or "").strip().replace("%", "")
        if raw_payout:
            payout_percent = float(raw_payout)

    return {
        "source_label": (raw_row.get(columns.get("source_label", "")) or "Imported").strip() or "Imported",
        "asset": asset, "direction": direction,
        "expiry": (raw_row.get(columns.get("expiry", "")) or "").strip() or None,
        "received_at": received_at, "result": result, "payout_percent": payout_percent,
        "notes": (raw_row.get(columns.get("notes", "")) or "").strip() or None,
    }


def parse_signal_csv(csv_text):
    """Pure parsing - returns (rows, errors). Each row in `rows` is a
    dict ready for database.create_imported_signal(**row) (plus grading
    via grade_imported_signal if result/payout_percent were present).
    `errors` is a list of {"line": n, "message": ...} for rows that
    couldn't be parsed - never silently dropped."""
    reader = csv.DictReader(io.StringIO(csv_text))
    if reader.fieldnames is None:
        return [], [{"line": 0, "message": "empty file or no header row"}]

    columns = _resolve_csv_columns(reader.fieldnames)
    missing_required = [c for c in ("asset", "direction", "received_at") if c not in columns]
    if missing_required:
        return [], [{"line": 0, "message": f"missing required column(s): {', '.join(missing_required)}"}]

    rows, errors = [], []
    for line_num, raw_row in enumerate(reader, start=2):  # header is line 1
        try:
            rows.append(_parse_signal_row(raw_row, columns))
        except Exception as e:
            errors.append({"line": line_num, "message": str(e)})

    return rows, errors


def parse_signal_excel(file_bytes):
    """Same contract as parse_signal_csv (rows, errors) - reads the
    FIRST worksheet only (multi-sheet workbooks aren't a modeled use
    case here; export/copy the relevant sheet to its own file if
    needed). Cell values come back from openpyxl as real Python types
    (datetime, float, etc, not strings) - normalized to str() before
    reusing the exact same row-validation logic parse_signal_csv uses,
    so a date cell and a "2024-01-15" text cell both work the same way
    a human typing dates into a spreadsheet would expect."""
    import openpyxl

    try:
        workbook = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    except Exception as e:
        return [], [{"line": 0, "message": f"could not read Excel file: {e}"}]

    sheet = workbook.worksheets[0]
    rows_iter = sheet.iter_rows(values_only=True)
    try:
        header_row = next(rows_iter)
    except StopIteration:
        return [], [{"line": 0, "message": "empty file or no header row"}]

    fieldnames = [str(h) if h is not None else "" for h in header_row]
    columns = _resolve_csv_columns(fieldnames)
    missing_required = [c for c in ("asset", "direction", "received_at") if c not in columns]
    if missing_required:
        return [], [{"line": 0, "message": f"missing required column(s): {', '.join(missing_required)}"}]

    def _cell_to_str(value):
        if value is None:
            return ""
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)

    rows, errors = [], []
    for line_num, raw_values in enumerate(rows_iter, start=2):  # header is row 1
        if raw_values is None or all(v is None for v in raw_values):
            continue  # a genuinely blank row, not a data row with blank required fields
        raw_row = {fieldnames[i]: _cell_to_str(v) for i, v in enumerate(raw_values) if i < len(fieldnames)}
        try:
            rows.append(_parse_signal_row(raw_row, columns))
        except Exception as e:
            errors.append({"line": line_num, "message": str(e)})

    return rows, errors


def _session_key(timestamp_iso, session_window):
    """Groups signals into sessions. 'daily' = one session per calendar
    date; 'all' = the entire pool as a single continuous session (useful
    for comparing strategies without daily reset noise)."""
    if session_window == "all":
        return "all"
    return timestamp_iso[:10]  # YYYY-MM-DD


def _group_signals_into_sessions(signal_pool, session_window):
    if session_window not in _VALID_SESSION_WINDOWS:
        session_window = "daily"
    groups = defaultdict(list)
    for signal in signal_pool:
        groups[_session_key(signal["timestamp"], session_window)].append(signal)
    return [groups[key] for key in sorted(groups.keys())]


_DISABLED = {"enabled": False}


def simulate_strategy(signal_pool, profile_snapshot, starting_bankroll, session_window="daily",
                       default_payout_percent=85, profit_target=0, loss_limit=0, max_trades=0):
    """Pure - no DB I/O, fully unit-testable (record_events=False on every
    risk_engine._base_amount call below keeps it that way even for
    apex_ascension, which would otherwise write a live capital_tier_events
    row). Returns {"sessions": [...], "trades": [...]} where each dict
    matches the shape database.create_backtest_session/create_backtest_trade
    expect (minus the ids assigned on insert).

    Applies every AXIM Capital Strategies (tm) layer compute_position_size/
    on_trade_closed apply live (Momentum, Cashflow, Sentinel, Fortress,
    Empire), reusing the exact same pure functions - a profile with these
    features enabled now backtests the same way it would actually trade,
    not silently as if they didn't exist. `.get(..., _DISABLED)` throughout
    means a profile_snapshot saved before these features existed (missing
    the new sub-config keys entirely) still simulates exactly as before -
    treated as not-enabled, the same default every new sub-table itself
    uses.

    Empire's current_level and Fortress's protected_principal are
    PROFILE-scoped state (they persist across sessions in live AXIM, tied
    to the risk_profile, not the trading_session) - tracked here outside
    the per-session loop for the same reason. Momentum's step is
    SESSION-scoped (trading_sessions.current_momentum_step), same as
    Martingale's, so it resets at the top of every simulated session.

    profit_target/loss_limit/max_trades are the SESSION-level stop
    conditions - same semantics as core/session_manager.check_session_limits
    (0 = disabled), applied fresh to every simulated session. Strike's own
    two genuinely distinct conditions (a consecutive-losses streak, a
    session duration cap) are simulated the same way check_session_limits
    enforces them live - its profit_target/max_session_loss/max_trades are
    the same fields already covered above, so re-simulating them under
    Strike's name would be inert."""
    session_groups = _group_signals_into_sessions(signal_pool, session_window)

    cumulative_realized_pnl = 0.0
    cumulative_vaulted = 0.0
    empire_current_level = profile_snapshot.get("empire", _DISABLED).get("current_level", 0)
    fortress_protected_principal = profile_snapshot.get("fortress", _DISABLED).get("protected_principal", 0)
    sessions = []
    trades = []

    for session_index, signals_in_session in enumerate(session_groups):
        trading_balance = max(0.0, starting_bankroll + cumulative_realized_pnl - cumulative_vaulted)
        profile = dict(profile_snapshot)
        profile["bankroll"] = trading_balance
        empire = {**profile.get("empire", _DISABLED), "current_level": empire_current_level}
        fortress = {**profile.get("fortress", _DISABLED), "protected_principal": fortress_protected_principal}
        profile["empire"] = empire
        profile["fortress"] = fortress
        momentum = profile.get("momentum", _DISABLED)
        cashflow = profile.get("cashflow", _DISABLED)
        sentinel = profile.get("drawdown_protection", _DISABLED)
        vault = profile.get("profit_vault", _DISABLED)
        strike = profile.get("strike", _DISABLED)

        session_state = {
            "realized_pnl": 0.0, "current_martingale_step": 0, "current_momentum_step": 0,
            "consecutive_losses": 0, "trades_count": 0,
        }
        session_vaulted = 0.0
        session_trades = []
        status = "completed"
        started_at = signals_in_session[0]["timestamp"]
        ended_at = started_at

        for seq, signal in enumerate(signals_in_session):
            try:
                amount = risk_engine._base_amount(profile, session_state, record_events=False)
            except risk_engine.EmpireChallengeOver as e:
                status = f"stopped_{e.rule}"
                break
            amount = risk_engine._apply_martingale(amount, profile["martingale"], session_state["current_martingale_step"])

            if momentum["enabled"]:
                amount = capital_strategies.momentum_deployment(amount, momentum, session_state["current_momentum_step"])

            stop_status = None
            if cashflow["enabled"]:
                amount, target_reached = capital_strategies.cashflow_adjusted_amount(
                    cashflow, amount, session_state["realized_pnl"],
                )
                if target_reached:
                    stop_status = "stopped_cashflow_target_reached"

            if stop_status is None and sentinel["enabled"] and profile["bankroll"] > 0:
                realized = session_state["realized_pnl"]
                drawdown_percent = max(0, -realized / profile["bankroll"] * 100) if realized < 0 else 0
                amount, sentinel_status = capital_strategies.sentinel_adjusted_amount(
                    sentinel, amount, drawdown_percent, profile["fixed_amount"],
                )
                if sentinel_status == "suspended":
                    stop_status = "stopped_sentinel_suspended"

            if fortress["enabled"]:
                current_bankroll = profile["bankroll"] + session_state["realized_pnl"]
                amount, new_protected, should_stop = capital_strategies.fortress_adjusted_amount(
                    fortress, amount, current_bankroll, profile["bankroll"],
                )
                if new_protected != fortress_protected_principal:
                    fortress_protected_principal = new_protected
                    fortress["protected_principal"] = new_protected
                if stop_status is None and should_stop:
                    stop_status = "stopped_fortress_principal_protected"

            if stop_status:
                status = stop_status
                break

            if profile.get("max_trade_amount", 0) > 0:
                amount = min(amount, profile["max_trade_amount"])
            amount = round(max(amount, 0), 2)

            result = signal["result"]
            won = result == "win"
            payout_percent = signal.get("payout_percent") or default_payout_percent
            if won:
                profit_loss = round(amount * (payout_percent / 100.0), 2)
            elif result == "loss":
                profit_loss = -amount
            else:
                profit_loss = 0.0

            session_state["realized_pnl"] += profit_loss
            session_state["trades_count"] += 1
            ended_at = signal["timestamp"]

            # Martingale/Momentum step advancement and Empire's ladder
            # both key off `won` (result == "win"), exactly matching
            # core/risk_engine.py's on_trade_closed - a draw behaves like
            # a loss for stepping purposes (not won), same as live.
            martingale = profile["martingale"]
            if martingale["enabled"]:
                if won and martingale["reset_after_win"]:
                    session_state["current_martingale_step"] = 0
                elif not won:
                    session_state["current_martingale_step"] += 1

            if momentum["enabled"]:
                if won:
                    session_state["current_momentum_step"] += 1
                else:
                    session_state["current_momentum_step"] = 0

            # A draw breaks a loss streak the same as a win does, matching
            # core/trade_statistics.py's consecutive_losses (breaks on the
            # first non-"loss" result, not just on a win).
            if result == "loss":
                session_state["consecutive_losses"] += 1
            else:
                session_state["consecutive_losses"] = 0

            if empire["enabled"] and empire["current_level"] >= 0:
                new_level = capital_strategies.empire_advance(empire, won)
                if new_level != empire["current_level"]:
                    empire["current_level"] = new_level
                    empire_current_level = new_level

            per_trade_skim = capital_strategies.per_trade_vault_skim(vault, profit_loss)
            if per_trade_skim > 0:
                session_vaulted += per_trade_skim
                cumulative_vaulted += per_trade_skim

            skim = risk_engine.milestone_vault_skim(vault, session_state["realized_pnl"], session_vaulted)
            if skim > 0:
                session_vaulted += skim
                cumulative_vaulted += skim

            running_balance = starting_bankroll + cumulative_realized_pnl + session_state["realized_pnl"]
            session_trades.append({
                "signal_source_type": signal["source_type"], "signal_id": signal["signal_id"],
                "sequence_in_session": seq, "asset": signal["asset"], "direction": signal["direction"],
                "occurred_at": signal["timestamp"], "trade_amount": amount,
                "martingale_step": session_state["current_martingale_step"], "result": result,
                "profit_loss": profit_loss, "running_balance": round(running_balance, 2),
            })

            if profit_target > 0 and session_state["realized_pnl"] >= profit_target:
                status = "stopped_target"
                break
            if loss_limit > 0 and session_state["realized_pnl"] <= -loss_limit:
                status = "stopped_loss_limit"
                break
            if max_trades > 0 and (seq + 1) >= max_trades:
                status = "stopped_max_trades"
                break

            # Strike (tm)'s own profit_target/max_session_loss/max_trades
            # are the same profile fields already checked above under
            # different names, so re-checking them here would be inert -
            # only its two genuinely distinct conditions (a consecutive-
            # losses streak, a session duration cap) are simulated, same
            # scope as core/session_manager.py's live wiring.
            if strike["enabled"]:
                strike_max_losses = strike.get("max_consecutive_losses", 0)
                if strike_max_losses > 0 and session_state["consecutive_losses"] >= strike_max_losses:
                    status = "stopped_strike_max_consecutive_losses"
                    break
                strike_max_minutes = strike.get("max_session_duration_minutes", 0)
                if strike_max_minutes > 0:
                    elapsed_minutes = (
                        datetime.fromisoformat(signal["timestamp"]) - datetime.fromisoformat(started_at)
                    ).total_seconds() / 60
                    if elapsed_minutes >= strike_max_minutes:
                        status = "stopped_strike_max_duration"
                        break

        end_skim = risk_engine.every_winning_session_vault_skim(vault, session_state["realized_pnl"])
        if end_skim > 0:
            session_vaulted += end_skim
            cumulative_vaulted += end_skim

        cumulative_realized_pnl += session_state["realized_pnl"]
        sessions.append({
            "session_index": session_index, "started_at": started_at, "ended_at": ended_at, "status": status,
            "starting_balance": round(trading_balance, 2), "realized_pnl": round(session_state["realized_pnl"], 2),
            "trades_count": len(session_trades),
            "ending_martingale_step": session_state["current_martingale_step"],
            "ending_vaulted_amount": round(session_vaulted, 2),
            "trades": session_trades,
        })
        trades.extend(session_trades)

    return {"sessions": sessions, "trades": trades}


def _risk_score(max_drawdown_percent, max_martingale_step_used):
    """Explicit heuristic, not a scientific risk model - thresholds
    chosen so a no-martingale, low-drawdown profile reads as Low and a
    deep martingale ladder or a large drawdown reads as High."""
    if max_drawdown_percent < 10 and max_martingale_step_used <= 1:
        return "Low"
    if max_drawdown_percent < 25 and max_martingale_step_used <= 3:
        return "Medium"
    return "High"


def _best_for_label(roi_percent, max_drawdown_percent):
    """Explicit heuristic pairing return with drawdown tolerance -
    thresholds are round numbers chosen for readability, not derived
    from any formal optimization."""
    if max_drawdown_percent < 10:
        return "Capital Preservation"
    if roi_percent >= 75:
        return "Aggressive Growth"
    if roi_percent >= 30:
        return "Growth"
    return "Balanced Growth"


def compute_metrics(sessions, trades, starting_bankroll):
    """Pure. Aggregates one strategy's simulate_strategy() output into
    the backtest_metrics row shape."""
    total_realized_pnl = sum(s["realized_pnl"] for s in sessions)
    final_bankroll = round(starting_bankroll + total_realized_pnl, 2)
    total_profit_loss = round(final_bankroll - starting_bankroll, 2)
    roi_percent = round((total_profit_loss / starting_bankroll) * 100, 2) if starting_bankroll > 0 else 0.0

    wins = sum(1 for t in trades if t["result"] == "win")
    losses = sum(1 for t in trades if t["result"] == "loss")
    decided = wins + losses
    win_rate = round(wins / decided, 4) if decided else None
    loss_rate = round(losses / decided, 4) if decided else None

    peak = starting_bankroll
    max_dd_percent = 0.0
    max_dd_amount = 0.0
    for t in trades:
        peak = max(peak, t["running_balance"])
        if peak > 0:
            dd = (peak - t["running_balance"]) / peak * 100
            max_dd_percent = max(max_dd_percent, dd)
            max_dd_amount = max(max_dd_amount, peak - t["running_balance"])

    by_date = defaultdict(float)
    for t in trades:
        by_date[t["occurred_at"][:10]] += t["profit_loss"]
    best_day_pnl = round(max(by_date.values()), 2) if by_date else 0.0
    worst_day_pnl = round(min(by_date.values()), 2) if by_date else 0.0

    longest_win = longest_loss = current_win = current_loss = 0
    for t in trades:
        if t["result"] == "win":
            current_win += 1
            current_loss = 0
        elif t["result"] == "loss":
            current_loss += 1
            current_win = 0
        else:
            current_win = current_loss = 0
        longest_win = max(longest_win, current_win)
        longest_loss = max(longest_loss, current_loss)

    max_martingale_step_used = max((t["martingale_step"] for t in trades), default=0)
    trade_sizes = [t["trade_amount"] for t in trades]
    avg_trade_size = round(sum(trade_sizes) / len(trade_sizes), 2) if trade_sizes else 0.0
    largest_trade_size = round(max(trade_sizes), 2) if trade_sizes else 0.0
    total_protected_profit = round(sum(s["ending_vaulted_amount"] for s in sessions), 2)

    max_dd_percent = round(max_dd_percent, 2)
    max_dd_amount = round(max_dd_amount, 2)

    # ---- Analyst-grade metrics (docs/AXIM_APP_PLAN.md's AI Strategy Lab) -
    # standard trading-performance formulas, computed from the SAME real
    # session/trade data everything else above uses, not a separate
    # simulation. Documented as heuristics where the formula itself is a
    # simplification (no risk-free rate exists for binary options, so
    # "Sharpe-like" is mean/stddev of session returns, not a textbook
    # Sharpe ratio) - never presented as more rigorous than it is.
    session_pnls = [s["realized_pnl"] for s in sessions]
    if len(session_pnls) >= 2:
        mean_session_pnl = sum(session_pnls) / len(session_pnls)
        variance = sum((p - mean_session_pnl) ** 2 for p in session_pnls) / len(session_pnls)
        volatility = round(variance ** 0.5, 2)
        sharpe_like_score = round(mean_session_pnl / volatility, 3) if volatility > 0 else None
    else:
        volatility = 0.0
        sharpe_like_score = None

    gross_profit = sum(t["profit_loss"] for t in trades if t["profit_loss"] > 0)
    gross_loss = abs(sum(t["profit_loss"] for t in trades if t["profit_loss"] < 0))
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (round(gross_profit, 2) if gross_profit > 0 else None)

    profitable_sessions = sum(1 for p in session_pnls if p > 0)
    consistency_percent = round((profitable_sessions / len(session_pnls)) * 100, 1) if session_pnls else None

    recovery_factor = round(total_profit_loss / max_dd_amount, 2) if max_dd_amount > 0 else None

    return {
        "final_bankroll": final_bankroll,
        "total_profit_loss": total_profit_loss,
        "roi_percent": roi_percent,
        "win_rate": win_rate,
        "loss_rate": loss_rate,
        "max_drawdown_percent": max_dd_percent,
        "max_drawdown_amount": max_dd_amount,
        "best_day_pnl": best_day_pnl,
        "worst_day_pnl": worst_day_pnl,
        "longest_win_streak": longest_win,
        "longest_loss_streak": longest_loss,
        "max_martingale_step_used": max_martingale_step_used,
        "sessions_completed": sum(1 for s in sessions if s["status"] == "completed"),
        "sessions_stopped_by_target": sum(1 for s in sessions if s["status"] == "stopped_target"),
        "sessions_stopped_by_loss_limit": sum(1 for s in sessions if s["status"] == "stopped_loss_limit"),
        "avg_trade_size": avg_trade_size,
        "largest_trade_size": largest_trade_size,
        "total_protected_profit": total_protected_profit,
        "risk_score": _risk_score(max_dd_percent, max_martingale_step_used),
        "best_for_label": _best_for_label(roi_percent, max_dd_percent),
        "sharpe_like_score": sharpe_like_score,
        "profit_factor": profit_factor,
        "consistency_percent": consistency_percent,
        "recovery_factor": recovery_factor,
        "volatility": volatility,
    }


def _normalize(values, higher_is_better):
    """0-1 normalization across a set of strategies, for the composite
    rank_overall score - a strategy tied for best on a dimension gets 1.0
    on that dimension, tied for worst gets 0.0. Constant series (all
    equal) normalize to 1.0 for every entry rather than dividing by zero."""
    lo, hi = min(values), max(values)
    if hi == lo:
        return [1.0] * len(values)
    return [((v - lo) / (hi - lo)) if higher_is_better else ((hi - v) / (hi - lo)) for v in values]


def rank_strategies(strategy_metrics):
    """Pure. Takes a list of (backtest_strategy_id, metrics_dict) tuples
    for one run and returns {strategy_id: {rank_overall, rank_safest,
    rank_highest_growth, rank_lowest_drawdown, rank_risk_adjusted}} -
    1 = best in that category. rank_overall is a documented composite:
    40% ROI + 40% inverse-drawdown + 20% win rate, each normalized
    across the compared strategies - a heuristic for a quick "best
    overall" badge, not a substitute for reading the actual numbers."""
    if not strategy_metrics:
        return {}
    ids = [sid for sid, _ in strategy_metrics]
    roi = [m["roi_percent"] for _, m in strategy_metrics]
    dd = [m["max_drawdown_percent"] for _, m in strategy_metrics]
    win_rate = [m["win_rate"] or 0 for _, m in strategy_metrics]

    roi_n = _normalize(roi, higher_is_better=True)
    dd_n = _normalize(dd, higher_is_better=False)
    win_n = _normalize(win_rate, higher_is_better=True)
    composite = [0.4 * r + 0.4 * d + 0.2 * w for r, d, w in zip(roi_n, dd_n, win_n)]
    risk_adjusted = [(roi[i] / dd[i]) if dd[i] > 0 else roi[i] for i in range(len(ids))]

    def _ranks(values, higher_is_better=True):
        order = sorted(range(len(values)), key=lambda i: values[i], reverse=higher_is_better)
        ranks = [0] * len(values)
        for position, idx in enumerate(order):
            ranks[idx] = position + 1
        return ranks

    rank_overall = _ranks(composite, higher_is_better=True)
    rank_safest = _ranks(dd, higher_is_better=False)
    rank_highest_growth = _ranks(roi, higher_is_better=True)
    rank_risk_adjusted = _ranks(risk_adjusted, higher_is_better=True)

    return {
        ids[i]: {
            "rank_overall": rank_overall[i],
            "rank_safest": rank_safest[i],
            "rank_highest_growth": rank_highest_growth[i],
            "rank_lowest_drawdown": rank_safest[i],
            "rank_risk_adjusted": rank_risk_adjusted[i],
        }
        for i in range(len(ids))
    }


def run_backtest(run_id):
    """The DB-driving orchestrator: loads the run + its strategies,
    simulates each via the pure simulate_strategy() above, persists
    sessions/trades/metrics, ranks strategies against each other, and
    marks the run completed (or failed, with the real error message -
    never silently swallowed)."""
    run = database.get_backtest_run(run_id)
    if run is None:
        raise ValueError(f"no backtest run with id {run_id}")

    database.update_backtest_run_status(run_id, "running")
    try:
        pool_config = run["signal_pool"]
        signal_pool = database.get_historical_signal_pool(
            pool_config.get("source", "both"),
            channel_filter=pool_config.get("channel_filter"),
            date_from=pool_config.get("date_from"),
            date_to=pool_config.get("date_to"),
        )
        if not signal_pool:
            raise ValueError("no graded historical signals match this run's filters")

        strategies = database.list_backtest_strategies(run_id)
        strategy_metrics = []
        for strategy in strategies:
            profile = strategy["profile_snapshot"]
            result = simulate_strategy(
                signal_pool, profile, run["starting_bankroll"],
                session_window=run["session_window"], default_payout_percent=run["default_payout_percent"],
                profit_target=profile.get("profit_target", 0) or 0,
                loss_limit=profile.get("max_session_loss", 0) or 0,
                max_trades=profile.get("max_trades", 0) or 0,
            )
            for session in result["sessions"]:
                session_trades = session.pop("trades")
                session_id = database.create_backtest_session(strategy["id"], **session)
                for trade in session_trades:
                    database.create_backtest_trade(session_id, **trade)
            metrics = compute_metrics(result["sessions"], result["trades"], run["starting_bankroll"])
            database.save_backtest_metrics(strategy["id"], metrics)
            strategy_metrics.append((strategy["id"], metrics))

        ranks = rank_strategies(strategy_metrics)
        for strategy_id, rank_fields in ranks.items():
            database.save_backtest_metrics(strategy_id, rank_fields)

        database.update_backtest_run_status(run_id, "completed")
    except Exception as e:
        logger.exception("backtest_engine: run %s failed", run_id)
        database.update_backtest_run_status(run_id, "failed", error_message=str(e))
        raise
