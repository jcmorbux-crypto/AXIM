import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))

import database
import fund_manager
import risk_engine


class ComputePositionSizeTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self.session_id = database.start_trading_session("Test", [1], "DEMO")

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_no_session_id_falls_through_to_risk_manager(self):
        amount = risk_engine.compute_position_size(None, 5.0)
        self.assertEqual(amount, 5.0)  # risk_manager.compute_trade_amount fixed fallback

    def test_session_without_risk_profile_falls_through(self):
        amount = risk_engine.compute_position_size(self.session_id, 5.0)
        self.assertEqual(amount, 5.0)

    def test_fixed_sizing(self):
        profile_id = database.create_risk_profile("Fixed Test", sizing_mode="fixed", fixed_amount=7.5)
        database.set_session_risk_profile(self.session_id, profile_id)
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 7.5)

    def test_percent_sizing_uses_static_bankroll(self):
        profile_id = database.create_risk_profile("Percent Test", sizing_mode="percent",
                                                    bankroll=1000, percent_of_bankroll=2.0)
        database.set_session_risk_profile(self.session_id, profile_id)
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 20.0)

    def test_dynamic_sizing_grows_with_session_pnl(self):
        profile_id = database.create_risk_profile("Dynamic Test", sizing_mode="dynamic",
                                                    bankroll=1000, percent_of_bankroll=2.0)
        database.set_session_risk_profile(self.session_id, profile_id)
        database.update_session_pnl(self.session_id, 500)  # bankroll now effectively 1500
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 30.0)

    def test_kelly_sizing_positive_edge(self):
        # p=0.6, b=0.85 -> f* = 0.6 - 0.4/0.85 = 0.6 - 0.4706 = 0.1294
        # half-Kelly (default multiplier 0.5) -> 0.0647 * 1000 = 64.7
        profile_id = database.create_risk_profile(
            "Kelly Test", sizing_mode="kelly", bankroll=1000,
            kelly_win_rate_estimate=0.6, kelly_payout_estimate=0.85, kelly_fraction_multiplier=0.5,
        )
        database.set_session_risk_profile(self.session_id, profile_id)
        amount = risk_engine.compute_position_size(self.session_id, 5.0)
        self.assertAlmostEqual(amount, 64.71, places=1)

    def test_kelly_sizing_negative_edge_clamped_to_zero(self):
        # p=0.3 (bad win rate) -> f* is deeply negative, clamped to 0
        profile_id = database.create_risk_profile(
            "Bad Kelly", sizing_mode="kelly", bankroll=1000,
            kelly_win_rate_estimate=0.3, kelly_payout_estimate=0.85, kelly_fraction_multiplier=0.5,
        )
        database.set_session_risk_profile(self.session_id, profile_id)
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 0.0)

    def test_max_trade_amount_caps_final_size(self):
        profile_id = database.create_risk_profile("Capped", sizing_mode="percent",
                                                    bankroll=10000, percent_of_bankroll=5.0, max_trade_amount=50)
        database.set_session_risk_profile(self.session_id, profile_id)
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 50)

    def test_martingale_steps_up_after_loss(self):
        profile_id = database.create_risk_profile("Martingale Test", sizing_mode="fixed", fixed_amount=10)
        database.update_martingale_settings(profile_id, enabled=True, max_steps=5, multiplier=2.0)
        database.set_session_risk_profile(self.session_id, profile_id)

        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 10)  # step 0
        database.advance_martingale_step(self.session_id)
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 20)  # step 1
        database.advance_martingale_step(self.session_id)
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 40)  # step 2

    def test_martingale_disabled_for_session_overrides_profile_setting(self):
        profile_id = database.create_risk_profile("Martingale Test", sizing_mode="fixed", fixed_amount=10)
        database.update_martingale_settings(profile_id, enabled=True, max_steps=5, multiplier=2.0)
        database.set_session_risk_profile(self.session_id, profile_id)
        database.advance_martingale_step(self.session_id)
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 20)  # stepped, martingale on

        database.set_session_martingale_disabled(self.session_id, True)
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 10)  # flat base amount now

    def test_martingale_resets_after_win_when_configured(self):
        profile_id = database.create_risk_profile("Martingale Reset", sizing_mode="fixed", fixed_amount=10)
        database.update_martingale_settings(profile_id, enabled=True, max_steps=5, multiplier=2.0, reset_after_win=True)
        database.set_session_risk_profile(self.session_id, profile_id)
        database.advance_martingale_step(self.session_id)
        database.advance_martingale_step(self.session_id)
        self.assertEqual(database.get_trading_session(self.session_id)["current_martingale_step"], 2)
        database.reset_martingale_step(self.session_id)
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 10)

    def test_martingale_custom_ladder_overrides_multiplier(self):
        profile_id = database.create_risk_profile("Ladder Test", sizing_mode="fixed", fixed_amount=10)
        database.update_martingale_settings(profile_id, enabled=True, max_steps=4,
                                             custom_ladder_json="[10, 22, 48, 105]")
        database.set_session_risk_profile(self.session_id, profile_id)
        database.advance_martingale_step(self.session_id)
        database.advance_martingale_step(self.session_id)
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 48)

    def test_martingale_max_total_exposure_caps_stepped_amount(self):
        profile_id = database.create_risk_profile("Exposure Cap", sizing_mode="fixed", fixed_amount=10)
        database.update_martingale_settings(profile_id, enabled=True, max_steps=10, multiplier=2.0,
                                             max_total_exposure=30)
        database.set_session_risk_profile(self.session_id, profile_id)
        database.advance_martingale_step(self.session_id)
        database.advance_martingale_step(self.session_id)  # would be 40 uncapped
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 30)

    def test_compounding_milestone_step_increases_percent(self):
        profile_id = database.create_risk_profile("Compounding Test", sizing_mode="percent",
                                                    bankroll=1000, percent_of_bankroll=2.0)
        database.update_compounding_settings(
            profile_id, mode="milestone_based",
            steps_json='[{"profit_threshold": 50, "risk_percent": 2.25}, {"profit_threshold": 100, "risk_percent": 2.5}]',
        )
        database.set_session_risk_profile(self.session_id, profile_id)
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 20.0)  # base 2%
        database.update_session_pnl(self.session_id, 60)  # crosses $50 threshold
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 22.5)  # 2.25% of 1000

    def test_compounding_drawdown_resets_to_base(self):
        profile_id = database.create_risk_profile("Drawdown Test", sizing_mode="percent",
                                                    bankroll=1000, percent_of_bankroll=2.0)
        database.update_compounding_settings(
            profile_id, mode="milestone_based", drawdown_reset_percent=8,
            steps_json='[{"profit_threshold": 50, "risk_percent": 3.0}]',
        )
        database.set_session_risk_profile(self.session_id, profile_id)
        database.update_session_pnl(self.session_id, 60)
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 30.0)  # stepped to 3%
        database.update_session_pnl(self.session_id, -150)  # net -90, -9% drawdown of 1000
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 20.0)  # back to base 2%

    def test_alternating_cycle_follows_the_real_trade_by_trade_pattern(self):
        # Money Management Studio's Alternating Compound: 2.5%, 5%, 2.5%,
        # 5%, repeating - keyed off the session's OWN trade count, never
        # an averaged approximation. Advances trades_count via
        # record_session_trade between reads, exactly like a real live
        # session would as each signal reaches execution.
        import json
        profile_id = database.create_risk_profile("Alternating Test", sizing_mode="percent",
                                                    bankroll=1000, percent_of_bankroll=2.5)
        database.update_compounding_settings(
            profile_id, mode="alternating_cycle", steps_json=json.dumps([2.5, 5.0, 2.5, 5.0]),
        )
        database.set_session_risk_profile(self.session_id, profile_id)

        expected_percents = [2.5, 5.0, 2.5, 5.0, 2.5]  # 5th trade wraps back to the start
        for expected_percent in expected_percents:
            amount = risk_engine.compute_position_size(self.session_id, 5.0)
            self.assertEqual(amount, 1000 * expected_percent / 100.0)
            database.record_session_trade(self.session_id)

    def test_alternating_cycle_ignores_session_pnl_entirely(self):
        # The whole point of this mode: unlike milestone_based, a losing
        # or winning session must never change which cycle step comes
        # next - only the trade count does.
        import json
        profile_id = database.create_risk_profile("Alternating PnL Test", sizing_mode="percent",
                                                    bankroll=1000, percent_of_bankroll=2.5)
        database.update_compounding_settings(
            profile_id, mode="alternating_cycle", steps_json=json.dumps([2.5, 5.0]),
        )
        database.set_session_risk_profile(self.session_id, profile_id)
        database.update_session_pnl(self.session_id, -400)  # a deep drawdown - should change nothing
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 25.0)  # still 2.5%
        database.record_session_trade(self.session_id)
        database.update_session_pnl(self.session_id, 900)  # a big win - should still change nothing
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 50.0)  # still 5%, trade 2


