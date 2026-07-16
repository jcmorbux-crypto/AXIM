"""Capital Recommendation Engine API (Tier 2 roadmap items 5-8) - HTTP
surface over core/capital_recommendation.py and the
capital_recommendations table. One-click Create Recommended Demo Fund
closes the loop: takes a provider's evidence-backed recommendation
(best backtested Money Studio strategy + a chosen allocation tier) and
turns it into a real, running Fund - Demo-only, matching this feature's
standing safety posture (docs/AXIM_ENGINEERING_JOURNAL.md).
"""
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = API_DIR.parent
CORE_DIR = PROJECT_ROOT / "core"
sys.path.insert(0, str(CORE_DIR))

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import database
import fund_manager
from auth_routes import get_current_user, require_admin

router = APIRouter(prefix="/api/capital-recommendations", tags=["capital-recommendations"])

_VALID_TIERS = {"minimum", "conservative", "suggested"}


@router.get("")
def list_recommendations(user=Depends(get_current_user)):
    return database.list_capital_recommendations()


@router.get("/{source_label}")
def get_recommendation(source_label: str, user=Depends(get_current_user)):
    recommendation = database.get_capital_recommendation(source_label)
    if recommendation is None:
        raise HTTPException(status_code=404, detail=f"no recommendation for {source_label!r} yet")
    return recommendation


class CreateDemoFundRequest(BaseModel):
    tier: str = "suggested"
    fund_name: Optional[str] = None
    broker_account_id: Optional[int] = None


@router.post("/{recommendation_id}/create-demo-fund")
def create_demo_fund(recommendation_id: int, body: CreateDemoFundRequest, user=Depends(require_admin)):
    """One-click Create Recommended Demo Fund. Creates a brand-new Fund
    (never reuses an existing one - a recommendation is a fresh
    allocation decision), sized at the requested allocation tier,
    deployed with the recommended strategy's own backtested profile
    snapshot (same create_risk_profile_from_snapshot mechanism
    POST /api/backtest/runs/{run_id}/strategies/{strategy_id}/deploy
    already uses), attached to a Demo broker account, and attached to
    the provider's Signal Source channel via fund_sources IF that
    channel has already been synced into AXIM (see
    docs/OPT_SIGNALS_RECOMMENDATIONS.md for which providers this
    applies to) - if not, the Fund is still created correctly, just
    without that one attachment, and the response says so plainly
    rather than silently pretending it happened.

    Demo-only by construction: requires an existing broker account with
    mode demo/both (never creates or selects a live-mode account), and
    the new Fund's live_enabled always defaults to false."""
    if body.tier not in _VALID_TIERS:
        raise HTTPException(status_code=400, detail=f"tier must be one of {sorted(_VALID_TIERS)}")

    recommendation = database.get_capital_recommendation_by_id(recommendation_id)
    if recommendation is None:
        raise HTTPException(status_code=404, detail="recommendation not found")

    strategy = database.get_backtest_strategy(recommendation["best_strategy_id"])
    if strategy is None:
        raise HTTPException(status_code=404, detail="the recommended strategy's backtest data no longer exists")

    if body.broker_account_id is not None:
        broker_account = database.get_broker_account(body.broker_account_id)
        if broker_account is None:
            raise HTTPException(status_code=404, detail="broker account not found")
        if broker_account["mode"] not in ("demo", "both"):
            raise HTTPException(status_code=400, detail="Create Recommended Demo Fund requires a demo-mode broker account")
    else:
        demo_accounts = [a for a in database.list_broker_accounts() if a["mode"] in ("demo", "both")]
        broker_account = demo_accounts[0] if demo_accounts else None

    allocation = recommendation[f"{body.tier}_allocation"]
    fund_name = body.fund_name or f"{recommendation['source_label']} - {body.tier.capitalize()} (Recommended)"

    fund_id = database.create_fund(
        fund_name, starting_balance=allocation,
        assigned_broker_label=recommendation["source_label"], live_enabled=False,
    )

    profile_name = f"{fund_name} - {recommendation['best_strategy_name']}"
    profile_id = database.create_risk_profile_from_snapshot(profile_name, strategy["profile_snapshot"])
    database.update_fund(fund_id, default_risk_profile_id=profile_id)

    if broker_account is not None:
        database.assign_broker_account_to_fund(fund_id, broker_account["id"], is_primary=True)

    source_attached = False
    channel = database.find_channel(title=recommendation["source_label"])
    if channel is not None:
        database.add_fund_source(fund_id, channel["id"])
        source_attached = True

    return {
        "fund_report": fund_manager.get_fund_report(fund_id),
        "deployed_profile_id": profile_id,
        "deployed_profile_name": profile_name,
        "allocation_tier": body.tier,
        "allocation_amount": allocation,
        "broker_account_attached": broker_account is not None,
        "source_channel_attached": source_attached,
        "note": None if source_attached else (
            f"{recommendation['source_label']!r} isn't synced as a Signal Source in AXIM yet - "
            "the Fund was created and sized correctly, but you'll need to add/sync that channel "
            "and attach it to this Fund manually from the Funds page."
        ),
    }
