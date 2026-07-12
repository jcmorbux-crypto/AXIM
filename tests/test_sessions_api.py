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


if __name__ == "__main__":
    unittest.main()
