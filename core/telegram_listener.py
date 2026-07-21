from telethon import TelegramClient, events
import asyncio
import os
import subprocess
import sys
import time
from dotenv import load_dotenv

# Windows consoles default to cp1252, which can't encode most emoji/non-
# Latin characters that arrive in real Telegram messages - forcing UTF-8
# here means print() never crashes the process over a character the
# terminal can't represent (previously fixed ad hoc per-script; this is
# the one place that matters since this is the live entry point).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, "parsers")
sys.path.insert(0, "execution")
sys.path.insert(0, "core")
sys.path.insert(0, "config")

from signal_parser import parse_signal, apply_signal_rules, apply_expiry_fallback, parse_asset_announcement
from signal_lifecycle import SignalLifecycleState
from trade_coordinator import TradeCoordinator
from browser_warmup import BrowserWarmupService
from browser_worker_pool import BrowserWorkerPool
from timeline import TradeTimeline
import pocket_dom
from settings import WATCH_CHANNELS, MAX_CONCURRENT_WORKERS, ACCOUNT
from logger import get_logger
from event_bus import get_event_bus
import recovery
import database
import session_manager
import broker_account_manager
import event_stream
import telegram_bot_trigger
import signal_assembler
import provider_profile

logger = get_logger("axim.lifecycle", filename="lifecycle.log")

# Captured once at process (module) load, not per _startup() call - a
# _startup() re-run (internal recovery) doesn't reset the OS process
# itself, so this should reflect the real process lifetime for
# scripts/soak_snapshot.py's uptime column.
_PROCESS_START_MONOTONIC = time.monotonic()

load_dotenv()

api_id = int(os.getenv("TELEGRAM_API_ID") or os.getenv("API_ID"))
api_hash = os.getenv("TELEGRAM_API_HASH") or os.getenv("API_HASH")
phone = os.getenv("TELEGRAM_PHONE") or os.getenv("PHONE")

# Originated as a one-off WATCH_CHANNELS/filter debugging aid; kept as a
# permanent, opt-in observability feature - logs every incoming message's
# routing decision BEFORE any filtering, execution-unrelated. Does not touch
# trade_coordinator, pocket_executor, risk_manager, or pocket_dom. Off by
# default (set TELEGRAM_DEBUG_LOG=true to enable).
TELEGRAM_DEBUG_LOG = os.getenv("TELEGRAM_DEBUG_LOG", "false").strip().lower() == "true"

client = TelegramClient("axim_session", api_id, api_hash)

# Rebuilt fresh on every (re)start by _startup() - not constructed once at
# module load, since a process-level restart needs a clean browser/worker
# stack rather than reusing objects that may be in a broken state.
warmup_service = None
worker_pool = None
coordinator = None

print("AXIM Telegram Listener Starting...")

database.initialize_database()
# One-time migration: populate ui_channels from the static .env
# WATCH_CHANNELS the very first time this runs against a DB that's never
# seen the UI channel manager - a no-op on every subsequent start (the
# function itself checks the table isn't empty first). From here on,
# ui_channels (editable via the API/web UI without touching .env or
# restarting) is the real source of truth; WATCH_CHANNELS still works too
# (see channel_allowed below) so nothing regresses for anyone not using
# the UI yet.
database.seed_channels_from_env(WATCH_CHANNELS)

if not WATCH_CHANNELS and not database.get_enabled_channels():
    print(
        "\n*** WARNING: no channel is allow-listed (.env WATCH_CHANNELS is\n"
        "empty and no channel is enabled in the UI channel manager). ***\n"
        "AXIM will NOT process signals from ANY chat until you enable at\n"
        "least one. This is intentional: previously this listener processed\n"
        "every message in every chat the account could see - fail-closed by\n"
        "default is safer than that.\n"
    )
else:
    print(f"Watching for chat titles containing: {WATCH_CHANNELS} (plus anything enabled in the UI channel manager)")


