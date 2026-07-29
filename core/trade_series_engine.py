"""
Martin Trader scheduled-entry execution (docs/opt_signals_gap_queue.md item 2 -
the "product decision" that item deliberately scoped but left unbuilt: "should
AXIM delay execution until the stated time, reject signals whose entry time
has already passed, or something else?").

2026-07-27 product decision (next-five-minute-boundary redesign): Martin
Trader signals reliably arrive a few minutes before the intended trade,
and the intended entry is always Pocket Option's own next 5-minute candle
boundary. AXIM does NOT interpret the provider's published "Entry:"/
"Martingale:" clock times, any provider timezone, or published retry
times for scheduling purposes - a prior version of this module did (see
git history: the 2026-07-27 Martin Trader timezone incident, where
those published times were misinterpreted and caused signals to be
scheduled ~9 hours late), and that entire class of complexity has been
removed. Entry #1 is scheduled for the next 5-minute boundary after AXIM
receives the signal; each later entry (if the previous one lost) is
scheduled for the next 5-minute boundary after that loss becomes known -
never precomputed, never derived from anything the provider published.
This module still treats the whole signal as ONE managed series rather
than 4 independent trades: execute Entry #1; if it wins, stop; if it
loses, schedule and execute the next entry the same way; stop after the
first win or after 4 total entries.

Despite the provider calling this "Martingale:", the stake is NEVER scaled
between entries - every entry uses the exact fixed stake this series was
created with (AXIM's own martingale_settings table, which DOES scale stake
after a loss, is a completely separate, unrelated feature and is never
touched by this module). "Martingale" here describes the provider's own
re-entry-on-loss vocabulary, not AXIM's stake-sizing behavior.

Design: series-level state lives in database.trade_series (the source of
truth - not in-memory, so a process restart naturally resumes from wherever
the DB says a series is, the same restart-safety discipline every other
poll loop in this codebase already follows, e.g.
core/telegram_listener.py's _test_trade_poll_loop). Each individual entry
that actually fires becomes its own real `signals` row (series_id/
entry_number columns) and goes through the EXACT SAME execution path as
every other provider's signal - broker_account_manager.route_signal ->
TradeCoordinator.handle_signal -> pocket_executor.prepare_trade - so it
gets the Browser Health Manager, the worker pool, every risk check,
duplicate-signal protection, and the full audit trail for free, with zero
new execution-path code. This module only decides WHEN to call that
existing path and WHETHER to call it again after an outcome.
"""
import asyncio
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CORE_DIR.parent
sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(PROJECT_ROOT / "execution"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))

import database
import broker_account_manager
import pocket_executor
import pocket_dom
from execution_latency import ExecutionLatency
from timeline import TradeTimeline
from trade_lifecycle import TradeStatus
from logger import get_logger
from event_bus import get_event_bus
from settings import MARTIN_TRADER_PRESTAGE_SECONDS

logger = get_logger("axim.lifecycle", filename="lifecycle.log")

DUE_ENTRY_POLL_INTERVAL_SECONDS = 3
MAX_ENTRIES = 4

# How long before the escalation deadline (scheduled boundary minus this
# many seconds) an unsecured worker gets logged as an escalation, per
# the 2026-07-27 precision-latency audit's explicit requirement ("escalate
# if the worker is not secured by T-minus 5 seconds").
PRESTAGE_ESCALATION_SECONDS = 5

# How often the precision final-wait loop re-checks the monotonic clock
# once inside the pre-stage window - short enough to submit within a few
# tens of milliseconds of the boundary, without busy-spinning tightly for
# an extended period (explicit constraint).
PRECISION_WAIT_TICK_SECONDS = 0.05

# The resolution method name persisted on every series
# (trade_series.schedule_resolution_method) - 2026-07-27 product
# decision: replace the provider-timezone-based resolver (which required
# interpreting the channel's own published clock time and declared
# timezone) with something far simpler - a Martin Trader signal reliably
# arrives a few minutes before the intended trade, and Pocket Option's
# own 5-minute candle boundaries are the only real market structure that
# matters. Exists so a FUTURE change to this algorithm is distinguishable
# from historical rows on disk (including the prior "provider_timezone_v1"
# rows), rather than silently reinterpreting old data under new rules.
SCHEDULE_RESOLUTION_METHOD = "next_five_minute_boundary_v1"

# A scheduled entry that's still this old past its own published time is
# treated as stale and rejected rather than fired - a Martin Trader entry
# is time-sensitive (it's a specific bet on where price will be relative
# to a specific clock time); one fired hours late, after a real outage
# (the listener was down, a deploy took a while), bears no real
# relationship to the market conditions the signal was actually based on.
# Generous enough that a normal restart (this codebase's own restarts
# typically take 1-3 minutes) never falsely rejects a legitimately
# slightly-delayed entry.
STALE_ENTRY_THRESHOLD_SECONDS = 30 * 60

# signals.execution_status values that mean "this entry's real-world
# outcome is already known" - used by reconcile_stuck_series to detect a
# result that was written (by pocket_executor.track_outcome, possibly via
# recovery.py's resume path) before this process's own trade.closed
# subscriber had a chance to react to it.
_RESOLVED_STATUS_TO_RESULT = {
    TradeStatus.RESULT_WIN.value: "win",
    TradeStatus.RESULT_LOSS.value: "loss",
    TradeStatus.RESULT_DRAW.value: "draw",
}

# The inverse mapping - used by reconcile_stuck_series/reconcile_series_
# manually to mirror pocket_executor.track_outcome's own final
# execution_status (RESULT_WIN/LOSS/DRAW, not just TRADE_CLOSED) whenever
# a result is applied outside the normal live-tracking path.
_RESULT_TO_STATUS = {
    "win": TradeStatus.RESULT_WIN, "loss": TradeStatus.RESULT_LOSS, "draw": TradeStatus.RESULT_DRAW,
}


