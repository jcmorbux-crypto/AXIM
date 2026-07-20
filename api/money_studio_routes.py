"""Money Management Studio - the 5 official strategies + Custom
Strategy Builder catalog (core/money_studio.py). The 5 official
strategies are defined ONLY in code and never become risk_profiles
rows (2026-07-19 product directive - see money_studio.py's module
docstring): "Use This Strategy" attaches a Fund directly to a
canonical plan key (money_plan_key), no row created. "Create From This
Template" is a genuinely different, unchanged path - it goes through
the existing POST /api/risk-profiles + its martingale/vault PATCH
sub-endpoints (web/risk.html's saveBuilder), producing a real,
user-owned custom profile - that's a legitimate "permitted user
customization," not a duplicate of the canonical catalog.
"""
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = API_DIR.parent
CORE_DIR = PROJECT_ROOT / "core"
sys.path.insert(0, str(CORE_DIR))

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import database
import money_studio
from auth_routes import get_current_user, require_admin

router = APIRouter(prefix="/api/money-strategies", tags=["money-studio"])


@router.get("")
def list_strategies(user=Depends(get_current_user)):
    return {"strategies": [money_studio.strategy_card(s) for s in money_studio.STRATEGIES]}


@router.get("/{strategy_key}")
def get_strategy(strategy_key: str, user=Depends(get_current_user)):
    detail = money_studio.strategy_detail(strategy_key)
    if detail is None:
        return {"error": "strategy not found"}
    return detail


class AttachToFundRequest(BaseModel):
    fund_id: int


@router.post("/{strategy_key}/attach-to-fund")
def attach_strategy_to_fund(strategy_key: str, body: AttachToFundRequest, user=Depends(require_admin)):
    """"Use This Strategy" - sets a Fund's default_money_plan_key
    directly to this canonical strategy, clearing any real custom
    default_risk_profile_id it had (the two are mutually exclusive - a
    Fund's default is either a canonical plan or a real custom profile,
    never both). No risk_profiles row is created or has ever needed to
    be: a canonical plan's bankroll is always the Fund's own real,
    live trading balance (core/risk_engine.py reads it fresh from
    fund_manager at trade time), never a value frozen at "use" time."""
    if strategy_key not in money_studio.STRATEGIES_BY_KEY:
        raise HTTPException(status_code=404, detail="strategy not found")
    fund = database.get_fund(body.fund_id)
    if fund is None:
        raise HTTPException(status_code=404, detail="fund not found")
    database.update_fund(
        body.fund_id, default_money_plan_key=strategy_key, default_risk_profile_id=None,
        changed_by=user["email"], reason=f"Use This Strategy: {strategy_key}",
    )
    return database.get_fund(body.fund_id)
