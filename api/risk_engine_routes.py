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
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import daily_compounding
import database
import risk_engine as risk_engine_module
from auth_routes import get_current_user, require_admin

router = APIRouter(prefix="/api/risk-profiles", tags=["risk-engine"])

# "apex_ascension"/"empire" added for AXIM Capital Strategies (tm) - core/
# capital_strategies.py's own real sizing calculations, alongside the
# pre-existing fixed/percent/dynamic/kelly modes (unchanged). Momentum/
# Fortress/Sentinel/Cashflow/Strike are modifiers layered on top of
# whichever of these base modes is active, not sizing modes of their own,
# so they don't belong in this set. "daily_compounding" (Money Management
# Studio's 5th official strategy) is a real sizing mode of its own too -
# see core/daily_compounding.py.
_VALID_SIZING_MODES = {"fixed", "percent", "dynamic", "kelly", "apex_ascension", "empire", "daily_compounding"}


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
    strategy_key: Optional[str] = None


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
    strategy_key: Optional[str] = None


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


class ApexAscensionUpdate(BaseModel):
    enabled: Optional[bool] = None
    starting_bankroll: Optional[float] = None
    starting_unit_value: Optional[float] = None
    standard_units: Optional[float] = None
    first_reset_threshold: Optional[float] = None
    reset_increment: Optional[float] = None
    reset_unit_step: Optional[float] = None
    downgrade_protection: Optional[bool] = None


class DrawdownProtectionUpdate(BaseModel):
    enabled: Optional[bool] = None
    bands_json: Optional[str] = None
    suspend_above_percent: Optional[float] = None
    scope: Optional[str] = None


class CashflowUpdate(BaseModel):
    enabled: Optional[bool] = None
    target_amount: Optional[float] = None
    target_period: Optional[str] = None
    partial_target_percent: Optional[float] = None
    partial_reduction_percent: Optional[float] = None


class StrikeUpdate(BaseModel):
    enabled: Optional[bool] = None
    max_session_duration_minutes: Optional[float] = None
    max_consecutive_losses: Optional[int] = None


class MomentumUpdate(BaseModel):
    enabled: Optional[bool] = None
    max_steps: Optional[int] = None
    multiplier: Optional[float] = None
    custom_ladder_json: Optional[str] = None
    profit_lock_percent: Optional[float] = None


class FortressUpdate(BaseModel):
    enabled: Optional[bool] = None
    protection_threshold: Optional[float] = None
    protected_principal: Optional[float] = None


class EmpireUpdate(BaseModel):
    enabled: Optional[bool] = None
    starting_amount: Optional[float] = None
    target_amount: Optional[float] = None
    num_levels: Optional[int] = None
    levels_json: Optional[str] = None
    failure_behavior: Optional[str] = None
    checkpoint_level: Optional[int] = None
    current_level: Optional[int] = None


class DailyCompoundingUpdate(BaseModel):
    enabled: Optional[bool] = None
    risk_percent: Optional[float] = None
    risk_fixed_amount: Optional[float] = None
    profit_target_percent: Optional[float] = None
    profit_target_fixed_amount: Optional[float] = None
    loss_limit_percent: Optional[float] = None
    loss_limit_fixed_amount: Optional[float] = None
    timezone: Optional[str] = None
    max_trades_per_day: Optional[int] = None
    max_concurrent_trades: Optional[int] = None
    cooldown_after_loss_seconds: Optional[int] = None
    consecutive_loss_stop: Optional[int] = None
    vault_enabled: Optional[bool] = None
    vault_percent_on_target: Optional[float] = None
    stop_after_target: Optional[bool] = None
    stop_after_loss_limit: Optional[bool] = None


def _get_or_404(profile_id):
    profile = database.get_risk_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="risk profile not found")
    return profile


def _reject_if_template(profile):
    if profile["is_template"]:
        raise HTTPException(status_code=400, detail="templates are read-only - duplicate first, then edit the copy")


@router.get("")
def list_profiles(include_templates: bool = True, include_archived: bool = False, user=Depends(get_current_user)):
    return database.list_risk_profiles(include_templates=include_templates, include_archived=include_archived)


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


def _reject_if_in_use(profile_id, action):
    """Shared "who's still using this" guard for both archive and delete
    (Money Management Studio's "My Strategies" section, 2026-07-18
    directive: "archive safely"/"delete safely") - same 409 pattern as
    broker account archival (api/broker_accounts_routes.py's archive_
    broker_account)."""
    funds_using_it = database.list_funds_using_risk_profile(profile_id)
    if funds_using_it:
        names = ", ".join(f["name"] for f in funds_using_it)
        raise HTTPException(
            status_code=409,
            detail=f"still the default strategy for: {names} - change their default strategy before you {action} it",
        )
    active_sessions = [s for s in database.list_active_trading_sessions() if s["risk_profile_id"] == profile_id]
    if active_sessions:
        raise HTTPException(
            status_code=409,
            detail=f"{len(active_sessions)} active session(s) are currently using this strategy - stop them before you {action} it",
        )


@router.post("/{profile_id}/archive")
def archive_profile(profile_id: int, user=Depends(require_admin)):
    profile = _get_or_404(profile_id)
    _reject_if_template(profile)
    _reject_if_in_use(profile_id, "archive")
    database.archive_risk_profile(profile_id)
    return database.get_risk_profile(profile_id)