def _now_utc():
    """The one seam for "what time is it right now" in this module -
    exists purely so tests can patch a single, easily-mocked function
    (patch.object(engine, "_now_utc", return_value=...)) instead of
    mocking the datetime module itself, which would also break every
    other real datetime(...)/fromisoformat(...) call in this file.
    Production behavior is identical to datetime.now(timezone.utc)."""
    return datetime.now(timezone.utc)


class ScheduleResolutionError(Exception):
    """Raised only when the reference timestamp itself is unusable (not
    timezone-aware) - never for anything about the published signal
    text, since the 2026-07-27 redesign no longer interprets that at
    all. The caller (core/telegram_listener.py's existing exception
    boundary) logs and tracks this as a real FAILED pipeline event
    rather than falling through to any default."""


def _next_five_minute_boundary_utc(reference_dt_utc):
    """The ENTIRE Martin Trader scheduling model (2026-07-27 product
    decision, replacing the provider-timezone-based resolver this same
    module used previously): find the next Pocket Option 5-minute clock
    mark (:00/:05/:10/.../:55) strictly after reference_dt_utc.

    No published clock time, no provider timezone, no Martingale retry
    times are ever consulted here - a Martin Trader signal reliably
    arrives a few minutes before its intended trade, and Pocket Option's
    own 5-minute candle boundaries are the only real market structure
    that matters. Verified examples:
        12:00:01 -> 12:05:00   12:02:30 -> 12:05:00
        12:04:59 -> 12:05:00   12:05:01 -> 12:10:00 (never the SAME instant)
        12:58:00 -> 13:00:00 (hour rollover)
        23:58:00 -> 00:00:00 next day (midnight rollover)
    Hour/midnight rollover both fall out of plain timedelta arithmetic -
    no special-casing needed.

    The exact-boundary case (reference_dt_utc == :00/:05/.../:55 with no
    leftover seconds) and a reference already past what would otherwise
    be "due soon" are NOT expected Martin Trader behavior - under normal
    operation the signal always arrives a few minutes BEFORE its
    intended candle, per the product's own stated timing. Landing
    exactly on a boundary, or arriving late, is abnormal telemetry (a
    delivery delay, a clock skew, a malformed/edge-case signal), not a
    strategy branch this module optimizes for. It is still handled
    defensively rather than raising - "too late for this boundary, roll
    to the next one" - so an unusual delivery never silently misfires
    into the WRONG candle, but this defensive path is deliberately
    distinct from, and should never be read as, the normal case."""
    if reference_dt_utc is None or reference_dt_utc.tzinfo is None:
        raise ScheduleResolutionError(
            "reference datetime must be timezone-aware - refusing to schedule from a naive one"
        )
    truncated = reference_dt_utc.astimezone(timezone.utc).replace(second=0, microsecond=0)
    remainder = truncated.minute % 5
    minutes_to_add = 5 - remainder if remainder != 0 else 5
    return truncated + timedelta(minutes=minutes_to_add)


def _published_entry_times_for_audit(signal):
    """AUDIT-ONLY (2026-07-27 product decision): the published "Entry:"
    time and "Martingale:" re-entry times a Martin Trader signal states,
    kept purely so a human can see what the provider originally claimed
    next to what AXIM actually did - never consulted for scheduling.
    Returns None if there's no entry_time at all, which is still used as
    the one gate for "is this a genuine, schedulable signal" (a message
    with no stated entry time was never a real trading signal to begin
    with)."""
    entry_time = signal.get("entry_time")
    if not entry_time:
        return None
    times = [entry_time]
    for scheduled in signal.get("scheduled_entries", []):
        if len(times) >= MAX_ENTRIES:
            break
        times.append(scheduled["time"])
    return times[:MAX_ENTRIES]


async def create_series_from_signal(signal, channel_id, stake, telegram_message_date_utc,
                                     fund_id=None, broker_account_id=None, session_id=None,
                                     source_message_id=None, signal_received_at_utc=None):
    """Entry point for a channel gated into scheduled-entry mode (today,
    only channel 163). Persists the series and returns its id - does NOT
    execute anything itself. There is deliberately no "execute Entry #1
    immediately" special case: run_due_entries_loop's poll tick is what
    fires every entry, including the first, once its own scheduled time
    arrives, keeping the state machine uniform.

    Idempotent by (channel_id, source_message_id): a redelivered Telegram
    event (Telethon reconnect, at-least-once delivery) for a message this
    channel already turned into a series returns that SAME series_id
    rather than starting a second, independent one for what is really one
    published signal.

    2026-07-27 product decision: Entry #1 is scheduled for the next
    Pocket Option five-minute boundary after signal_received_at_utc (AXIM's
    own real receipt/processing moment - defaults to right now if not
    given), never from the published clock time or a provider timezone.
    telegram_message_date_utc is preserved purely for audit (see
    core/database.py's own column comment) - it never influences the
    schedule. max_entries is always MAX_ENTRIES (4), regardless of how
    many times the provider happened to publish - "no more than four
    total entries" is an AXIM-side rule, not derived from the signal."""
    existing = await asyncio.to_thread(database.get_trade_series_by_message, channel_id, source_message_id)
    if existing is not None:
        logger.info(
            "trade_series_engine: message_id=%s already has series_id=%s - not creating a duplicate",
            source_message_id, existing["id"],
        )
        return existing["id"]

    published_times = _published_entry_times_for_audit(signal)
    if not published_times:
        logger.warning(
            "trade_series_engine: signal for %s has no entry_time - not scheduled as a series",
            signal.get("asset"),
        )
        return None

    received_at = signal_received_at_utc or _now_utc()
    entry_1_utc = _next_five_minute_boundary_utc(received_at)

    series_id = await asyncio.to_thread(
        database.create_trade_series,
        channel_id=channel_id, asset=signal["asset"], direction=signal["direction"],
        expiry=signal["expiry"], stake=stake, entry_times=published_times,
        source_message_id=source_message_id, raw_message=signal.get("raw_message"),
        fund_id=fund_id, broker_account_id=broker_account_id, session_id=session_id,
        max_entries=MAX_ENTRIES,
        published_entry_time=published_times[0],
        entry_times_utc=[entry_1_utc.isoformat()],
        telegram_message_date_utc=telegram_message_date_utc.isoformat() if telegram_message_date_utc else None,
        schedule_resolution_method=SCHEDULE_RESOLUTION_METHOD,
    )
    logger.info(
        "trade_series_engine: series_id=%s created for %s %s, entry #1 scheduled for next five-minute "
        "boundary %s (signal received %s)",
        series_id, signal["asset"], signal["direction"], entry_1_utc.isoformat(), received_at.isoformat(),
    )
    return series_id


