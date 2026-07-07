"""Billing/licensing scaffold (docs/AXIM_APP_PLAN.md Phase 6) - pricing
plan catalog, Stripe Checkout Session creation, and webhook event
handling. Deliberately built and shipped WITHOUT live Stripe keys:
every function checks is_configured() first and returns an honest
"not configured" result rather than raising or pretending to charge
anyone - see the ADR note in docs/AXIM_APP_PLAN.md's Phase 6 section.

Owner manual tier changes (api/admin.py's set-tier / set-trial /
grant-free-access / activate / disable / revoke-access) remain the
ONE real, working way to change a user's access_tier today. This
module only becomes a second, automated path once real
STRIPE_SECRET_KEY / STRIPE_WEBHOOK_SECRET values are set in .env -
never a replacement for Owner control.

Feature lists in PRICING_PLANS are the planned marketing
differentiation for a future pricing page, not technically-enforced
limits - AXIM does not yet gate any feature or usage count by
access_tier anywhere in the app (access_tier today only affects the
account's ability to log in - see api/auth_routes.py's
_BLOCKED_ACCESS_STATES and core/database.py's check_and_expire_trial -
plus the pre-existing demo_only_forced / live_trading_allowed flags).
"""
import sys
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CORE_DIR.parent
sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(PROJECT_ROOT / "config"))

import stripe

import database
import settings
from logger import get_logger

logger = get_logger("axim.ui", filename="ui.log")

# access_tier values that a real subscription can put someone on.
# "owner"/"internal"/"suspended" are never touched by billing.
_PAID_TIERS = {"basic", "pro", "elite"}

# Six user-facing plan names from docs/AXIM_APP_PLAN.md's terminology
# rebrand table, mapped onto the existing access_tier enum - no schema
# change needed. "Enterprise" is display-only (contact-us, no self-serve
# checkout) and grants the same "elite" tier under the hood rather than
# a new enum value.
PRICING_PLANS = [
    {
        "tier": "free_beta", "display_name": "Free", "price_usd_monthly": 0,
        "stripe_price_id": None, "contact_only": False,
        "features": ["Demo trading", "1 signal source", "Community support"],
    },
    {
        "tier": "trial", "display_name": "Trial", "price_usd_monthly": 0,
        "stripe_price_id": None, "contact_only": False,
        "features": ["Full feature access", "Demo trading", "Time-limited"],
    },
    {
        "tier": "basic", "display_name": "Basic", "price_usd_monthly": 29,
        "stripe_price_id": settings.STRIPE_PRICE_BASIC, "contact_only": False,
        "features": ["Live trading", "Risk Engine", "Email support"],
    },
    {
        "tier": "pro", "display_name": "Professional", "price_usd_monthly": 79,
        "stripe_price_id": settings.STRIPE_PRICE_PRO, "contact_only": False,
        "features": ["Everything in Basic", "Rule Builder automation", "Priority support"],
    },
    {
        "tier": "elite", "display_name": "Elite", "price_usd_monthly": 199,
        "stripe_price_id": settings.STRIPE_PRICE_ELITE, "contact_only": False,
        "features": ["Everything in Professional", "Dedicated support"],
    },
    {
        "tier": "elite", "display_name": "Enterprise", "price_usd_monthly": None,
        "stripe_price_id": None, "contact_only": True,
        "features": ["Custom terms", "White-glove onboarding", "Contact us"],
    },
]

TIER_DISPLAY_NAMES = {
    "owner": "Owner", "internal": "Internal", "free_beta": "Free", "trial": "Trial",
    "basic": "Basic", "pro": "Professional", "elite": "Elite", "suspended": "Suspended",
}


def is_configured():
    return bool(settings.STRIPE_SECRET_KEY)


def webhook_configured():
    return bool(settings.STRIPE_WEBHOOK_SECRET)


def get_plan(tier):
    return next((p for p in PRICING_PLANS if p["tier"] == tier and not p["contact_only"]), None)


def billing_status(user):
    return {
        "configured": is_configured(),
        "access_tier": user["access_tier"],
        "display_name": TIER_DISPLAY_NAMES.get(user["access_tier"], user["access_tier"]),
        "access_state": user["access_state"],
        "trial_expires_at": user["trial_expires_at"],
        "has_stripe_customer": bool(user["stripe_customer_id"]),
    }