def channel_allowed(chat_title, chat_username=None, chat_id=None):
    """A chat is allowed if it matches EITHER the static .env WATCH_CHANNELS
    list OR a currently-enabled row in the UI-managed ui_channels table.

    For the WATCH_CHANNELS list (informal short names typed into .env, with
    no real chat_id to match against): a case-insensitive substring of the
    chat's display title/first_name (works for anything, but display names
    can be renamed or duplicated - e.g. this account has both "Pocket
    Option Quant Algorithm" and a "PO Quant Algo" contact), or an exact
    case-insensitive match against the chat's @username.

    For real synced ui_channels rows, prefer an exact chat_id match over
    the fuzzy title substring whenever both sides have one - a real,
    live bug otherwise: "OTC Pro Trading Robot" was explicitly disabled by
    the operator, but its title contains "Pro Trading Robot" (a
    DIFFERENT, separately-enabled channel) as a literal substring, so the
    old title-only check incorrectly treated the disabled channel as
    allowed. Falls back to the fuzzy title/username check only for rows
    with no chat_id yet (freshly seeded from WATCH_CHANNELS, not yet
    synced) or when the caller doesn't have a chat_id to compare (the
    TELEGRAM_DEBUG_LOG preview path). Fail-closed if neither source has
    anything configured."""
    title_lower = (chat_title or "").lower()
    username_lower = (chat_username or "").lower()

    if any(
        channel.lower() in title_lower or channel.lower() == username_lower
        for channel in WATCH_CHANNELS
    ):
        return True

    for row in database.get_enabled_channels():
        if chat_id is not None and row["chat_id"] is not None:
            try:
                if int(row["chat_id"]) == int(chat_id):
                    return True
            except (TypeError, ValueError):
                pass
            continue
        entry_username = (row["username"] or "").lower()
        entry_title = (row["title"] or "").lower()
        if (entry_username and entry_username == username_lower) or (
            entry_title and entry_title in title_lower
        ):
            return True

    return False


# Legacy single-slot fallback for a chat AXIM has NO ui_channels record
# for at all (an informal .env WATCH_CHANNELS title match that's never
# been synced into the channel manager - core/signal_assembler.py below
# is the real, general mechanism for every properly-registered channel,
# which is every channel actually used in practice). Kept only for this
# narrow legacy path since it needs no channel_id to key its per-channel
# state. Some real providers (confirmed live: OTC Pro Trading Robot)
# split one trade across two separate Telegram messages - a standalone
# "Preparing trading asset X" announcement, then a later entry message
# with direction/expiry but no asset repeated at all. Keyed by chat_id so
# concurrently-active channels never cross-contaminate each other's
# pending asset. In-memory only (not persisted) - a process restart
# losing an in-flight announcement just means that one entry message is
# treated as unparseable, same fail-closed behavior as if the asset was
# never announced, not a corrupted trade.
_carried_assets_by_channel = {}

# Real gap observed live between a "Preparing trading asset" message and
# its paired entry message: consistently ~60 seconds. 5 minutes is a
# generous safety margin against Telegram delivery jitter while still
# refusing to combine an entry with a stale, unrelated earlier
# announcement from the same channel.
_CARRIED_ASSET_STALE_AFTER_SECONDS = 300


def _remember_asset_announcement(chat_id, asset):
    _carried_assets_by_channel[chat_id] = (asset, time.monotonic())


def _get_carried_asset(chat_id):
    entry = _carried_assets_by_channel.get(chat_id)
    if entry is None:
        return None
    asset, seen_at = entry
    if time.monotonic() - seen_at > _CARRIED_ASSET_STALE_AFTER_SECONDS:
        return None
    return asset


# Universal Signal Intelligence Engine (2026-07-18 directive, promoted
# from shadow-only to the real per-channel decision path 2026-07-20 per
# the live-trading refocus mandate) - core/signal_assembler.py is now
# what actually decides whether a message completes a tradeable signal
# for any channel AXIM has a ui_channels record for (the legacy
# _carried_assets_by_channel mechanism above only still runs for a chat
# with no record at all). Real, concrete correctness fix over the old
# mechanism it replaces: the old single-slot-per-channel carried-asset
# dict silently OVERWROTE an earlier pending announcement if a second
# one arrived before the first resolved (two different assets in flight
# at once, which the mandate explicitly requires never merging) -
# signal_assembler tracks one pending sequence PER ASSET, plus
# Telegram-reply correlation and a configurable per-provider timeout.
_shadow_assembler = signal_assembler.SignalAssembler()


def _observe_message(channel_row, message_text, message_id, reply_to_message_id=None):
    """The ONE call into signal_assembler.process_message() per message
    for a channel AXIM has a record for - serves both the real trade
    decision (the returned dict is what the caller routes to
    broker_account_manager.route_signal) AND the Universal Signal
    Intelligence Engine's observation/graduation bookkeeping
    (core/provider_profile.py), from the same call. These must never be
    two separate calls: signal_assembler is stateful (a pending sequence
    is consumed/deleted once completed), so calling it twice for the
    same message would have a second, redundant call fail to find a
    pending sequence the first call already resolved - silently
    misclassifying a real, completable multi-message signal as
    unparseable and dropping the trade.

    Fails closed: returns None (no signal, no trade) if anything here
    raises, logged - a bug in observation bookkeeping must never
    silently execute an unintended trade, and skipping a real signal on
    an internal error is the safe default, consistent with this
    module's fail-closed philosophy elsewhere (see channel_allowed's own
    docstring)."""
    if channel_row is None:
        return None
    try:
        profile = database.get_or_create_provider_profile(channel_row["id"])
        timeout = profile["assembly_timeout_seconds"] or signal_assembler.DEFAULT_ASSEMBLY_TIMEOUT_SECONDS
        result = _shadow_assembler.process_message(
            channel_row["id"], message_id, message_text,
            reply_to_message_id=reply_to_message_id, assembly_timeout_seconds=timeout,
        )
        for _ in result.get("expired_assets", []):
            provider_profile.record_observed_signal(profile["id"], parsed_successfully=False)
        if result["action"] == "signal_ready":
            provider_profile.record_observed_signal(profile["id"], parsed_successfully=True)
        return result
    except Exception as e:
        logger.error("telegram_listener: signal assembly failed for channel_id=%s: %s", channel_row.get("id"), e)
        return None


