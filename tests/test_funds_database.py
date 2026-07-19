import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))

import database


class FundsDbTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()


class FundCrudTests(FundsDbTestCase):
    def test_create_get(self):
        fund_id = database.create_fund("Demo Fund", starting_balance=1000, assigned_broker_label="Pocket Option Demo")
        fund = database.get_fund(fund_id)
        self.assertEqual(fund["name"], "Demo Fund")
        self.assertEqual(fund["starting_balance"], 1000)
        self.assertEqual(fund["status"], "active")

    def test_create_rejects_unknown_field(self):
        with self.assertRaises(ValueError):
            database.create_fund("F", not_a_real_field=1)

    def test_create_rejects_invalid_status(self):
        with self.assertRaises(ValueError):
            database.create_fund("F", status="deleted")

    def test_status_not_duplicated_in_insert(self):
        # regression test: passing status as a kwarg must not double it
        # up in the INSERT column list
        fund_id = database.create_fund("F", status="paused")
        self.assertEqual(database.get_fund(fund_id)["status"], "paused")

    def test_list_funds_filters_by_status(self):
        database.create_fund("Active Fund", status="active")
        database.create_fund("Paused Fund", status="paused")
        self.assertEqual(len(database.list_funds()), 2)
        self.assertEqual(len(database.list_funds(status="active")), 1)

    def test_update_fund(self):
        fund_id = database.create_fund("F")
        database.update_fund(fund_id, profit_target=500, loss_limit=100)
        fund = database.get_fund(fund_id)
        self.assertEqual(fund["profit_target"], 500)
        self.assertEqual(fund["loss_limit"], 100)

    def test_update_rejects_unknown_field(self):
        fund_id = database.create_fund("F")
        with self.assertRaises(ValueError):
            database.update_fund(fund_id, made_up=1)

    def test_archiving_sets_archived_at(self):
        fund_id = database.create_fund("F")
        self.assertIsNone(database.get_fund(fund_id)["archived_at"])
        database.update_fund(fund_id, status="archived")
        fund = database.get_fund(fund_id)
        self.assertEqual(fund["status"], "archived")
        self.assertIsNotNone(fund["archived_at"])


class FundSourceTests(FundsDbTestCase):
    def test_add_list_remove(self):
        fund_id = database.create_fund("F")
        database.add_fund_source(fund_id, 1)
        database.add_fund_source(fund_id, 2)
        self.assertEqual(sorted(database.list_fund_source_channel_ids(fund_id)), [1, 2])
        database.remove_fund_source(fund_id, 1)
        self.assertEqual(database.list_fund_source_channel_ids(fund_id), [2])

    def test_duplicate_add_is_idempotent(self):
        fund_id = database.create_fund("F")
        database.add_fund_source(fund_id, 1)
        database.add_fund_source(fund_id, 1)
        self.assertEqual(database.list_fund_source_channel_ids(fund_id), [1])


class DuplicateFundTests(FundsDbTestCase):
    def test_duplicate_copies_fields_and_sources(self):
        fund_id = database.create_fund("Original", starting_balance=1000, profit_target=200,
                                        assigned_broker_label="Live")
        database.add_fund_source(fund_id, 7)
        new_id = database.duplicate_fund(fund_id, "Copy")
        new_fund = database.get_fund(new_id)
        self.assertEqual(new_fund["name"], "Copy")
        self.assertEqual(new_fund["starting_balance"], 1000)
        self.assertEqual(new_fund["profit_target"], 200)
        self.assertEqual(database.list_fund_source_channel_ids(new_id), [7])
        self.assertNotEqual(new_id, fund_id)

    def test_duplicate_missing_fund_raises(self):
        with self.assertRaises(ValueError):
            database.duplicate_fund(999999, "X")


class FundSessionAttributionTests(FundsDbTestCase):
    def test_session_started_without_fund_still_works(self):
        session_id = database.start_trading_session("Test", [1], "DEMO")
        session = database.get_trading_session(session_id)
        self.assertIsNone(session["fund_id"])

    def test_session_attributed_to_fund(self):
        fund_id = database.create_fund("F")
        session_id = database.start_trading_session("Test", [1], "DEMO", fund_id=fund_id)
        sessions = database.list_fund_sessions(fund_id)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["id"], session_id)

    def test_fund_sessions_empty_for_unrelated_fund(self):
        fund_id = database.create_fund("F1")
        other_fund_id = database.create_fund("F2")
        database.start_trading_session("Test", [1], "DEMO", fund_id=fund_id)
        self.assertEqual(database.list_fund_sessions(other_fund_id), [])


class FundBacktestAttributionTests(FundsDbTestCase):
    def test_backtest_run_attributed_to_fund(self):
        fund_id = database.create_fund("F")
        run_id = database.create_backtest_run("Run", {"source": "imported"}, 1000)
        conn = database.get_connection()
        conn.execute("UPDATE backtest_runs SET fund_id = ? WHERE id = ?", (fund_id, run_id))
        conn.commit()
        conn.close()
        runs = database.list_fund_backtest_runs(fund_id)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["id"], run_id)


