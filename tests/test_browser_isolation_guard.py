"""2026-08-01 audit: execution/browser.py and execution/page_probe.py were
found to independently redefine USER_DATA_DIR = Path("sessions/pocket_browser")
and call launch_persistent_context directly, with no relation at all to
execution/browser_session.py's guard_against_production_profile_in_tests -
a test importing either module (both live directly under execution/, the
exact directory every browser-touching test already adds to sys.path) had
zero protection against actually launching Chrome against the real
production profile. Fixed by making browser_session.py the single
authoritative source for both USER_DATA_DIR and the guard - these tests
prove that fix rather than just asserting the current file contents."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "execution"))

import browser_session
import browser as browser_script
import page_probe


class SingleSourceOfTruthTests(unittest.TestCase):
    """Every module that can launch a browser against the production
    profile must get USER_DATA_DIR and the guard from browser_session.py
    by import, never by redefining either - `is` (not just `==`) proves
    it's the literal same object, not a coincidentally-equal copy that
    could silently drift again in the future."""

    def test_browser_script_imports_the_shared_user_data_dir(self):
        self.assertIs(browser_script.USER_DATA_DIR, browser_session.USER_DATA_DIR)

    def test_page_probe_imports_the_shared_user_data_dir(self):
        self.assertIs(page_probe.USER_DATA_DIR, browser_session.USER_DATA_DIR)

    def test_browser_script_imports_the_shared_guard_function(self):
        self.assertIs(
            browser_script.guard_against_production_profile_in_tests,
            browser_session.guard_against_production_profile_in_tests,
        )

    def test_page_probe_imports_the_shared_guard_function(self):
        self.assertIs(
            page_probe.guard_against_production_profile_in_tests,
            browser_session.guard_against_production_profile_in_tests,
        )


class ManualScriptsRefuseToRunUnderPytestTests(unittest.TestCase):
    """The actual behavioral proof: invoking either manual debugging
    script's entry point while pytest is running must refuse before ever
    touching Playwright - both scripts default to the real production
    USER_DATA_DIR with no override, the exact shape of the original gap."""

    def test_open_pocket_option_refuses_under_pytest_without_launching_anything(self):
        with patch("browser.sync_playwright") as mock_playwright:
            with self.assertRaises(browser_session.ProductionProfileInTestError):
                browser_script.open_pocket_option()
            mock_playwright.assert_not_called()

    def test_probe_page_refuses_under_pytest_without_launching_anything(self):
        with patch("page_probe.sync_playwright") as mock_playwright:
            with self.assertRaises(browser_session.ProductionProfileInTestError):
                page_probe.probe_page()
            mock_playwright.assert_not_called()


if __name__ == "__main__":
    unittest.main()
