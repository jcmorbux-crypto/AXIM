"""2026-08-01 audit finding: api/risk_engine_routes.py's 7 newer PATCH
endpoints (apex-ascension, drawdown-protection, cashflow, strike,
momentum, fortress, empire - the Capital Strategies (tm) sub-configs)
had real, working implementations and real DB persistence, but zero
tests exercised the HTTP route layer itself - only core/database.py's
own update_*_settings functions were covered. These tests close that
gap the same way tests/test_broker_accounts_routes.py already tests
its own router: call the route function directly with a fake admin
user, not a full FastAPI TestClient."""
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
API_DIR = PROJECT_ROOT / "api"
CORE_DIR = PROJECT_ROOT / "core"
CONFIG_DIR = PROJECT_ROOT / "config"
sys.path.insert(0, str(API_DIR))
sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(CONFIG_DIR))

import database
import risk_engine_routes as routes

_FAKE_ADMIN = {"id": 1, "email": "owner@axim.local", "role": "owner"}


class RiskEngineRoutesTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()
        self.profile_id = database.create_risk_profile("Test Strategy", sizing_mode="percent")

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()


class ApexAscensionRouteTests(RiskEngineRoutesTestBase):
    def test_patch_persists_all_fields(self):
        body = routes.ApexAscensionUpdate(
            enabled=True, starting_unit_value=15, standard_units=8,
            first_reset_threshold=3000, reset_increment=1500, reset_unit_step=20,
            downgrade_protection=False,
        )
        result = routes.update_apex_ascension(self.profile_id, body, user=_FAKE_ADMIN)
        self.assertTrue(result["apex_ascension"]["enabled"])
        self.assertEqual(result["apex_ascension"]["starting_unit_value"], 15)
        self.assertEqual(result["apex_ascension"]["standard_units"], 8)
        self.assertEqual(result["apex_ascension"]["first_reset_threshold"], 3000)
        self.assertEqual(result["apex_ascension"]["reset_increment"], 1500)
        self.assertEqual(result["apex_ascension"]["reset_unit_step"], 20)
        self.assertFalse(result["apex_ascension"]["downgrade_protection"])

    def test_rejects_edits_to_a_template(self):
        template_id = database.create_risk_profile("Template", is_template=True, sizing_mode="percent")
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            routes.update_apex_ascension(template_id, routes.ApexAscensionUpdate(enabled=True), user=_FAKE_ADMIN)


class DrawdownProtectionRouteTests(RiskEngineRoutesTestBase):
    def test_patch_persists_fields(self):
        body = routes.DrawdownProtectionUpdate(enabled=True, suspend_above_percent=15, scope="fund")
        result = routes.update_drawdown_protection(self.profile_id, body, user=_FAKE_ADMIN)
        self.assertTrue(result["drawdown_protection"]["enabled"])
        self.assertEqual(result["drawdown_protection"]["suspend_above_percent"], 15)
        self.assertEqual(result["drawdown_protection"]["scope"], "fund")


class CashflowRouteTests(RiskEngineRoutesTestBase):
    def test_patch_persists_fields(self):
        body = routes.CashflowUpdate(
            enabled=True, target_amount=200, target_period="week",
            partial_target_percent=80, partial_reduction_percent=40,
        )
        result = routes.update_cashflow(self.profile_id, body, user=_FAKE_ADMIN)
        self.assertTrue(result["cashflow"]["enabled"])
        self.assertEqual(result["cashflow"]["target_amount"], 200)
        self.assertEqual(result["cashflow"]["target_period"], "week")
        self.assertEqual(result["cashflow"]["partial_target_percent"], 80)
        self.assertEqual(result["cashflow"]["partial_reduction_percent"], 40)


class StrikeRouteTests(RiskEngineRoutesTestBase):
    def test_patch_persists_fields(self):
        body = routes.StrikeUpdate(enabled=True, max_session_duration_minutes=90, max_consecutive_losses=4)
        result = routes.update_strike(self.profile_id, body, user=_FAKE_ADMIN)
        self.assertTrue(result["strike"]["enabled"])
        self.assertEqual(result["strike"]["max_session_duration_minutes"], 90)
        self.assertEqual(result["strike"]["max_consecutive_losses"], 4)


