import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))
sys.path.insert(0, str(PROJECT_ROOT / "execution"))

import database
import broker_account_manager
from broker_account_manager import AccountUnavailable, account_effective_cabinet_mode


def _run(coro):
    return asyncio.run(coro)


class FakeCoordinator:
    def __init__(self):
        self.calls = []

    async def handle_signal(self, signal, **kwargs):
        self.calls.append(kwargs)
        return {"status": "clicked", "trade_id": 999, **kwargs}


class BrokerAccountManagerTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        broker_account_manager._registry.clear()
        broker_account_manager._build_locks.clear()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()
        broker_account_manager._registry.clear()
        broker_account_manager._build_locks.clear()

    def _signal(self):
        return {"asset": "EUR/USD OTC", "direction": "BUY", "expiry": "1 Minute", "raw_message": "test"}

    # ---- resolve_coordinator_for_session: every failure mode ----------

    def test_raises_when_session_not_found(self):
        with self.assertRaises(AccountUnavailable):
            _run(broker_account_manager.resolve_coordinator_for_session(99999))

    def test_raises_when_session_has_no_fund(self):
        session_id = database.start_trading_session("Test", [1], "DEMO")
        self.assertIsNone(database.get_trading_session(session_id)["fund_id"])
        with self.assertRaises(AccountUnavailable):
            _run(broker_account_manager.resolve_coordinator_for_session(session_id))

    def test_raises_when_fund_has_no_broker_account(self):
        fund_id = database.create_fund("F1", starting_balance=100)
        session_id = database.start_trading_session("Test", [1], "DEMO", fund_id=fund_id)
        with self.assertRaises(AccountUnavailable) as ctx:
            _run(broker_account_manager.resolve_coordinator_for_session(session_id))
        self.assertIn("not connected", str(ctx.exception))

    def test_raises_when_broker_account_not_connected(self):
        fund_id = database.create_fund("F1", starting_balance=100)
        account_id = database.create_broker_account("Acc1", mode="demo")
        database.assign_broker_account_to_fund(fund_id, account_id)
        session_id = database.start_trading_session("Test", [1], "DEMO", fund_id=fund_id)
        with self.assertRaises(AccountUnavailable) as ctx:
            _run(broker_account_manager.resolve_coordinator_for_session(session_id))
        self.assertIn("not connected", str(ctx.exception))

    def test_resolves_to_registered_coordinator_when_connected(self):
        fund_id = database.create_fund("F1", starting_balance=100)
        account_id = database.create_broker_account("Acc1", mode="demo")
        database.update_broker_account(account_id, connection_status="connected")
        database.assign_broker_account_to_fund(fund_id, account_id)
        session_id = database.start_trading_session("Test", [1], "DEMO", fund_id=fund_id)

        fake_coordinator = FakeCoordinator()
        broker_account_manager._registry[account_id] = {
            "warmup": None, "pool": None, "coordinator": fake_coordinator,
        }

        coordinator, resolved_fund_id, resolved_account_id = _run(
            broker_account_manager.resolve_coordinator_for_session(session_id)
        )
        self.assertIs(coordinator, fake_coordinator)
        self.assertEqual(resolved_fund_id, fund_id)
        self.assertEqual(resolved_account_id, account_id)

    # ---- route_signal: the actual dispatcher ---------------------------

    def test_route_signal_with_no_session_uses_default_coordinator(self):
        default_coordinator = FakeCoordinator()
        result = _run(broker_account_manager.route_signal(
            self._signal(), default_coordinator, session_id=None,
        ))
        self.assertEqual(result["status"], "clicked")
        self.assertEqual(len(default_coordinator.calls), 1)

    def test_route_signal_with_unusable_account_rejects_without_touching_default(self):
        fund_id = database.create_fund("F1", starting_balance=100)
        session_id = database.start_trading_session("Test", [1], "DEMO", fund_id=fund_id)
        default_coordinator = FakeCoordinator()

        result = _run(broker_account_manager.route_signal(
            self._signal(), default_coordinator, session_id=session_id,
        ))
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["rule"], "broker_account_unavailable")
        self.assertEqual(default_coordinator.calls, [])  # never touched - fail closed
        # Still recorded for audit, per the "always record" principle.
        trade = database.get_signal_detail(result["trade_id"])
        self.assertIsNotNone(trade)

    def test_route_signal_delegates_to_resolved_account_and_threads_ids(self):
        fund_id = database.create_fund("F1", starting_balance=100)
        account_id = database.create_broker_account("Acc1", mode="demo")
        database.update_broker_account(account_id, connection_status="connected")
        database.assign_broker_account_to_fund(fund_id, account_id)
        session_id = database.start_trading_session("Test", [1], "DEMO", fund_id=fund_id)

        fake_coordinator = FakeCoordinator()
        broker_account_manager._registry[account_id] = {
            "warmup": None, "pool": None, "coordinator": fake_coordinator,
        }
        default_coordinator = FakeCoordinator()

        result = _run(broker_account_manager.route_signal(
            self._signal(), default_coordinator, session_id=session_id,
        ))
        self.assertEqual(default_coordinator.calls, [])  # the OTHER account, not default
        self.assertEqual(len(fake_coordinator.calls), 1)
        self.assertEqual(fake_coordinator.calls[0]["fund_id"], fund_id)
        self.assertEqual(fake_coordinator.calls[0]["broker_account_id"], account_id)

    def test_concurrent_resolution_for_same_account_only_builds_once(self):
        """get_or_build_account_context's lock should serialize concurrent
        builders for the SAME account rather than racing two real browser
        launches - verified with a fake _build_account_context that counts
        calls and yields control, so two concurrent callers actually
        interleave instead of trivially running sequentially."""
        account_id = database.create_broker_account("Acc1", mode="demo")
        database.update_broker_account(account_id, connection_status="connected")

        build_calls = []

        async def fake_build(acc_id):
            build_calls.append(acc_id)
            await asyncio.sleep(0.05)
            entry = {"warmup": None, "pool": None, "coordinator": FakeCoordinator()}
            broker_account_manager._registry[acc_id] = entry
            return entry

        original_build = broker_account_manager._build_account_context
        broker_account_manager._build_account_context = fake_build
        try:
            async def _scenario():
                return await asyncio.gather(
                    broker_account_manager.get_or_build_account_context(account_id),
                    broker_account_manager.get_or_build_account_context(account_id),
                )
            entry_a, entry_b = _run(_scenario())
        finally:
            broker_account_manager._build_account_context = original_build

        self.assertEqual(len(build_calls), 1)  # built exactly once, not twice
        self.assertIs(entry_a, entry_b)


