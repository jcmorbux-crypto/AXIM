"""Multi-broker-account execution registry (docs/AXIM_APP_PLAN.md) - the
piece that actually makes "each Fund connects to its own Pocket Option
account" real at execution time, not just in the database.

Before this module existed, core/telegram_listener.py held exactly one
BrowserWarmupService/BrowserWorkerPool/TradeCoordinator for the whole
process - every Fund's trades went through the same shared browser
session. This module owns a registry of those same three objects, one
full set per CONNECTED broker account, built lazily the first time a
session actually needs that account and kept warm for the rest of the
process's life (same "warm execution" philosophy as the original
single-account design, just per account now).

Each account's BrowserWarmupService is given that account's own
user_data_dir (see core/database.py's broker_accounts.user_data_dir),
so its persistent Chrome profile/cookies/login session can never bleed
into another account's - two Funds pointing at two different broker
accounts are genuinely, independently connected, not sharing anything.
"""
import asyncio
import sys
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CORE_DIR.parent
EXECUTION_DIR = PROJECT_ROOT / "execution"
CONFIG_DIR = PROJECT_ROOT / "config"

sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(EXECUTION_DIR))
sys.path.insert(0, str(CONFIG_DIR))

import database
import recovery
import fund_manager
from trade_lifecycle import TradeStatus
from trade_coordinator import TradeCoordinator
from browser_warmup import BrowserWarmupService
from browser_worker_pool import BrowserWorkerPool
from settings import MAX_CONCURRENT_WORKERS
from logger import get_logger

logger = get_logger("axim.lifecycle", filename="lifecycle.log")

# {broker_account_id: {"warmup": BrowserWarmupService, "pool": BrowserWorkerPool,
#                       "coordinator": TradeCoordinator}}
_registry = {}
# Guards registry mutation - only one build/rebuild per account_id at a
# time; a second concurrent signal for the same not-yet-built account
# waits for the first build rather than racing it.
_build_locks = {}


def _lock_for(broker_account_id):
    lock = _build_locks.get(broker_account_id)
    if lock is None:
        lock = asyncio.Lock()
        _build_locks[broker_account_id] = lock
    return lock


class AccountUnavailable(Exception):
    """Raised (and always caught by route_signal, never left to crash the
    listener) when a session's fund has no usable broker account right
    now - no account attached, not connected, or its context failed to
    start. Carries a human-readable reason so the rejected signal's own
    `result` column stays useful for the operator, same as every other
    rejection reason in this codebase."""

    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


async def _build_account_context(broker_account_id):
    account = database.get_broker_account(broker_account_id)
    if account is None:
        raise AccountUnavailable(f"broker account {broker_account_id} not found")

    logger.info("broker_account_manager: building context for account_id=%s (%s)",
                broker_account_id, account["name"])
    warmup = BrowserWarmupService(user_data_dir=PROJECT_ROOT / account["user_data_dir"])
    try:
        await warmup.start()
    except Exception as e:
        database.update_broker_account(broker_account_id, connection_status="error")
        logger.error("broker_account_manager: account_id=%s failed to start: %s", broker_account_id, e)
        raise AccountUnavailable(f"broker account {account['name']!r} failed to connect: {e}")

    pool = BrowserWorkerPool(warmup, num_workers=MAX_CONCURRENT_WORKERS)
    await pool.start()
    coordinator = TradeCoordinator(pool, warmup, asset_cache=warmup.asset_cache)

    # Reconcile any trade this specific account left open across a
    # restart before it starts taking new signals - same discipline as
    # the legacy default connection's startup recovery, just scoped to
    # this account's own trade history via broker_account_id.
    await recovery.run_recovery(warmup, broker_account_id=broker_account_id, skip_abandoned_pass=True)

    database.update_broker_account(broker_account_id, connection_status="connected")
    entry = {"warmup": warmup, "pool": pool, "coordinator": coordinator}
    _registry[broker_account_id] = entry
    logger.info("broker_account_manager: account_id=%s ready", broker_account_id)
    return entry