class MissingSettingsRowBackfillTests(unittest.TestCase):
    """A real production crash: risk_profile_id=29 predated the
    momentum_settings table (added well after the original martingale/
    compounding/vault trio), so get_risk_profile's momentum key came
    back None and compute_position_size's `momentum["enabled"]` raised
    TypeError - never caught before because every real trade until this
    project's first Fund-routed session had session_id=None, bypassing
    this function via its own early-return. initialize_database()'s
    backfill migration (core/database.py) is the real fix - simulates
    the "old profile, table added later" scenario directly by deleting
    a settings row after creation, then re-running the migration."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self.session_id = database.start_trading_session("Test", [1], "DEMO")

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_profile_missing_a_settings_row_does_not_crash_after_migration_reruns(self):
        profile_id = database.create_risk_profile("Old Profile", sizing_mode="fixed", fixed_amount=3.0)
        database.set_session_risk_profile(self.session_id, profile_id)

        conn = database.get_connection()
        conn.execute("DELETE FROM momentum_settings WHERE risk_profile_id = ?", (profile_id,))
        conn.commit()
        conn.close()
        self.assertIsNone(database.get_risk_profile(profile_id)["momentum"])

        database.initialize_database()  # the backfill migration, re-run (as it is on every real startup)

        self.assertIsNotNone(database.get_risk_profile(profile_id)["momentum"])
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 3.0)

    def test_profile_missing_a_settings_row_does_not_crash_even_without_the_migration(self):
        # Belt-and-suspenders check on risk_engine.py's own `or {"enabled": 0}`
        # guards, independent of whether the migration has run yet.
        profile_id = database.create_risk_profile("Old Profile", sizing_mode="fixed", fixed_amount=3.0)
        database.set_session_risk_profile(self.session_id, profile_id)

        conn = database.get_connection()
        conn.execute("DELETE FROM momentum_settings WHERE risk_profile_id = ?", (profile_id,))
        conn.commit()
        conn.close()

        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 3.0)


class FundAwareBankrollTests(unittest.TestCase):
    """Fixes docs/AXIM_LIVE_READINESS_CHECKLIST.md's 'risk-profile bankroll
    does not auto-update from real P&L' gap - WITHOUT mutating the shared
    risk_profiles.bankroll column (which can be attached to more than one
    Fund), by reading fund_manager.get_fund_balances as a live override
    instead. A session with no fund_id (pre-Fund-architecture sessions,
    or the existing tests above) is completely untouched - see
    ComputePositionSizeTests' setUp, which starts sessions with no
    fund_id and still passes unchanged."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_new_session_picks_up_prior_sessions_real_pnl(self):
        fund_id = database.create_fund("Fund A", starting_balance=1000)
        profile_id = database.create_risk_profile(
            "Percent Test", sizing_mode="percent", bankroll=1, percent_of_bankroll=2.0,  # static value deliberately wrong
        )
        first = database.start_trading_session("First", [1], "DEMO", fund_id=fund_id, risk_profile_id=profile_id)
        database.update_session_pnl(first, 500)  # fund is now worth 1500
        database.stop_trading_session(first, "stopped_manual")

        second = database.start_trading_session("Second", [1], "DEMO", fund_id=fund_id, risk_profile_id=profile_id)
        # 2% of the fund's real 1500, NOT 2% of the profile's stale static bankroll=1
        self.assertEqual(risk_engine.compute_position_size(second, 5.0), 30.0)

    def test_current_sessions_own_pnl_is_not_double_counted(self):
        fund_id = database.create_fund("Fund B", starting_balance=1000)
        profile_id = database.create_risk_profile("Dynamic Test", sizing_mode="dynamic",
                                                    bankroll=1, percent_of_bankroll=2.0)
        session_id = database.start_trading_session("S", [1], "DEMO", fund_id=fund_id, risk_profile_id=profile_id)
        database.update_session_pnl(session_id, 500)  # THIS session's own pnl - fund trading_balance already includes it
        # dynamic = 2% of (starting_balance + this session's pnl) = 2% of 1500 = 30, not 2% of 2000
        self.assertEqual(risk_engine.compute_position_size(session_id, 5.0), 30.0)

    def test_vaulted_amount_excluded_from_next_sessions_bankroll(self):
        fund_id = database.create_fund("Fund C", starting_balance=1000)
        profile_id = database.create_risk_profile("Percent Test", sizing_mode="percent",
                                                    bankroll=1, percent_of_bankroll=2.0)
        first = database.start_trading_session("First", [1], "DEMO", fund_id=fund_id, risk_profile_id=profile_id)
        database.update_session_pnl(first, 500)
        database.add_to_vault(first, 300)  # 300 of the 500 profit is protected, not tradeable
        database.stop_trading_session(first, "stopped_manual")

        second = database.start_trading_session("Second", [1], "DEMO", fund_id=fund_id, risk_profile_id=profile_id)
        # trading_balance = 1000 + 500 - 300 = 1200; 2% of 1200 = 24
        self.assertEqual(risk_engine.compute_position_size(second, 5.0), 24.0)

    def test_two_funds_sharing_one_profile_do_not_bleed_into_each_other(self):
        fund_a = database.create_fund("Fund A", starting_balance=1000)
        fund_b = database.create_fund("Fund B", starting_balance=1000)
        profile_id = database.create_risk_profile("Shared Profile", sizing_mode="percent",
                                                    bankroll=1, percent_of_bankroll=2.0)

        session_a = database.start_trading_session("A", [1], "DEMO", fund_id=fund_a, risk_profile_id=profile_id)
        database.update_session_pnl(session_a, 1000)  # Fund A doubled to 2000
        database.stop_trading_session(session_a, "stopped_manual")

        # Fund B never traded - its own session should size off ITS OWN
        # 1000, not Fund A's 2000, even though they share a risk_profile.
        session_b = database.start_trading_session("B", [1], "DEMO", fund_id=fund_b, risk_profile_id=profile_id)
        self.assertEqual(risk_engine.compute_position_size(session_b, 5.0), 20.0)  # 2% of 1000, not 2% of 2000

    def test_real_concurrent_sizing_never_cross_contaminates_two_funds(self):
        """Phase 2 Priority #3 ("Trades MUST size from the provider's
        virtual allocation... while sharing one broker account"): the
        two tests above prove isolation under SEQUENTIAL calls - this
        proves it under real OS-thread concurrency (ThreadPoolExecutor,
        not just asyncio interleaving a single thread), the actual shape
        of two providers' signals arriving at nearly the same moment.
        Each fund's own P&L is updated between every sizing call
        specifically so a real cross-contamination bug (e.g. a shared/
        cached bankroll value) would show up as a wrong position size,
        not just a wrong final balance caught after the fact.

        Uses two DIFFERENT broker accounts, not one shared one - a real,
        separate finding surfaced while writing this test (see
        test_two_funds_on_the_same_broker_account_cannot_both_have_an_
        active_session_today below) is that database.start_trading_session's
        exclusivity check is scoped to broker_account_id, not fund_id or
        channel: two Funds sharing the SAME broker account cannot
        currently both have an active session at once at all, so
        "concurrent" for the ONE-shared-account case can't be
        constructed via the normal session-start path today. This test
        instead verifies what it CAN today: that the underlying
        risk-engine/database layer itself has no shared-state bug under
        true concurrent execution once two sessions ARE both active
        (today, that requires two broker accounts) - a necessary but not
        sufficient condition for the full one-account vision."""
        broker_a = database.create_broker_account("Broker A", mode="demo")
        broker_b = database.create_broker_account("Broker B", mode="demo")
        fund_a = database.create_fund("Concurrent Fund A", starting_balance=1000)
        fund_b = database.create_fund("Concurrent Fund B", starting_balance=2000)
        profile_id = database.create_risk_profile(
            "Shared Concurrent Profile", sizing_mode="percent", bankroll=1, percent_of_bankroll=10.0,
        )
        session_a = database.start_trading_session(
            "A", [1], "DEMO", fund_id=fund_a, risk_profile_id=profile_id, broker_account_id=broker_a,
        )
        session_b = database.start_trading_session(
            "B", [2], "DEMO", fund_id=fund_b, risk_profile_id=profile_id, broker_account_id=broker_b,
        )

        # DB_FILE is a module-level global swapped in setUp - each worker
        # thread must see the SAME test database, not the real one.
        db_file = database.DB_FILE
        delta_a, delta_b = 10, 100  # deliberately different scales/signs of drift

        def trade_fund_a():
            database.DB_FILE = db_file
            database.update_session_pnl(session_a, delta_a)  # increments, does not set
            return risk_engine.compute_position_size(session_a, 5.0)

        def trade_fund_b():
            database.DB_FILE = db_file
            database.update_session_pnl(session_b, delta_b)
            return risk_engine.compute_position_size(session_b, 5.0)

        # Interleaved real-thread execution: 50 rounds, each round fires
        # both funds' sizing calls into the thread pool at once.
        #
        # Expected size per fund stays FLAT at starting_balance * 10% for
        # every round, not growing with the accumulating P&L - a real,
        # separate finding while writing this test:
        # risk_engine.compute_position_size deliberately computes
        # fund_balances["trading_balance"] - session["realized_pnl"],
        # cancelling the CURRENT session's own still-live P&L back out
        # (see test_current_sessions_own_pnl_is_not_double_counted) - so
        # sizing is stable within one active session by design, only
        # moving between sessions. The real cross-contamination risk this
        # test guards against is size_a ever reading FUND B's balance (or
        # vice versa) under true concurrent execution, not size drifting
        # with its own fund's mid-session P&L.
        with ThreadPoolExecutor(max_workers=4) as pool:
            for round_num in range(50):
                future_a = pool.submit(trade_fund_a)
                future_b = pool.submit(trade_fund_b)
                size_a = future_a.result()
                size_b = future_b.result()
                self.assertAlmostEqual(size_a, 100.0, places=2,
                                        msg=f"round {round_num}: Fund A sized off the wrong balance")
                self.assertAlmostEqual(size_b, 200.0, places=2,
                                        msg=f"round {round_num}: Fund B sized off the wrong balance")

        # Final balances confirm each fund's OWN accumulated P&L landed
        # in the right place, with no cross-contamination between them.
        balances_a = fund_manager.get_fund_balances(fund_a)
        balances_b = fund_manager.get_fund_balances(fund_b)
        self.assertEqual(balances_a["trading_balance"], 1000 + 50 * delta_a)
        self.assertEqual(balances_b["trading_balance"], 2000 + 50 * delta_b)

    def test_two_funds_on_the_same_broker_account_cannot_both_have_an_active_session_today(self):
        """Characterizes a real, load-bearing gap found while building
        the test above: Phase 2 Priority #3's vision is explicitly ONE
        broker account virtually divided across multiple providers, each
        with its own concurrently-running Fund/strategy - but
        database.start_trading_session's exclusivity check
        (get_active_trading_session_for_broker_account) is scoped per
        broker_account_id, not per fund_id or per channel. Two Funds
        sharing the SAME broker account cannot both have an active
        session right now, full stop - the second start_trading_session
        call raises before a second session row is even created, so
        fund-scoped routing (core/broker_account_manager.route_signal
        requires an active session_id to resolve a Fund at all) can only
        ever apply to ONE of them at a time today.

        This is NOT fixed here: relaxing trading_sessions' exclusivity
        model is a change to core, safety-critical trading-session
        machinery (heartbeat monitoring, recovery.py's resume-on-restart
        logic, and every "at most one session" assumption elsewhere would
        all need a fresh audit) - exactly the "fundamentally change
        existing production behavior" category flagged for a deliberate
        decision, not a quick patch. Recorded as a real, current
        limitation so it isn't silently lost, and as a regression guard:
        if this test ever starts failing, the underlying constraint has
        already changed and this comment (and the Priority #3 status)
        needs updating alongside it."""
        broker_id = database.create_broker_account("Shared Broker", mode="demo")
        fund_a = database.create_fund("Shared Fund A", starting_balance=1000)
        fund_b = database.create_fund("Shared Fund B", starting_balance=2000)
        profile_id = database.create_risk_profile("Shared Profile", sizing_mode="percent", bankroll=1, percent_of_bankroll=5.0)

        database.start_trading_session(
            "A", [1], "DEMO", fund_id=fund_a, risk_profile_id=profile_id, broker_account_id=broker_id,
        )
        with self.assertRaises(ValueError):
            database.start_trading_session(
                "B", [2], "DEMO", fund_id=fund_b, risk_profile_id=profile_id, broker_account_id=broker_id,
            )

    def test_no_fund_id_falls_back_to_static_profile_bankroll(self):
        # A session with no Fund attached (pre-Fund-architecture, or a
        # profile-only test session) must behave exactly as before -
        # same assertion ComputePositionSizeTests already covers, kept
        # here too as an explicit regression guard for this specific fix.
        profile_id = database.create_risk_profile("Percent Test", sizing_mode="percent",
                                                    bankroll=1000, percent_of_bankroll=2.0)
        session_id = database.start_trading_session("No Fund", [1], "DEMO", risk_profile_id=profile_id)
        self.assertEqual(risk_engine.compute_position_size(session_id, 5.0), 20.0)

    def test_apex_ascension_uses_fund_aware_bankroll(self):
        # Confirms the override reaches every sizing mode, not just
        # percent/dynamic - Apex Ascension's tier lookup also reads
        # profile["bankroll"] internally.
        fund_id = database.create_fund("Fund D", starting_balance=1000)
        profile_id = database.create_risk_profile("Apex Test", sizing_mode="apex_ascension", bankroll=1)
        database.update_apex_ascension_settings(
            profile_id, enabled=1, starting_bankroll=1000, starting_unit_value=10,
            standard_units=5, first_reset_threshold=2500, reset_increment=1000,
        )
        first = database.start_trading_session("First", [1], "DEMO", fund_id=fund_id, risk_profile_id=profile_id)
        database.update_session_pnl(first, 2000)  # fund now worth 3000 -> should be past the first tier
        database.stop_trading_session(first, "stopped_manual")

        second = database.start_trading_session("Second", [1], "DEMO", fund_id=fund_id, risk_profile_id=profile_id)
        amount = risk_engine.compute_position_size(second, 5.0)
        self.assertGreater(amount, 50)  # tier-1 standard deployment (5 units * $10) would be exactly 50


