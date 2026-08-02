"""2026-08-01 trading-engine priority directive audit: fund_sources
(Portfolio's "Signal Providers" assignment) was real, persisted, and had
a working UI, but nothing at session-start time actually checked a
session's channel_ids against it - an operator could assign Provider X
to a Fund, then start a session against Provider Y's channel instead,
and every Y signal would route to that Fund anyway. These tests prove
the new enforcement in api/sessions.py's start_session."""
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

PROJECT_ROOT = Path(__file__).resolve().parent.parent
API_DIR = PROJECT_ROOT / "api"
CORE_DIR = PROJECT_ROOT / "core"
CONFIG_DIR = PROJECT_ROOT / "config"
sys.path.insert(0, str(API_DIR))
sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(CONFIG_DIR))

import database
import sessions as routes

_FAKE_ADMIN = {"id": 1, "email": "owner@axim.local", "role": "owner"}


class FundSourceEnforcementTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def _channel(self, chat_id, title):
        database.upsert_channel(chat_id=chat_id, username=title.lower(), title=title, kind="channel")
        channel = next(c for c in database.list_channels() if c["title"] == title)
        database.set_channel_enabled(channel["id"], True)
        return channel["id"]

    def _connected_fund(self, name="Test Fund"):
        account_id = database.create_broker_account("Test Broker", mode="demo")
        database.update_broker_account(account_id, connection_status="connected")
        fund_id = database.create_fund(name, starting_balance=1000)
        database.assign_broker_account_to_fund(fund_id, account_id)
        profile_id = database.create_risk_profile("S1", sizing_mode="fixed", fixed_amount=10, bankroll=1000)
        return fund_id, profile_id

    def _start(self, fund_id, channel_ids, profile_id):
        body = routes.SessionStart(channel_ids=channel_ids, fund_id=fund_id, risk_profile_id=profile_id)
        return routes.start_session(body, user=_FAKE_ADMIN)

    def test_starts_normally_when_fund_has_no_sources_assigned(self):
        # Backward compatible - a fund that never used provider
        # assignment (the common case for every fund that predates this)
        # keeps working with any channel, unrestricted.
        fund_id, profile_id = self._connected_fund()
        channel_id = self._channel(1, "Any Channel")
        result = self._start(fund_id, [channel_id], profile_id)
        self.assertEqual(result["status"], "active")

    def test_rejected_when_channel_is_not_an_assigned_source(self):
        fund_id, profile_id = self._connected_fund()
        assigned_channel_id = self._channel(1, "Assigned Provider")
        other_channel_id = self._channel(2, "Unassigned Provider")
        database.add_fund_source(fund_id, assigned_channel_id)

        with self.assertRaises(HTTPException) as ctx:
            self._start(fund_id, [other_channel_id], profile_id)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("not assigned", ctx.exception.detail)

    def test_succeeds_when_channel_is_an_assigned_source(self):
        fund_id, profile_id = self._connected_fund()
        assigned_channel_id = self._channel(1, "Assigned Provider")
        database.add_fund_source(fund_id, assigned_channel_id)

        result = self._start(fund_id, [assigned_channel_id], profile_id)
        self.assertEqual(result["status"], "active")

    def test_rejected_if_any_channel_in_a_mixed_list_is_unassigned(self):
        fund_id, profile_id = self._connected_fund()
        assigned_channel_id = self._channel(1, "Assigned Provider")
        other_channel_id = self._channel(2, "Unassigned Provider")
        database.add_fund_source(fund_id, assigned_channel_id)

        with self.assertRaises(HTTPException) as ctx:
            self._start(fund_id, [assigned_channel_id, other_channel_id], profile_id)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_error_names_the_fund_and_the_rejected_channel(self):
        fund_id, profile_id = self._connected_fund("Growth Fund")
        assigned_channel_id = self._channel(1, "Assigned Provider")
        other_channel_id = self._channel(2, "Unassigned Provider")
        database.add_fund_source(fund_id, assigned_channel_id)

        with self.assertRaises(HTTPException) as ctx:
            self._start(fund_id, [other_channel_id], profile_id)
        self.assertIn("Growth Fund", ctx.exception.detail)
        self.assertIn(str(other_channel_id), ctx.exception.detail)

    def test_a_different_funds_source_assignment_does_not_leak_across_funds(self):
        fund_a, profile_a = self._connected_fund("Fund A")
        fund_b, profile_b = self._connected_fund("Fund B")
        channel_for_a = self._channel(1, "Provider For A")
        database.add_fund_source(fund_a, channel_for_a)
        # Fund B has never been assigned anything - it stays fully open,
        # including against Fund A's own assigned channel.
        result = self._start(fund_b, [channel_for_a], profile_b)
        self.assertEqual(result["status"], "active")


if __name__ == "__main__":
    unittest.main()
