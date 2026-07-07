"""Billing API (docs/AXIM_APP_PLAN.md Phase 6) - HTTP surface over
core/billing.py's pricing catalog, checkout-session creation, and
webhook handling. See core/billing.py's module docstring for the
"scaffold only, no live keys yet" framing - this router just exposes
that honestly over HTTP, including a real "not configured" response
shape rather than a generic error.
"""
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = API_DIR.parent
CORE_DIR = PROJECT_ROOT / "core"
sys.path.insert(0, str(CORE_DIR))

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

import billing as billing_module
from auth_routes import get_current_user

router = APIRouter(prefix="/api/billing", tags=["billing"])


class CheckoutRequest(BaseModel):
    tier: str


@router.get("/plans")
def list_plans(user=Depends(get_current_user)):
    return {"configured": billing_module.is_configured(), "plans": billing_module.PRICING_PLANS}


@router.get("/status")
def status(user=Depends(get_current_user)):
    return billing_module.billing_status(user)


@router.post("/checkout")
def checkout(body: CheckoutRequest, user=Depends(get_current_user)):
    return billing_module.create_checkout_session(user, body.tier)


@router.post("/webhook")
async def webhook(request: Request):
    """No auth dependency - Stripe calls this directly with its own
    signature scheme (Stripe-Signature header), verified inside
    core/billing.py.handle_webhook_event against STRIPE_WEBHOOK_SECRET."""
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    try:
        result = billing_module.handle_webhook_event(payload, signature)
    except billing_module.BillingNotConfiguredError:
        raise HTTPException(status_code=503, detail="billing is not configured")
    except stripe.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="invalid webhook signature")
    return result
