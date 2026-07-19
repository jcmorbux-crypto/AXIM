import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))

import database


class ClaimBrokerAccountConnectingTests(unittest.TestCase):
    """api/broker_accounts_routes.py's POST /{account_id}/connect used to
    check connection_status != "connecting" (a SELECT) and only later,
    in a separate DB connection, write connection_status = "connecting" -
    with a real subprocess.Popen() spawning a browser login in between.
    Two near-simultaneous /connect calls for the same account (a
    double-click on "Connect", or a frontend retry after a slow
    response - ordinary operator UI use) could both pass the check and
    both spawn scripts/connect_broker_account.py against the same Chrome
    profile directory. claim_broker_account_connecting makes the claim
    itself atomic."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self.account_id = database.create_broker_account("Acct A")

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_first_claim_succeeds_and_sets_connecting(self):
        self.assertTrue(database.claim_broker_account_connecting(self.account_id))
        self.assertEqual(database.get_broker_account(self.account_id)["connection_status"], "connecting")

    def test_second_claim_while_already_connecting_fails(self):
        database.claim_broker_account_connecting(self.account_id)
        self.assertFalse(database.claim_broker_account_connecting(self.account_id))

    def test_claim_succeeds_again_after_disconnecting(self):
        database.claim_broker_account_connecting(self.account_id)
        database.update_broker_account(self.account_id, connection_status="disconnected")
        self.assertTrue(database.claim_broker_account_connecting(self.account_id))

    def test_concurrent_claims_on_the_same_account_only_one_wins(self):
        import threading
        results = []

        def attempt():
            results.append(database.claim_broker_account_connecting(self.account_id))

        threads = [threading.Thread(target=attempt) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), 9)
        self.assertEqual(database.get_broker_account(self.account_id)["connection_status"], "connecting")


class FinalizeBrokerAccountConnectionTests(unittest.TestCase):
    """scripts/connect_broker_account.py used to write its final outcome
    ("connected"/"error") unconditionally - an operator clicking
    Disconnect while a login attempt is still running (nothing kills the
    script; it keeps polling in the background) would have that explicit
    disconnect silently overwritten once the script later finished.
    finalize_broker_account_connection only writes if the account is
    still in the exact "connecting" state the script itself started."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self.account_id = database.create_broker_account("Acct A")
        database.claim_broker_account_connecting(self.account_id)

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_finalizes_normally_when_still_connecting(self):
        wrote = database.finalize_broker_account_connection(self.account_id, "connected")
        self.assertTrue(wrote)
        self.assertEqual(database.get_broker_account(self.account_id)["connection_status"], "connected")

    def test_does_not_overwrite_a_disconnect_that_happened_mid_login(self):
        database.update_broker_account(self.account_id, connection_status="disconnected")
        wrote = database.finalize_broker_account_connection(self.account_id, "connected")
        self.assertFalse(wrote)
        self.assertEqual(database.get_broker_account(self.account_id)["connection_status"], "disconnected")

    def test_error_outcome_also_respects_a_mid_login_disconnect(self):
        database.update_broker_account(self.account_id, connection_status="disconnected")
        wrote = database.finalize_broker_account_connection(self.account_id, "error")
        self.assertFalse(wrote)
        self.assertEqual(database.get_broker_account(self.account_id)["connection_status"], "disconnected")

    def test_extra_fields_are_written_alongside_the_status(self):
        database.finalize_broker_account_connection(
            self.account_id, "connected", last_connected_at="2026-01-01T00:00:00")
        account = database.get_broker_account(self.account_id)
        self.assertEqual(account["connection_status"], "connected")
        self.assertEqual(account["last_connected_at"], "2026-01-01T00:00:00")

    def test_error_outcome_records_the_real_reason(self):
        database.finalize_broker_account_connection(
            self.account_id, "error", last_error="TimeoutError: login not detected",
            last_error_at="2026-01-01T00:00:00",
        )
        account = database.get_broker_account(self.account_id)
        self.assertEqual(account["connection_status"], "error")
        self.assertEqual(account["last_error"], "TimeoutError: login not detected")
        self.assertEqual(account["last_error_at"], "2026-01-01T00:00:00")

    def test_successful_connect_clears_a_stale_error(self):
        database.finalize_broker_account_connection(
            self.account_id, "error", last_error="some earlier failure", last_error_at="2026-01-01T00:00:00")
        database.claim_broker_account_connecting(self.account_id)
        database.finalize_broker_account_connection(
            self.account_id, "connected", last_connected_at="2026-01-02T00:00:00",
            last_error=None, last_error_at=None,
        )
        account = database.get_broker_account(self.account_id)
        self.assertEqual(account["connection_status"], "connected")
        self.assertIsNone(account["last_error"])


if __name__ == "__main__":
    unittest.main()