def _track_pipeline_event(chat_id, message_id, channel_id, state, detail=None):
    """Live Signal Pipeline instrumentation (2026-07-19 v2 mandate) -
    pure observability, called alongside the handler's real decisions,
    never instead of them. Same "exception never propagates" discipline
    as _observe_message above (database.record_pipeline_event already
    fails silent on its own, this is belt-and-suspenders)."""
    try:
        database.record_pipeline_event(chat_id, message_id, state, channel_id=channel_id, detail=detail)
    except Exception as e:
        logger.error("telegram_listener: pipeline event tracking failed: %s", e)


def _debug_safe(text):
    """Belt-and-suspenders alongside the UTF-8 stdout reconfigure above -
    guards print() calls if stdout is ever swapped for a stream that
    doesn't support .reconfigure, so a stray unencodable character never
    crashes the process. Never touches what actually gets parsed or
    executed."""
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    return (text or "").encode(encoding, "replace").decode(encoding, "replace")


@client.on(events.NewMessage)
async def handler(event):
    chat = await event.get_chat()
    chat_title = getattr(chat, "title", "") or getattr(chat, "first_name", "") or ""
    chat_username = getattr(chat, "username", "") or ""

    # Captured unconditionally - even for channels not yet enabled/allowed -
    # so the Telegram Sources / Signal Inspector pages can show "last
    # message received" and a recent-messages preview for a channel BEFORE
    # the operator decides whether to enable it, not just after.
    try:
        database.record_channel_message(
            chat_id=event.chat_id, username=chat_username, title=chat_title, message_text=event.raw_text,
        )
    except Exception as e:
        logger.error("telegram_listener: record_channel_message failed: %s", e)

    if TELEGRAM_DEBUG_LOG:
        debug_sender = await event.get_sender()
        filter_decision = channel_allowed(chat_title, chat_username, event.chat_id)
        print("\n[TELEGRAM_DEBUG] ------------------------------")
        print(f"[TELEGRAM_DEBUG] chat_title    : {_debug_safe(chat_title)}")
        print(f"[TELEGRAM_DEBUG] chat_username : {_debug_safe(chat_username)}")
        print(f"[TELEGRAM_DEBUG] sender_id     : {getattr(debug_sender, 'id', None)}")
        print(f"[TELEGRAM_DEBUG] chat_id       : {event.chat_id}")
        print(f"[TELEGRAM_DEBUG] raw_text[:200]: {_debug_safe(event.raw_text)[:200]}")
        print(f"[TELEGRAM_DEBUG] filter_decision: {'ALLOWED' if filter_decision else 'BLOCKED'}")
        if not filter_decision:
            print("[TELEGRAM_DEBUG] parser_decision: (not evaluated - blocked by filter)")
        else:
            debug_signal = parse_signal(event.raw_text)
            print(f"[TELEGRAM_DEBUG] parser_decision: {'PARSED -> ' + repr(debug_signal) if debug_signal else 'REJECTED (returned None)'}")

    # Resolved once, used for both the session-scope check below and the
    # per-channel signal rules further down - same match precedence as
    # record_channel_signal_seen (chat_id, then username, then title
    # substring), so a channel seeded from .env without a real chat_id yet
    # still matches.
    channel_row = database.find_channel(chat_id=event.chat_id, username=chat_username, title=chat_title)

    # Live Signal Pipeline (2026-07-19 v2 mandate) - tracked only for
    # messages in a chat AXIM already has a ui_channels record for
    # (regardless of whether it's currently enabled); an entirely
    # unconfigured chat isn't a "signal" in the product's sense at all,
    # so this deliberately doesn't track every message in every chat the
    # account can see. See core/database.py's signal_pipeline_events
    # table docstring for why this can't just extend the `signals` table.
    if channel_row is not None:
        _track_pipeline_event(event.chat_id, event.id, channel_row["id"], SignalLifecycleState.RECEIVED)

    # A Bot Command Channel's messages are only ever valid as a direct
    # reply to a request core/telegram_bot_trigger.py made - never as an
    # unprompted passive signal. That module awaits the bot's reply
    # itself via a conversation-scoped listener (separate from this
    # global handler); passively processing the same message here too
    # would double-handle every interactive reply as if it were also a
    # pushed signal.
    if channel_row is not None and channel_row.get("source_type") == "bot_command":
        _track_pipeline_event(event.chat_id, event.id, channel_row["id"], SignalLifecycleState.SKIPPED,
                               detail="bot_command_channel")
        return

    # When a Trading Session covers this channel, IT is the authoritative
    # allow-list for its duration - only its own channels execute,
    # whether or not they're separately "enabled" in the channel manager.
    # Resolved per-channel (not "the" newest active session app-wide) so
    # different Funds' concurrently active sessions each route correctly
    # - see docs/AXIM_SESSION_ARCHITECTURE.md and core/database.py's
    # get_active_trading_session_for_channel.
    active_session = session_manager.get_active_session_for_channel(channel_row)
    if active_session:
        if active_session["fund_id"] is not None:
            fund = database.get_fund(active_session["fund_id"])
            if fund is not None and fund["status"] == "paused":
                print(f"\n[FUND PAUSED] Signal from {_debug_safe(chat_title)!r} skipped - "
                      f"Fund {fund['name']!r} is paused.")
                if channel_row is not None:
                    _track_pipeline_event(event.chat_id, event.id, channel_row["id"], SignalLifecycleState.SKIPPED,
                                           detail="fund_paused")
                return
    # No session covers this channel specifically - a DIFFERENT channel
    # having its own active session (a different Fund) must not block
    # this one, so fall back to the legacy global WATCH_CHANNELS/
    # enabled-channels check exactly as if no session existed anywhere.
    elif not channel_allowed(chat_title, chat_username, event.chat_id):
        if channel_row is not None:
            _track_pipeline_event(event.chat_id, event.id, channel_row["id"], SignalLifecycleState.SKIPPED,
                                   detail="channel_not_watched")
        return

    database.record_channel_signal_seen(chat_id=event.chat_id, username=chat_username, title=chat_title)

    control_state = database.get_control_state()
    if control_state["emergency_stop"] or control_state["paused"]:
        reason = "emergency_stop" if control_state["emergency_stop"] else "paused"
        print(f"\n[UI CONTROL] Signal from {_debug_safe(chat_title)!r} skipped - AXIM is {reason} via the UI.")
        if channel_row is not None:
            _track_pipeline_event(event.chat_id, event.id, channel_row["id"], SignalLifecycleState.SKIPPED,
                                   detail=f"ui_control_{reason}")
        return

    timeline = TradeTimeline()
    timeline.mark("signal_received")

    sender = await event.get_sender()

    print("\n========================")
    print("New Telegram Message")
    print("========================")
    print(f"Chat   : {_debug_safe(chat_title)}")
    print(f"Sender : {sender.id}")
    print(f"Message:\n{_debug_safe(event.raw_text)}")

    # Channel-specific find/replace rules (Signal Inspector's "Save Rule
    # for This Channel") run BEFORE the real parser, never as a
    # replacement for it.
    message_text = event.raw_text
    if channel_row:
        rules = database.get_enabled_rules_for_channel(channel_row["id"])
        if rules:
            message_text = apply_signal_rules(message_text, rules)

    reply_to_message_id = getattr(getattr(event.message, "reply_to", None), "reply_to_msg_id", None)

    if channel_row is not None:
        # The real, general multi-message-aware decision path for any
        # properly-registered channel - see _observe_message's own
        # docstring for why this must be the ONE call into the assembler.
        assembly_result = _observe_message(channel_row, message_text, event.id, reply_to_message_id=reply_to_message_id)
        timeline.mark("signal_parsed")

        if assembly_result is None or assembly_result["action"] == "no_signal":
            _track_pipeline_event(event.chat_id, event.id, channel_row["id"], SignalLifecycleState.FAILED,
                                   detail="parse_failed")
            return

        if assembly_result["action"] == "announced":
            print(f"[ASSET ANNOUNCED] {_debug_safe(chat_title)!r} announced {assembly_result['asset']!r} - "
                  f"carrying it forward for this channel's next entry message.")
            _track_pipeline_event(event.chat_id, event.id, channel_row["id"], SignalLifecycleState.SKIPPED,
                                   detail="asset_announcement_only")
            return

        # action == "signal_ready"
        signal = {
            "asset": assembly_result["asset"], "direction": assembly_result["direction"],
            "expiry": assembly_result.get("expiry") or "Unknown", "raw_message": assembly_result["raw_message"],
        }
        _track_pipeline_event(event.chat_id, event.id, channel_row["id"], SignalLifecycleState.PARSED)
    else:
        # Legacy fallback for a chat with no ui_channels record at all -
        # see _carried_assets_by_channel's own comment.
        announced_asset = parse_asset_announcement(message_text)
        if announced_asset:
            _remember_asset_announcement(event.chat_id, announced_asset)
            print(f"[ASSET ANNOUNCED] {_debug_safe(chat_title)!r} announced {announced_asset!r} - "
                  f"carrying it forward for this channel's next entry message.")
            return

        signal = parse_signal(message_text, carried_asset=_get_carried_asset(event.chat_id))
        timeline.mark("signal_parsed")
        if signal is None:
            return

    if signal["expiry"] == "Unknown" and channel_row and channel_row.get("default_expiry"):
        print(f"[EXPIRY FALLBACK] {_debug_safe(chat_title)!r} sent no expiry - using its configured "
              f"default_expiry={channel_row['default_expiry']!r} instead of rejecting.")
        signal = apply_expiry_fallback(signal, channel_row["default_expiry"])

    # route_signal resolves which broker account's coordinator should
    # actually handle this (via the session's Fund -> attached Pocket
    # Option account - see core/broker_account_manager.py), falling
    # back to `coordinator` (the legacy single-shared-connection
    # default) only when no session covers this channel at all.
    execution_result = await broker_account_manager.route_signal(
        signal,
        coordinator,
        source=chat_title,
        sender=str(sender.id),
        message_id=event.id,
        sent_at=event.date,
        timeline=timeline,
        session_id=active_session["id"] if active_session else None,
        channel_id=channel_row["id"] if channel_row else None,
    )

    print(f"Execution Status: {execution_result['status']}")

    print("\nAXIM SIGNAL PARSED")
    print(f"Asset     : {signal['asset']}")
    print(f"Direction : {signal['direction']}")
    print(f"Expiry    : {signal['expiry']}")


