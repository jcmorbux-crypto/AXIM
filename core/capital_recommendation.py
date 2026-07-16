"""Capital Recommendation Engine (Tier 2 roadmap items 5-6) - turns a
completed backtest_run's per-strategy metrics into one actionable
recommendation per provider: which of the 4 official Money Studio
strategies performed best, and three capital allocation tiers
(minimum/conservative/suggested) derived from that strategy's own real
backtested drawdown.

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


def compute_recommendation(source_label, report, trades_backtested):
    """report: database.get_backtest_report(run_id)'s shape. Returns a
    dict ready for database.save_capital_recommendation(**dict), or
    None if the run has no rankable strategy (e.g. it failed, or has
    fewer strategies than rank_strategies needs to produce a ranking)."""
    run = report["run"]
    strategies = report["strategies"]
    best = pick_best_strategy(strategies)
    if best is None:
        return None
    metrics = best["metrics"]
    tiers = compute_allocation_tiers(metrics.get("max_drawdown_amount"), metrics.get("avg_trade_size"))
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
    recommendation = compute_recommendation(source_label, report, trades_backtested)
    if recommendation is None:
        database.delete_capital_recommendation(source_label)
        return None
    return database.save_capital_recommendation(**recommendation)