async def get_or_build_account_context(broker_account_id):
    """Returns the {"warmup", "pool", "coordinator"} entry for this
    account, building it (a real browser launch, ~10s+ - see
    BrowserWarmupService) on first use. Raises AccountUnavailable if the
    account doesn't exist, isn't marked connected (the operator hasn't
    completed the login flow - see scripts/connect_broker_account.py),
    or fails to actually start."""
    existing = _registry.get(broker_account_id)
    if existing is not None:
        return existing

    async with _lock_for(broker_account_id):
        existing = _registry.get(broker_account_id)
        if existing is not None:
            return existing

        account = database.get_broker_account(broker_account_id)
        if account is None:
            raise AccountUnavailable(f"broker account {broker_account_id} not found")
        if account["connection_status"] != "connected":
            raise AccountUnavailable(
                f"broker account {account['name']!r} is not connected "
                f"(status={account['connection_status']!r}) - connect it from Connections/Broker first"
            )
        return await _build_account_context(broker_account_id)


async def resolve_coordinator_for_session(session_id):
    """session_id -> trading_sessions.fund_id -> that fund's primary
    broker account -> that account's TradeCoordinator. Raises
    AccountUnavailable (never returns a partially-wrong coordinator) if
    any link in that chain is missing - a signal that can't be resolved
    to a real, connected account must be rejected, not silently executed
    against the wrong (or the legacy shared) connection, per docs/
    AXIM_APP_PLAN.md: "Do not let a Fund trade unless it has a valid
    broker account attached." """
    session = database.get_trading_session(session_id)
    if session is None:
        raise AccountUnavailable(f"session {session_id} not found")
    fund_id = session["fund_id"]
    if fund_id is None:
        raise AccountUnavailable("session has no fund attached")

    can_trade, reason, _ = fund_manager.can_trade(fund_id)
    if not can_trade:
        raise AccountUnavailable(reason)

    account = database.get_fund_primary_broker_account(fund_id)
    entry = await get_or_build_account_context(account["id"])
    return entry["coordinator"], fund_id, account["id"]


async def route_signal(signal, default_coordinator, source=None, sender=None, message_id=None,
                        sent_at=None, timeline=None, session_id=None):
    """The single entry point core/telegram_listener.py calls instead of
    a bare coordinator.handle_signal() - resolves which broker account's
    coordinator should actually handle this signal and delegates to it.

    session_id is None (no active Trading Session covers this channel) ->
    default_coordinator, the legacy single-shared-connection path kept
    for backward compatibility with the pre-Fund/Session passive-channel
    flow (docs/AXIM_SESSION_ARCHITECTURE.md) - this was never described
    as Fund-scoped in the first place, so multi-broker-account routing
    doesn't apply to it.

    session_id set -> resolve_coordinator_for_session. If that raises
    AccountUnavailable (no fund, no attached account, not connected, or
    the account's context failed to start), the signal is still recorded
    for audit - same "always record, even rejected" principle
    TradeCoordinator.handle_signal already follows for every other
    rejection reason - and rejected cleanly, never silently executed
    against the wrong (or the legacy shared) connection."""
    if session_id is None:
        return await default_coordinator.handle_signal(
            signal, source=source, sender=sender, message_id=message_id,
            sent_at=sent_at, timeline=timeline, session_id=None,
        )

    try:
        coordinator, fund_id, broker_account_id = await resolve_coordinator_for_session(session_id)
    except AccountUnavailable as e:
        trade_id = database.record_signal_received(
            signal, source=source, sender=sender, message_id=message_id, session_id=session_id,
        )
        logger.info("STAGE trade_id=%s stage=broker_account status=rejected reason=%s", trade_id, e.reason)
        database.update_trade_status(trade_id, TradeStatus.ERROR, result="rejected:broker_account_unavailable")
        return {
            "status": "rejected", "trade_id": trade_id,
            "rule": "broker_account_unavailable", "reason": e.reason,
        }

    return await coordinator.handle_signal(
        signal, source=source, sender=sender, message_id=message_id,
        sent_at=sent_at, timeline=timeline, session_id=session_id,
        fund_id=fund_id, broker_account_id=broker_account_id,
    )


async def stop_all():
    """Graceful shutdown - closes every account's browser context, not
    just the legacy default one. Best-effort: one account failing to
    close cleanly should not prevent the others from closing."""
    for account_id, entry in list(_registry.items()):
        try:
            await entry["pool"].stop()
            await entry["warmup"].stop()
        except Exception as e:
            logger.error("broker_account_manager: error stopping account_id=%s: %s", account_id, e)
    _registry.clear()
