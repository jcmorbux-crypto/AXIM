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


if __name__ == "__main__":
    unittest.main()
