"""AI Strategy Lab analysis engine (docs/AXIM_APP_PLAN.md) - turns
core/backtest_engine.py's real computed metrics into analyst-style
narrative and recommendations, instead of a wall of numbers.

Deliberately rule-based, not an LLM call: no external API key exists in
this deployment, and wiring one up is a real cost/data-privacy decision
the user needs to make explicitly, not something to assume silently.
Every sentence this module produces is generated from the actual
computed metrics passed in - never a fabricated claim, never invented
data - so the "analyst feel" comes from how the real numbers are
selected, compared, and phrased, not from making anything up. This can
be swapped for genuine LLM-generated prose later without changing the
callers (same input shape, richer output), if the user provides an API
key.

Central discipline (explicit product requirement): recommendations are
NEVER "pick whatever made the most money" - they weight risk-adjusted
return, drawdown, and consistency alongside raw growth, matching
backtest_engine.rank_strategies' own composite (40% ROI + 40% inverse-
drawdown + 20% win rate) rather than re-deriving a different, looser
notion of "best" here.
"""


def _qualify(value, bands, labels):
    """bands is an ascending list of thresholds; labels has one more
    entry than bands. Returns the label for the bracket `value` falls
    into. Used throughout instead of a magic-number if/elif chain so
    every qualitative word in this module traces back to one place."""
    for threshold, label in zip(bands, labels):
        if value < threshold:
            return label
    return labels[-1]


def _drawdown_word(max_drawdown_percent):
    return _qualify(max_drawdown_percent, [8, 18, 30], ["very low", "low", "moderate", "high"])


def _consistency_word(consistency_percent):
    if consistency_percent is None:
        return "unclear (too few sessions to judge)"
    return _qualify(consistency_percent, [40, 60, 80], ["poor", "mixed", "solid", "excellent"])


def _martingale_word(max_martingale_step_used):
    if max_martingale_step_used == 0:
        return "no martingale recovery"
    return _qualify(max_martingale_step_used, [2, 4], ["light martingale recovery", "moderate martingale recovery", "deep martingale recovery"])


def generate_strategy_narrative(label, metrics):
    """One strategy's paragraph - headline result, risk framing,
    consistency, martingale dependency, and a closing fit statement.
    Every clause cites a real field from `metrics` (core/backtest_engine
    .compute_metrics' output) - nothing here is invented."""
    if metrics is None or metrics.get("final_bankroll") is None:
        return f"{label} has no results yet."

    roi = metrics["roi_percent"]
    pl = metrics["total_profit_loss"]
    dd = metrics["max_drawdown_percent"]
    sessions = metrics["sessions_completed"] + metrics["sessions_stopped_by_target"] + metrics["sessions_stopped_by_loss_limit"]
    consistency = metrics.get("consistency_percent")
    martingale_steps = metrics.get("max_martingale_step_used", 0)
    win_rate = metrics.get("win_rate")

    direction = "produced" if pl >= 0 else "lost"
    sentences = [
        f"{label} {direction} {'$' + format(abs(pl), ',.2f')} ({roi:+.1f}% ROI) across {sessions} simulated session{'s' if sessions != 1 else ''}."
    ]

    dd_word = _drawdown_word(dd)
    sentences.append(f"Its maximum drawdown was {dd:.1f}%, which is {dd_word} for this style of trading.")

    if consistency is not None:
        consistency_word = _consistency_word(consistency)
        sentences.append(f"It was profitable in {consistency:.0f}% of sessions - {consistency_word} consistency.")

    if martingale_steps > 0:
        sentences.append(
            f"It relied on {_martingale_word(martingale_steps)} (up to step {martingale_steps}), "
            f"meaning part of its return came from recovering losing streaks rather than pure win rate."
        )
    elif win_rate is not None:
        sentences.append(f"It won {win_rate * 100:.1f}% of individual trades with no martingale recovery to lean on.")

    best_for = metrics.get("best_for_label")
    if best_for:
        sentences.append(f"Best suited for: {best_for}.")

    return " ".join(sentences)


def generate_run_narrative(report):
    """The top-level synthesis across every strategy in a backtest run -
    the actual "which strategy should I use" answer, explaining the
    tradeoff rather than just naming a winner. report is
    database.get_backtest_report()'s shape: {"run": {...}, "strategies":
    [{"id", "label", "metrics": {...}}, ...]}."""
    strategies = [s for s in report["strategies"] if s.get("metrics")]
    if not strategies:
        return "No strategies have results yet - run the backtest first."
    if len(strategies) == 1:
        s = strategies[0]
        return generate_strategy_narrative(s["label"], s["metrics"])

    overall = next((s for s in strategies if s["metrics"].get("rank_overall") == 1), None)
    highest_growth = max(strategies, key=lambda s: s["metrics"]["roi_percent"])
    safest = min(strategies, key=lambda s: s["metrics"]["max_drawdown_percent"])

    parts = []
    if overall:
        parts.append(
            f"Across {len(strategies)} strategies tested on the same signals, {overall['label']} is the "
            f"recommended choice - it balances growth, drawdown, and win rate better than the alternatives, "
            f"not just the one that made the most money."
        )
        if overall["id"] != highest_growth["id"]:
            gap = highest_growth["metrics"]["roi_percent"] - overall["metrics"]["roi_percent"]
            dd_saved = highest_growth["metrics"]["max_drawdown_percent"] - overall["metrics"]["max_drawdown_percent"]
            if gap > 0 and dd_saved > 0:
                parts.append(
                    f"{highest_growth['label']} produced {gap:.1f} percentage points more ROI, but did so with "
                    f"{dd_saved:.1f} percentage points more drawdown - {overall['label']} gives up some upside "
                    f"for meaningfully better capital protection."
                )
        if overall["id"] == safest["id"]:
            parts.append(f"It also had the lowest drawdown of any strategy tested ({safest['metrics']['max_drawdown_percent']:.1f}%).")
    else:
        parts.append(f"{len(strategies)} strategies were tested on the same signals; no single strategy dominates every dimension.")

    return " ".join(parts)