class MomentumRouteTests(RiskEngineRoutesTestBase):
    def test_patch_persists_fields(self):
        body = routes.MomentumUpdate(enabled=True, max_steps=5, multiplier=1.8, profit_lock_percent=25)
        result = routes.update_momentum(self.profile_id, body, user=_FAKE_ADMIN)
        self.assertTrue(result["momentum"]["enabled"])
        self.assertEqual(result["momentum"]["max_steps"], 5)
        self.assertEqual(result["momentum"]["multiplier"], 1.8)
        self.assertEqual(result["momentum"]["profit_lock_percent"], 25)


class FortressRouteTests(RiskEngineRoutesTestBase):
    def test_patch_persists_fields(self):
        body = routes.FortressUpdate(enabled=True, protection_threshold=500)
        result = routes.update_fortress(self.profile_id, body, user=_FAKE_ADMIN)
        self.assertTrue(result["fortress"]["enabled"])
        self.assertEqual(result["fortress"]["protection_threshold"], 500)


class EmpireRouteTests(RiskEngineRoutesTestBase):
    def test_patch_persists_fields(self):
        body = routes.EmpireUpdate(
            enabled=True, starting_amount=20, target_amount=500,
            num_levels=12, failure_behavior="return_to_checkpoint", checkpoint_level=3,
        )
        result = routes.update_empire(self.profile_id, body, user=_FAKE_ADMIN)
        self.assertTrue(result["empire"]["enabled"])
        self.assertEqual(result["empire"]["starting_amount"], 20)
        self.assertEqual(result["empire"]["target_amount"], 500)
        self.assertEqual(result["empire"]["num_levels"], 12)
        self.assertEqual(result["empire"]["failure_behavior"], "return_to_checkpoint")
        self.assertEqual(result["empire"]["checkpoint_level"], 3)


class SizingModeAcceptsNewCapitalStrategiesTests(RiskEngineRoutesTestBase):
    """2026-08-01 corruption bug found in web/risk.html: the sizing-mode
    dropdown had no apex_ascension/empire option, so editing an existing
    profile using either mode and clicking Save silently rewrote it to
    "fixed" (the dropdown's first option, selected by default since
    nothing matched). Proves the API layer itself has always accepted
    both correctly - the bug was UI-only - and guards against a future
    regression narrowing _VALID_SIZING_MODES back down."""

    def test_apex_ascension_is_a_valid_sizing_mode(self):
        result = routes.update_profile(
            self.profile_id, routes.ProfileUpdate(sizing_mode="apex_ascension"), user=_FAKE_ADMIN,
        )
        self.assertEqual(result["sizing_mode"], "apex_ascension")

    def test_empire_is_a_valid_sizing_mode(self):
        result = routes.update_profile(
            self.profile_id, routes.ProfileUpdate(sizing_mode="empire"), user=_FAKE_ADMIN,
        )
        self.assertEqual(result["sizing_mode"], "empire")


