"""
"Flat 10% Session Compounding - One Chase" - temporary test strategy
(2026-08-02 multi-provider Demo validation).

Applies the same 2-executed-entries-per-signal cap to every enrolled
provider's signals: Entry 1 fires immediately; Entry 2 fires only if
Entry 1 resolves to an authoritative loss or draw (never on a win, never
on PENDING/UNKNOWN); Entry 2's own outcome always ends the series
(win or lose - never a 3rd entry). Unlike core/trade_series_engine.py's
Martin Trader mechanism, this is EVENT-triggered, not clock-scheduled:
none of the enrolled providers publish their own re-entry clock times,
so there is nothing to wait for - Entry 2 fires the moment Entry 1's
result is known.

Architecture note (2026-08-02, found while wiring this in): AXIM allows
only ONE active trading_sessions row per broker_account at a time
(core/database.py's start_trading_session), and all 4 enrolled providers
share the single real connected Demo account (broker_account_id=11) -
they cannot each have their own concurrently-active Fund-attached
session the way core/risk_engine.py's normal percent-of-bankroll sizing
needs. So this module does NOT use a real trading_session at all -
every entry routes with session_id=None, the exact same legacy shared-
connection path Martin Trader's OWN production engine (core/
trade_series_engine.py) has always safely used concurrently with
whatever Fund-attached session happens to be active elsewhere. Stake
sizing is computed here instead, from this module's own lightweight
per-channel "virtual fund" ledger (chase_test_sessions/chase_series.
net_profit_loss - core/database.py), and passed explicitly as
fixed_stake - never the Risk Engine's session-based percent sizing,
which is unreachable without a real session.

Session-boundary sizing (flat within a session, recomputed only between
sessions): the FIRST time a channel's signal arrives, get_or_start_
test_session opens a chase_test_sessions row: opening_virtual_fund =
$100 + this channel's own all-time realized P&L (0 the first time),
stake = 10% of that, fixed for every entry until an operator explicitly
calls end_chase_test_session for that channel (there is no automatic
mid-run recompounding - matches "do not recalculate the stake until
that provider's session is complete").

Honesty note on watch-only entries: a signal that publishes MORE than
2 entries (only Martin Trader's real format does this, via its
"Martingale:" block) has its 3rd/4th entries recorded watch-only
(scheduled time, asset, direction) but AXIM has no capability today to
determine what an un-placed trade's outcome would have been without a
live price-read mechanism this codebase does not yet have - hypothetical_
result is always left null, never fabricated.

Deliberately isolated from core/trade_series_engine.py: separate table
(chase_series, not trade_series), separate trade.closed subscriber,
separate id space - see core/database.py's chase_series migration
comment for why reusing trade_series would risk a real id collision
with Martin Trader's own production series.
"""
import sys
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CORE_DIR.parent
sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(PROJECT_ROOT / "execution"))

import database
import broker_account_manager
from logger import get_logger
from event_bus import get_event_bus

logger = get_logger("axim.lifecycle", filename="lifecycle.log")

MAX_ENTRIES = 2
STARTING_VIRTUAL_FUND = 100.0
STAKE_PERCENT = 10.0

# Temporary, clearly-named test configuration (2026-08-02) - which
# channels are currently enrolled in this test. NOT part of the
# permanent strategy catalog. To fully revert a provider to its normal
# assigned strategy, remove its entry here (and, for Martin Trader
# specifically, restore telegram_listener.py's normal
# MARTIN_TRADER_CHANNEL_ID routing instead of this one). fund_id is the
# real AXIM Fund created for this provider (docs/scratchpad/
# setup_test_funds.py) - kept only for display/reporting in the normal
# Funds UI; the actual $100 virtual-fund ledger this module sizes
# stakes from is computed independently, from chase_series' own real
# executed P&L (see chase_channel_lifetime_net_pl), not from this Fund's
# own balance (which requires a real session to update automatically -
# see the module docstring's architecture note).
TEST_CHANNELS = {
    163: {"label": "Martin Trader", "fund_id": 31},
    207: {"label": "NTrade Private Trading Group", "fund_id": 32},
    405: {"label": "VIP signals OTC Market (TM)", "fund_id": 33},
    401: {"label": "COPY TRADING | Private Chat", "fund_id": 34},
}