async def _execute_entry(default_coordinator, series, entry_number):
    """Fires one entry through the real, existing execution path. Marks
    the series 'active' BEFORE calling route_signal - not after - so a
    concurrent poll tick (or this same tick running long) can never fire
    the same entry twice; this is the series-level duplicate-execution
    guard, on top of (not instead of) TradeCoordinator's own existing
    duplicate_detection stage."""
    await asyncio.to_thread(database.advance_trade_series, series["id"], entry_number, "active")

    signal = {
        "asset": series["asset"],
        "direction": series["direction"],
        "expiry": series["expiry"],
        "raw_message": (
            f"[Martin Trader series {series['id']}, entry #{entry_number}/{series['max_entries']}] "
            f"{series['raw_message'] or ''}"
        ),
    }
    logger.info(
        "trade_series_engine: series_id=%s firing entry #%d (%s %s)",
        series["id"], entry_number, series["asset"], series["direction"],
    )
    try:
        # sent_at deliberately omitted - TradeCoordinator's MAX_SIGNAL_AGE
        # staleness check (route_signal -> handle_signal ->
        # _run_preflight_checks) only runs when sent_at is given, and it
        # exists to catch a laggy PIPELINE (a message that arrived and
        # then took too long to reach execution), not a signal that is
        # intentionally, correctly executing later at its own published
        # time. _fire_due_entries already only calls this once that
        # scheduled time has genuinely arrived - that IS this entry's
        # staleness check.
        result = await broker_account_manager.route_signal(
            signal, default_coordinator, source="martin_trader_series",
            session_id=series["session_id"], channel_id=series["channel_id"],
            series_id=series["id"], entry_number=entry_number,
            # This series' own fixed stake, set once at creation - see
            # TradeCoordinator.handle_signal's docstring for why this
            # must be explicit rather than relying on the Risk Engine's
            # session_id=None fallback (the GLOBAL TRADE_AMOUNT setting,
            # independently configured for unrelated purposes).
            fixed_stake=series["stake"],
        )
    except Exception as e:
        logger.error("trade_series_engine: series_id=%s entry #%d failed to route: %s", series["id"], entry_number, e)
        await asyncio.to_thread(
            database.advance_trade_series, series["id"], entry_number, "error", result="error",
        )
        return
    await _handle_route_result(series, entry_number, result)


async def _handle_route_result(series, entry_number, result):
    """The shared "what does this route_signal/submit_staged_trade result
    dict mean for the series" logic - used by both _execute_entry (the
    original, always-available path) and _run_precision_entry (2026-07-27
    precision-latency audit's pre-staged path), so a policy rejection or
    a transient retry is handled identically regardless of which path
    produced it."""
    if isinstance(result, dict) and result.get("status") == "clicked":
        return  # real trade placed - wait for _on_trade_closed to decide what happens next

    # Not every non-"clicked" result means the same thing. A handful of
    # reasons are genuinely transient (a worker was busy for a moment) and
    # deserve exactly the retry a fresh signal would get on the next poll
    # tick. Everything else - an active risk gate (consecutive-loss lock,
    # minimum payout, observation mode), a broker account problem, a stale
    # signal - is a POLICY reason that will very likely still be true on
    # the next tick too, sometimes for a long time (confirmed real: this
    # codebase's own consecutive-loss lock has stayed engaged for over a
    # day waiting on an explicit operator reset). Retrying those silently,
    # forever, every 3 seconds, would be a real defect, not resilience -
    # this series is marked 'blocked' instead so it stops consuming the
    # poll loop's attention and shows up in the summary as needing a human
    # look, rather than spinning invisibly.
    rule = result.get("rule") if isinstance(result, dict) else None
    reason = result.get("reason") if isinstance(result, dict) else str(result)
    if rule == "all_workers_busy":
        logger.warning(
            "trade_series_engine: series_id=%s entry #%d found every worker busy - retrying",
            series["id"], entry_number,
        )
        await asyncio.to_thread(database.advance_trade_series, series["id"], entry_number - 1, "pending")
        return

    logger.error(
        "trade_series_engine: series_id=%s entry #%d blocked by policy (%s: %s) - not retrying automatically",
        series["id"], entry_number, rule, reason,
    )
    await asyncio.to_thread(
        database.advance_trade_series, series["id"], entry_number - 1, "blocked", result=f"blocked:{rule}",
    )


