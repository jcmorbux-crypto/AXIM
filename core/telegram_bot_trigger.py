"""Interactive Telegram bot trigger-command workflow
(docs/AXIM_SESSION_ARCHITECTURE.md section 5) - for a Bot Command
Channel source, drives the send-command / await-reply / parse / execute
/ wait-for-result / request-next loop until the session ends.

A genuinely new kind of Telegram interaction (the account SENDS
messages, not just receives them) - separate from
core/telegram_listener.py's passive event.chat_id handling, which
explicitly skips source_type='bot_command' channels (see that module's
handler()) since a bot-command channel's messages are only ever valid
as replies to a request THIS module made, never as unprompted passive
signals. Without that exclusion, the same reply would be double-
processed: once here as the awaited response, once by the passive
handler as if it were an ordinary pushed message.

Everything here takes its Telegram client and TradeCoordinator as
parameters rather than importing a specific global instance, so it's
testable against a fake client without a real Telegram connection -
there is no way to live-verify the actual send/receive interaction in
this environment without real Telegram API credentials (same class of
limitation execution/pocket_dom.py's DOM-interaction functions already
have and document - live-fire tested by an operator, not covered by an
automated net that touches the real network)."""
import asyncio
import time
from datetime import datetime

import broker_account_manager
import database
from signal_parser import parse_signal
from logger import get_logger

logger = get_logger("axim.lifecycle", filename="lifecycle.log")

REPLY_TIMEOUT_SECONDS = 30
REQUEST_INTERVAL_SECONDS = 2
TRADE_RESULT_TIMEOUT_SECONDS = 300
TRADE_RESULT_POLL_SECONDS = 2

# session_id -> asyncio.Task - so the supervisor never spawns a second
# loop for a session that already has one running, and so a crashed
# loop's exception is discoverable rather than silently swallowed.
_active_loops = {}


def bot_command_channel_for_session(session):
    """The first channel this session covers that's typed as a Bot
    Command Channel with a configured trigger command, or None. A
    session could in principle cover more than one channel, but only one
    interactive loop drives it - this mirrors the UI, which only offers
    a single signal-source selection per session today."""
    for channel_id in session.get("channel_ids") or []:
        row = database.get_channel(channel_id)
        if row and row.get("source_type") == "bot_command" and row.get("trigger_command"):
            return row
    return None