class MartingaleProjectionTests(unittest.TestCase):
    def test_project_exposure_with_multiplier(self):
        martingale = {"enabled": True, "max_steps": 4, "multiplier": 2.0,
                      "custom_ladder_json": None, "max_total_exposure": 0}
        result = risk_engine.project_martingale_exposure(martingale, 10)
        self.assertEqual(result["steps"], [10, 20, 40, 80])
        self.assertEqual(result["total_exposure"], 150)

    def test_project_exposure_disabled_returns_empty(self):
        result = risk_engine.project_martingale_exposure({"enabled": False}, 10)
        self.assertEqual(result["steps"], [])
        self.assertEqual(result["total_exposure"], 0)

    def test_project_exposure_respects_cap(self):
        martingale = {"enabled": True, "max_steps": 3, "multiplier": 2.0,
                      "custom_ladder_json": None, "max_total_exposure": 25}
        result = risk_engine.project_martingale_exposure(martingale, 10)
        self.assertEqual(result["steps"], [10, 20, 25])


class VaultTriggerTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self.session_id = database.start_trading_session("Vault Test", [1], "DEMO")

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_every_winning_session_vaults_on_session_end(self):
        profile_id = database.create_risk_profile("Vault Profile", sizing_mode="fixed", fixed_amount=10)
        database.update_profit_vault_settings(profile_id, enabled=True, vault_percent=20,
                                               trigger_event="every_winning_session")
        database.set_session_risk_profile(self.session_id, profile_id)
        database.update_session_pnl(self.session_id, 100)
        risk_engine.on_session_ended(self.session_id)
        self.assertEqual(database.get_trading_session(self.session_id)["vaulted_amount"], 20.0)

    def test_no_vault_on_losing_session(self):
        profile_id = database.create_risk_profile("Vault Profile", sizing_mode="fixed", fixed_amount=10)
        database.update_profit_vault_settings(profile_id, enabled=True, vault_percent=20,
                                               trigger_event="every_winning_session")
        database.set_session_risk_profile(self.session_id, profile_id)
        database.update_session_pnl(self.session_id, -50)
        risk_engine.on_session_ended(self.session_id)
        self.assertEqual(database.get_trading_session(self.session_id)["vaulted_amount"], 0.0)

    def test_milestone_based_vault_skims_at_each_milestone(self):
        profile_id = database.create_risk_profile("Milestone Vault", sizing_mode="fixed", fixed_amount=10)
        database.update_profit_vault_settings(profile_id, enabled=True, vault_percent=10,
                                               trigger_event="milestone_based", milestone_amount=50)
        database.set_session_risk_profile(self.session_id, profile_id)
        database.update_session_pnl(self.session_id, 60)  # crosses one $50 milestone
        risk_engine.on_trade_closed(self.session_id, won=True, profit_loss=60)
        self.assertEqual(database.get_trading_session(self.session_id)["vaulted_amount"], 5.0)

        database.update_session_pnl(self.session_id, 50)  # now at 110, crosses second milestone
        risk_engine.on_trade_closed(self.session_id, won=True, profit_loss=50)
        self.assertEqual(database.get_trading_session(self.session_id)["vaulted_amount"], 10.0)


