import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
API_DIR = PROJECT_ROOT / "api"
sys.path.insert(0, str(API_DIR))

import process_control


class ListenerPidsCacheTests(unittest.TestCase):
    """find_listener_pids() spawns a real powershell.exe + WMI query -
    measured at 1.4-1.65s per call, purely from process-spawn overhead.
    web/dashboard.html's refreshGlobal() calls two endpoints that both
    hit this in the same Promise.all - live-verified (100% reproducible
    across 5 attempts) this made Mission Control sit on "Loading..." for
    5-8s on every single page load before this cache existed. See
    docs/AXIM_ROADMAP.md's writeup of this fix for the full
    before/after."""

    def setUp(self):
        process_control._cache["pids"] = None
        process_control._cache["at"] = 0.0

    def tearDown(self):
        process_control._cache["pids"] = None
        process_control._cache["at"] = 0.0

    def test_second_call_within_ttl_uses_cache_not_a_new_subprocess(self):
        with patch.object(process_control, "_find_listener_pids_uncached", return_value=[123]) as mock_find:
            process_control.find_listener_pids()
            process_control.find_listener_pids()
            process_control.find_listener_pids()
        self.assertEqual(mock_find.call_count, 1)

    def test_use_cache_false_always_does_a_fresh_lookup(self):
        with patch.object(process_control, "_find_listener_pids_uncached", return_value=[123]) as mock_find:
            process_control.find_listener_pids(use_cache=False)
            process_control.find_listener_pids(use_cache=False)
        self.assertEqual(mock_find.call_count, 2)

    def test_cache_expires_after_ttl(self):
        with patch.object(process_control, "_find_listener_pids_uncached", return_value=[123]) as mock_find:
            process_control.find_listener_pids()
            process_control._cache["at"] -= (process_control.CACHE_TTL_SECONDS + 0.1)
            process_control.find_listener_pids()
        self.assertEqual(mock_find.call_count, 2)

    def test_cached_result_is_actually_correct_not_just_fast(self):
        with patch.object(process_control, "_find_listener_pids_uncached", return_value=[456, 789]):
            first = process_control.find_listener_pids()
            second = process_control.find_listener_pids()
        self.assertEqual(first, [456, 789])
        self.assertEqual(second, [456, 789])

    def test_get_status_reflects_cached_pids(self):
        with patch.object(process_control, "_find_listener_pids_uncached", return_value=[999]):
            status = process_control.get_status()
        self.assertEqual(status, {"running": True, "pids": [999]})

    def test_start_listener_bypasses_cache_for_its_own_check(self):
        # Pre-warm the cache with a stale "not running" result, then
        # confirm start_listener() still sees a fresh (different) answer
        # rather than trusting the stale cache for its own gating check.
        with patch.object(process_control, "_find_listener_pids_uncached", return_value=[]):
            process_control.find_listener_pids()  # warms the cache with []
        with patch.object(process_control, "_find_listener_pids_uncached", return_value=[321]) as mock_find:
            with patch.object(process_control, "_run_powershell", return_value=("", "", 0)):
                result = process_control.start_listener()
        self.assertEqual(result["status"], "already_running")
        self.assertEqual(result["pids"], [321])
        self.assertGreaterEqual(mock_find.call_count, 1)


if __name__ == "__main__":
    unittest.main()