def answer_strategy_questions(report):
    """Structured Q&A over one run's results - direct lookups against
    real metrics, matching docs/AXIM_APP_PLAN.md's explicit "AI
    Questions" list. Returns {question: {"answer": label, "detail": str}}
    - None values mean "not enough data", never a guess."""
    strategies = [s for s in report["strategies"] if s.get("metrics")]
    if not strategies:
        return {}

    def _best(key, higher_is_better=True, detail_fmt=None, predicate=None):
        candidates = [s for s in strategies if s["metrics"].get(key) is not None]
        if predicate is not None:
            candidates = [s for s in candidates if predicate(s["metrics"])]
        if not candidates:
            return {"answer": None, "detail": "not enough data"}
        winner = (max if higher_is_better else min)(candidates, key=lambda s: s["metrics"][key])
        value = winner["metrics"][key]
        detail = detail_fmt(value) if detail_fmt else str(value)
        return {"answer": winner["label"], "detail": detail}

    return {
        "which_made_the_most_money": _best("total_profit_loss", True, lambda v: f"${v:,.2f} total profit"),
        "which_had_lowest_drawdown": _best("max_drawdown_percent", False, lambda v: f"{v:.1f}% max drawdown"),
        # Only counts as "recovered" if it actually finished net profitable -
        # the least-negative recovery factor among a field of losers isn't
        # a real recovery, and presenting it as one would be misleading.
        "which_recovered_best": _best(
            "recovery_factor", True, lambda v: f"recovery factor {v:.2f}",
            predicate=lambda m: m["total_profit_loss"] > 0,
        ),
        "which_protected_capital_best": _best("total_protected_profit", True, lambda v: f"${v:,.2f} vaulted"),
        "which_survived_losing_streaks_best": _best("longest_loss_streak", False, lambda v: f"longest losing streak: {v} trades"),
        "which_has_the_best_risk_adjusted_return": _best("sharpe_like_score", True, lambda v: f"score {v:.2f}"),
        "which_is_safest": _best("max_drawdown_percent", False, lambda v: f"{v:.1f}% max drawdown"),
        # "Aggressive" means highest volatility/swings, not highest ROI -
        # a losing strategy can still be the most aggressive one tested
        # (it just means its bets were the biggest relative to outcome),
        # so this must not flip meaning depending on whether the run
        # happened to be profitable.
        "which_is_most_aggressive": _best("volatility", True, lambda v: f"session P/L volatility {v:.2f}"),
        "which_is_most_consistent": _best("consistency_percent", True, lambda v: f"profitable in {v:.0f}% of sessions"),
    }


def _average_losing_streak(signal_pool):
    """Average LENGTH of consecutive-loss runs (not the longest one -
    backtest_engine.compute_metrics already has that as
    longest_loss_streak). Computed directly from the graded signal
    sequence, independent of any strategy's sizing, since streak length
    is a property of the win/loss sequence itself."""
    streaks = []
    current = 0
    for signal in signal_pool:
        if signal["result"] == "loss":
            current += 1
        else:
            if current > 0:
                streaks.append(current)
            current = 0
    if current > 0:
        streaks.append(current)
    return round(sum(streaks) / len(streaks), 1) if streaks else 0.0


def _overall_score(win_rate, max_drawdown_percent, consistency_percent):
    """0-100 composite for the scorecard's headline number - same spirit
    as backtest_engine.rank_strategies' composite (reward return/
    consistency, penalize drawdown) but rescaled to a single 0-100
    "star rating" instead of a relative rank, since a scorecard is about
    ONE source, not a comparison across several. An explicit, documented
    heuristic, not a statistical model."""
    win_component = (win_rate or 0) * 100
    drawdown_component = max(0, 100 - (max_drawdown_percent or 0) * 2.5)
    consistency_component = consistency_percent if consistency_percent is not None else 50
    score = 0.4 * win_component + 0.35 * drawdown_component + 0.25 * consistency_component
    return round(max(0, min(100, score)))


def _confidence_from_sample_size(graded_count, target=60):
    """More graded signals = more confidence the backtest result reflects
    this source's real behavior, not noise. Reaches 100% at `target`
    signals - an explicit, round-number threshold, not a statistical
    significance test; documented as a heuristic (docs/AXIM_APP_PLAN.md)."""
    return round(min(100, (graded_count / target) * 100))