class ApexAscensionSizingTests(unittest.TestCase):
    """AXIM Capital Strategies (tm) - Apex Ascension wired into
    compute_position_size as a real sizing_mode, not just the standalone
    demo simulator."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self.session_id = database.start_trading_session("Test", [1], "DEMO")

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_disabled_falls_back_to_fixed_amount(self):
        profile_id = database.create_risk_profile(
            "Apex Test", sizing_mode="apex_ascension", bankroll=1000, fixed_amount=3,
        )
        database.set_session_risk_profile(self.session_id, profile_id)
        # apex_ascension_settings.enabled defaults to 0 - matches every
        # other Capital Strategies sub-table's default-off convention.
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 3.0)

    def test_enabled_uses_real_tier_deployment_at_starting_bankroll(self):
        profile_id = database.create_risk_profile(
            "Apex Test", sizing_mode="apex_ascension", bankroll=1000, fixed_amount=3,
        )
        database.update_apex_ascension_settings(profile_id, enabled=True, starting_bankroll=1000)
        database.set_session_risk_profile(self.session_id, profile_id)
        # $1,000 bankroll, no session P&L yet -> tier 0, $10 unit x 5 = $50
        # standard deployment, exactly the spec's own worked example.
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 50.0)

    def test_enabled_recalculates_against_current_bankroll(self):
        profile_id = database.create_risk_profile(
            "Apex Test", sizing_mode="apex_ascension", bankroll=1000, fixed_amount=3,
        )
        database.update_apex_ascension_settings(profile_id, enabled=True, starting_bankroll=1000)
        database.set_session_risk_profile(self.session_id, profile_id)
        database.update_session_pnl(self.session_id, 1500)  # bankroll now 2500 -> tier 1
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 100.0)

    def test_reaching_a_new_tier_records_an_audit_event(self):
        profile_id = database.create_risk_profile(
            "Apex Test", sizing_mode="apex_ascension", bankroll=1000, fixed_amount=3,
        )
        database.update_apex_ascension_settings(profile_id, enabled=True, starting_bankroll=1000)
        database.set_session_risk_profile(self.session_id, profile_id)
        database.update_session_pnl(self.session_id, 1500)  # crosses into tier 1
        risk_engine.compute_position_size(self.session_id, 5.0)
        events = database.list_tier_events(profile_id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["tier_index"], 1)
        self.assertEqual(database.get_apex_ascension_settings(profile_id)["highest_tier_reached"], 1)

    def test_repeated_calls_at_the_same_tier_do_not_duplicate_events(self):
        profile_id = database.create_risk_profile(
            "Apex Test", sizing_mode="apex_ascension", bankroll=1000, fixed_amount=3,
        )
        database.update_apex_ascension_settings(profile_id, enabled=True, starting_bankroll=1000)
        database.set_session_risk_profile(self.session_id, profile_id)
        database.update_session_pnl(self.session_id, 1500)
        risk_engine.compute_position_size(self.session_id, 5.0)
        risk_engine.compute_position_size(self.session_id, 5.0)
        risk_engine.compute_position_size(self.session_id, 5.0)
        self.assertEqual(len(database.list_tier_events(profile_id)), 1)


class CashflowSentinelSizingTests(unittest.TestCase):
    """AXIM Capital Strategies (tm) - Cashflow/Sentinel opt-in
    post-processing layers, both default-disabled."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self.session_id = database.start_trading_session("Test", [1], "DEMO")

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_cashflow_disabled_by_default_no_behavior_change(self):
        profile_id = database.create_risk_profile("Plain Fixed", sizing_mode="fixed", fixed_amount=10)
        database.set_session_risk_profile(self.session_id, profile_id)
        database.update_session_pnl(self.session_id, 1000)  # would exceed any real target
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 10.0)

    def test_cashflow_target_reached_raises_and_is_rejected_cleanly(self):
        profile_id = database.create_risk_profile("Cashflow Test", sizing_mode="fixed", fixed_amount=10)
        database.update_cashflow_settings(profile_id, enabled=True, target_amount=50)
        database.set_session_risk_profile(self.session_id, profile_id)
        database.update_session_pnl(self.session_id, 60)
        with self.assertRaises(risk_engine.CashflowTargetReached) as ctx:
            risk_engine.compute_position_size(self.session_id, 5.0)
        self.assertEqual(ctx.exception.rule, "cashflow_target_reached")

    def test_cashflow_partial_target_reduces_size(self):
        profile_id = database.create_risk_profile("Cashflow Test", sizing_mode="fixed", fixed_amount=10)
        database.update_cashflow_settings(
            profile_id, enabled=True, target_amount=100,
            partial_target_percent=75, partial_reduction_percent=50,
        )
        database.set_session_risk_profile(self.session_id, profile_id)
        database.update_session_pnl(self.session_id, 80)  # past 75% of target
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 5.0)

    def test_sentinel_disabled_by_default_no_behavior_change(self):
        profile_id = database.create_risk_profile(
            "Plain Fixed", sizing_mode="fixed", fixed_amount=10, bankroll=1000,
        )
        database.set_session_risk_profile(self.session_id, profile_id)
        database.update_session_pnl(self.session_id, -500)  # deep drawdown
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 10.0)

    def test_sentinel_reduces_size_in_a_drawdown_band(self):
        profile_id = database.create_risk_profile(
            "Sentinel Test", sizing_mode="fixed", fixed_amount=10, bankroll=1000,
        )
        database.update_drawdown_protection_settings(profile_id, enabled=True)
        database.set_session_risk_profile(self.session_id, profile_id)
        database.update_session_pnl(self.session_id, -70)  # -7% drawdown -> 5-10% band, reduce 25%
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 7.5)

    def test_sentinel_suspends_above_threshold_and_is_rejected_cleanly(self):
        profile_id = database.create_risk_profile(
            "Sentinel Test", sizing_mode="fixed", fixed_amount=10, bankroll=1000,
        )
        database.update_drawdown_protection_settings(profile_id, enabled=True, suspend_above_percent=20)
        database.set_session_risk_profile(self.session_id, profile_id)
        database.update_session_pnl(self.session_id, -250)  # -25% drawdown
        with self.assertRaises(risk_engine.SentinelSuspended) as ctx:
            risk_engine.compute_position_size(self.session_id, 5.0)
        self.assertEqual(ctx.exception.rule, "sentinel_suspended")