def get_or_start_test_session(channel_id):
    """Returns the currently open chase_test_sessions row for this
    channel, opening a new one (computed from this channel's own
    all-time real P&L) if none is open yet. This is the ONLY place a
    stake gets (re)computed - every entry within the open session uses
    this exact same session's stake, flat, per the test's own "do not
    recalculate after individual wins or losses" rule."""
    existing = database.get_open_chase_test_session(channel_id)
    if existing is not None:
        return existing
    lifetime_pl = database.chase_channel_lifetime_net_pl(channel_id)
    opening_virtual_fund = round(STARTING_VIRTUAL_FUND + lifetime_pl, 2)
    stake = round(opening_virtual_fund * (STAKE_PERCENT / 100.0), 2)
    session_id = database.start_chase_test_session(channel_id, opening_virtual_fund, stake)
    logger.info(
        "chase_series_engine: channel_id=%s opened test session_id=%s - opening_virtual_fund=$%.2f, stake=$%.2f",
        channel_id, session_id, opening_virtual_fund, stake,
    )
    return database.get_open_chase_test_session(channel_id)


async def create_and_fire(signal, channel_id, coordinator, source=None,
                           source_message_id=None, sent_at=None, timeline=None):
    """Entry point for a TEST_CHANNELS channel's signal - creates the
    chase_series row (with this channel's current open-session stake),
    records any watch-only overflow entries the provider's own signal
    published beyond MAX_ENTRIES, and fires Entry 1 immediately.
    Idempotent by (channel_id, source_message_id) - a redelivered
    Telegram event returns the same series_id rather than starting a
    second series for one published signal."""
    existing = database.get_chase_series_by_message(channel_id, source_message_id)
    if existing is not None:
        logger.info(
            "chase_series_engine: message_id=%s already has chase series_id=%s - not creating a duplicate",
            source_message_id, existing["id"],
        )
        return existing["id"]

    config = TEST_CHANNELS.get(channel_id, {})
    test_session = get_or_start_test_session(channel_id)
    series_id = database.create_chase_series(
        channel_id=channel_id, asset=signal["asset"], direction=signal["direction"],
        expiry=signal.get("expiry"), fund_id=config.get("fund_id"),
        test_session_id=test_session["id"], stake=test_session["stake"],
        max_entries=MAX_ENTRIES, source_message_id=source_message_id, raw_message=signal.get("raw_message"),
    )

    _record_overflow_watch_only(series_id, signal)

    await _fire_entry(coordinator, series_id, entry_number=1, source=source, sent_at=sent_at, timeline=timeline)
    return series_id


def _record_overflow_watch_only(series_id, signal):
    """Any entry beyond MAX_ENTRIES the provider's own signal published -
    only ever non-empty for a Martin-Trader-format signal (its
    "Martingale:" block). scheduled_entries is signal_parser.py's own
    field, already entry-numbered starting at 2 (entry #1 is the separate
    "Entry:" field) - anything with entry_number > MAX_ENTRIES is watch-only."""
    scheduled = signal.get("scheduled_entries") or []
    overflow = [e for e in scheduled if e.get("entry_number", 0) > MAX_ENTRIES]
    if not overflow:
        return
    database.record_chase_watch_only_entries(series_id, [
        {
            "entry_number": e["entry_number"], "asset": signal["asset"], "direction": signal["direction"],
            "scheduled_time": e.get("time"),
        }
        for e in overflow
    ])
    logger.info(
        "chase_series_engine: series_id=%s recorded %d watch-only entr%s (published beyond max_entries=%d)",
        series_id, len(overflow), "y" if len(overflow) == 1 else "ies", MAX_ENTRIES,
    )