class AccountEffectiveCabinetModeTests(unittest.TestCase):
    """account_effective_cabinet_mode() - see core/database.py's
    broker_accounts.mode docstring: 'a both account can still be
    demo-only in practice until [live_enabled] is flipped'."""

    def _account(self, mode, live_enabled):
        return {"mode": mode, "live_enabled": live_enabled}

    def test_demo_mode_is_always_demo_regardless_of_live_enabled(self):
        self.assertEqual(account_effective_cabinet_mode(self._account("demo", False)), "demo")
        self.assertEqual(account_effective_cabinet_mode(self._account("demo", True)), "demo")

    def test_both_mode_is_demo_until_live_enabled_flipped(self):
        self.assertEqual(account_effective_cabinet_mode(self._account("both", False)), "demo")
        self.assertEqual(account_effective_cabinet_mode(self._account("both", True)), "live")

    def test_live_mode_is_demo_until_live_enabled_flipped(self):
        self.assertEqual(account_effective_cabinet_mode(self._account("live", False)), "demo")
        self.assertEqual(account_effective_cabinet_mode(self._account("live", True)), "live")


class LiveAuthorizationGateTests(unittest.TestCase):
    """resolve_coordinator_for_session's new gate: an account whose own
    session would be pointed at the live cabinet requires THIS Fund to
    be independently live_enabled too - can_go_live was previously
    computed and discarded here, making the Fund/Account "Live" UI
    toggles decorative. See the AXIM_APP_PLAN.md correction this
    accompanies."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        broker_account_manager._registry.clear()
        broker_account_manager._build_locks.clear()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()
        broker_account_manager._registry.clear()
        broker_account_manager._build_locks.clear()

    def _live_capable_account_and_fund(self, fund_live_enabled, account_live_enabled):
        fund_id = database.create_fund("F1", starting_balance=100)
        database.update_fund(fund_id, live_enabled=int(fund_live_enabled))
        account_id = database.create_broker_account("Acc1", mode="both")
        database.update_broker_account(
            account_id, connection_status="connected", live_enabled=int(account_live_enabled),
        )
        database.assign_broker_account_to_fund(fund_id, account_id)
        session_id = database.start_trading_session("Test", [1], "DEMO", fund_id=fund_id)
        fake_coordinator = FakeCoordinator()
        broker_account_manager._registry[account_id] = {
            "warmup": None, "pool": None, "coordinator": fake_coordinator,
        }
        return session_id, fund_id, account_id, fake_coordinator

    def test_live_capable_account_rejects_fund_not_authorized_for_live(self):
        session_id, _, _, _ = self._live_capable_account_and_fund(
            fund_live_enabled=False, account_live_enabled=True,
        )
        with self.assertRaises(AccountUnavailable) as ctx:
            _run(broker_account_manager.resolve_coordinator_for_session(session_id))
        self.assertIn("not authorized for Live", str(ctx.exception))

    def test_account_not_authorized_for_live_stays_demo_and_still_resolves(self):
        """The account itself hasn't flipped its own live_enabled switch,
        so account_effective_cabinet_mode is 'demo' regardless of the
        Fund's setting - the session should resolve normally (safely, in
        demo), not be rejected. Only a Fund trying to use an account
        whose session IS pointed at live gets rejected for being
        unauthorized (see test_live_capable_account_rejects_fund_not_authorized_for_live)."""
        session_id, fund_id, account_id, fake_coordinator = self._live_capable_account_and_fund(
            fund_live_enabled=True, account_live_enabled=False,
        )
        coordinator, resolved_fund_id, resolved_account_id = _run(
            broker_account_manager.resolve_coordinator_for_session(session_id)
        )
        self.assertIs(coordinator, fake_coordinator)
        self.assertEqual(resolved_fund_id, fund_id)
        self.assertEqual(resolved_account_id, account_id)

    def test_live_capable_account_resolves_when_both_authorized(self):
        session_id, fund_id, account_id, fake_coordinator = self._live_capable_account_and_fund(
            fund_live_enabled=True, account_live_enabled=True,
        )
        coordinator, resolved_fund_id, resolved_account_id = _run(
            broker_account_manager.resolve_coordinator_for_session(session_id)
        )
        self.assertIs(coordinator, fake_coordinator)
        self.assertEqual(resolved_fund_id, fund_id)
        self.assertEqual(resolved_account_id, account_id)

    def test_demo_only_account_resolves_regardless_of_fund_live_enabled(self):
        """A plain demo-mode account was never live-capable in the first
        place - the new gate must not start rejecting existing, ordinary
        demo Funds that never touched the Live toggles at all."""
        fund_id = database.create_fund("F1", starting_balance=100)
        account_id = database.create_broker_account("Acc1", mode="demo")
        database.update_broker_account(account_id, connection_status="connected")
        database.assign_broker_account_to_fund(fund_id, account_id)
        session_id = database.start_trading_session("Test", [1], "DEMO", fund_id=fund_id)
        fake_coordinator = FakeCoordinator()
        broker_account_manager._registry[account_id] = {
            "warmup": None, "pool": None, "coordinator": fake_coordinator,
        }

        coordinator, _, _ = _run(broker_account_manager.resolve_coordinator_for_session(session_id))
        self.assertIs(coordinator, fake_coordinator)


class ArchivedOrPausedFundMidSessionTests(unittest.TestCase):
    """api/funds_routes.py's archive_fund/pause_fund endpoints don't
    force-stop an already-active session (pause_fund's own docstring:
    "without stopping the session outright, so resuming picks back up
    exactly where it left off") - this only works safely because
    resolve_coordinator_for_session re-checks fund_manager.can_trade on
    EVERY signal, not just at session start. Confirms that safety
    property directly rather than just reading the code and assuming -
    no prior test covered this exact "fund status changes out from under
    an already-running session" scenario."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        broker_account_manager._registry.clear()
        broker_account_manager._build_locks.clear()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()
        broker_account_manager._registry.clear()
        broker_account_manager._build_locks.clear()

    def _active_session(self):
        fund_id = database.create_fund("F1", starting_balance=100)
        account_id = database.create_broker_account("Acc1", mode="demo")
        database.update_broker_account(account_id, connection_status="connected")
        database.assign_broker_account_to_fund(fund_id, account_id)
        session_id = database.start_trading_session("Test", [1], "DEMO", fund_id=fund_id)
        broker_account_manager._registry[account_id] = {
            "warmup": None, "pool": None, "coordinator": FakeCoordinator(),
        }
        return fund_id, session_id

    def test_archiving_the_fund_blocks_the_next_signal_on_its_still_active_session(self):
        fund_id, session_id = self._active_session()
        # Session itself is untouched - still "active" in the DB - archiving
        # alone doesn't stop it. The next signal must be blocked anyway.
        self.assertEqual(database.get_trading_session(session_id)["status"], "active")
        database.update_fund(fund_id, status="archived")
        with self.assertRaises(AccountUnavailable) as ctx:
            _run(broker_account_manager.resolve_coordinator_for_session(session_id))
        self.assertIn("archived", str(ctx.exception))

    def test_pausing_the_fund_blocks_the_next_signal_on_its_still_active_session(self):
        fund_id, session_id = self._active_session()
        database.update_fund(fund_id, status="paused")
        with self.assertRaises(AccountUnavailable) as ctx:
            _run(broker_account_manager.resolve_coordinator_for_session(session_id))
        self.assertIn("paused", str(ctx.exception))

    def test_resuming_the_fund_lets_the_same_session_resolve_again(self):
        # pause_fund's whole point: resuming picks back up exactly where
        # it left off, not requiring a fresh session.
        fund_id, session_id = self._active_session()
        database.update_fund(fund_id, status="paused")
        with self.assertRaises(AccountUnavailable):
            _run(broker_account_manager.resolve_coordinator_for_session(session_id))

        database.update_fund(fund_id, status="active")
        coordinator, resolved_fund_id, _ = _run(
            broker_account_manager.resolve_coordinator_for_session(session_id)
        )
        self.assertEqual(resolved_fund_id, fund_id)
        self.assertEqual(database.get_trading_session(session_id)["id"], session_id)


class BalanceRefreshLoopEvictionTests(unittest.TestCase):
    """api/broker_accounts_routes.py's disconnect/archive endpoints run in
    the separate API process and only ever flip a DB column - they have
    no way to reach into this process's _registry to close the account's
    actual browser context. Nothing evicted a stale entry except a full
    stop_all() (whole-listener shutdown), so a disconnected/archived
    account's browser+worker pool would keep running indefinitely.
    _balance_refresh_loop now self-evicts on its own existing wake-up
    cadence instead - confirms that directly rather than just reading the
    new code and assuming it works."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        broker_account_manager._registry.clear()
        broker_account_manager._build_locks.clear()
        self._original_interval = broker_account_manager.BALANCE_REFRESH_INTERVAL_SECONDS
        broker_account_manager.BALANCE_REFRESH_INTERVAL_SECONDS = 0
        self._original_read_balance = broker_account_manager.pocket_dom.read_balance
        broker_account_manager.pocket_dom.read_balance = AsyncMock(return_value=None)

    def tearDown(self):
        broker_account_manager.BALANCE_REFRESH_INTERVAL_SECONDS = self._original_interval
        broker_account_manager.pocket_dom.read_balance = self._original_read_balance
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()
        broker_account_manager._registry.clear()
        broker_account_manager._build_locks.clear()

    def test_loop_self_evicts_and_stops_pool_and_warmup_once_disconnected(self):
        account_id = database.create_broker_account("Acc1", mode="demo")
        database.update_broker_account(account_id, connection_status="connected")

        pool = AsyncMock()
        warmup = AsyncMock()
        entry = {"warmup": warmup, "pool": pool, "coordinator": FakeCoordinator()}
        broker_account_manager._registry[account_id] = entry

        async def _scenario():
            task = asyncio.create_task(broker_account_manager._balance_refresh_loop(account_id, warmup))
            entry["balance_task"] = task
            await asyncio.sleep(0.01)  # let it wake at least once while still "connected"
            database.update_broker_account(account_id, connection_status="disconnected")
            await asyncio.wait_for(task, timeout=1.0)

        _run(_scenario())

        self.assertNotIn(account_id, broker_account_manager._registry)
        pool.stop.assert_awaited_once()
        warmup.stop.assert_awaited_once()

    def test_loop_evicts_when_account_is_deleted_outright(self):
        account_id = database.create_broker_account("Acc1", mode="demo")
        database.update_broker_account(account_id, connection_status="connected")

        pool = AsyncMock()
        warmup = AsyncMock()
        broker_account_manager._registry[account_id] = {
            "warmup": warmup, "pool": pool, "coordinator": FakeCoordinator(),
        }

        async def _scenario():
            conn = database.get_connection()
            try:
                conn.execute("DELETE FROM broker_accounts WHERE id = ?", (account_id,))
                conn.commit()
            finally:
                conn.close()
            await asyncio.wait_for(
                broker_account_manager._balance_refresh_loop(account_id, warmup), timeout=1.0,
            )

        _run(_scenario())

        self.assertNotIn(account_id, broker_account_manager._registry)
        pool.stop.assert_awaited_once()
        warmup.stop.assert_awaited_once()


class AdoptExistingConnectionTests(unittest.TestCase):
    """core/telegram_listener.py's _startup() always eagerly builds one
    legacy warmup/pool/coordinator against sessions/pocket_browser (the
    heartbeat loop and the session_id=None fallback path both depend on
    this). adopt_existing_connection lets a broker account that shares
    that exact profile directory reuse those already-built objects
    instead of get_or_build_account_context launching a second
    BrowserWarmupService against the same locked profile - confirmed
    real in production (docs/AXIM_LIVE_READINESS_CHECKLIST.md's "second
    telegram_listener.py" incident)."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        broker_account_manager._registry.clear()
        broker_account_manager._build_locks.clear()
        self._original_interval = broker_account_manager.BALANCE_REFRESH_INTERVAL_SECONDS
        broker_account_manager.BALANCE_REFRESH_INTERVAL_SECONDS = 3600  # never fires during the test

    def tearDown(self):
        broker_account_manager.BALANCE_REFRESH_INTERVAL_SECONDS = self._original_interval
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()
        broker_account_manager._registry.clear()
        broker_account_manager._build_locks.clear()

    def test_registers_the_exact_objects_passed_in_without_building_anything_new(self):
        account_id = database.create_broker_account("Legacy-adopted", mode="demo")
        warmup, pool, coordinator = AsyncMock(), AsyncMock(), FakeCoordinator()

        async def _scenario():
            broker_account_manager.adopt_existing_connection(account_id, warmup, pool, coordinator)
            await asyncio.sleep(0)  # let the balance-refresh task get scheduled
            entry = broker_account_manager._registry[account_id]
            self.assertIs(entry["warmup"], warmup)
            self.assertIs(entry["pool"], pool)
            self.assertIs(entry["coordinator"], coordinator)
            entry["balance_task"].cancel()

        _run(_scenario())
        self.assertEqual(database.get_broker_account(account_id)["connection_status"], "connected")
        warmup.start.assert_not_called()  # adopted, not (re)built

    def test_resolve_coordinator_for_session_then_returns_the_adopted_coordinator_directly(self):
        fund_id = database.create_fund("F1", starting_balance=100)
        account_id = database.create_broker_account("Legacy-adopted", mode="demo")
        database.assign_broker_account_to_fund(fund_id, account_id)
        session_id = database.start_trading_session("Test", [1], "DEMO", fund_id=fund_id)
        warmup, pool, coordinator = AsyncMock(), AsyncMock(), FakeCoordinator()

        async def _scenario():
            broker_account_manager.adopt_existing_connection(account_id, warmup, pool, coordinator)
            resolved_coordinator, resolved_fund_id, resolved_account_id = (
                await broker_account_manager.resolve_coordinator_for_session(session_id)
            )
            self.assertIs(resolved_coordinator, coordinator)
            self.assertEqual(resolved_fund_id, fund_id)
            self.assertEqual(resolved_account_id, account_id)
            broker_account_manager._registry[account_id]["balance_task"].cancel()

        _run(_scenario())
        warmup.start.assert_not_called()  # never built a second context


if __name__ == "__main__":
    unittest.main()