class MomentumSizingTests(unittest.TestCase):
    """AXIM Capital Strategies (tm) Phase 2 - Momentum wired into both
    compute_position_size (sizing) and on_trade_closed (step advance)."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self.session_id = database.start_trading_session("Test", [1], "DEMO")

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_disabled_by_default_no_behavior_change(self):
        profile_id = database.create_risk_profile("Plain Fixed", sizing_mode="fixed", fixed_amount=10)
        database.set_session_risk_profile(self.session_id, profile_id)
        database.advance_martingale_step(self.session_id)  # unrelated step shouldn't matter either
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 10.0)

    def test_win_advances_step_and_increases_next_size(self):
        profile_id = database.create_risk_profile("Momentum Test", sizing_mode="fixed", fixed_amount=10)
        database.update_momentum_settings(profile_id, enabled=True, multiplier=1.5)
        database.set_session_risk_profile(self.session_id, profile_id)
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 10.0)  # step 0
        risk_engine.on_trade_closed(self.session_id, won=True, profit_loss=10)
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 15.0)  # step 1

    def test_loss_resets_step_to_zero(self):
        profile_id = database.create_risk_profile("Momentum Test", sizing_mode="fixed", fixed_amount=10)
        database.update_momentum_settings(profile_id, enabled=True, multiplier=1.5)
        database.set_session_risk_profile(self.session_id, profile_id)
        risk_engine.on_trade_closed(self.session_id, won=True, profit_loss=10)
        risk_engine.on_trade_closed(self.session_id, won=True, profit_loss=15)
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 22.5)  # step 2
        risk_engine.on_trade_closed(self.session_id, won=False, profit_loss=-22.5)
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 10.0)  # back to step 0


class FortressSizingTests(unittest.TestCase):
    """AXIM Capital Strategies (tm) Phase 2 - Fortress wired into
    compute_position_size, including the persisted protected_principal
    write-back."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self.session_id = database.start_trading_session("Test", [1], "DEMO")

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_disabled_by_default_no_behavior_change(self):
        profile_id = database.create_risk_profile("Plain Fixed", sizing_mode="fixed", fixed_amount=10, bankroll=1000)
        database.set_session_risk_profile(self.session_id, profile_id)
        database.update_session_pnl(self.session_id, 5000)  # would trigger any real threshold
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 10.0)

    def test_crossing_threshold_persists_protected_principal(self):
        profile_id = database.create_risk_profile("Fortress Test", sizing_mode="fixed", fixed_amount=10, bankroll=1000)
        database.update_fortress_settings(profile_id, enabled=True, protection_threshold=500)
        database.set_session_risk_profile(self.session_id, profile_id)
        database.update_session_pnl(self.session_id, 600)
        risk_engine.compute_position_size(self.session_id, 5.0)
        self.assertEqual(database.get_fortress_settings(profile_id)["protected_principal"], 1000)

    def test_stops_trading_once_profit_is_gone(self):
        profile_id = database.create_risk_profile("Fortress Test", sizing_mode="fixed", fixed_amount=10, bankroll=1000)
        database.update_fortress_settings(profile_id, enabled=True, protected_principal=1000)
        database.set_session_risk_profile(self.session_id, profile_id)
        database.update_session_pnl(self.session_id, 0)  # exactly back to protected principal
        with self.assertRaises(risk_engine.FortressPrincipalProtected) as ctx:
            risk_engine.compute_position_size(self.session_id, 5.0)
        self.assertEqual(ctx.exception.rule, "fortress_principal_protected")