class FundActivityLogTests(FundsDbTestCase):
    """2026-07-19 directive: Fund config/lifecycle changes are auditable
    (the roadmap's own previously-deferred follow-up)."""

    def test_create_logs_created(self):
        fund_id = database.create_fund("F", starting_balance=1000, changed_by="ops@axim")
        log = database.list_fund_activity_log(fund_id)
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["action"], "created")
        self.assertEqual(log[0]["changed_by"], "ops@axim")

    def test_create_with_no_changed_by_still_logs(self):
        fund_id = database.create_fund("F")
        log = database.list_fund_activity_log(fund_id)
        self.assertEqual(len(log), 1)
        self.assertIsNone(log[0]["changed_by"])

    def test_field_update_logs_updated_with_changed_fields(self):
        fund_id = database.create_fund("F")
        database.update_fund(fund_id, name="Renamed", changed_by="ops@axim", reason="typo fix")
        log = database.list_fund_activity_log(fund_id)
        self.assertEqual(log[0]["action"], "updated")
        self.assertEqual(log[0]["reason"], "typo fix")
        import json
        self.assertEqual(json.loads(log[0]["changed_fields_json"]), {"name": "Renamed"})

    def test_pure_status_transition_logs_specific_action(self):
        fund_id = database.create_fund("F")
        database.update_fund(fund_id, status="paused", changed_by="ops")
        database.update_fund(fund_id, status="active", changed_by="ops")
        database.update_fund(fund_id, status="archived", changed_by="ops")
        log = database.list_fund_activity_log(fund_id)
        actions = [entry["action"] for entry in log]
        self.assertIn("status_paused", actions)
        self.assertIn("status_active", actions)
        self.assertIn("status_archived", actions)

    def test_status_plus_other_field_logs_generic_updated(self):
        fund_id = database.create_fund("F")
        database.update_fund(fund_id, status="archived", live_enabled=False, changed_by="ops")
        log = database.list_fund_activity_log(fund_id)
        self.assertEqual(log[0]["action"], "updated")

    def test_source_added_and_removed_are_logged(self):
        fund_id = database.create_fund("F")
        database.add_fund_source(fund_id, 42, changed_by="ops")
        database.remove_fund_source(fund_id, 42, changed_by="ops")
        log = database.list_fund_activity_log(fund_id)
        actions = [entry["action"] for entry in log]
        self.assertIn("source_added", actions)
        self.assertIn("source_removed", actions)

    def test_re_adding_an_already_attached_source_is_not_logged_again(self):
        fund_id = database.create_fund("F")
        database.add_fund_source(fund_id, 42, changed_by="ops")
        database.add_fund_source(fund_id, 42, changed_by="ops")  # INSERT OR IGNORE no-op
        log = database.list_fund_activity_log(fund_id)
        self.assertEqual(sum(1 for e in log if e["action"] == "source_added"), 1)

    def test_removing_a_source_that_was_never_attached_is_not_logged(self):
        fund_id = database.create_fund("F")
        database.remove_fund_source(fund_id, 999, changed_by="ops")
        log = database.list_fund_activity_log(fund_id)
        self.assertEqual(len(log), 1)  # only the fund's own "created" entry
        self.assertEqual(log[0]["action"], "created")

    def test_duplicate_logs_created_and_duplicated_from_on_the_new_fund(self):
        source_id = database.create_fund("Source", starting_balance=500)
        new_id = database.duplicate_fund(source_id, "Copy", changed_by="ops", reason="cloning for testing")
        log = database.list_fund_activity_log(new_id)
        actions = [entry["action"] for entry in log]
        self.assertIn("created", actions)
        self.assertIn("duplicated_from", actions)
        # The SOURCE fund's own log must be untouched by duplicating it
        source_log = database.list_fund_activity_log(source_id)
        self.assertEqual(len(source_log), 1)  # only its own "created" entry
        self.assertEqual(source_log[0]["action"], "created")

    def test_activity_log_is_isolated_per_fund(self):
        fund_a = database.create_fund("A")
        fund_b = database.create_fund("B")
        database.update_fund(fund_a, name="A2", changed_by="ops")
        self.assertEqual(len(database.list_fund_activity_log(fund_a)), 2)
        self.assertEqual(len(database.list_fund_activity_log(fund_b)), 1)

    def test_activity_log_most_recent_first(self):
        fund_id = database.create_fund("F")
        database.update_fund(fund_id, name="Second", changed_by="ops")
        database.update_fund(fund_id, name="Third", changed_by="ops")
        log = database.list_fund_activity_log(fund_id)
        self.assertEqual(len(log), 3)
        self.assertEqual(log[0]["id"], max(e["id"] for e in log))

    def test_broker_attach_and_detach_are_logged(self):
        fund_id = database.create_fund("F")
        account_id = database.create_broker_account("PO Demo")
        database.assign_broker_account_to_fund(fund_id, account_id, changed_by="ops")
        database.unassign_broker_account_from_fund(fund_id, account_id, changed_by="ops")
        log = database.list_fund_activity_log(fund_id)
        actions = [entry["action"] for entry in log]
        self.assertIn("broker_attached", actions)
        self.assertIn("broker_detached", actions)

    def test_detaching_an_unattached_broker_account_is_not_logged(self):
        fund_id = database.create_fund("F")
        database.unassign_broker_account_from_fund(fund_id, 999, changed_by="ops")
        log = database.list_fund_activity_log(fund_id)
        self.assertEqual(len(log), 1)  # only the fund's own "created" entry
        self.assertEqual(log[0]["action"], "created")


if __name__ == "__main__":
    unittest.main()