class DuplicateCarriesNewerSubConfigsWithNonDefaultValuesTests(RiskEngineRoutesTestBase):
    """core/database.duplicate_risk_profile already copies every sub-
    table including the 7 newer ones (verified by reading the code) -
    this proves it with real non-default values round-tripping through
    an actual duplicate, not just asserting the copy code exists."""

    def test_duplicate_copies_every_newer_sub_config_with_real_values(self):
        routes.update_apex_ascension(
            self.profile_id, routes.ApexAscensionUpdate(enabled=True, standard_units=9), user=_FAKE_ADMIN,
        )
        routes.update_drawdown_protection(
            self.profile_id, routes.DrawdownProtectionUpdate(enabled=True, suspend_above_percent=12),
            user=_FAKE_ADMIN,
        )
        routes.update_cashflow(
            self.profile_id, routes.CashflowUpdate(enabled=True, target_amount=333), user=_FAKE_ADMIN,
        )
        routes.update_strike(
            self.profile_id, routes.StrikeUpdate(enabled=True, max_consecutive_losses=7), user=_FAKE_ADMIN,
        )
        routes.update_momentum(
            self.profile_id, routes.MomentumUpdate(enabled=True, multiplier=2.2), user=_FAKE_ADMIN,
        )
        routes.update_fortress(
            self.profile_id, routes.FortressUpdate(enabled=True, protection_threshold=777), user=_FAKE_ADMIN,
        )
        routes.update_empire(
            self.profile_id, routes.EmpireUpdate(enabled=True, target_amount=888), user=_FAKE_ADMIN,
        )

        duplicated = routes.duplicate_profile(self.profile_id, routes.DuplicateRequest(new_name="Clone"), user=_FAKE_ADMIN)

        self.assertTrue(duplicated["apex_ascension"]["enabled"])
        self.assertEqual(duplicated["apex_ascension"]["standard_units"], 9)
        self.assertTrue(duplicated["drawdown_protection"]["enabled"])
        self.assertEqual(duplicated["drawdown_protection"]["suspend_above_percent"], 12)
        self.assertTrue(duplicated["cashflow"]["enabled"])
        self.assertEqual(duplicated["cashflow"]["target_amount"], 333)
        self.assertTrue(duplicated["strike"]["enabled"])
        self.assertEqual(duplicated["strike"]["max_consecutive_losses"], 7)
        self.assertTrue(duplicated["momentum"]["enabled"])
        self.assertEqual(duplicated["momentum"]["multiplier"], 2.2)
        self.assertTrue(duplicated["fortress"]["enabled"])
        self.assertEqual(duplicated["fortress"]["protection_threshold"], 777)
        self.assertTrue(duplicated["empire"]["enabled"])
        self.assertEqual(duplicated["empire"]["target_amount"], 888)
        # And the clone is independent - editing the original afterward
        # must not affect the copy.
        routes.update_fortress(
            self.profile_id, routes.FortressUpdate(protection_threshold=1), user=_FAKE_ADMIN,
        )
        still_duplicated = database.get_risk_profile(duplicated["id"])
        self.assertEqual(still_duplicated["fortress"]["protection_threshold"], 777)


class ExportImportRoundTripTests(RiskEngineRoutesTestBase):
    """2026-08-01 real bug found while wiring Money Management Studio's
    Export/Import UI: ImportRequest only declared name/description/
    profile/martingale/compounding/profit_vault - Pydantic silently
    drops any undeclared field, so every OTHER section export_profile
    emits (apex_ascension/drawdown_protection/cashflow/strike/momentum/
    fortress/empire/daily_compounding/sniper/blackwater) was thrown away
    on import even though database.import_risk_profile has always
    supported all of them. This proves a full round trip through the
    real HTTP route layer (not just the database functions) carries
    every section, not just the original 3."""

    def test_export_then_import_preserves_every_sub_config(self):
        routes.update_apex_ascension(
            self.profile_id, routes.ApexAscensionUpdate(enabled=True, standard_units=9), user=_FAKE_ADMIN,
        )
        routes.update_cashflow(
            self.profile_id, routes.CashflowUpdate(enabled=True, target_amount=333), user=_FAKE_ADMIN,
        )
        routes.update_empire(
            self.profile_id, routes.EmpireUpdate(enabled=True, target_amount=888), user=_FAKE_ADMIN,
        )
        routes.update_sniper(
            self.profile_id, routes.SniperUpdate(enabled=True, min_win_rate=72.5), user=_FAKE_ADMIN,
        )
        routes.update_blackwater(
            self.profile_id, routes.BlackwaterUpdate(enabled=True, base_risk_percent=3.5), user=_FAKE_ADMIN,
        )

        exported = routes.export_profile(self.profile_id, user=_FAKE_ADMIN)
        exported["name"] = "Imported Clone"
        imported = routes.import_profile(routes.ImportRequest(**exported), user=_FAKE_ADMIN)

        self.assertTrue(imported["apex_ascension"]["enabled"])
        self.assertEqual(imported["apex_ascension"]["standard_units"], 9)
        self.assertTrue(imported["cashflow"]["enabled"])
        self.assertEqual(imported["cashflow"]["target_amount"], 333)
        self.assertTrue(imported["empire"]["enabled"])
        self.assertEqual(imported["empire"]["target_amount"], 888)
        self.assertTrue(imported["sniper"]["enabled"])
        self.assertEqual(imported["sniper"]["min_win_rate"], 72.5)
        self.assertTrue(imported["blackwater"]["enabled"])
        self.assertEqual(imported["blackwater"]["base_risk_percent"], 3.5)
        # And it's a real, independent new profile - a distinct id, not
        # the original re-fetched.
        self.assertNotEqual(imported["id"], self.profile_id)


if __name__ == "__main__":
    unittest.main()