async def _wait_for_trade_result(trade_id, timeout_seconds=TRADE_RESULT_TIMEOUT_SECONDS,
                                  poll_seconds=TRADE_RESULT_POLL_SECONDS):
    """Polls until this trade has a real result (win/loss/draw/error/
    rejected) or the timeout elapses. There's no event-based "this one
    trade finished" hook available without a much larger refactor of the
    outcome-tracking pipeline - polling a single in-flight trade every
    couple seconds is cheap, and this only runs when the channel's
    command_wait_for_result flag asks for it."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        trade = database.get_signal_detail(trade_id)
        if trade is not None and trade.get("result") is not None:
            return
        await asyncio.sleep(poll_seconds)
    logger.warning("telegram_bot_trigger: trade_id=%s had no result within %ss - requesting next signal anyway",
                    trade_id, timeout_seconds)


async def _request_one_signal(client, channel_row):
    """Sends the configured trigger command to this channel and waits for
    the bot's reply, using Telethon's Conversation helper (a temporary,
    conversation-scoped listener - does not touch or duplicate
    telegram_listener.py's main event handler). Returns the raw reply
    Message, or None on timeout/error (logged, not raised - a single bad
    round-trip should not kill the whole session loop)."""
    chat_id = int(channel_row["chat_id"])
    try:
        async with client.conversation(chat_id, timeout=REPLY_TIMEOUT_SECONDS) as conv:
            await conv.send_message(channel_row["trigger_command"])
            return await conv.get_response()
    except asyncio.TimeoutError:
        logger.warning("telegram_bot_trigger: chat_id=%s no reply to trigger command within %ss",
                        chat_id, REPLY_TIMEOUT_SECONDS)
        return None
    except Exception as e:
        logger.error("telegram_bot_trigger: chat_id=%s trigger request failed: %s", chat_id, e)
        return None


async def run_session_loop(client, session_id, channel_row, default_coordinator):
    """Drives one session's interactive request/response/execute cycle
    until the session ends (any stop condition - profit target, loss
    limit, max trades, manual stop, emergency stop all already transition
    trading_sessions.status away from "active", checked fresh every
    iteration) or channel_row's max_requests_per_session is reached."""
    requests_sent = 0
    max_requests = channel_row.get("max_requests_per_session") or 0
    try:
        while True:
            session = database.get_trading_session(session_id)
            if session is None or session["status"] != "active":
                logger.info("telegram_bot_trigger: session_id=%s no longer active - stopping request loop", session_id)
                return
            if max_requests and requests_sent >= max_requests:
                logger.info("telegram_bot_trigger: session_id=%s reached max_requests_per_session=%s",
                            session_id, max_requests)
                return

            sent_at = datetime.now().isoformat()
            response = await _request_one_signal(client, channel_row)
            requests_sent += 1
            if response is None:
                database.record_bot_command_activity(
                    session_id, channel_row["id"], channel_row["trigger_command"], sent_at,
                    outcome="no_reply",
                )
                await asyncio.sleep(REQUEST_INTERVAL_SECONDS)
                continue

            signal = parse_signal(response.raw_text)
            if signal is None:
                logger.warning("telegram_bot_trigger: session_id=%s bot reply did not parse as a signal: %r",
                                session_id, (response.raw_text or "")[:200])
                database.record_bot_command_activity(
                    session_id, channel_row["id"], channel_row["trigger_command"], sent_at,
                    response_text=response.raw_text, response_message_id=response.id,
                    responded_at=response.date.isoformat() if response.date else None,
                    outcome="no_signal",
                )
                await asyncio.sleep(REQUEST_INTERVAL_SECONDS)
                continue

            result = await broker_account_manager.route_signal(
                signal, default_coordinator, source=channel_row["title"], sender="bot_command",
                message_id=response.id, sent_at=response.date, session_id=session_id,
            )
            database.record_bot_command_activity(
                session_id, channel_row["id"], channel_row["trigger_command"], sent_at,
                response_text=response.raw_text, response_message_id=response.id,
                responded_at=response.date.isoformat() if response.date else None,
                parsed_signal=signal, trade_id=result.get("trade_id"), outcome="routed",
            )

            if channel_row.get("command_wait_for_result") and result.get("trade_id"):
                await _wait_for_trade_result(result["trade_id"])

            await asyncio.sleep(REQUEST_INTERVAL_SECONDS)
    except Exception:
        logger.exception("telegram_bot_trigger: session_id=%s request loop crashed", session_id)
    finally:
        _active_loops.pop(session_id, None)


async def supervisor_tick(client, default_coordinator):
    """Called periodically from telegram_listener.py's own loop (see
    _bot_trigger_supervisor_loop) - starts a request loop for any active
    session covering a Bot Command Channel that doesn't already have one
    running. Ending a session is detected by run_session_loop itself
    re-reading session status every iteration, not by this supervisor
    cancelling anything - simpler, and avoids a race between "session
    just ended" and "supervisor about to cancel the task"."""
    for session in database.list_active_trading_sessions():
        if session["id"] in _active_loops:
            continue
        channel_row = bot_command_channel_for_session(session)
        if channel_row is None:
            continue
        task = asyncio.create_task(run_session_loop(client, session["id"], channel_row, default_coordinator))
        _active_loops[session["id"]] = task
        logger.info("telegram_bot_trigger: session_id=%s started interactive request loop for channel %r",
                    session["id"], channel_row["title"])
