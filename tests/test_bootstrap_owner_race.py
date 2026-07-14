import sys
import tempfile
import threading
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
API_DIR = PROJECT_ROOT / "api"
CORE_DIR = PROJECT_ROOT / "core"
CONFIG_DIR = PROJECT_ROOT / "config"
sys.path.insert(0, str(API_DIR))
sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(CONFIG_DIR))

from starlette.requests import Request
from fastapi import HTTPException, Response

import database
import auth_routes


def _make_request():
    scope = {
        "type": "http", "scheme": "http", "headers": [], "method": "POST",
        "path": "/api/auth/bootstrap-owner", "query_string": b"", "server": ("testserver", 80),
    }
    return Request(scope)


class BootstrapOwnerRaceTests(unittest.TestCase):
    """bootstrap_owner()'s count_users()==0 check and the create_user()
    that follows it used to be two separate, non-atomic DB calls - two
    concurrent first-run requests (e.g. two devices on the Tailscale
    network racing to set up AXIM at the same moment) could both pass the
    check before either inserted, minting two owners instead of one.
    Fixed with an in-process lock (the API always runs as a single
    uvicorn process, so this fully closes the race)."""

    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()

    def test_concurrent_bootstraps_only_ever_create_one_owner(self):
        results = []

        def attempt(email):
            body = auth_routes.BootstrapOwnerRequest(email=email, password="CorrectPass123!")
            try:
                auth_routes.bootstrap_owner(body, Response(), _make_request())
                results.append("ok")
            except HTTPException as exc:
                results.append(exc.status_code)

        threads = [threading.Thread(target=attempt, args=(f"racer{i}@axim.local",)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(results.count("ok"), 1)
        self.assertEqual(results.count(409), 9)
        self.assertEqual(database.count_users(), 1)

    def test_normal_single_bootstrap_still_works(self):
        body = auth_routes.BootstrapOwnerRequest(email="owner@axim.local", password="CorrectPass123!")
        result = auth_routes.bootstrap_owner(body, Response(), _make_request())
        self.assertEqual(result["email"], "owner@axim.local")
        self.assertEqual(result["role"], "owner")


if __name__ == "__main__":
    unittest.main()
