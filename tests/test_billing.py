import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))

import database
import billing
import settings


class BillingTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_db_file = database.DB_FILE
        database.DB_FILE = Path(self._tmp_dir.name) / "test_axim.db"
        database.initialize_database()

        self._original_secret = settings.STRIPE_SECRET_KEY
        self._original_webhook_secret = settings.STRIPE_WEBHOOK_SECRET
        settings.STRIPE_SECRET_KEY = None
        settings.STRIPE_WEBHOOK_SECRET = None

        self.user_id = database.create_user("plan-test@axim.local", "password123", access_tier="trial",
                                             access_state="trial")

    def tearDown(self):
        database.DB_FILE = self._original_db_file
        self._tmp_dir.cleanup()
        settings.STRIPE_SECRET_KEY = self._original_secret
        settings.STRIPE_WEBHOOK_SECRET = self._original_webhook_secret


class ConfigurationStateTests(BillingTestCase):
    def test_not_configured_by_default(self):
        self.assertFalse(billing.is_configured())
        self.assertFalse(billing.webhook_configured())

    def test_configured_when_key_set(self):
        settings.STRIPE_SECRET_KEY = "sk_test_fake"
        self.assertTrue(billing.is_configured())


class PricingCatalogTests(BillingTestCase):
    def test_six_plans_present(self):
        self.assertEqual(len(billing.PRICING_PLANS), 6)

    def test_get_plan_returns_real_plan_not_contact_only_row(self):
        # "elite" tier has two PRICING_PLANS rows (real Elite + display-only
        # Enterprise) - get_plan must return the real, self-serve one.
        elite = billing.get_plan("elite")
        self.assertEqual(elite["display_name"], "Elite")
        self.assertFalse(elite["contact_only"])

        basic = billing.get_plan("basic")
        self.assertEqual(basic["display_name"], "Basic")

    def test_no_new_access_tier_values_invented(self):
        valid_tiers = {"owner", "internal", "free_beta", "trial", "basic", "pro", "elite", "suspended"}
        for plan in billing.PRICING_PLANS:
            self.assertIn(plan["tier"], valid_tiers)


class CheckoutSessionTests(BillingTestCase):
    def test_checkout_not_configured(self):
        user = database.get_user_by_id(self.user_id)
        result = billing.create_checkout_session(user, "basic")
        self.assertFalse(result["configured"])
        self.assertIsNone(result["checkout_url"])
        self.assertIn("isn't configured", result["message"])

    def test_checkout_unknown_plan(self):
        settings.STRIPE_SECRET_KEY = "sk_test_fake"
        user = database.get_user_by_id(self.user_id)
        result = billing.create_checkout_session(user, "does_not_exist")
        self.assertTrue(result["configured"])
        self.assertIsNone(result["checkout_url"])

    def test_checkout_contact_only_plan(self):
        settings.STRIPE_SECRET_KEY = "sk_test_fake"
        user = database.get_user_by_id(self.user_id)
        result = billing.create_checkout_session(user, "elite")
        # first "elite" entry in PRICING_PLANS is the real self-serve plan,
        # not the contact-only "Enterprise" display row - so this should
        # fail on missing stripe_price_id (no real price configured), not
        # on contact_only.
        self.assertTrue(result["configured"])
        self.assertIsNone(result["checkout_url"])
        self.assertIn("No Stripe price", result["message"])


class SubscriptionMutationTests(BillingTestCase):
    def test_apply_subscription_tier(self):
        billing.apply_subscription_tier(self.user_id, "pro", subscription_id="sub_123")
        user = database.get_user_by_id(self.user_id)
        self.assertEqual(user["access_tier"], "pro")
        self.assertEqual(user["access_state"], "active")
        self.assertEqual(user["stripe_subscription_id"], "sub_123")

    def test_apply_subscription_tier_rejects_non_paid_tier(self):
        with self.assertRaises(ValueError):
            billing.apply_subscription_tier(self.user_id, "owner")

    def test_downgrade_from_customer_id(self):
        database.update_user(self.user_id, stripe_customer_id="cus_abc", access_tier="pro", access_state="active")
        result_user_id = billing.downgrade_from_customer_id("cus_abc")
        self.assertEqual(result_user_id, self.user_id)
        user = database.get_user_by_id(self.user_id)
        self.assertEqual(user["access_tier"], "free_beta")
        self.assertEqual(user["access_state"], "free_access")
        self.assertIsNone(user["stripe_subscription_id"])

    def test_downgrade_unknown_customer_is_noop(self):
        result = billing.downgrade_from_customer_id("cus_does_not_exist")
        self.assertIsNone(result)


class WebhookTests(BillingTestCase):
    def test_webhook_raises_when_not_configured(self):
        with self.assertRaises(billing.BillingNotConfiguredError):
            billing.handle_webhook_event(b"{}", "sig")


class TrialExpirationTests(BillingTestCase):
    def test_trial_not_yet_expired_is_untouched(self):
        future = (datetime.now() + timedelta(days=1)).isoformat()
        database.update_user(self.user_id, trial_expires_at=future)
        user = database.check_and_expire_trial(database.get_user_by_id(self.user_id))
        self.assertEqual(user["access_state"], "trial")

    def test_expired_trial_flips_to_expired_state(self):
        past = (datetime.now() - timedelta(days=1)).isoformat()
        database.update_user(self.user_id, trial_expires_at=past)
        user = database.check_and_expire_trial(database.get_user_by_id(self.user_id))
        self.assertEqual(user["access_state"], "expired")

    def test_non_trial_tier_is_never_touched(self):
        past = (datetime.now() - timedelta(days=1)).isoformat()
        database.update_user(self.user_id, access_tier="pro", access_state="active", trial_expires_at=past)
        user = database.check_and_expire_trial(database.get_user_by_id(self.user_id))
        self.assertEqual(user["access_state"], "active")

    def test_already_disabled_user_not_overwritten_to_expired(self):
        past = (datetime.now() - timedelta(days=1)).isoformat()
        database.update_user(self.user_id, access_state="disabled", trial_expires_at=past)
        user = database.check_and_expire_trial(database.get_user_by_id(self.user_id))
        self.assertEqual(user["access_state"], "disabled")

    def test_no_trial_expires_at_is_untouched(self):
        user = database.check_and_expire_trial(database.get_user_by_id(self.user_id))
        self.assertEqual(user["access_state"], "trial")


if __name__ == "__main__":
    unittest.main()