async def _fire_entry(coordinator, series_id, entry_number, source=None, sent_at=None, timeline=None):
    """Fires one entry through the real, existing execution path -
    identical discipline to trade_series_engine._execute_entry: marks the
    series 'active' before calling route_signal (never after), so a
    duplicate trade.closed delivery can never fire the same entry twice.
    session_id=None (see module docstring for why) + fixed_stake=series
    stake (computed once, at series creation, from that channel's
    currently open test session) - both entries of one series always use
    the identical dollar amount, matching "Entry 2 uses the same flat
    amount as Entry 1"."""
    series = database.get_chase_series(series_id)
    if series is None:
        logger.error("chase_series_engine: series_id=%s vanished before entry #%d could fire", series_id, entry_number)
        return
    database.advance_chase_series(series_id, entry_number, "active")

    signal = {
        "asset": series["asset"], "direction": series["direction"], "expiry": series["expiry"],
        "raw_message": (
            f"[Chase series {series_id}, entry #{entry_number}/{series['max_entries']}] "
            f"{series['raw_message'] or ''}"
        ),
    }
    logger.info(
        "chase_series_engine: series_id=%s firing entry #%d (%s %s) stake=$%.2f",
        series_id, entry_number, series["asset"], series["direction"], series["stake"],
    )
    try:
        result = await broker_account_manager.route_signal(
            signal, coordinator, source=source or "chase_series_test",
            sent_at=sent_at, timeline=timeline,
            session_id=None, channel_id=series["channel_id"],
            fixed_stake=series["stake"],
        )
    except Exception as e:
        logger.error("chase_series_engine: series_id=%s entry #%d failed to route: %s", series_id, entry_number, e)
        database.advance_chase_series(series_id, entry_number, "error", result="error")
        return

    trade_id = result.get("trade_id")
    if trade_id is not None:
        database.advance_chase_series(series_id, entry_number, "active", entry_trade_id=trade_id)

    status = result.get("status")
    # Every status that means "no real trade was opened, so no
    # trade.closed event will ever arrive for this entry" - a risk-
    # manager rejection, a stale/observation-mode signal being ignored,
    # or the global PREVIEW_ONLY/test_mode short-circuits (defensive -
    # this session runs with real execution on, but a chase series must
    # never get stuck 'active' forever if those flags are ever toggled
    # mid-test). Anything else (the real "submitted, tracking outcome"
    # case) leaves the series 'active' to await _on_trade_closed.
    if status in ("rejected", "ignored", "preview", "test_mode"):
        logger.warning(
            "chase_series_engine: series_id=%s entry #%d did not open (status=%s): %s",
            series_id, entry_number, status, result.get("reason"),
        )
        database.advance_chase_series(
            series_id, entry_number, "error", result=f"{status}:{result.get('rule') or result.get('reason')}",
        )


async def _on_trade_closed(payload):
    """event_bus subscriber - reacts only to a trade this module itself
    fired (entry_1_trade_id or entry_2_trade_id match), a cheap no-op
    lookup miss for every other trade in the system."""
    trade_id = payload.get("trade_id")
    if trade_id is None:
        return
    series = database.get_chase_series_by_trade_id(trade_id)
    if series is None or series["status"] != "active":
        return

    result = payload.get("result")
    net_pl = payload.get("profit_loss") or 0.0
    entry_number = series["current_entry_number"]

    if result not in ("win", "loss", "draw"):
        # Same explicit "only a genuinely terminal result ever advances
        # anything" guard trade_series_engine.py holds itself to -
        # reachable in practice only via a malformed trade.closed payload.
        logger.warning(
            "chase_series_engine: series_id=%s entry #%d result=%r is not terminal - not advancing",
            series["id"], entry_number, result,
        )
        return

    if result == "win":
        database.advance_chase_series(series["id"], entry_number, "won", result="win", net_profit_loss=net_pl)
        logger.info("chase_series_engine: series_id=%s entry #%d WON - series closed", series["id"], entry_number)
        return

    # loss or draw - "did not win" either way, same treatment
    # trade_series_engine.py gives a draw (never a loss, never a win).
    if entry_number >= series["max_entries"]:
        database.advance_chase_series(
            series["id"], entry_number, "lost_exhausted", result="loss", net_profit_loss=net_pl,
        )
        logger.info(
            "chase_series_engine: series_id=%s entry #%d was the last entry and did not win - series exhausted",
            series["id"], entry_number,
        )
        return

    # Entry 1 lost/drew and a second entry is still available - fire it
    # immediately (event-triggered, no clock schedule to wait for).
    logger.info(
        "chase_series_engine: series_id=%s entry #%d did not win - firing entry #%d",
        series["id"], entry_number, entry_number + 1,
    )
    coordinator = _default_coordinator_ref.get("coordinator")
    if coordinator is None:
        logger.error(
            "chase_series_engine: series_id=%s cannot fire entry #%d - no coordinator registered "
            "(register_coordinator was never called)", series["id"], entry_number + 1,
        )
        database.advance_chase_series(series["id"], entry_number, "error", result="no_coordinator_registered")
        return
    await _fire_entry(coordinator, series["id"], entry_number + 1)


# The event subscriber (trade.closed) fires with only a payload - it has
# no access to telegram_listener.py's own `coordinator` variable, unlike
# create_and_fire (called directly from the message handler, which DOES
# have it in scope). register_coordinator() is called once at listener
# startup, mirroring how trade_series_engine.run_due_entries_loop
# receives its own default_coordinator once and holds it for the life of
# the process - the same coordinator instance, not a second one.
_default_coordinator_ref = {"coordinator": None}


def register_coordinator(coordinator):
    _default_coordinator_ref["coordinator"] = coordinator


def register(event_bus=None):
    """Subscribes this module's own trade.closed handler - called once at
    listener startup, same pattern as trade_series_engine.register."""
    (event_bus or get_event_bus()).subscribe("trade.closed", _on_trade_closed)
