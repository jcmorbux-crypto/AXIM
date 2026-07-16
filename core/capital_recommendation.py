"""Capital Recommendation Engine (Tier 2 roadmap items 5-6, extended in
Phase 2) - turns a completed backtest_run's per-strategy metrics into
one actionable recommendation per provider: which of the 4 official
Money Studio strategies performed best, three capital allocation tiers
(minimum/conservative/suggested) derived from that strategy's own real
backtested drawdown, and the full recommendation-card field set (net
profit, ending balance, longest losing streak, average daily trades, a
documented confidence score/star rating, and a recommended session
goal/daily stop scaled to the suggested allocation). Every one of these
is derived from the real backtest - none is fabricated; a strategy
whose own backtest lost money on average gets no session goal at all
rather than an invented positive number.

Every number here is an explicit, documented heuristic multiple of
max_drawdown_amount (the single largest peak-to-trough dollar loss the
backtest actually observed) - never a fabricated performance
projection, matching core/backtest_engine.py's own risk_score/
best_for_label discipline. A future real drawdown can always exceed a
historical sample, which is exactly why these are buffered multiples of
the worst *observed* case, not the observed case itself:

- minimum: 1.5x max_drawdown_amount - the bare floor a deployment needs
  to survive a repeat of the worst historical drawdown with some
  margin. Below this, a single bad stretch identical to the backtest's
  own worst case would already wipe the fund out.
- conservative: 2.5x max_drawdown_amount - room for a moderately worse
  drawdown than anything yet observed, for an operator who wants more
  safety margin than the bare floor.
- suggested: 4x max_drawdown_amount - a comfortable operating bankroll
  with real room to keep trading through a bad stretch without forcing
  a stop.

All three are floored at max(ABSOLUTE_FLOOR, avg_trade_size *
AVG_TRADE_SIZE_FLOOR_MULTIPLE) - a provider with a tiny historical
drawdown (thin sample, or a strategy that was never actually tested
hard) still needs enough capital to place a reasonable number of trades
at its own sizing, not a number smaller than a handful of trades would
consume.

**Real finding from the first live run against actual production data**:
Martin Trader's automated backtest picked "Alternating Compound" (a
fixed percent-of-bankroll strategy with no profit vault, per
money_studio.py's own real-engine-mapping disclosure) as its #1-ranked
strategy with an ROI of ~1.4 x 10^14 percent and a suggested allocation
of ~$1.5 x 10^14 - not a display bug, a genuine unbounded-compounding
artifact: this provider's real backtested win rate is ~93% (a
martingale-recovery provider, where most chains eventually resolve as
a "win" from the operator's perspective), and sizing a fixed percent of
an ever-growing, never-vaulted bankroll over 1630 real trades compounds
hyperexponentially. `rank_overall`'s composite score (backtest_engine.
rank_strategies) normalizes ROI trivially to 1.0 regardless of scale,
so an inflated-beyond-reason ROI always wins that ranking - this module
must not blindly trust rank_overall #1 the way a human comparing a
handful of sane strategies safely could. See MAX_PLAUSIBLE_ROI_PERCENT.
"""
MINIMUM_DRAWDOWN_MULTIPLE = 1.5
CONSERVATIVE_DRAWDOWN_MULTIPLE = 2.5
SUGGESTED_DRAWDOWN_MULTIPLE = 4.0
ABSOLUTE_FLOOR = 50.0
AVG_TRADE_SIZE_FLOOR_MULTIPLE = 10

# A documented implausibility ceiling, not a scientific bound: a real,
# deployable strategy - even a very good one - backtested over the
# sample sizes this engine works with should never realistically clear
# a 50x return. Anything beyond that is a percent-of-growing-bankroll
# compounding-math artifact (see the module docstring's Martin Trader
# finding), not a tradeable edge, and must never be surfaced as "the
# best strategy" just because it trivially maximizes a composite rank.
MAX_PLAUSIBLE_ROI_PERCENT = 5000