async def _apply_entry_outcome(series, entry_number, result, net_pl):
    """Shared by _on_trade_closed (the live path) and reconcile_stuck_series
    (the startup-recovery path) - one place decides whether a resolved
    entry stops the series (a win) or schedules the next entry at the
    next Pocket Option five-minute boundary (a loss/draw, entries
    remaining) or exhausts it (a loss/draw, none remaining).

    Only a genuinely terminal, known result ever advances anything -
    2026-07-27 explicit product requirement ("PENDING, UNKNOWN, or
    missing result -> do not advance"). This is reachable in practice
    only via a malformed/unexpected trade.closed payload (the live event
    only ever fires once pocket_executor has a real win/loss/draw), but
    the guard is explicit rather than incidental."""
    if result not in ("win", "loss", "draw"):
        logger.warning(
            "trade_series_engine: series_id=%s entry #%d result=%r is not a terminal outcome - not advancing",
            series["id"], entry_number, result,
        )
        return

    if result == "win":
        logger.info(
            "trade_series_engine: series_id=%s entry #%d WON - series closed", series["id"], entry_number,
        )
        await asyncio.to_thread(
            database.advance_trade_series, series["id"], entry_number, "won", result="win", net_profit_loss=net_pl,
        )
        return

    # loss or draw - draw is treated as "did not win", same as a loss, for
    # the purpose of "was the objective (a winning entry) met", per the
    # explicit "do not treat as a loss; reconcile according to existing
    # safe draw handling" requirement - net_pl for a real draw is 0.0,
    # so net_profit_loss is unaffected either way; only the win/loss
    # counters this feeds elsewhere ever distinguish "won" from anything
    # else, and a draw was never a win.
    if entry_number >= series["max_entries"]:
        logger.info(
            "trade_series_engine: series_id=%s entry #%d was the last entry and did not win - "
            "series exhausted", series["id"], entry_number,
        )
        await asyncio.to_thread(
            database.advance_trade_series, series["id"], entry_number, "lost_exhausted",
            result="loss", net_profit_loss=net_pl,
        )
    else:
        next_entry_utc = _next_five_minute_boundary_utc(_now_utc())
        logger.info(
            "trade_series_engine: series_id=%s entry #%d lost - entry #%d scheduled for next five-minute "
            "boundary %s",
            series["id"], entry_number, entry_number + 1, next_entry_utc.isoformat(),
        )
        await asyncio.to_thread(
            database.schedule_next_entry, series["id"], entry_number, next_entry_utc.isoformat(),
        )


async def _on_trade_closed(payload):
    """event_bus subscriber - the live path: a real trade this process
    itself placed and tracked to a result. Looked up by trade_id, not
    series_id, since that's all trade.closed's payload carries - a trade
    with no series_id (every other provider's signals, the overwhelming
    majority) is a fast, cheap no-op lookup miss."""
    trade_id = payload.get("trade_id")
    if trade_id is None:
        return
    entry = await asyncio.to_thread(database.get_signal_detail, trade_id)
    if entry is None or entry.get("series_id") is None:
        return

    series = await asyncio.to_thread(database.get_trade_series, entry["series_id"])
    if series is None or series["status"] not in ("active",):
        return

    await _apply_entry_outcome(series, entry["entry_number"], payload.get("result"), payload.get("profit_loss") or 0.0)