@client.on(events.MessageEdited)
async def edit_handler(event):
    """Real edit/cancellation support for a still-pending multi-message
    announcement (mandate: providers correcting or cancelling a signal
    by editing the original message, rather than posting a follow-up).
    Deliberately narrow in scope - see SignalAssembler.handle_edit's own
    docstring for exactly which window this can and cannot affect: only
    a message that is STILL part of a pending, not-yet-completed
    sequence. A signal that already reached "signal_ready" was already
    routed to real execution synchronously in that same NewMessage
    handler call - there is no later point an edit could retroactively
    touch an already-placed trade, and this never tries to. Runs
    regardless of channel_allowed/emergency-stop/paused state (it never
    itself executes anything, only keeps in-memory assembly state
    accurate for whenever a real completing message next arrives)."""
    chat = await event.get_chat()
    chat_title = getattr(chat, "title", "") or getattr(chat, "first_name", "") or ""
    chat_username = getattr(chat, "username", "") or ""

    try:
        database.record_channel_message(
            chat_id=event.chat_id, username=chat_username, title=chat_title, message_text=event.raw_text,
        )
    except Exception as e:
        logger.error("telegram_listener: record_channel_message (edit) failed: %s", e)

    channel_row = database.find_channel(chat_id=event.chat_id, username=chat_username, title=chat_title)
    if channel_row is None or channel_row.get("source_type") == "bot_command":
        return

    message_text = event.raw_text
    rules = database.get_enabled_rules_for_channel(channel_row["id"])
    if rules:
        message_text = apply_signal_rules(message_text, rules)

    try:
        result = _shadow_assembler.handle_edit(channel_row["id"], event.id, message_text)
    except Exception as e:
        logger.error("telegram_listener: edit handling failed for channel_id=%s message_id=%s: %s",
                     channel_row["id"], event.id, e)
        return

    if result["action"] == "not_pending":
        return
    if result["action"] == "cancelled":
        print(f"[SIGNAL EDIT] {_debug_safe(chat_title)!r} edited its pending {result['asset']!r} "
              f"announcement to something no longer recognizable - treating it as cancelled.")
        _track_pipeline_event(event.chat_id, event.id, channel_row["id"], SignalLifecycleState.SKIPPED,
                               detail="edit_cancelled_pending_signal")
    elif result["action"] == "updated":
        print(f"[SIGNAL EDIT] {_debug_safe(chat_title)!r} corrected its pending announcement from "
              f"{result['old_asset']!r} to {result['new_asset']!r}.")
        _track_pipeline_event(event.chat_id, event.id, channel_row["id"], SignalLifecycleState.SKIPPED,
                               detail=f"edit_corrected_to_{result['new_asset']}")