# ---- Confidence Score (Phase 2) - an explicit, documented heuristic,
# not a statistical p-value. Two ingredients, weighted:
#   60%: sample size, saturating at CONFIDENCE_TRADE_SATURATION trades -
#        a strategy tested on 500+ real trades gets full marks here; one
#        tested on 20 does not, regardless of how good those 20 looked.
#   40%: consistency_percent (share of profitable sessions) - a strategy
#        that wins on most individual days is more trustworthy than one
#        whose total return hides one huge outlier day.
# Never a substitute for reading the underlying numbers - shown for
# quick comparison only, same "explicit heuristic" framing as
# backtest_engine.py's risk_score/best_for_label. ----
CONFIDENCE_TRADE_SATURATION = 500
CONFIDENCE_SAMPLE_SIZE_WEIGHT = 60
CONFIDENCE_CONSISTENCY_WEIGHT = 40

# The daily stop is set tighter than the worst single day actually
# observed - a real future bad day can be as bad as history's worst,
# and stopping somewhat before that point preserves capital for the
# next session rather than confirming the historical worst case exactly.
DAILY_STOP_SAFETY_FACTOR = 0.75


def _money(n):
    return round(n, 2)


def _is_plausible(metrics):
    roi = metrics.get("roi_percent")
    return roi is not None and roi < MAX_PLAUSIBLE_ROI_PERCENT


def pick_best_strategy(strategies):
    """strategies: a backtest report's "strategies" list (each carrying
    ["metrics"]["rank_overall"], set by backtest_engine.rank_strategies).
    Returns the strategy dict ranked #1 overall AMONG PLAUSIBLE
    strategies (see _is_plausible/MAX_PLAUSIBLE_ROI_PERCENT) - an
    unbounded-compounding artifact is excluded before ranking even
    applies, never picked just because its inflated ROI trivially wins
    the composite score. Returns None if nothing is both ranked and
    plausible (an empty run, a report fetched before ranking ran, or
    every candidate strategy blew up) - no confident recommendation is
    better than a fabricated one."""
    ranked = [s for s in strategies if s.get("metrics") and s["metrics"].get("rank_overall")]
    plausible = [s for s in ranked if _is_plausible(s["metrics"])]
    if not plausible:
        return None
    return min(plausible, key=lambda s: s["metrics"]["rank_overall"])


def compute_allocation_tiers(max_drawdown_amount, avg_trade_size):
    floor = max(ABSOLUTE_FLOOR, (avg_trade_size or 0) * AVG_TRADE_SIZE_FLOOR_MULTIPLE)
    max_drawdown_amount = max_drawdown_amount or 0
    minimum = max(floor, max_drawdown_amount * MINIMUM_DRAWDOWN_MULTIPLE)
    conservative = max(floor, max_drawdown_amount * CONSERVATIVE_DRAWDOWN_MULTIPLE)
    suggested = max(floor, max_drawdown_amount * SUGGESTED_DRAWDOWN_MULTIPLE)
    return {
        "minimum_allocation": _money(minimum),
        "conservative_allocation": _money(conservative),
        "suggested_allocation": _money(suggested),
    }


def compute_confidence(actual_trade_count, consistency_percent):
    """0-100. See the module-level comment above CONFIDENCE_TRADE_SATURATION
    for the exact weighting and why - an explicit heuristic, not a
    statistical measure."""
    sample_component = min(actual_trade_count or 0, CONFIDENCE_TRADE_SATURATION) / CONFIDENCE_TRADE_SATURATION
    consistency_component = min(100, max(0, consistency_percent or 0)) / 100
    score = sample_component * CONFIDENCE_SAMPLE_SIZE_WEIGHT + consistency_component * CONFIDENCE_CONSISTENCY_WEIGHT
    return round(min(100, score), 1)


def compute_star_rating(confidence_score):
    """1-5, floor of 1 - a recommendation only exists at all once
    pick_best_strategy has already excluded implausible strategies, so
    even a low-confidence surviving recommendation is real evidence,
    never zero stars."""
    return max(1, min(5, round(confidence_score / 20)))