async def reconcile_stuck_series(warmup_service=None):
    """Startup-only recovery pass (core/telegram_listener.py calls this
    once, right after recovery.run_recovery() - see that module's own
    docstring for why 'active' entries need this at all): a series left
    'active' by a crash never gets a trade.closed event for the specific
    case recovery.mark_abandoned_preparations() handles - an entry stuck
    at trade_prepared (ARMED was false, or the process died before the
    click itself actually happened). There is no real position there to
    reconcile, so recovery.py correctly marks that signals row ERROR
    without publishing an outcome event - but this series would otherwise
    wait forever for an event that will never come.

    A trade that WAS actually clicked before the crash and is still
    genuinely being tracked is NOT this function's concern -
    recovery.resume_pending_trades already re-attaches real track_outcome
    tracking for it (see that module), which naturally publishes a
    genuine trade.closed once it resolves; _on_trade_closed (registered
    before this ever runs - see telegram_listener._startup()'s ordering)
    picks that up exactly like any other outcome, live or resumed.

    2026-07-29 reconciliation-gap fix (verified production incident:
    series 105 - a real browser crash during wait_for_trade_result left
    the entry's execution_status='error', result=f"error:{e}", a string
    this function's OLD narrow "== 'error:abandoned_on_restart'" check
    could never match, and recovery.resume_pending_trades' OLD narrow
    get_open_trades() scan (trade_clicked/trade_opened only) had already
    stopped seeing it too - so the series sat 'active' forever with
    nothing watching it). Eligibility for reconciliation is now based on
    trade lifecycle state, not error wording:

        possibly_submitted (opened_at is set - a real click happened)
        AND not_authoritatively_resolved (no win/loss/draw recorded)
        = reconciliation_required

    which covers browser crashes, context closure, timeouts, process
    restarts, database interruptions, and any other unexpected exception
    during result monitoring - not just the one exact string recovery.py
    happens to write for the "never clicked" case. When warmup_service is
    given, an entry in this state is checked against Pocket Option's own
    Closed-trades history (pocket_dom.find_closed_trade_by_criteria) -
    asset+direction+amount+approximate close time, since no broker trade
    ID is ever readable from the DOM (verified, documented limitation).
    Only a UNIQUE match is ever auto-applied; "no_match"/"multiple_matches"/
    "read_failed" all fail closed (database.mark_series_reconciliation_
    required) rather than guess - the series is explicitly flagged and
    excluded from list_pending_trade_series (status != 'pending'/'active')
    so it can never silently resume or re-fire on its own. Without a
    warmup_service (e.g. a caller that hasn't got a browser context),
    the same fail-closed marking happens immediately, never guessing."""
    for series in await asyncio.to_thread(database.list_pending_trade_series):
        if series["status"] != "active":
            continue
        entry = await asyncio.to_thread(database.get_series_entry, series["id"], series["current_entry_number"])
        if entry is None:
            logger.error(
                "trade_series_engine: series_id=%s is active but entry #%d has no signals row at all - "
                "inconsistent state, marking reconciliation_required",
                series["id"], series["current_entry_number"],
            )
            await asyncio.to_thread(
                database.mark_series_reconciliation_required, series["id"], "series_active_with_no_child_entry",
            )
            continue

        # A genuinely resolved outcome (win/loss/draw) already recorded on
        # this entry's own row but never applied to the series - possible
        # if the process crashed in the narrow window between
        # pocket_executor.track_outcome writing the result and this
        # module's own trade.closed subscriber reacting to it. Applying it
        # here is the same logic _on_trade_closed uses, just driven by the
        # DB's own already-written result instead of a live event. Checked
        # BEFORE the "never executed" branch below since a resolved result
        # is more specific/definitive than the mere absence of opened_at.
        if entry.get("execution_status") in _RESOLVED_STATUS_TO_RESULT:
            result = _RESOLVED_STATUS_TO_RESULT[entry["execution_status"]]
            logger.warning(
                "trade_series_engine: series_id=%s entry #%d resolved to %r before this process could react to "
                "it - reconciling now",
                series["id"], series["current_entry_number"], result,
            )
            await _apply_entry_outcome(series, series["current_entry_number"], result, entry.get("profit_loss") or 0.0)
            continue
        if entry.get("result") in ("win", "loss", "draw"):
            # Same case, but only the plain `result` column ended up
            # terminal (execution_status wasn't mapped to a RESULT_* value)
            # - treat identically rather than falling through to
            # broker-history reconciliation for an outcome we already have.
            await asyncio.to_thread(
                database.update_trade_status, entry["id"], _RESULT_TO_STATUS.get(entry["result"], TradeStatus.ERROR),
            )
            await _apply_entry_outcome(
                series, series["current_entry_number"], entry["result"], entry.get("profit_loss") or 0.0,
            )
            continue

        # Never actually became a real trade (no click happened, so there
        # is no real position to reconcile) - driven by the absence of
        # opened_at (possibly_submitted is False), not by matching the one
        # specific string recovery.py happens to write for this case, so
        # ANY failure before the click is treated the same way.
        if not entry.get("opened_at"):
            logger.warning(
                "trade_series_engine: series_id=%s entry #%d never actually executed (no opened_at, result=%r) - "
                "retrying the same entry",
                series["id"], series["current_entry_number"], entry.get("result"),
            )
            await asyncio.to_thread(
                database.advance_trade_series, series["id"], series["current_entry_number"] - 1, "pending",
            )
            continue

        # possibly_submitted AND not_authoritatively_resolved - the general
        # case. entry["execution_status"] here is whatever it happened to
        # be left at (trade_clicked, trade_opened, or 'error' with an
        # arbitrary message) - none of that matters; only opened_at set +
        # no terminal result does.
        logger.warning(
            "trade_series_engine: series_id=%s entry #%d was possibly submitted (opened_at=%s) but has no "
            "authoritative result (execution_status=%r, result=%r) - attempting broker-history reconciliation",
            series["id"], series["current_entry_number"], entry.get("opened_at"),
            entry.get("execution_status"), entry.get("result"),
        )

        if warmup_service is None:
            logger.error(
                "trade_series_engine: series_id=%s entry #%d needs reconciliation but no warmup_service was "
                "provided - marking reconciliation_required rather than guessing",
                series["id"], series["current_entry_number"],
            )
            await asyncio.to_thread(
                database.mark_series_reconciliation_required, series["id"],
                "possibly_submitted_not_resolved_no_warmup_service", entry.get("result"),
            )
            continue

        try:
            opened_at = datetime.fromisoformat(entry["opened_at"])
            expiry_seconds = pocket_dom.expiry_to_seconds(entry["timeframe"])
            match_status, match = await pocket_dom.find_closed_trade_by_criteria(
                warmup_service, entry["asset"], entry["direction"], entry.get("trade_amount"),
                opened_at, expiry_seconds,
            )
        except Exception as e:
            logger.error(
                "trade_series_engine: series_id=%s entry #%d broker-history lookup raised %s - failing closed",
                series["id"], series["current_entry_number"], e,
            )
            await asyncio.to_thread(
                database.mark_series_reconciliation_required, series["id"], "broker_history_lookup_raised", str(e),
            )
            continue

        if match_status != "unique":
            logger.error(
                "trade_series_engine: series_id=%s entry #%d broker-history match=%s - failing closed, marking "
                "reconciliation_required rather than guessing",
                series["id"], series["current_entry_number"], match_status,
            )
            await asyncio.to_thread(
                database.mark_series_reconciliation_required, series["id"],
                f"broker_history_{match_status}", entry.get("result"),
            )
            continue

        profit_loss = None
        if match["final_value"] is not None and match["stake"] is not None:
            profit_loss = match["final_value"] - match["stake"]
        logger.warning(
            "trade_series_engine: series_id=%s entry #%d uniquely matched broker history: result=%s - "
            "reconciling automatically",
            series["id"], series["current_entry_number"], match["result"],
        )
        # Mirrors pocket_executor.track_outcome's own two-step update
        # (TRADE_CLOSED, then the final RESULT_WIN/LOSS/DRAW status) so a
        # broker-history-reconciled entry's execution_status ends up
        # identical to one resolved through live tracking.
        closed_at = datetime.now().isoformat()
        await asyncio.to_thread(
            database.update_trade_status, entry["id"], TradeStatus.TRADE_CLOSED,
            closed_at=closed_at, result=match["result"], profit_loss=profit_loss,
        )
        result_status = _RESULT_TO_STATUS.get(match["result"], TradeStatus.ERROR)
        await asyncio.to_thread(
            database.update_trade_status, entry["id"], result_status,
            closed_at=closed_at, result=match["result"], profit_loss=profit_loss,
        )
        await _apply_entry_outcome(series, series["current_entry_number"], match["result"], profit_loss or 0.0)