HEARTBEAT_INTERVAL_SECONDS = 30
MAINTENANCE_INTERVAL_SECONDS = 3600  # hourly - cheap, infrequent housekeeping


async def _maintenance_loop():
    """Periodic housekeeping that doesn't need the tight heartbeat
    interval - today just pruning core/database.py's server_events
    outbox (docs/AXIM_REMOTE_ACCESS.md) so it never grows unbounded.
    No cron/scheduler exists in this codebase; piggybacking on this
    process's own asyncio loop is the simplest option, matching how
    _heartbeat_loop already does the same for a different interval."""
    while True:
        try:
            database.prune_server_events()
        except Exception as e:
            logger.error("telegram_listener: server_events prune failed: %s", e)
        await asyncio.sleep(MAINTENANCE_INTERVAL_SECONDS)


def _query_own_process_health():
    """Self-reported PID/memory (this process) and Chrome worker
    count/memory (its own spawned children), for scripts/soak_snapshot.py
    (docs/AXIM_RELEASE_CHECKLIST.md) - see core/database.py's
    ui_listener_heartbeat migration comment for why this is self-reported
    rather than discovered externally via WMI. A process reading its OWN
    session's CommandLine/memory isn't restricted; a DIFFERENT process in
    a different logon session (confirmed live: a Task-Scheduler-spawned
    process, even under the same user account) reading those same
    properties on a process outside its session IS blocked by Windows -
    self-reporting sidesteps that boundary entirely. Synchronous
    (subprocess.run) - always called via asyncio.to_thread, never
    directly on the event loop."""
    my_pid = os.getpid()
    listener_uptime_min = round((time.monotonic() - _PROCESS_START_MONOTONIC) / 60, 1)
    listener_mem_mb = None
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"$proc = Get-Process -Id {my_pid} -ErrorAction SilentlyContinue; "
             "if ($proc) { [math]::Round($proc.WorkingSet64/1MB,1) }"],
            capture_output=True, text=True, timeout=10,
        )
        if out.stdout.strip():
            listener_mem_mb = float(out.stdout.strip())
    except Exception as e:
        logger.warning("telegram_listener: self-reported memory query failed: %s", e)

    chrome_count, chrome_mem_mb = None, None
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "$procs = Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
             "Where-Object { $_.CommandLine -like '*sessions\\pocket_browser*' }; "
             "$total = 0; foreach ($p in $procs) { try { $total += (Get-Process -Id $p.ProcessId).WorkingSet64 } catch {} }; "
             "\"$($procs.Count)|$([math]::Round($total/1MB,1))\""],
            capture_output=True, text=True, timeout=10,
        )
        stripped = out.stdout.strip()
        if stripped:
            count_str, mem_str = stripped.split("|")
            chrome_count = int(count_str)
            chrome_mem_mb = float(mem_str)
    except Exception as e:
        logger.warning("telegram_listener: self-reported Chrome stats query failed: %s", e)

    return my_pid, listener_uptime_min, listener_mem_mb, chrome_count, chrome_mem_mb


