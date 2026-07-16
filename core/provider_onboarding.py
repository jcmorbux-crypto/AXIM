"""Automatic Provider Onboarding (Phase 2 Priority #1/#2) - the real,
end-user-facing "add a new provider" workflow this phase's mandate
calls the flagship feature: fetch ~30 days of a Telegram channel's
history, auto-detect its signal language
(core/provider_language_learner.py), import the decided (win/loss/
draw) trades into the backtest engine's imported_signals pool, auto-run
all 4 official Money Studio strategies, and generate a capital
recommendation (core/capital_recommendation.py) - one action, no
hand-written adapter required for a common format.

Never raises for an honestly-inconclusive outcome ("no pattern
detected", "a pattern was found but nothing links to a result") - those
are expected, informative results for formats this auto-detector
doesn't cover yet (the same providers the OPT SIGNALS research branch
needed a hand-built adapter for - Martin Trader, OTC Pro Trading Robot,
TYLER VIP CLUB, Pattern Signals - are exactly the ones this module's
pattern library doesn't catch either, confirmed by direct validation
against that same research database). Only a real failure (Telegram
connection, credentials) raises.
"""
import sys
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CORE_DIR))

DEFAULT_HISTORY_LIMIT = 2000  # telegram_channels.MAX_HISTORY_SCAN caps this further anyway
STARTING_BANKROLL = 1000.0
DEFAULT_PAYOUT_PERCENT = 88  # matches money_studio.PAYOUT_PERCENT - AXIM's real observed historical average


def _extract_decided_trades(analysis, date_by_message_id):
    by_message_id = {r["source_message_id"]: r for r in analysis["signal_records"]}
    trades = []
    for link in analysis["result_links"]:
        if link["result"] not in ("win", "loss", "draw"):
            continue
        signal_message_id = link["signal_message_id"]
        if signal_message_id is None:
            continue
        record = by_message_id.get(signal_message_id)
        if record is None or not record["normalized_asset"] or not record["direction"]:
            continue
        received_at = date_by_message_id.get(signal_message_id)
        if not received_at:
            continue
        trades.append({
            "asset": record["normalized_asset"], "direction": record["direction"],
            "expiry": record.get("expiry"), "received_at": received_at, "result": link["result"],
        })
    trades.sort(key=lambda t: t["received_at"])
    return trades


async def analyze_and_onboard_provider(chat_id, source_label=None, created_by="provider_onboarding"):
    """Returns a result dict with a "status" field:
    - "no_history": the channel has no messages at all
    - "pattern_not_detected": no common signal shape was recognized -
      needs a hand-built adapter
    - "no_decided_trades": a pattern matched, but nothing could be
      linked to a win/loss/draw outcome (e.g. results are photo-only)
    - "complete": imported, backtested, and a recommendation was
      generated (or explicitly wasn't, if every strategy was
      implausible for this provider - see capital_recommendation.py)
    """
    import database
    import telegram_channels
    import provider_language_learner as learner
    import backtest_engine
    import capital_recommendation
    import money_studio

    messages, title = await telegram_channels.fetch_channel_raw_history(
        chat_id, limit=DEFAULT_HISTORY_LIMIT, source_label=source_label,
    )
    if not messages:
        return {"status": "no_history", "title": title, "message_count": 0}

    analysis = learner.analyze_provider(messages)
    if analysis is None:
        return {
            "status": "pattern_not_detected", "title": title, "message_count": len(messages),
            "note": (
                "No common signal format was automatically recognized for this provider - "
                "it needs a hand-built adapter (the same path Martin Trader, OTC Pro Trading "
                "Robot, and TYLER VIP CLUB required)."
            ),
        }

    date_by_message_id = {m["message_id"]: m["date_utc"] for m in messages}
    trades = _extract_decided_trades(analysis, date_by_message_id)

    import_batch = f"auto_onboard_{chat_id}"
    database.delete_imported_signals_by_batch(import_batch)
    for trade in trades:
        database.create_imported_signal(
            title, trade["asset"], trade["direction"], trade["expiry"], trade["received_at"],
            result=trade["result"],
            notes=(
                f"Auto-detected via core/provider_language_learner.py (pattern={analysis['pattern']}, "
                f"coverage={analysis['coverage']:.1%}) - the provider's own reported outcome, "
                "not independently verified against a broker."
            ),
            import_batch=import_batch,
        )

    if not trades:
        return {
            "status": "no_decided_trades", "title": title, "message_count": len(messages),
            "pattern": analysis["pattern"], "coverage": analysis["coverage"],
            "note": (
                "A signal pattern was detected, but no trade could be linked to a win/loss/draw "
                "result - this provider may only post results as images, or never confirm "
                "outcomes in text."
            ),
        }

    database.seed_money_studio_templates()
    profiles = database.list_risk_profiles(include_templates=True)
    strategy_profiles = [p for p in profiles if p["strategy_key"] in money_studio.STRATEGIES_BY_KEY]

    signal_pool = {"source": "imported", "channel_filter": [title]}
    run_id = database.create_backtest_run(
        f"Auto-onboard: {title}", signal_pool, STARTING_BANKROLL,
        default_payout_percent=DEFAULT_PAYOUT_PERCENT, session_window="daily", created_by=created_by,
    )
    for profile in strategy_profiles:
        database.create_backtest_strategy(run_id, profile["id"], profile["name"], profile)
    backtest_engine.run_backtest(run_id)

    recommendation_id = capital_recommendation.generate_recommendation_for_provider(
        title, run_id, trades_backtested=len(trades),
    )

    return {
        "status": "complete", "title": title, "message_count": len(messages),
        "pattern": analysis["pattern"], "coverage": analysis["coverage"],
        "imported_trades": len(trades), "backtest_run_id": run_id, "recommendation_id": recommendation_id,
    }