@router.post("/{profile_id}/unarchive")
def unarchive_profile(profile_id: int, user=Depends(require_admin)):
    _get_or_404(profile_id)
    database.unarchive_risk_profile(profile_id)
    return database.get_risk_profile(profile_id)


@router.delete("/{profile_id}")
def delete_profile(profile_id: int, user=Depends(require_admin)):
    """"Delete safely" (Money Management Studio product directive,
    2026-07-18): blocks deletion while a Fund still points at this
    profile as its own default, or while a currently active session is
    using it, rather than silently leaving a Fund/session pointing at a
    now-nonexistent risk_profile_id."""
    profile = _get_or_404(profile_id)
    _reject_if_template(profile)
    _reject_if_in_use(profile_id, "delete")
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


@router.patch("/{profile_id}/apex-ascension")
def update_apex_ascension(profile_id: int, body: ApexAscensionUpdate, user=Depends(require_admin)):
    profile = _get_or_404(profile_id)
    _reject_if_template(profile)
    database.update_apex_ascension_settings(profile_id, **body.model_dump(exclude_unset=True))
    return database.get_risk_profile(profile_id)


@router.get("/{profile_id}/tier-history")
def tier_history(profile_id: int, user=Depends(get_current_user)):
    _get_or_404(profile_id)
    return database.list_tier_events(profile_id)


@router.patch("/{profile_id}/drawdown-protection")
def update_drawdown_protection(profile_id: int, body: DrawdownProtectionUpdate, user=Depends(require_admin)):
    profile = _get_or_404(profile_id)
    _reject_if_template(profile)
    database.update_drawdown_protection_settings(profile_id, **body.model_dump(exclude_unset=True))
    return database.get_risk_profile(profile_id)


@router.patch("/{profile_id}/cashflow")
def update_cashflow(profile_id: int, body: CashflowUpdate, user=Depends(require_admin)):
    profile = _get_or_404(profile_id)
    _reject_if_template(profile)
    database.update_cashflow_settings(profile_id, **body.model_dump(exclude_unset=True))
    return database.get_risk_profile(profile_id)


@router.patch("/{profile_id}/strike")
def update_strike(profile_id: int, body: StrikeUpdate, user=Depends(require_admin)):
    profile = _get_or_404(profile_id)
    _reject_if_template(profile)
    database.update_strike_settings(profile_id, **body.model_dump(exclude_unset=True))
    return database.get_risk_profile(profile_id)


@router.patch("/{profile_id}/momentum")
def update_momentum(profile_id: int, body: MomentumUpdate, user=Depends(require_admin)):
    profile = _get_or_404(profile_id)
    _reject_if_template(profile)
    database.update_momentum_settings(profile_id, **body.model_dump(exclude_unset=True))
    return database.get_risk_profile(profile_id)


@router.patch("/{profile_id}/fortress")
def update_fortress(profile_id: int, body: FortressUpdate, user=Depends(require_admin)):
    profile = _get_or_404(profile_id)
    _reject_if_template(profile)
    database.update_fortress_settings(profile_id, **body.model_dump(exclude_unset=True))
    return database.get_risk_profile(profile_id)


@router.patch("/{profile_id}/empire")
def update_empire(profile_id: int, body: EmpireUpdate, user=Depends(require_admin)):
    profile = _get_or_404(profile_id)
    _reject_if_template(profile)
    database.update_empire_settings(profile_id, **body.model_dump(exclude_unset=True))
    return database.get_risk_profile(profile_id)


@router.patch("/{profile_id}/daily-compounding")
def update_daily_compounding(profile_id: int, body: DailyCompoundingUpdate, user=Depends(require_admin)):
    """Money Management Studio's 5th official strategy (2026-07-18
    directive): "minimum 1%" is a real, server-enforced floor - a
    risk_percent below daily_compounding.MIN_RISK_PERCENT is rejected
    outright, not silently clamped, so the UI's own validation can never
    silently drift from what the backend actually accepts. timezone is
    validated against the real IANA tz database (the same one core/
    daily_compounding.trading_date_for uses to compute the daily
    boundary) - a typo here must be caught at save time, not discovered
    the first time it silently falls back to UTC in production."""
    profile = _get_or_404(profile_id)
    _reject_if_template(profile)
    fields = body.model_dump(exclude_unset=True)

    if "risk_percent" in fields and fields["risk_percent"] is not None:
        if fields["risk_percent"] < daily_compounding.MIN_RISK_PERCENT:
            raise HTTPException(
                status_code=400,
                detail=f"risk_percent must be at least {daily_compounding.MIN_RISK_PERCENT}%",
            )
    if "timezone" in fields and fields["timezone"]:
        try:
            ZoneInfo(fields["timezone"])
        except (ZoneInfoNotFoundError, ValueError):
            raise HTTPException(status_code=400, detail=f"unknown timezone {fields['timezone']!r}")

    database.update_daily_compounding_settings(profile_id, **fields)
    return database.get_risk_profile(profile_id)


@router.get("/{profile_id}/projected-exposure")
def projected_exposure(profile_id: int, base_amount: Optional[float] = None, user=Depends(get_current_user)):
    profile = _get_or_404(profile_id)
    amount = base_amount if base_amount is not None else profile["fixed_amount"]
    return risk_engine_module.project_martingale_exposure(profile["martingale"], amount)
