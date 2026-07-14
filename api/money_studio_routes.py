"""Money Management Studio - the 4 official strategies + Custom
Strategy Builder catalog (core/money_studio.py). Read-only content:
"Use This Strategy"/"Create From This Template" saves through the
existing, unchanged POST /api/risk-profiles + its martingale/vault
PATCH sub-endpoints, not a new write path here.
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


class CreateFromStrategyRequest(BaseModel):
    name: str
    bankroll: float = 1000


@router.post("/{strategy_key}/create-profile")
def create_profile_from_strategy(strategy_key: str, body: CreateFromStrategyRequest, user=Depends(require_admin)):
    """"Use This Strategy" - the ONE write path this router has, and it
    goes through the exact same core/database.py functions the existing
    hand-built risk-profile form already uses (create_risk_profile,
    update_martingale_settings, update_profit_vault_settings) - not a
    new mechanism. See core/money_studio.risk_profile_fields_for for
    exactly what's real vs approximated per strategy."""
    create_fields, martingale_fields, vault_fields = money_studio.risk_profile_fields_for(
        strategy_key, body.name, body.bankroll)
    if create_fields is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    name = create_fields.pop("name")
    description = create_fields.pop("description", None)
    profile_id = database.create_risk_profile(name, description=description, **create_fields)
    if martingale_fields:
        database.update_martingale_settings(profile_id, **martingale_fields)
    if vault_fields:
        database.update_profit_vault_settings(profile_id, **vault_fields)
    return database.get_risk_profile(profile_id)