async def reconcile_series_manually(series_id, operator, reconciliation_source, reconciliation_reason,
                                     authoritative_result, authoritative_pnl=None,
                                     original_error=None, broker_trade_id=None, entry_number=None):
    """The explicit, audited administrative action required whenever
    automatic broker-history matching (reconcile_stuck_series, above)
    cannot uniquely resolve an entry on its own - e.g. series 105, where
    Pocket Option's DOM exposes no broker trade/order ID at all, so a
    human operator must read the authoritative Pocket Option trade
    history directly and supply the real result. Never infers a result
    from price movement, local logs, or the intended direction - the
    caller is expected to have already determined authoritative_result/
    authoritative_pnl from the broker's own trade history.

    Routes through the SAME _apply_entry_outcome every live and automatic
    reconciliation path uses, so risk counters and net P&L update through
    exactly one, already-tested code path - never a parallel
    reimplementation that could double-count or diverge.

    Idempotency guard: a series that is no longer 'active' (already
    resolved, by this function or any other path) is rejected rather than
    silently re-applying an outcome - required so a duplicate startup
    reconciliation attempt, or a duplicate manual call, can never
    double-grade the same trade twice."""
    if authoritative_result not in ("win", "loss", "draw"):
        raise ValueError(f"authoritative_result must be one of win/loss/draw, got {authoritative_result!r}")
    if not operator or not reconciliation_source or not reconciliation_reason:
        raise ValueError("operator, reconciliation_source, and reconciliation_reason are all mandatory")

    series = await asyncio.to_thread(database.get_trade_series, series_id)
    if series is None:
        raise ValueError(f"no such trade_series id={series_id}")
    if series["status"] not in ("active", "reconciliation_required"):
        # Only 'active' (never yet reconciled) and 'reconciliation_required'
        # (flagged by reconcile_stuck_series, awaiting exactly this manual
        # step) are eligible - any other status (won/lost_exhausted/error/
        # blocked/cancelled) means some path already resolved this series,
        # so applying an outcome again would double-grade it.
        logger.warning(
            "trade_series_engine: series_id=%s reconcile_series_manually called but series status=%r "
            "is already resolved - refusing to double-grade",
            series_id, series["status"],
        )
        return {"applied": False, "reason": f"series_status_is_{series['status']}_already_resolved"}

    entry_number = entry_number if entry_number is not None else series["current_entry_number"]
    entry = await asyncio.to_thread(database.get_series_entry, series_id, entry_number)

    audit = {
        "series_id": series_id,
        "reconciliation_source": reconciliation_source,
        "reconciliation_reason": reconciliation_reason,
        "operator": operator,
        "original_error": original_error,
        "broker_trade_id": broker_trade_id,
        "authoritative_result": authoritative_result,
        "authoritative_pnl": authoritative_pnl,
    }
    logger.warning(
        "trade_series_engine: series_id=%s entry #%d manually reconciled by operator=%r via %s (%s): "
        "result=%s pnl=%s broker_trade_id=%s",
        series_id, entry_number, operator, reconciliation_source, reconciliation_reason,
        authoritative_result, authoritative_pnl, broker_trade_id,
    )

    if entry is not None:
        closed_at = datetime.now().isoformat()
        await asyncio.to_thread(
            database.update_trade_status, entry["id"], TradeStatus.TRADE_CLOSED,
            closed_at=closed_at, result=authoritative_result, profit_loss=authoritative_pnl,
        )
        await asyncio.to_thread(
            database.update_trade_status, entry["id"], _RESULT_TO_STATUS[authoritative_result],
            closed_at=closed_at, result=authoritative_result, profit_loss=authoritative_pnl,
        )

    await _apply_entry_outcome(series, entry_number, authoritative_result, authoritative_pnl or 0.0)
    reconciled_at = await asyncio.to_thread(database.record_manual_reconciliation, series_id, audit)
    return {"applied": True, "reconciled_at": reconciled_at, **audit}


# In-memory only (2026-07-27 precision-latency audit) - tracks which
# (series_id, entry_number) pairs already have a dedicated precision
# task running, so the coarse 3-second poll never spawns a second one
# for the same entry while the first is still pre-staging. Never the
# source of truth for correctness: the DB's own current_entry_number/
# status are what actually prevent a duplicate CLICK (route_signal's
# series-level "active" guard, unchanged) - this set only avoids
# redundant, wasted pre-stage attempts, and a process restart resetting
# it is always safe (see _run_precision_entry's own docstring).
_precision_tasks_in_flight = set()