async def _heartbeat_loop():
    """Periodically writes generation/worker-count/demo-mode-verified
    (plus self-reported process-health stats, see
    _query_own_process_health) to ui_listener_heartbeat - the (separate-
    process) API/UI's only way to show "is the browser/worker pool
    actually healthy right now" without sharing memory with this
    process. Runs for the life of the asyncio loop; a new one is
    implicitly started by the next _startup() call after a process-level
    restart, so nothing needs explicit cancellation here - the old loop
    (and this task with it) is simply gone by then."""
    while True:
        try:
            if warmup_service is not None and worker_pool is not None:
                listener_pid, listener_uptime_min, listener_mem_mb, chrome_count, chrome_mem_mb = (
                    await asyncio.to_thread(_query_own_process_health)
                )
                # Read-only against warmup_service's own dedicated page -
                # the same one track_outcome already reads the Closed-trades
                # list from concurrently with placement workers, so this
                # doesn't contend with anything on the trade critical path.
                # None (pocket_dom.read_balance's own non-fatal failure
                # value) is handled by update_listener_heartbeat's COALESCE,
                # not overwritten here.
                balance = None
                try:
                    page = await warmup_service.get_page()
                    balance = await pocket_dom.read_balance(page)
                except Exception as e:
                    logger.warning("telegram_listener: heartbeat balance read failed: %s", e)
                await asyncio.to_thread(
                    database.update_listener_heartbeat,
                    generation=warmup_service.generation,
                    worker_count=worker_pool.num_workers,
                    demo_mode_verified=True,
                    listener_pid=listener_pid,
                    listener_uptime_min=listener_uptime_min,
                    listener_mem_mb=listener_mem_mb,
                    chrome_count=chrome_count,
                    chrome_mem_mb=chrome_mem_mb,
                    balance=balance,
                )
        except Exception as e:
            logger.error("telegram_listener: heartbeat write failed: %s", e)
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)


