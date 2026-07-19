"""Provider Profile API (Universal Signal Intelligence Engine,
2026-07-18 directive) - HTTP surface over core/database.py's
provider_profiles + core/provider_profile.py's trading_mode lifecycle.
This is the "Edit Parsing Profile" / "Start Observation" / "Enable Demo
Trading" / "Reanalyze" browser workflow's real backend - before this,
none of provider_profiles was reachable outside a direct database
query.
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
import provider_profile as pp
from auth_routes import get_current_user, require_admin

router = APIRouter(prefix="/api/provider-profiles", tags=["provider-profiles"])


class ProfileUpdate(BaseModel):
    aliases_json: Optional[str] = None
    asset_patterns_json: Optional[str] = None
    direction_patterns_json: Optional[str] = None
    expiry_patterns_json: Optional[str] = None
    entry_time_rules_json: Optional[str] = None
    timezone: Optional[str] = None
    otc_rules_json: Optional[str] = None
    preparation_rules_json: Optional[str] = None
    final_trigger_rules_json: Optional[str] = None
    result_rules_json: Optional[str] = None
    correction_rules_json: Optional[str] = None
    cancellation_rules_json: Optional[str] = None
    expected_sequence_json: Optional[str] = None
    assembly_timeout_seconds: Optional[int] = None
    confidence_threshold: Optional[float] = None
    known_unsupported_formats_json: Optional[str] = None
    requires_image: Optional[bool] = None
    graduation_min_signals: Optional[int] = None
    graduation_min_success_rate: Optional[float] = None
    graduation_min_confidence: Optional[float] = None


class RevertRequest(BaseModel):
    reason: str


def _get_or_404(profile_id):
    profile = database.get_provider_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="provider profile not found")
    return profile


def _with_graduation(profile):
    import json
    breakdown = json.loads(profile["coverage_breakdown_json"]) if profile.get("coverage_breakdown_json") else None
    return {**profile, "graduation": pp.graduation_status(profile), "coverage_breakdown": breakdown}


@router.get("")
def list_profiles(trading_mode: Optional[str] = None, user=Depends(get_current_user)):
    """Bulk list, keyed by channel_id client-side - lets the Telegram
    Sources page show every source's trading_mode/pattern at a glance
    without one request per card."""
    return database.list_provider_profiles(trading_mode=trading_mode)


@router.get("/by-channel/{channel_id}")
def get_or_create_profile_for_channel(channel_id: int, user=Depends(get_current_user)):
    """Lazily creates a profile (safe defaults, trading_mode=observation)
    the first time a channel's profile is ever viewed - a channel synced
    from OPT SIGNALS but never analyzed still has SOMETHING sane to show
    rather than a 404."""
    if database.get_channel(channel_id) is None:
        raise HTTPException(status_code=404, detail="channel not found")
    profile = database.get_or_create_provider_profile(channel_id)
    return _with_graduation(profile)


@router.get("/{profile_id}")
def get_profile(profile_id: int, user=Depends(get_current_user)):
    return _with_graduation(_get_or_404(profile_id))


@router.patch("/{profile_id}")
def update_profile(profile_id: int, body: ProfileUpdate, user=Depends(require_admin)):
    """The real "Edit Parsing Profile" write path - an operator changing
    timezone/assembly_timeout/confidence_threshold/graduation thresholds
    (or any of the *_rules_json fields, once the browser exposes a real
    editor for them) here changes how this source is parsed/graduated
    without a code change or a deploy, exactly what the directive
    requires. Every change is auditable (core/database.py's
    update_provider_profile always appends a provider_profile_history
    row)."""
    _get_or_404(profile_id)
    fields = body.model_dump(exclude_unset=True)
    try:
        database.update_provider_profile(
            profile_id, changed_by=user["email"], reason="edited via browser", **fields,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _with_graduation(database.get_provider_profile(profile_id))


@router.get("/{profile_id}/history")
def get_profile_history(profile_id: int, limit: int = 50, user=Depends(get_current_user)):
    _get_or_404(profile_id)
    return database.list_provider_profile_history(profile_id, limit=limit)


@router.post("/{profile_id}/graduate")
def graduate_profile(profile_id: int, user=Depends(require_admin)):
    """observation -> demo_ready, only if graduation_status says
    eligible - see core/provider_profile.py's own docstring for exactly
    why this can fail (a real 400 with the specific unmet criteria, not
    a silent no-op)."""
    _get_or_404(profile_id)
    try:
        pp.graduate_to_demo_ready(profile_id, changed_by=user["email"])
    except pp.ProviderProfileError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _with_graduation(database.get_provider_profile(profile_id))


@router.post("/{profile_id}/approve-demo")
def approve_demo_profile(profile_id: int, user=Depends(require_admin)):
    """demo_ready -> demo. A real, attributed human decision - never
    automatic, even once graduation criteria are met."""
    _get_or_404(profile_id)
    try:
        pp.approve_demo(profile_id, approved_by=user["email"])
    except pp.ProviderProfileError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _with_graduation(database.get_provider_profile(profile_id))


@router.post("/{profile_id}/approve-live")
def approve_live_profile(profile_id: int, user=Depends(require_admin)):
    """demo -> live. SEPARATE explicit approval from Demo (directive:
    "Live trading requires separate explicit approval") - requires this
    exact profile to have already been through a real approve_demo, not
    just any prior state."""
    _get_or_404(profile_id)
    try:
        pp.approve_live(profile_id, approved_by=user["email"])
    except pp.ProviderProfileError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _with_graduation(database.get_provider_profile(profile_id))


@router.post("/{profile_id}/revert-to-observation")
def revert_profile(profile_id: int, body: RevertRequest, user=Depends(require_admin)):
    """Any state -> observation - the safe fallback for format drift or
    manual operator judgment, always allowed. reason is required (not
    optional) - this is exactly the kind of change that must be
    auditable with a real "why," per the directive."""
    _get_or_404(profile_id)
    pp.revert_to_observation(profile_id, reason=body.reason, changed_by=user["email"])
    return _with_graduation(database.get_provider_profile(profile_id))