class EmpireSizingTests(unittest.TestCase):
    """AXIM Capital Strategies (tm) Phase 2 - Empire as a real sizing_mode,
    with level advancement wired into on_trade_closed."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self.session_id = database.start_trading_session("Test", [1], "DEMO")

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_disabled_falls_back_to_fixed_amount(self):
        profile_id = database.create_risk_profile("Empire Test", sizing_mode="empire", fixed_amount=3)
        database.set_session_risk_profile(self.session_id, profile_id)
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 3.0)

    def test_enabled_starts_at_level_zero_stake(self):
        profile_id = database.create_risk_profile("Empire Test", sizing_mode="empire", fixed_amount=3)
        database.update_empire_settings(
            profile_id, enabled=True, starting_amount=10, target_amount=100, num_levels=5,
        )
        database.set_session_risk_profile(self.session_id, profile_id)
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 10.0)

    def test_win_advances_to_the_next_level_stake(self):
        profile_id = database.create_risk_profile("Empire Test", sizing_mode="empire", fixed_amount=3)
        database.update_empire_settings(
            profile_id, enabled=True, starting_amount=10, target_amount=100, num_levels=5,
        )
        database.set_session_risk_profile(self.session_id, profile_id)
        risk_engine.on_trade_closed(self.session_id, won=True, profit_loss=10)
        from capital_strategies import empire_generate_ladder
        ladder = empire_generate_ladder(10, 100, 5)
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), ladder[1])

    def test_loss_resets_to_start_by_default(self):
        profile_id = database.create_risk_profile("Empire Test", sizing_mode="empire", fixed_amount=3)
        database.update_empire_settings(
            profile_id, enabled=True, starting_amount=10, target_amount=100, num_levels=5,
        )
        database.set_session_risk_profile(self.session_id, profile_id)
        risk_engine.on_trade_closed(self.session_id, won=True, profit_loss=10)
        risk_engine.on_trade_closed(self.session_id, won=False, profit_loss=-15)
        self.assertEqual(risk_engine.compute_position_size(self.session_id, 5.0), 10.0)

    def test_reaching_the_final_level_raises_challenge_complete(self):
        profile_id = database.create_risk_profile("Empire Test", sizing_mode="empire", fixed_amount=3)
        database.update_empire_settings(
            profile_id, enabled=True, starting_amount=10, target_amount=100,
            num_levels=2, current_level=1,  # already at the final level
        )
        database.set_session_risk_profile(self.session_id, profile_id)
        with self.assertRaises(risk_engine.EmpireChallengeOver) as ctx:
            risk_engine.compute_position_size(self.session_id, 5.0)
        self.assertEqual(ctx.exception.rule, "empire_challenge_complete")


class PerTradeVaultTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self.session_id = database.start_trading_session("Test", [1], "DEMO")

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_per_trade_trigger_vaults_immediately_on_a_winning_trade(self):
        profile_id = database.create_risk_profile("Per-Trade Vault", sizing_mode="fixed", fixed_amount=10)
        database.update_profit_vault_settings(profile_id, enabled=True, vault_percent=20, trigger_event="per_trade")
        database.set_session_risk_profile(self.session_id, profile_id)
        database.update_session_pnl(self.session_id, 50)
        risk_engine.on_trade_closed(self.session_id, won=True, profit_loss=50)
        self.assertEqual(database.get_trading_session(self.session_id)["vaulted_amount"], 10.0)

    def test_per_trade_trigger_skips_losing_trades(self):
        profile_id = database.create_risk_profile("Per-Trade Vault", sizing_mode="fixed", fixed_amount=10)
        database.update_profit_vault_settings(profile_id, enabled=True, vault_percent=20, trigger_event="per_trade")
        database.set_session_risk_profile(self.session_id, profile_id)
        database.update_session_pnl(self.session_id, -20)
        risk_engine.on_trade_closed(self.session_id, won=False, profit_loss=-20)
        self.assertEqual(database.get_trading_session(self.session_id)["vaulted_amount"], 0.0)


if __name__ == "__main__":
    unittest.main()
