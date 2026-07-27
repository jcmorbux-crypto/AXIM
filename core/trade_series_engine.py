"""
Martin Trader scheduled-entry execution (docs/opt_signals_gap_queue.md item 2 -
the "product decision" that item deliberately scoped but left unbuilt: "should
AXIM delay execution until the stated time, reject signals whose entry time
has already passed, or something else?").

Martin Trader (channel 163) publishes one signal that describes up to 4
possible entries at fixed future clock times, e.g.:

    SIGNAL
    AUD/JPY OTC
    Entry: 09:00
    Direction: SELL
    Martingale:
    1 09:05
    2 09:10
    3 09:15

parsers/signal_parser.py already extracts this into entry_time="09:00" and
scheduled_entries=[{"entry_number":2,"time":"09:05"}, ...]. This module is
the actual consumer: treat the whole signal as ONE managed series rather
than 4 independent trades - execute Entry #1 at its published time; if it
wins, stop; if it loses, wait for the next published time and try again;
stop after the first win or after every published entry has been tried.

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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CORE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CORE_DIR))

import database
import broker_account_manager
from trade_lifecycle import TradeStatus
from logger import get_logger
from event_bus import get_event_bus

logger = get_logger("axim.lifecycle", filename="lifecycle.log")

DUE_ENTRY_POLL_INTERVAL_SECONDS = 3
MAX_ENTRIES = 4

# The evidence-based resolution method name persisted on every series
# (trade_series.schedule_resolution_method) - 2026-07-27 Martin Trader
# timezone incident. Exists so a FUTURE change to this algorithm is
# distinguishable from historical rows on disk, rather than silently
# reinterpreting old data under new rules.
SCHEDULE_RESOLUTION_METHOD = "provider_timezone_v1"

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


class ScheduleResolutionError(Exception):
    """Raised when a provider's published entry time cannot be safely
    resolved to a real, unambiguous UTC datetime. 2026-07-27 Martin
    Trader timezone incident: the previous resolver silently combined
    the published HH:MM with AXIM's own local system clock (Pacific),
    with no timezone conversion at all, despite the channel explicitly
    declaring UTC+3 - verified live to schedule Entry 1 roughly 9 hours
    after the channel had already reported the same signal's outcome.
    The corrected resolver below never guesses when it lacks what it
    needs (a real provider timezone, a real tz-aware reference
    timestamp) - it raises instead, and the caller (core/
    telegram_listener.py's existing exception boundary) logs and tracks
    it as a real FAILED pipeline event rather than falling through to
    any default."""


def _resolve_scheduled_datetime_utc(hhmm, provider_timezone_name, telegram_message_date_utc):
    """The verified, evidence-based replacement for the old naive
    same-day/AXIM-local-clock resolver. The 2026-07-27 investigation
    cross-validated, across three independent signal/result pairs and
    four consecutive real signal-to-signal deltas (all exact to the
    second), that Martin Trader's published entry times only resolve
    correctly against the SIGNAL MESSAGE'S OWN real Telegram send time,
    converted into the provider's declared timezone - never AXIM's own
    local system clock.

    Rollover rule (evidence-based, not a blind "roll every past time
    forward" guess): every verified real example showed the signal sent
    a few minutes BEFORE its own published Entry 1 time, in the
    provider's timezone. If combining the published HH:MM with the
    message's own calendar date (in that timezone) would put the entry
    BEFORE the message's own send time, the only pattern actually
    observed that explains this is a midnight boundary - the entry is
    for the next calendar day. There is no observed case of a Martin
    Trader signal arriving after its own Entry 1 time, so this is
    applied unconditionally when it's needed, not selectively guessed."""
    if not provider_timezone_name:
        raise ScheduleResolutionError(
            "provider_timezone is required to resolve a scheduled entry - refusing to guess without one"
        )
    try:
        tz = ZoneInfo(provider_timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ScheduleResolutionError(f"invalid provider_timezone {provider_timezone_name!r}: {e}") from e

    if telegram_message_date_utc is None or telegram_message_date_utc.tzinfo is None:
        raise ScheduleResolutionError(
            "telegram_message_date_utc must be a timezone-aware datetime - refusing to schedule from a naive one"
        )

    message_local = telegram_message_date_utc.astimezone(tz)
    hour, minute = (int(part) for part in hhmm.split(":"))
    candidate_local = message_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate_local < message_local:
        candidate_local += timedelta(days=1)
    return candidate_local.astimezone(timezone.utc)


def _resolve_entry_schedule_utc(entry_times, provider_timezone_name, telegram_message_date_utc):
    """Resolves Entry 1 against the message's own send time (the only
    entry that genuinely needs timezone conversion/rollover), then
    derives every later Martingale re-entry as a fixed offset from
    Entry 1's own resolved UTC moment - each later published clock time
    is always Entry 1's own time plus some number of minutes (verified
    identical, real spacing in every example examined), so this never
    re-runs rollover logic per entry; a later entry is never
    independently ambiguous about which calendar day it falls on once
    Entry 1 itself is correctly anchored."""
    if not entry_times:
        return []
    first_utc = _resolve_scheduled_datetime_utc(entry_times[0], provider_timezone_name, telegram_message_date_utc)
    hour0, minute0 = (int(part) for part in entry_times[0].split(":"))
    base_minutes = hour0 * 60 + minute0
    resolved = [first_utc]
    for t in entry_times[1:]:
        hour, minute = (int(part) for part in t.split(":"))
        delta_minutes = (hour * 60 + minute) - base_minutes
        if delta_minutes < 0:
            delta_minutes += 24 * 60
        resolved.append(first_utc + timedelta(minutes=delta_minutes))
    return resolved


def build_entry_schedule(signal):
    """From a parsed signal (parsers/signal_parser.py's output) build the
    ordered list of every entry's published clock-time string, capped at
    MAX_ENTRIES: Entry #1's own "Entry:" time first, then each
    "Martingale:" re-entry time in order. Returns None if there's no
    entry_time at all - a signal with no stated entry time was never
    something this engine can schedule (see parse_signal's own docstring:
    "every existing caller ignores this key" until now)."""
    entry_time = signal.get("entry_time")
    if not entry_time:
        return None
    times = [entry_time]
    for scheduled in signal.get("scheduled_entries", []):
        if len(times) >= MAX_ENTRIES:
            break
        times.append(scheduled["time"])
    return times[:MAX_ENTRIES]


async def create_series_from_signal(signal, channel_id, stake, provider_timezone, telegram_message_date_utc,
                                     fund_id=None, broker_account_id=None, session_id=None,
                                     source_message_id=None):
    """Entry point for a channel gated into scheduled-entry mode (today,
    only channel 163). Persists the series and returns its id - does NOT
    execute anything itself. There is deliberately no "execute Entry #1
    immediately" special case: run_due_entries_loop's poll tick is what
    fires every entry, including the first, once its own scheduled time
    arrives, keeping the state machine uniform (a signal whose Entry #1
    time is already due fires on the very next poll tick, same as any
    later re-entry becoming due).

    Idempotent by (channel_id, source_message_id): a redelivered Telegram
    event (Telethon reconnect, at-least-once delivery) for a message this
    channel already turned into a series returns that SAME series_id
    rather than starting a second, independent one for what is really one
    published signal.

    provider_timezone/telegram_message_date_utc are required, not
    optional (2026-07-27 Martin Trader timezone incident) - the whole
    point of the fix is that a schedule is never computed without a
    real provider timezone and a real tz-aware reference timestamp;
    _resolve_entry_schedule_utc raises ScheduleResolutionError rather
    than falling back to a guess, and this function deliberately lets
    that propagate to the caller's own existing exception boundary
    (core/telegram_listener.py) instead of swallowing it here."""
    existing = await asyncio.to_thread(database.get_trade_series_by_message, channel_id, source_message_id)
    if existing is not None:
        logger.info(
            "trade_series_engine: message_id=%s already has series_id=%s - not creating a duplicate",
            source_message_id, existing["id"],
        )
        return existing["id"]

    entry_times = build_entry_schedule(signal)
    if not entry_times:
        logger.warning(
            "trade_series_engine: signal for %s has no entry_time - not scheduled as a series",
            signal.get("asset"),
        )
        return None

    entry_times_utc = _resolve_entry_schedule_utc(entry_times, provider_timezone, telegram_message_date_utc)

    series_id = await asyncio.to_thread(
        database.create_trade_series,
        channel_id=channel_id, asset=signal["asset"], direction=signal["direction"],
        expiry=signal["expiry"], stake=stake, entry_times=entry_times,
        source_message_id=source_message_id, raw_message=signal.get("raw_message"),
        fund_id=fund_id, broker_account_id=broker_account_id, session_id=session_id,
        max_entries=len(entry_times),
        published_entry_time=entry_times[0],
        provider_timezone=provider_timezone,
        entry_times_utc=[dt.isoformat() for dt in entry_times_utc],
        telegram_message_date_utc=telegram_message_date_utc.isoformat(),
        schedule_resolution_method=SCHEDULE_RESOLUTION_METHOD,
    )
    logger.info(
        "trade_series_engine: series_id=%s created for %s %s, %d scheduled entr%s (%s) resolved_utc=(%s)",
        series_id, signal["asset"], signal["direction"], len(entry_times),
        "y" if len(entry_times) == 1 else "ies", ", ".join(entry_times),
        ", ".join(dt.isoformat() for dt in entry_times_utc),
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
    entry stops the series (a win) or schedules the next published entry
    (a loss/draw, entries remaining) or exhausts it (a loss/draw, none
    remaining)."""
    if result == "win":
        logger.info(
            "trade_series_engine: series_id=%s entry #%d WON - series closed", series["id"], entry_number,
        )
        await asyncio.to_thread(
            database.advance_trade_series, series["id"], entry_number, "won", result="win", net_profit_loss=net_pl,
        )
        return

    # loss or draw - draw is treated as "did not win", same as a loss, for
    # the purpose of "was the objective (a winning entry) met" - Martin
    # Trader's own re-entry schedule doesn't distinguish a draw from a
    # loss either (both simply lead to the next published entry).
    if entry_number >= series["max_entries"]:
        logger.info(
            "trade_series_engine: series_id=%s entry #%d was the last scheduled entry and did not win - "
            "series exhausted", series["id"], entry_number,
        )
        await asyncio.to_thread(
            database.advance_trade_series, series["id"], entry_number, "lost_exhausted",
            result="loss", net_profit_loss=net_pl,
        )
    else:
        logger.info(
            "trade_series_engine: series_id=%s entry #%d lost - waiting for entry #%d",
            series["id"], entry_number, entry_number + 1,
        )
        await asyncio.to_thread(database.advance_trade_series, series["id"], entry_number, "pending")


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


async def reconcile_stuck_series():
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

    A trade that WAS actually clicked before the crash is NOT this
    function's concern - recovery.resume_pending_trades already
    re-attaches real track_outcome tracking for it (see that module),
    which naturally publishes a genuine trade.closed once it resolves;
    _on_trade_closed (registered before this ever runs - see
    telegram_listener._startup()'s ordering) picks that up exactly like
    any other outcome, live or resumed. This function ONLY handles the
    "never actually became a real trade" case, treating it as neither a
    win nor a loss (no attempt was genuinely made) - the same entry
    number is retried, exactly like a transient all_workers_busy
    rejection would be, rather than being counted as a loss for an
    attempt that never happened."""
    for series in await asyncio.to_thread(database.list_pending_trade_series):
        if series["status"] != "active":
            continue
        entry = await asyncio.to_thread(database.get_series_entry, series["id"], series["current_entry_number"])
        if entry is None:
            continue

        if entry.get("result") == "error:abandoned_on_restart":
            logger.warning(
                "trade_series_engine: series_id=%s entry #%d never actually executed before a restart "
                "(no real position to reconcile) - retrying the same entry",
                series["id"], series["current_entry_number"],
            )
            await asyncio.to_thread(
                database.advance_trade_series, series["id"], series["current_entry_number"] - 1, "pending",
            )
            continue

        # A genuinely resolved outcome (win/loss/draw) already recorded on
        # this entry's own row but never applied to the series - possible
        # if the process crashed in the narrow window between
        # pocket_executor.track_outcome writing the result and this
        # module's own trade.closed subscriber reacting to it. Applying it
        # here is the same logic _on_trade_closed uses, just driven by the
        # DB's own already-written result instead of a live event.
        if entry.get("execution_status") in _RESOLVED_STATUS_TO_RESULT:
            result = _RESOLVED_STATUS_TO_RESULT[entry["execution_status"]]
            logger.warning(
                "trade_series_engine: series_id=%s entry #%d resolved to %r before this process could react to "
                "it - reconciling now",
                series["id"], series["current_entry_number"], result,
            )
            await _apply_entry_outcome(series, series["current_entry_number"], result, entry.get("profit_loss") or 0.0)


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
    series' own stored, already-resolved entry_times_utc (computed once,
    at creation time, from the real Telegram message timestamp and the
    provider's declared timezone - see create_series_from_signal) rather
    than recomputing anything from AXIM's local clock or created_at.
    2026-07-27 Martin Trader timezone incident: the previous version of
    this function re-derived each entry's scheduled moment at FIRE time
    via a naive same-day/AXIM-local-clock calculation - restart-safe in
    the sense that it always recomputed the same (wrong) answer, but the
    answer itself was never correct. A series with no resolved UTC
    schedule at all (only possible for pre-fix legacy data, since every
    real series now always gets one) is skipped and logged rather than
    guessed at."""
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
        if now_utc < scheduled_dt:
            continue  # not due yet

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

        await _execute_entry(default_coordinator, series, next_entry_number)