def compute_recommendation(source_label, report, trades_backtested, session_count=None, actual_trade_count=None):
    """report: database.get_backtest_report(run_id)'s shape. Returns a
    dict ready for database.save_capital_recommendation(**dict), or
    None if the run has no rankable strategy (e.g. it failed, or has
    fewer strategies than rank_strategies needs to produce a ranking).

    session_count/actual_trade_count are supplied by the caller (real
    database.list_backtest_sessions/list_backtest_trades_for_strategy
    counts) rather than derived here, so this function stays pure and
    testable without a database. If omitted, avg_daily_trades/
    confidence_score/session-goal/daily-stop are computed as best-effort
    from trades_backtested (the imported signal-pool size) instead of
    the strategy's own actual trade count - close but not exact, since a
    session can stop early (profit target/loss limit) before reaching
    every signal in the pool."""
    run = report["run"]
    strategies = report["strategies"]
    best = pick_best_strategy(strategies)
    if best is None:
        return None
    metrics = best["metrics"]
    tiers = compute_allocation_tiers(metrics.get("max_drawdown_amount"), metrics.get("avg_trade_size"))

    trade_count = actual_trade_count if actual_trade_count is not None else trades_backtested
    starting_bankroll = run.get("starting_bankroll") or 0
    scale_factor = (tiers["suggested_allocation"] / starting_bankroll) if starting_bankroll else 1.0

    net_profit = metrics.get("total_profit_loss")
    avg_daily_trades = round(trade_count / session_count, 1) if session_count else None

    confidence_score = compute_confidence(trade_count, metrics.get("consistency_percent"))
    star_rating = compute_star_rating(confidence_score)

    # Never invent a positive daily target from a losing backtest - a
    # strategy whose own average day lost money gets no session goal,
    # not a fabricated positive number.
    avg_daily_pnl = (net_profit / session_count) if (session_count and net_profit is not None) else None
    recommended_session_goal = (
        _money(avg_daily_pnl * scale_factor) if (avg_daily_pnl is not None and avg_daily_pnl > 0) else None
    )
    worst_day_pnl = metrics.get("worst_day_pnl")
    recommended_daily_stop = (
        _money(abs(worst_day_pnl) * scale_factor * DAILY_STOP_SAFETY_FACTOR)
        if worst_day_pnl is not None else None
    )

    return {
        "source_label": source_label,
        "backtest_run_id": run["id"],
        "best_strategy_id": best["id"],
        "best_strategy_key": (best.get("profile_snapshot") or {}).get("strategy_key"),
        "best_strategy_name": best["label"],
        "roi_percent": metrics.get("roi_percent"),
        "win_rate": metrics.get("win_rate"),
        "max_drawdown_percent": metrics.get("max_drawdown_percent"),
        "max_drawdown_amount": metrics.get("max_drawdown_amount"),
        "trades_backtested": trades_backtested,
        "net_profit": net_profit,
        "ending_balance": metrics.get("final_bankroll"),
        "longest_losing_streak": metrics.get("longest_loss_streak"),
        "avg_daily_trades": avg_daily_trades,
        "confidence_score": confidence_score,
        "star_rating": star_rating,
        "recommended_session_goal": recommended_session_goal,
        "recommended_daily_stop": recommended_daily_stop,
        **tiers,
    }


def generate_recommendation_for_provider(source_label, run_id, trades_backtested):
    """DB-driving orchestrator - fetches the completed run's report,
    computes the recommendation, and persists it (replacing any
    previous recommendation for this source_label). Returns the saved
    recommendation id, or None if the run couldn't be turned into one
    (see compute_recommendation) - in which case any STALE prior
    recommendation for this provider is deleted, not left behind. A
    provider whose every candidate strategy blew up on re-backtest (or
    simply has none yet) must never keep showing an old number that no
    longer reflects reality."""
    import database
    report = database.get_backtest_report(run_id)
    if report is None:
        return None

    best = pick_best_strategy(report["strategies"])
    session_count = actual_trade_count = None
    if best is not None:
        sessions = database.list_backtest_sessions(best["id"])
        session_count = len(sessions) or None
        actual_trade_count = sum(s["trades_count"] for s in sessions) or None

    recommendation = compute_recommendation(
        source_label, report, trades_backtested, session_count=session_count, actual_trade_count=actual_trade_count,
    )
    if recommendation is None:
        database.delete_capital_recommendation(source_label)
        return None
    return database.save_capital_recommendation(**recommendation)