def generate_signal_provider_scorecard(source_label, candidate_profiles, starting_bankroll=1000, default_payout_percent=85):
    """The per-source report docs/AXIM_APP_PLAN.md's spec calls for -
    runs a real backtest across `candidate_profiles` (risk profile
    snapshots) restricted to this source's own graded signal history,
    picks the best-performing one via backtest_engine's own composite
    ranking, and reports both raw signal-quality facts (win rate,
    average losing streak - independent of any sizing strategy) and
    strategy-dependent recommendations (bankroll, trade size) from
    whichever profile actually performed best on this source's signals.

    Returns None if there's no graded history for this source at all -
    never a fabricated scorecard for a source with no evidence."""
    import database
    import backtest_engine

    signal_pool = database.get_historical_signal_pool("both", channel_filter=[source_label])
    if not signal_pool:
        return None

    wins = sum(1 for s in signal_pool if s["result"] == "win")
    losses = sum(1 for s in signal_pool if s["result"] == "loss")
    decided = wins + losses
    win_rate = round(wins / decided, 4) if decided else None

    strategy_metrics = []
    sessions_by_profile = {}
    for profile in candidate_profiles:
        result = backtest_engine.simulate_strategy(
            signal_pool, profile, starting_bankroll, session_window="daily",
            default_payout_percent=default_payout_percent,
            profit_target=profile.get("profit_target", 0) or 0,
            loss_limit=profile.get("max_session_loss", 0) or 0,
            max_trades=profile.get("max_trades", 0) or 0,
        )
        metrics = backtest_engine.compute_metrics(result["sessions"], result["trades"], starting_bankroll)
        strategy_metrics.append((profile["name"], metrics))
        sessions_by_profile[profile["name"]] = result["sessions"]

    if not strategy_metrics:
        return None

    ranks = backtest_engine.rank_strategies([(name, m) for name, m in strategy_metrics])
    best_name = min(strategy_metrics, key=lambda nm: ranks.get(nm[0], {}).get("rank_overall", 999))[0]
    best_metrics = dict(strategy_metrics)[best_name]
    best_sessions = sessions_by_profile[best_name]
    best_profile = next(p for p in candidate_profiles if p["name"] == best_name)

    avg_session_profit = round(sum(s["realized_pnl"] for s in best_sessions) / len(best_sessions), 2) if best_sessions else 0.0
    recommended_trade_size_percent = (
        best_profile.get("percent_of_bankroll") if best_profile.get("sizing_mode") in ("percent", "dynamic")
        else round((best_profile.get("fixed_amount", 0) / starting_bankroll) * 100, 2) if starting_bankroll > 0 else None
    )

    return {
        "source_label": source_label,
        "graded_signal_count": decided,
        "overall_score": _overall_score(win_rate, best_metrics["max_drawdown_percent"], best_metrics.get("consistency_percent")),
        "historical_win_rate": win_rate,
        "consistency": _consistency_word(best_metrics.get("consistency_percent")),
        "consistency_percent": best_metrics.get("consistency_percent"),
        "max_drawdown_percent": best_metrics["max_drawdown_percent"],
        "average_session_profit": avg_session_profit,
        "average_losing_streak": _average_losing_streak(signal_pool),
        "recommended_strategy": best_name,
        "risk_rating": best_metrics["risk_score"],
        "recommended_starting_bankroll": starting_bankroll,
        "recommended_trade_size_percent": recommended_trade_size_percent,
        "confidence_percent": _confidence_from_sample_size(decided),
    }


def generate_extended_rankings(report):
    """Ranking categories beyond backtest_engine.rank_strategies' four
    (overall/safest/highest_growth/risk_adjusted) - most consistent, best
    recovery, best compounding (scoped to strategies that actually had
    compounding enabled, so a fixed-sizing profile can't "win" a category
    it never participated in). Returns {strategy_id: {category: rank}}."""
    strategies = [s for s in report["strategies"] if s.get("metrics")]
    if not strategies:
        return {}

    def _rank_by(key, higher_is_better=True, subset=None):
        pool = subset if subset is not None else strategies
        pool = [s for s in pool if s["metrics"].get(key) is not None]
        order = sorted(pool, key=lambda s: s["metrics"][key], reverse=higher_is_better)
        return {s["id"]: i + 1 for i, s in enumerate(order)}

    consistency_ranks = _rank_by("consistency_percent", True)
    recovery_ranks = _rank_by("recovery_factor", True)
    compounding_pool = [s for s in strategies if (s.get("profile_snapshot") or {}).get("compounding", {}).get("mode", "disabled") != "disabled"]
    compounding_ranks = _rank_by("roi_percent", True, subset=compounding_pool)

    result = {}
    for s in strategies:
        result[s["id"]] = {
            "rank_most_consistent": consistency_ranks.get(s["id"]),
            "rank_best_recovery": recovery_ranks.get(s["id"]),
            "rank_best_compounding": compounding_ranks.get(s["id"]),
        }
    return result