TEST_TRADE_POLL_INTERVAL_SECONDS = 3
# Fixed, always-tradeable test signal (Broker page's "Test Trade" button) -
# deliberately hardcoded rather than user-configurable, so this feature
# can never become a general-purpose "type any signal and execute it"
# bypass around the real Telegram-sourced pipeline.
_TEST_TRADE_SIGNAL = {"asset": "EUR/USD OTC", "direction": "BUY", "expiry": "1 Minute", "raw_message": "manual test trade"}


async def _test_trade_poll_loop():
    """Broker page's "Test Trade (Demo Only)" button writes a pending
    request via database.request_test_trade() - the API process never
    calls into the trading engine directly (see docs/AXIM_APP_PLAN.md's
    architecture notes), so this poll loop is what actually runs it,
    through the SAME real coordinator/worker_pool already live in this
    process. Hard-blocks on ACCOUNT != DEMO as a second, independent gate
    on top of the API layer's own check - belt and suspenders for
    anything that can place a real trade."""
    while True:
        try:
            pending = database.get_pending_test_trade()
            if pending and pending["status"] == "pending":
                if ACCOUNT.upper() != "DEMO":
                    database.fail_test_trade(f"refused: ACCOUNT is {ACCOUNT!r}, not DEMO")
                elif coordinator is None:
                    database.fail_test_trade("listener not fully started yet")
                else:
                    result = await broker_account_manager.route_signal(
                        dict(_TEST_TRADE_SIGNAL), coordinator, source="manual-test-trade",
                        session_id=pending.get("session_id"),
                    )
                    database.complete_test_trade(result)
        except Exception as e:
            logger.error("telegram_listener: test trade poll failed: %s", e)
            try:
                database.fail_test_trade(str(e))
            except Exception:
                pass
        await asyncio.sleep(TEST_TRADE_POLL_INTERVAL_SECONDS)


CONNECTION_TEST_POLL_INTERVAL_SECONDS = 3


async def _connection_test_poll_loop():
    """Broker Accounts page's "Test Connection" button (distinct from
    both "Connect" - the full login flow - and "Run a Test Trade" -
    which places a real Demo trade) - verifies a broker account's
    already-established connection is genuinely still responsive by
    reading its real balance, through the SAME per-account browser
    context core/broker_account_manager.py already owns. Never routes
    anything through a coordinator, never submits an order - "connection
    verification that does not submit an order" is the whole point.
    Keyed by broker_account_id (unlike the singleton pending_test_trade),
    so more than one account can have a test in flight at once."""
    while True:
        try:
            for pending in database.list_pending_connection_tests():
                account_id = pending["broker_account_id"]
                try:
                    entry = await broker_account_manager.get_or_build_account_context(account_id)
                    page = await entry["warmup"].get_page()
                    balance = await pocket_dom.read_balance(page)
                    if balance is not None:
                        database.complete_connection_test(account_id, {"balance": balance})
                    else:
                        database.fail_connection_test(account_id, "connected, but could not read a balance from the page")
                except broker_account_manager.AccountUnavailable as e:
                    database.fail_connection_test(account_id, e.reason)
                except Exception as e:
                    database.fail_connection_test(account_id, str(e))
        except Exception as e:
            logger.error("telegram_listener: connection test poll failed: %s", e)
        await asyncio.sleep(CONNECTION_TEST_POLL_INTERVAL_SECONDS)


BOT_TRIGGER_SUPERVISOR_INTERVAL_SECONDS = 3


async def _bot_trigger_supervisor_loop():
    """Starts core/telegram_bot_trigger.py's interactive request loop for
    any active session covering a Bot Command Channel that doesn't
    already have one running - see that module's supervisor_tick()."""
    while True:
        try:
            if coordinator is not None:
                await telegram_bot_trigger.supervisor_tick(client, coordinator)
        except Exception as e:
            logger.error("telegram_listener: bot trigger supervisor tick failed: %s", e)
        await asyncio.sleep(BOT_TRIGGER_SUPERVISOR_INTERVAL_SECONDS)


