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
DEFAULT_HISTORY_DAYS = 30  # Provider Onboarding Wizard's default history window
STARTING_BANKROLL = 1000.0
DEFAULT_PAYOUT_PERCENT = 88  # matches money_studio.PAYOUT_PERCENT - AXIM's real observed historical average
PREVIEW_SAMPLE_SIZE = 30  # how many parsed sample trades preview_provider() returns for review


def _extract_decided_trades(analysis, date_by_message_id, excluded_message_ids=None):
    """excluded_message_ids (by signal_message_id) lets the Provider
    Onboarding Wizard's preview/correct step drop specific signals a
    human reviewer flagged as wrong before committing the import - a
    real, if modest, version of "correct incorrect mappings" (excluding
    a bad match, not yet re-teaching the pattern itself - see
    preview_provider's own docstring for what's honestly still a manual-
    research follow-up)."""
    excluded_message_ids = excluded_message_ids or set()
    by_message_id = {r["source_message_id"]: r for r in analysis["signal_records"]}
    trades = []
    for link in analysis["result_links"]:
        if link["result"] not in ("win", "loss", "draw"):
            continue
        signal_message_id = link["signal_message_id"]
        if signal_message_id is None or signal_message_id in excluded_message_ids:
            continue
        record = by_message_id.get(signal_message_id)
        if record is None or not record["normalized_asset"] or not record["direction"]:
            continue
        received_at = date_by_message_id.get(signal_message_id)
        if not received_at:
            continue
        trades.append({
            "signal_message_id": signal_message_id,
            "asset": record["normalized_asset"], "direction": record["direction"],
            "expiry": record.get("expiry"), "received_at": received_at, "result": link["result"],
        })
    trades.sort(key=lambda t: t["received_at"])
    return trades


def _build_preview_sample(analysis, messages, limit=PREVIEW_SAMPLE_SIZE):
    """The Provider Onboarding Wizard's Step 4 (Preview and Validate):
    up to `limit` decided trades, each with the ORIGINAL message text
    alongside what AXIM parsed from it, so a human can see exactly what
    would be imported before committing to it - matches the spec's
    "original message / parsed asset / direction / entry / expiry /
    matched result / confidence / warning" fields."""
    text_by_message_id = {m["message_id"]: m["text"] for m in messages}
    by_message_id = {r["source_message_id"]: r for r in analysis["signal_records"]}
    sample = []
    for link in analysis["result_links"]:
        if link["result"] not in ("win", "loss", "draw"):
            continue
        signal_message_id = link["signal_message_id"]
        if signal_message_id is None:
            continue
        record = by_message_id.get(signal_message_id)
        if record is None:
            continue
        warnings = []
        if not record["normalized_asset"]:
            warnings.append("No asset recognized in this message.")
        if not record["direction"]:
            warnings.append("No direction (BUY/SELL) recognized in this message.")
        sample.append({
            "signal_message_id": signal_message_id,
            "raw_signal_text": text_by_message_id.get(signal_message_id, ""),
            "raw_result_text": text_by_message_id.get(link["result_message_id"], "") if link["result_message_id"] else "",
            "parsed_asset": record["normalized_asset"], "parsed_direction": record["direction"],
            "parsed_expiry": record.get("expiry"), "confidence": record.get("confidence"),
            "matched_result": link["result"], "warnings": warnings,
        })
        if len(sample) >= limit:
            break
    return sample


async def preview_provider(chat_id, source_label=None, days=DEFAULT_HISTORY_DAYS):
    """Provider Onboarding Wizard Steps 1-4: fetch this provider's real
    history over the given day-window, detect its signal format, and
    return a preview - WITHOUT writing anything to the database (no
    imported_signals, no backtest_run, no recommendation). The caller
    reviews the sample, optionally notes which signal_message_ids look
    wrong, then calls analyze_and_onboard_provider with
    excluded_message_ids to actually commit.

    Honest scope note: this excludes bad matches from the commit: it
    does not yet let a reviewer CORRECT a wrong asset/direction and have
    AXIM re-learn the provider's pattern from that correction - that
    would need a genuinely editable, database-stored parsing-rule system
    (the wizard spec's "hybrid" ask), which is real, separate follow-up
    work, not something to silently claim as done here.

    Returns a result dict with a "status" field - same possible values
    as analyze_and_onboard_provider ("no_history", "pattern_not_detected",
    "no_decided_trades", "complete", where "complete" here means "ready
    to review/commit", not "already imported")."""
    import telegram_channels
    import provider_language_learner as learner

    messages, title = await telegram_channels.fetch_channel_raw_history(
        chat_id, limit=DEFAULT_HISTORY_LIMIT, source_label=source_label, days=days,
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

    sample = _build_preview_sample(analysis, messages)
    if not sample:
        return {
            "status": "no_decided_trades", "title": title, "message_count": len(messages),
            "pattern": analysis["pattern"], "coverage": analysis["coverage"],
            "note": (
                "A signal pattern was detected, but no trade could be linked to a win/loss/draw "
                "result - this provider may only post results as images, or never confirm "
                "outcomes in text."
            ),
        }

    return {
        "status": "complete", "title": title, "message_count": len(messages),
        "pattern": analysis["pattern"], "coverage": analysis["coverage"],
        "days": days, "sample": sample, "sample_count": len(sample),
        "total_decided_trades": len(_extract_decided_trades(
            analysis, {m["message_id"]: m["date_utc"] for m in messages},
        )),
    }


async def analyze_and_onboard_provider(chat_id, source_label=None, created_by="provider_onboarding",
                                        days=DEFAULT_HISTORY_DAYS, excluded_message_ids=None):
    """Returns a result dict with a "status" field:
    - "no_history": the channel has no messages at all
    - "pattern_not_detected": no common signal shape was recognized -
      needs a hand-built adapter
    - "no_decided_trades": a pattern matched, but nothing could be
      linked to a win/loss/draw outcome (e.g. results are photo-only)
    - "complete": imported, backtested, and a recommendation was
      generated (or explicitly wasn't, if every strategy was
      implausible for this provider - see capital_recommendation.py)

    excluded_message_ids (by signal_message_id, e.g. from
    preview_provider's sample) drops specific signals a human reviewer
    flagged as wrong before this actually commits the import - the
    Provider Onboarding Wizard's Step 4 correction, applied here."""
    import database
    import telegram_channels
    import provider_language_learner as learner
    import backtest_engine
    import capital_recommendation
    import money_studio

    messages, title = await telegram_channels.fetch_channel_raw_history(
        chat_id, limit=DEFAULT_HISTORY_LIMIT, source_label=source_label, days=days,
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
    trades = _extract_decided_trades(analysis, date_by_message_id, excluded_message_ids=excluded_message_ids)

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