async def _run_precision_entry(default_coordinator, series, entry_number, scheduled_dt, key):
    """2026-07-27 precision-latency audit's core mechanism - "the only
    critical action remaining at the boundary should be the final order
    submission":

    1. Records the signal and runs every risk check that does NOT need a
       browser worker immediately (Section C: "pass all risk checks that
       can safely be completed early") - reuses
       TradeCoordinator._run_preflight_checks directly rather than
       reimplementing it, so Emergency Stop/daily-loss/consecutive-loss/
       demo-only/etc. all run through the exact same audited code every
       other signal does.
    2. Waits (coarse, cheap asyncio.sleep) until MARTIN_TRADER_PRESTAGE_
       SECONDS before the boundary, then reserves a worker and pre-stages
       asset/expiry/amount/direction-controls-ready (Section C/D). Logged
       escalation if a worker isn't secured by PRESTAGE_ESCALATION_SECONDS
       before the boundary - per explicit requirement, this does NOT
       interrupt an active trade submission elsewhere; it waits its turn
       (existing FIFO worker_pool.acquire_worker), not true queue-jumping
       priority (which would require changing the shared pool's queue
       discipline - a risk to every other provider this audit avoids).
    3. Falls back to the ordinary _execute_entry() path if a worker still
       isn't secured by the boundary itself - a slow/unavailable worker
       degrades into a normal (less precise, but never missed) entry.
    4. Sleeps precisely (monotonic clock, short ticks - never one long
       blocking sleep, never a tight busy-spin) until the exact boundary,
       marks the series 'active' (the same duplicate-execution guard
       _execute_entry has always used, just timed differently), then
       submits only the direction click.

    Restart safety: if this task is killed mid-flight (process restart),
    the series' own DB status is untouched until step 4 ('active') - a
    fresh _fire_due_entries poll after restart simply notices the entry
    is (still) within its pre-stage window and spawns a new attempt.
    Known, accepted, narrow limitation: since record_signal_received (in
    _run_preflight_checks's caller below) has no idempotency guard of its
    own (unlike create_trade_series), a restart during the ~20s pre-stage
    window could leave one harmless, never-clicked extra `signals` row
    behind - never a duplicate real trade/click, since neither attempt
    ever reached that point."""
    latency = ExecutionLatency(series_id=series["id"], entry_number=entry_number)
    if entry_number == 1:
        # Entry 1's signal_received_at/series_created_at/boundary_calculated_at
        # all happen within microseconds of each other inside
        # create_series_from_signal - approximated here from the series'
        # own already-stored created_at/telegram_message_date_utc rather
        # than threading three more values through create_trade_series
        # for sub-millisecond precision on fields that are audit context,
        # not part of the six measured latency metrics themselves.
        if series.get("telegram_message_date_utc"):
            latency.mark("signal_received_at", at=datetime.fromisoformat(series["telegram_message_date_utc"]))
        if series.get("created_at"):
            created_dt = datetime.fromisoformat(series["created_at"])
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=timezone.utc)
            latency.mark("series_created_at", at=created_dt)
            latency.mark("boundary_calculated_at", at=created_dt)
    latency.set_scheduled_boundary(scheduled_dt)
    latency.mark("scheduler_awakened_at")

    signal = {
        "asset": series["asset"], "direction": series["direction"], "expiry": series["expiry"],
        "raw_message": (
            f"[Martin Trader series {series['id']}, entry #{entry_number}/{series['max_entries']}] "
            f"{series['raw_message'] or ''}"
        ),
    }
    timeline_token = None
    try:
        trade_id = await asyncio.to_thread(
            database.record_signal_received, signal,
            source="martin_trader_series", session_id=series["session_id"], channel_id=series["channel_id"],
            series_id=series["id"], entry_number=entry_number,
        )
        await asyncio.to_thread(database.record_execution_latency, trade_id, latency.timestamps)
        timeline = TradeTimeline(trade_id=trade_id)
        # 2026-07-27 precision-bottleneck investigation: this task never
        # called timeline.activate() before pre_stage_trade/
        # submit_staged_trade, so pocket_dom.click_direction's own
        # timeline.mark("clicked")/"confirmation_detected" calls (which
        # read the ambient get_current_timeline(), not an explicit
        # reference) were silently landing on no timeline at all - the
        # "clicked" vs "confirmation_detected" split has existed in
        # pocket_dom.py since before this feature, but was never actually
        # captured for a single Martin Trader execution. Scoped to this
        # task's own asyncio context (contextvars copy-on-task-creation),
        # so concurrent entries on other workers are unaffected.
        timeline_token = timeline.activate()

        outcome, payload = await asyncio.to_thread(
            default_coordinator._run_preflight_checks, trade_id, series["stake"], series["session_id"],
            series["asset"], series["direction"], series["expiry"], None, timeline,
            series["broker_account_id"], series["channel_id"], series["id"],
        )
        if outcome != "passed":
            logger.info(
                "trade_series_engine: series_id=%s entry #%d rejected during early (worker-independent) "
                "risk checks - %s",
                series["id"], entry_number, payload,
            )
            latency.mark("rejected_at")
            await asyncio.to_thread(database.record_execution_latency, trade_id, latency.timestamps)
            await _handle_route_result(series, entry_number, payload)
            return

        prestage_at = scheduled_dt - timedelta(seconds=MARTIN_TRADER_PRESTAGE_SECONDS)
        now = datetime.now(timezone.utc)
        if now < prestage_at:
            await asyncio.sleep((prestage_at - now).total_seconds())

        latency.mark("worker_requested_at")
        escalate_at = scheduled_dt - timedelta(seconds=PRESTAGE_ESCALATION_SECONDS)
        worker = None
        acquire_timeout = max(0.0, (scheduled_dt - datetime.now(timezone.utc)).total_seconds())
        if acquire_timeout > 0:
            worker = await default_coordinator.worker_pool.acquire_worker(
                timeout=min(acquire_timeout, MARTIN_TRADER_PRESTAGE_SECONDS),
            )
        if worker is None and datetime.now(timezone.utc) >= escalate_at:
            logger.error(
                "trade_series_engine: series_id=%s entry #%d could not secure a worker by T-minus %ds "
                "(escalation threshold) - still trying until the boundary",
                series["id"], entry_number, PRESTAGE_ESCALATION_SECONDS,
            )
        if worker is None:
            remaining = max(0.0, (scheduled_dt - datetime.now(timezone.utc)).total_seconds())
            if remaining > 0:
                worker = await default_coordinator.worker_pool.acquire_worker(timeout=remaining)
        if worker is None:
            logger.error(
                "trade_series_engine: series_id=%s entry #%d could not secure a worker before the "
                "boundary - falling back to standard (non-precision) execution",
                series["id"], entry_number,
            )
            await _execute_entry(default_coordinator, series, entry_number)
            return
        latency.mark("worker_acquired_at")
        await asyncio.to_thread(database.record_execution_latency, trade_id, latency.timestamps)

        staged = await pocket_executor.pre_stage_trade(
            trade_id, series["asset"], series["direction"], series["expiry"], series["stake"],
            worker, default_coordinator.worker_pool, default_coordinator.warmup_service,
            timeline=timeline, latency=latency,
        )
        await asyncio.to_thread(database.record_execution_latency, trade_id, latency.timestamps)
        if not isinstance(staged, pocket_executor.StagedTrade):
            await _handle_route_result(series, entry_number, staged)
            return

        # Precision final wait - monotonic clock, short ticks, never one
        # long blocking sleep and never a tight busy-spin.
        latency.mark("precision_wait_started_at")
        remaining = (scheduled_dt - datetime.now(timezone.utc)).total_seconds()
        if remaining > 0:
            deadline_monotonic = time.monotonic() + remaining
            while True:
                left = deadline_monotonic - time.monotonic()
                if left <= 0:
                    break
                await asyncio.sleep(min(left, PRECISION_WAIT_TICK_SECONDS))

        logger.info(
            "trade_series_engine: series_id=%s firing entry #%d (%s %s) [precision path]",
            series["id"], entry_number, series["asset"], series["direction"],
        )
        await asyncio.to_thread(database.advance_trade_series, series["id"], entry_number, "active")
        result = await pocket_executor.submit_staged_trade(staged, latency=latency)
        await asyncio.to_thread(database.record_execution_latency, trade_id, latency.timestamps)
        await _handle_route_result(series, entry_number, result)
    except Exception as e:
        # Terminal, not a retry - same semantics as _execute_entry's own
        # generic exception handler (entry_number, not entry_number - 1):
        # an exception escaping this far is unexpected/unmodeled, not one
        # of the known transient/policy outcomes _handle_route_result
        # already handles by retrying or blocking.
        logger.error(
            "trade_series_engine: series_id=%s entry #%d precision execution failed: %s",
            series["id"], entry_number, e,
        )
        await asyncio.to_thread(
            database.advance_trade_series, series["id"], entry_number, "error", result="error",
        )
    finally:
        if timeline_token is not None:
            TradeTimeline.deactivate(timeline_token)
        _precision_tasks_in_flight.discard(key)