async def _startup():
    """(Re)builds the browser/worker-pool stack and runs startup recovery.
    Called both on first launch and after every process-level restart -
    each call creates a fresh BrowserWarmupService/BrowserWorkerPool rather
    than reusing instances that may be left in a broken state by whatever
    triggered the restart."""
    global warmup_service, worker_pool, coordinator
    print("AXIM: starting persistent Pocket Option session...")
    warmup_service = BrowserWarmupService()
    await warmup_service.start()
    print(f"AXIM: starting browser worker pool ({MAX_CONCURRENT_WORKERS} worker(s))...")
    worker_pool = BrowserWorkerPool(warmup_service, num_workers=MAX_CONCURRENT_WORKERS)
    await worker_pool.start()
    coordinator = TradeCoordinator(worker_pool, warmup_service, asset_cache=warmup_service.asset_cache)
    print("AXIM: worker pool ready, running startup recovery...")
    await recovery.run_recovery(warmup_service)

    # A broker account whose user_data_dir is this same legacy default
    # profile (sessions/pocket_browser) is adopted onto the connection
    # just built above, rather than broker_account_manager launching a
    # second BrowserWarmupService against the same directory the first
    # time a Fund-scoped session needs it - see adopt_existing_connection's
    # own docstring for why that would collide. This is how the
    # single-shared-connection legacy path and the Fund/broker-account
    # architecture became the same physical browser session rather than
    # two competing ones.
    for account in database.list_broker_accounts():
        if account["user_data_dir"] == "sessions/pocket_browser":
            broker_account_manager.adopt_existing_connection(
                account["id"], warmup_service, worker_pool, coordinator,
            )

    session_manager.register(get_event_bus())
    event_stream.register(get_event_bus())
    asyncio.create_task(_heartbeat_loop())
    asyncio.create_task(_maintenance_loop())
    asyncio.create_task(_test_trade_poll_loop())
    asyncio.create_task(_connection_test_poll_loop())
    asyncio.create_task(_bot_trigger_supervisor_loop())


async def _shutdown():
    global warmup_service, worker_pool
    if worker_pool is not None:
        try:
            await worker_pool.stop()
        except Exception as e:
            logger.error("telegram_listener: error stopping worker pool: %s", e)
    if warmup_service is not None:
        try:
            await warmup_service.stop()
        except Exception as e:
            logger.error("telegram_listener: error stopping browser: %s", e)
    try:
        await broker_account_manager.stop_all()
    except Exception as e:
        logger.error("telegram_listener: error stopping broker account contexts: %s", e)


def run_forever():
    """Top-level 24/7 supervisor (P0 requirement: continuous operation with
    automatic recovery). Rebuilds the whole browser/worker-pool/Telegram
    connection stack and retries with exponential backoff (capped) on any
    unexpected failure - a crashed browser that couldn't self-heal, a
    Telegram-side disconnect, or any other unhandled error - so the process
    survives without manual intervention. Complements the existing
    browser-level (BrowserWarmupService) and worker-level
    (BrowserWorkerPool) recovery, which handle failures that don't need a
    full process restart; this is the outermost layer for the failures
    that do.

    Records "process_restart" succeeded/failed recovery_events - matching
    the other 3 recovery layers (browser_reconnect, worker_pool_rebuild,
    resume_open_trade), which all record a real terminal outcome, not just
    an attempt. Only recorded for actual RESTARTS (following a prior
    disconnect or failure), not the initial clean startup, which isn't a
    recovery from anything.

    Ctrl+C (or SIGTERM via SystemExit) shuts down cleanly and does not
    retry - only genuinely unexpected failures trigger a restart."""
    backoff = 1
    max_backoff = 60
    is_restart = False
    while True:
        try:
            client.loop.run_until_complete(_startup())
            client.start(phone=phone)
            print("Connected to Telegram")
            if is_restart:
                database.record_recovery_event("process_restart", "succeeded", "listener operational again after restart")
            backoff = 1  # reset only after a fully successful (re)start
            is_restart = False
            client.run_until_disconnected()
            # A clean, intentional stop arrives as KeyboardInterrupt/
            # SystemExit below, not a normal return here - reaching this
            # point means the connection dropped unexpectedly. Not itself
            # a recovery outcome - it's the failure that the NEXT
            # successful (or failed) restart attempt reports on.
            logger.warning("telegram_listener: client disconnected unexpectedly - restarting")
            is_restart = True
        except (KeyboardInterrupt, SystemExit):
            print("\nAXIM: shutdown requested, closing cleanly...")
            client.loop.run_until_complete(_shutdown())
            raise
        except Exception as e:
            logger.error("telegram_listener: unhandled error, restarting: %s", e)
            database.record_recovery_event("process_restart", "failed", str(e))
            is_restart = True
            try:
                client.loop.run_until_complete(_shutdown())
            except Exception as shutdown_error:
                logger.error("telegram_listener: error during shutdown before restart: %s", shutdown_error)

        print(f"AXIM: restarting in {backoff}s...")
        time.sleep(backoff)
        backoff = min(backoff * 2, max_backoff)


if __name__ == "__main__":
    run_forever()
