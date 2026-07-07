"""Risk Engine API (docs/AXIM_APP_PLAN.md Phase 4) - profile CRUD,
duplicate/export/import, and Martingale/Compounding/Vault sub-config.
Real sizing math lives in core/risk_engine.py; this router is the HTTP
surface over core/database.py's risk_profiles tables.
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
import risk_engine as risk_engine_module
from auth_routes import get_current_user, require_admin

router = APIRouter(prefix="/api/risk-profiles", tags=["risk-engine"])

_VALID_SIZING_MODES = {"fixed", "percent", "dynamic", "kelly"}


class ProfileCreate(BaseModel):
    name: str
    description: Optional[str] = None
    bankroll: float = 0
    sizing_mode: str = "fixed"
    fixed_amount: float = 1
    percent_of_bankroll: float = 1
    kelly_win_rate_estimate: Optional[float] = None
    kelly_payout_estimate: Optional[float] = None
    kelly_fraction_multiplier: float = 0.5
    max_trade_amount: float = 0
    max_daily_loss: float = 0
    max_session_loss: float = 0
    profit_target: float = 0
    max_trades: int = 0
    live_allowed: bool = False


class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    bankroll: Optional[float] = None
    sizing_mode: Optional[str] = None
    fixed_amount: Optional[float] = None
    percent_of_bankroll: Optional[float] = None
    kelly_win_rate_estimate: Optional[float] = None
    kelly_payout_estimate: Optional[float] = None
    kelly_fraction_multiplier: Optional[float] = None
    max_trade_amount: Optional[float] = None
    max_daily_loss: Optional[float] = None
    max_session_loss: Optional[float] = None
    profit_target: Optional[float] = None
    max_trades: Optional[int] = None
    live_allowed: Optional[bool] = None


class DuplicateRequest(BaseModel):
    new_name: str


class ImportRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    profile: dict
    martingale: Optional[dict] = None
    compounding: Optional[dict] = None
    profit_vault: Optional[dict] = None


class MartingaleUpdate(BaseModel):
    enabled: Optional[bool] = None
    max_steps: Optional[int] = None
    multiplier: Optional[float] = None
    custom_ladder_json: Optional[str] = None
    reset_after_win: Optional[bool] = None
    reset_after_session: Optional[bool] = None
    max_total_exposure: Optional[float] = None
    confidence_threshold: Optional[float] = None
    same_asset_only: Optional[bool] = None
    same_source_only: Optional[bool] = None


class CompoundingUpdate(BaseModel):
    mode: Optional[str] = None
    base_risk_percent: Optional[float] = None
    steps_json: Optional[str] = None
    drawdown_reset_percent: Optional[float] = None
    max_risk_percent: Optional[float] = None
    min_risk_percent: Optional[float] = None


class VaultUpdate(BaseModel):
    enabled: Optional[bool] = None
    vault_percent: Optional[float] = None
    trigger_event: Optional[str] = None
    milestone_amount: Optional[float] = None


def _get_or_404(profile_id):
    profile = database.get_risk_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="risk profile not found")
    return profile


def _reject_if_template(profile):
    if profile["is_template"]:
        raise HTTPException(status_code=400, detail="templates are read-only - duplicate first, then edit the copy")


@router.get("")
def list_profiles(include_templates: bool = True, user=Depends(get_current_user)):
    return database.list_risk_profiles(include_templates=include_templates)


@router.post("")
def create_profile(body: ProfileCreate, user=Depends(require_admin)):
    if body.sizing_mode not in _VALID_SIZING_MODES:
        raise HTTPException(status_code=400, detail=f"invalid sizing_mode, must be one of {sorted(_VALID_SIZING_MODES)}")
    fields = body.model_dump(exclude={"name", "description"})
    profile_id = database.create_risk_profile(body.name, is_template=False, description=body.description, **fields)
    return database.get_risk_profile(profile_id)


@router.get("/{profile_id}")
def get_profile(profile_id: int, user=Depends(get_current_user)):
    return _get_or_404(profile_id)


@router.patch("/{profile_id}")
def update_profile(profile_id: int, body: ProfileUpdate, user=Depends(require_admin)):
    profile = _get_or_404(profile_id)
    _reject_if_template(profile)
    updates = body.model_dump(exclude_unset=True)
    if "sizing_mode" in updates and updates["sizing_mode"] not in _VALID_SIZING_MODES:
        raise HTTPException(status_code=400, detail=f"invalid sizing_mode, must be one of {sorted(_VALID_SIZING_MODES)}")
    try:
        database.update_risk_profile(profile_id, **updates)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return database.get_risk_profile(profile_id)


@router.delete("/{profile_id}")
def delete_profile(profile_id: int, user=Depends(require_admin)):
    profile = _get_or_404(profile_id)
    _reject_if_template(profile)
    database.delete_risk_profile(profile_id)
    return {"status": "deleted"}


@router.post("/{profile_id}/duplicate")
def duplicate_profile(profile_id: int, body: DuplicateRequest, user=Depends(require_admin)):
    _get_or_404(profile_id)
    new_id = database.duplicate_risk_profile(profile_id, body.new_name)
    return database.get_risk_profile(new_id)


@router.get("/{profile_id}/export")
def export_profile(profile_id: int, user=Depends(require_admin)):
    _get_or_404(profile_id)
    return database.export_risk_profile(profile_id)


@router.post("/import")
def import_profile(body: ImportRequest, user=Depends(require_admin)):
    new_id = database.import_risk_profile(body.model_dump(exclude_unset=True), name=body.name)
    return database.get_risk_profile(new_id)


@router.patch("/{profile_id}/martingale")
def update_martingale(profile_id: int, body: MartingaleUpdate, user=Depends(require_admin)):
    profile = _get_or_404(profile_id)
    _reject_if_template(profile)
    database.update_martingale_settings(profile_id, **body.model_dump(exclude_unset=True))
    return database.get_risk_profile(profile_id)


@router.patch("/{profile_id}/compounding")
def update_compounding(profile_id: int, body: CompoundingUpdate, user=Depends(require_admin)):
    profile = _get_or_404(profile_id)
    _reject_if_template(profile)
    database.update_compounding_settings(profile_id, **body.model_dump(exclude_unset=True))
    return database.get_risk_profile(profile_id)


@router.patch("/{profile_id}/vault")
def update_vault(profile_id: int, body: VaultUpdate, user=Depends(require_admin)):
    profile = _get_or_404(profile_id)
    _reject_if_template(profile)
    database.update_profit_vault_settings(profile_id, **body.model_dump(exclude_unset=True))
    return database.get_risk_profile(profile_id)


@router.get("/{profile_id}/projected-exposure")
def projected_exposure(profile_id: int, base_amount: Optional[float] = None, user=Depends(get_current_user)):
    profile = _get_or_404(profile_id)
    amount = base_amount if base_amount is not None else profile["fixed_amount"]
    return risk_engine_module.project_martingale_exposure(profile["martingale"], amount)
