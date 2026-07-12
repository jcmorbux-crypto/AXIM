import sys
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))
sys.path.insert(0, str(PROJECT_ROOT / "api"))

import database
import sessions
from trade_lifecycle import TradeStatus


class VaultTransferTests(unittest.TestCase):
    """sessions.vault_transfer takes no auth-dependent branches - user is
    only there for FastAPI's Depends(require_admin) gate - so it's safe
    to call directly as a plain function, same direct-call style already
    used for core/rule_engine.py's action executors."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_moves_exactly_the_unvaulted_profit(self):
        session_id = database.start_trading_session("Test", [1], "DEMO")
        database.update_session_pnl(session_id, 80)
        result = sessions.vault_transfer(session_id, sessions.VaultTransfer(amount=80))
        self.assertEqual(result["vaulted_amount"], 80)

    def test_rejects_amount_exceeding_unvaulted_profit(self):
        # Without this, an operator could vault more than the session
        # ever actually earned, driving fund_manager.get_fund_balances'
        # trading_balance negative for the whole Fund - the same bound
        # core/rule_engine.py's automated move_profit_to_vault action
        # already enforces on its own side.
        session_id = database.start_trading_session("Test", [1], "DEMO")
        database.update_session_pnl(session_id, 80)
        with self.assertRaises(HTTPException) as ctx:
            sessions.vault_transfer(session_id, sessions.VaultTransfer(amount=500))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("exceeds unvaulted profit", ctx.exception.detail)
        self.assertEqual(database.get_trading_session(session_id)["vaulted_amount"], 0)

    def test_rejects_amount_exceeding_remaining_unvaulted_after_a_prior_transfer(self):
        session_id = database.start_trading_session("Test", [1], "DEMO")
        database.update_session_pnl(session_id, 80)
        sessions.vault_transfer(session_id, sessions.VaultTransfer(amount=50))
        with self.assertRaises(HTTPException) as ctx:
            sessions.vault_transfer(session_id, sessions.VaultTransfer(amount=31))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(database.get_trading_session(session_id)["vaulted_amount"], 50)

    def test_rejects_nonpositive_amount(self):
        session_id = database.start_trading_session("Test", [1], "DEMO")
        with self.assertRaises(HTTPException) as ctx:
            sessions.vault_transfer(session_id, sessions.VaultTransfer(amount=0))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_rejects_unknown_session(self):
        with self.assertRaises(HTTPException) as ctx:
            sessions.vault_transfer(99999, sessions.VaultTransfer(amount=10))
        self.assertEqual(ctx.exception.status_code, 404)


class WithProgressTests(unittest.TestCase):
    """sessions._with_progress feeds the Trading Sessions UI's "remaining
    to loss limit" display - it must match what core/session_manager.py's
    check_session_limits will actually enforce on the next signal, or an
    operator sees reassuring headroom that doesn't explain a rejection
    they're about to hit."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_remaining_to_loss_limit_with_no_pending_exposure(self):
        session_id = database.start_trading_session("Test", [1], "DEMO", loss_limit=20)
        database.update_session_pnl(session_id, -5)
        result = sessions._with_progress(database.get_trading_session(session_id))
        self.assertEqual(result["remaining_to_loss_limit"], 15)

    def test_remaining_to_loss_limit_nets_out_pending_stake(self):
        session_id = database.start_trading_session("Test", [1], "DEMO", loss_limit=20)
        database.update_session_pnl(session_id, -5)
        signal = {"asset": "EUR/USD OTC", "direction": "BUY", "expiry": "1 Minute", "raw_message": "test"}
        trade_id = database.record_signal_received(signal, session_id=session_id)
        database.update_trade_status(trade_id, TradeStatus.TRADE_CLICKED, trade_amount=6)
        result = sessions._with_progress(database.get_trading_session(session_id))
        self.assertEqual(result["remaining_to_loss_limit"], 9)  # 20 - 5 - 6

    def test_remaining_to_loss_limit_floors_at_zero_not_negative(self):
        session_id = database.start_trading_session("Test", [1], "DEMO", loss_limit=20)
        database.update_session_pnl(session_id, -5)
        signal = {"asset": "EUR/USD OTC", "direction": "BUY", "expiry": "1 Minute", "raw_message": "test"}
        trade_id = database.record_signal_received(signal, session_id=session_id)
        database.update_trade_status(trade_id, TradeStatus.TRADE_CLICKED, trade_amount=100)
        result = sessions._with_progress(database.get_trading_session(session_id))
        self.assertEqual(result["remaining_to_loss_limit"], 0)

    def test_remaining_to_loss_limit_none_when_disabled(self):
        session_id = database.start_trading_session("Test", [1], "DEMO", loss_limit=0)
        result = sessions._with_progress(database.get_trading_session(session_id))
        self.assertIsNone(result["remaining_to_loss_limit"])


if __name__ == "__main__":
    unittest.main()