def register(event_bus=None):
    """Subscribes this module's own trade.closed handler - called once at
    listener startup, same pattern as session_manager.register and
    event_stream.register."""
    (event_bus or get_event_bus()).subscribe("trade.closed", _on_trade_closed)


async def run_due_entries_loop(default_coordinator, channel_id=None):
    """Top-level supervisor loop (core/telegram_listener.py starts this
    as an asyncio.create_task, same as its other poll loops) - the only
    thing that ever actually fires an entry. A series sits in the
    database (list_pending_trade_series - status 'pending' or 'active')
    between calls; nothing about this loop's own in-memory state matters
    across a restart, since the DB is the sole source of truth for what's
    due next."""
    while True:
        try:
            await _fire_due_entries(default_coordinator, channel_id)
        except Exception as e:
            logger.error("trade_series_engine: due-entries poll failed: %s", e)
        await asyncio.sleep(DUE_ENTRY_POLL_INTERVAL_SECONDS)


async def _fire_due_entries(default_coordinator, channel_id):
    """Restart-safe by construction: every comparison here uses the
    series' own stored entry_times_utc - never recomputed from AXIM's
    local clock, created_at, or the published signal text. 2026-07-27
    product decision: entries are computed one at a time (Entry #1 at
    series creation; each later entry the moment the previous one's loss
    becomes known - see _apply_entry_outcome/database.schedule_next_entry),
    so entry_times_utc always has exactly current_entry_number + 1
    elements for a 'pending' series - the NEXT entry to fire is always
    its last element. A series missing this (only possible for
    pre-redesign legacy data) is skipped and logged rather than guessed
    at."""
    pending = await asyncio.to_thread(database.list_pending_trade_series, channel_id)
    now_utc = datetime.now(timezone.utc)
    for series in pending:
        if series["status"] != "pending":
            continue  # 'active' - an entry is already in flight, waiting on its outcome
        if series["channel_id"] is not None and await asyncio.to_thread(
            database.is_provider_execution_paused, series["channel_id"]
        ):
            # Per-provider safety hold (2026-07-27 Martin Trader timezone
            # incident) - defense in depth alongside the listener's own
            # check before create_series_from_signal: a series created
            # just before the hold was set must not fire either. Left
            # exactly as 'pending', not blocked/cancelled - this is a
            # temporary hold, not a verdict on the series itself.
            continue
        next_entry_number = series["current_entry_number"] + 1
        if next_entry_number > series["max_entries"]:
            continue  # defensive - _on_trade_closed should already have marked this exhausted

        if not series.get("entry_times_utc") or len(series["entry_times_utc"]) < next_entry_number:
            logger.error(
                "trade_series_engine: series_id=%s has no resolved entry_times_utc for entry #%d - "
                "refusing to guess a schedule; skipping until this is investigated",
                series["id"], next_entry_number,
            )
            continue

        scheduled_dt = datetime.fromisoformat(series["entry_times_utc"][next_entry_number - 1])
        prestage_at = scheduled_dt - timedelta(seconds=MARTIN_TRADER_PRESTAGE_SECONDS)
        if now_utc < prestage_at:
            continue  # not even time to pre-stage yet

        age_seconds = (now_utc - scheduled_dt).total_seconds()
        if age_seconds > STALE_ENTRY_THRESHOLD_SECONDS:
            logger.error(
                "trade_series_engine: series_id=%s entry #%d was due at %s, now %.0fs stale (likely a real "
                "outage - listener down, a deploy) - rejecting rather than firing a trade with no real "
                "relationship to current market conditions",
                series["id"], next_entry_number, scheduled_dt.isoformat(), age_seconds,
            )
            await asyncio.to_thread(
                database.advance_trade_series, series["id"], next_entry_number - 1, "blocked",
                result="blocked:stale_entry",
            )
            continue

        # 2026-07-27 precision-latency audit: rather than firing
        # immediately (which would only ever happen on the NEXT 3-second
        # poll tick after the boundary - exactly the coarse "scheduler
        # lateness" this audit measures and reduces), spawn a dedicated
        # task the moment the pre-stage window opens. That task owns its
        # own precise timing from here on - this coarse poll's only job
        # for this entry is to notice the window has opened and hand off
        # once, never twice (the in-flight set below).
        key = (series["id"], next_entry_number)
        if key in _precision_tasks_in_flight:
            continue
        _precision_tasks_in_flight.add(key)
        asyncio.create_task(_run_precision_entry(default_coordinator, series, next_entry_number, scheduled_dt, key))
