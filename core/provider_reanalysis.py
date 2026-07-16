"""Automatic Provider Re-analysis (Phase 2 Priority #4) - periodically
re-runs the recommendation pipeline for every provider that has a real,
currently-synced Telegram channel to refresh history from, and notifies
the owner if a provider's recommendation meaningfully changed: a
different best strategy now wins, or its confidence/performance
deteriorated.

Deliberately scoped to providers with a REAL synced channel
(core/provider_onboarding.py's flow, or any channel a human has since
added to AXIM's Signal Sources) - re-running analysis against a static
historical dump (the OPT SIGNALS research branch's 5 Demo-ready
providers, imported via scripts/import_provider_research.py) would
always produce the exact same result, since that data never changes.
Automatic re-analysis is only meaningful where there's live history to
actually refresh from - this module says so plainly for the providers
it skips, rather than silently "succeeding" at a no-op.
"""
import sys
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CORE_DIR))

# A change below this many percentage points in win_rate/ROI is treated
# as normal sample noise, not real deterioration worth interrupting the
# owner about - an explicit, documented threshold, not a scientific one.
DETERIORATION_WIN_RATE_DROP_THRESHOLD = 0.05  # 5 percentage points
DETERIORATION_ROI_DROP_THRESHOLD = 10.0  # 10 percentage points of ROI


def _fmt_pct(value):
    return f"{value * 100:.1f}%" if value is not None else "n/a"


def classify_change(old_recommendation, new_recommendation):
    """Pure. Returns a list of human-readable change notes, or an empty
    list if nothing meaningful changed - the caller only notifies when
    this is non-empty. old_recommendation may be None (first-ever
    analysis, nothing to compare against - never itself "a change")."""
    if old_recommendation is None or new_recommendation is None:
        return []

    notes = []
    old_key = old_recommendation.get("best_strategy_key")
    new_key = new_recommendation.get("best_strategy_key")
    if old_key != new_key:
        notes.append(
            f"Recommended strategy changed from {old_recommendation.get('best_strategy_name')!r} "
            f"to {new_recommendation.get('best_strategy_name')!r}."
        )

    old_win_rate = old_recommendation.get("win_rate")
    new_win_rate = new_recommendation.get("win_rate")
    if old_win_rate is not None and new_win_rate is not None:
        if (old_win_rate - new_win_rate) >= DETERIORATION_WIN_RATE_DROP_THRESHOLD:
            notes.append(f"Win rate dropped from {_fmt_pct(old_win_rate)} to {_fmt_pct(new_win_rate)}.")

    old_roi = old_recommendation.get("roi_percent")
    new_roi = new_recommendation.get("roi_percent")
    if old_roi is not None and new_roi is not None:
        if (old_roi - new_roi) >= DETERIORATION_ROI_DROP_THRESHOLD:
            notes.append(f"ROI dropped from {old_roi:.1f}% to {new_roi:.1f}%.")

    # A provider that HAD a recommendation and now has none at all (every
    # strategy became implausible on the fresh data) is itself real news.
    if old_recommendation is not None and new_recommendation.get("_no_recommendation"):
        notes.append("No strategy is recommended anymore - every official strategy's backtest became implausible.")

    return notes


async def reanalyze_provider(source_label, chat_id):
    """Re-runs the full onboarding pipeline for one already-known
    provider using its current, live Telegram history, and returns
    (old_recommendation, new_recommendation_or_marker, change_notes).
    new_recommendation_or_marker is {"_no_recommendation": True} if the
    re-analysis found no plausible strategy this time (a real, reportable
    outcome, not a failure)."""
    import database
    import provider_onboarding

    old_recommendation = database.get_capital_recommendation(source_label)
    result = await provider_onboarding.analyze_and_onboard_provider(chat_id, source_label=source_label)

    if result["status"] != "complete":
        return old_recommendation, {"_no_recommendation": True, "_status": result["status"]}, (
            [f"Re-analysis could not complete: {result.get('note', result['status'])}"] if old_recommendation else []
        )

    new_recommendation = database.get_capital_recommendation(source_label)
    if new_recommendation is None:
        new_recommendation = {"_no_recommendation": True}
    notes = classify_change(old_recommendation, new_recommendation)
    return old_recommendation, new_recommendation, notes


async def reanalyze_all_known_providers():
    """DB-driving orchestrator - finds every provider with BOTH an
    existing capital_recommendation AND a real synced Telegram channel
    (source_label matches a ui_channels.title), re-analyzes each, and
    notifies the owner (core/database.create_notification) for every
    provider whose recommendation meaningfully changed. Returns a
    summary list, one entry per provider considered, so a caller
    (the scheduled script, or a future admin-triggered endpoint) can log
    or display exactly what happened - including providers skipped
    because they have no live channel to refresh from."""
    import database

    recommendations = database.list_capital_recommendations()
    owner = database.get_owner_user()
    summary = []

    for rec in recommendations:
        source_label = rec["source_label"]
        channel = database.find_channel(title=source_label)
        if channel is None:
            summary.append({
                "source_label": source_label, "status": "skipped_no_live_channel",
                "note": "Not synced as a real Telegram channel - re-analysis would only repeat the same static historical data.",
            })
            continue

        old_rec, new_rec, notes = await reanalyze_provider(source_label, int(channel["chat_id"]))
        entry = {"source_label": source_label, "status": "reanalyzed", "changes": notes}
        summary.append(entry)

        if notes and owner is not None:
            message = f"{source_label}: " + " ".join(notes)
            database.create_notification(owner["id"], message, source="provider_reanalysis")

    return summary