class BillingNotConfiguredError(Exception):
    pass


def create_checkout_session(user, tier):
    """Returns {configured, checkout_url, message} - never raises for the
    "not configured yet" case, so the UI can render a plain message
    instead of an error state."""
    if not is_configured():
        return {"configured": False, "checkout_url": None,
                "message": "Billing isn't configured yet. Contact the Owner to change your plan."}

    plan = next((p for p in PRICING_PLANS if p["tier"] == tier), None)
    if plan is None:
        return {"configured": True, "checkout_url": None, "message": f"Unknown plan: {tier}"}
    if plan["contact_only"]:
        return {"configured": True, "checkout_url": None, "message": "This plan requires contacting us directly."}
    if not plan["stripe_price_id"]:
        return {"configured": True, "checkout_url": None,
                "message": f"No Stripe price is configured for {plan['display_name']} yet."}

    stripe.api_key = settings.STRIPE_SECRET_KEY
    customer_id = user["stripe_customer_id"]
    if not customer_id:
        customer = stripe.Customer.create(email=user["email"], metadata={"axim_user_id": str(user["id"])})
        customer_id = customer["id"]
        database.update_user(user["id"], stripe_customer_id=customer_id)

    session = stripe.checkout.Session.create(
        customer=customer_id,
        mode="subscription",
        line_items=[{"price": plan["stripe_price_id"], "quantity": 1}],
        success_url=f"{settings.APP_BASE_URL}/billing?checkout=success",
        cancel_url=f"{settings.APP_BASE_URL}/billing?checkout=cancelled",
        metadata={"axim_user_id": str(user["id"]), "axim_tier": tier},
    )
    return {"configured": True, "checkout_url": session["url"], "message": None}


def apply_subscription_tier(user_id, tier, subscription_id=None):
    if tier not in _PAID_TIERS:
        raise ValueError(f"not a paid tier: {tier!r}")
    database.update_user(user_id, access_tier=tier, access_state="active", stripe_subscription_id=subscription_id)
    logger.info("billing: user %s activated on tier %s (subscription=%s)", user_id, tier, subscription_id)


def downgrade_from_customer_id(stripe_customer_id):
    """Subscription cancelled/deleted - soft-downgrade to the same
    (free_beta, free_access) pairing api/admin.py's own
    grant-free-access action already uses, rather than locking the
    account out entirely."""
    user = database.get_user_by_stripe_customer_id(stripe_customer_id)
    if user is None:
        logger.warning("billing: webhook referenced unknown stripe_customer_id=%s", stripe_customer_id)
        return None
    database.update_user(user["id"], access_tier="free_beta", access_state="free_access", stripe_subscription_id=None)
    logger.info("billing: user %s downgraded to free_beta after subscription cancellation", user["id"])
    return user["id"]


def handle_webhook_event(payload_body, signature_header):
    """Verifies and processes one Stripe webhook delivery. Raises
    BillingNotConfiguredError if STRIPE_WEBHOOK_SECRET isn't set (the
    caller turns that into HTTP 503) and stripe.SignatureVerificationError
    if the signature doesn't check out (caller turns that into HTTP 400) -
    never processes an unverified payload."""
    if not webhook_configured():
        raise BillingNotConfiguredError("STRIPE_WEBHOOK_SECRET is not set")

    event = stripe.Webhook.construct_event(payload_body, signature_header, settings.STRIPE_WEBHOOK_SECRET)
    event_type = event["type"]
    data = event["data"]["object"]

    if event_type == "checkout.session.completed":
        user_id = data.get("metadata", {}).get("axim_user_id")
        tier = data.get("metadata", {}).get("axim_tier")
        if user_id and tier in _PAID_TIERS:
            if data.get("customer"):
                database.update_user(int(user_id), stripe_customer_id=data["customer"])
            apply_subscription_tier(int(user_id), tier, subscription_id=data.get("subscription"))
    elif event_type in ("customer.subscription.deleted", "customer.subscription.updated"):
        if event_type == "customer.subscription.deleted" or data.get("status") in ("canceled", "unpaid", "incomplete_expired"):
            downgrade_from_customer_id(data.get("customer"))

    return {"handled": True, "type": event_type}
