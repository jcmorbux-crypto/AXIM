"""Rule Builder API (docs/AXIM_APP_PLAN.md) - visual IF/THEN automation.
core/rule_engine.py owns the actual condition/action logic and the
CONDITION_TYPES/ACTION_TYPES catalogs; this router is the HTTP surface
over core/database.py's rules table, plus a read-only catalog endpoint
so web/rules.html can render dropdowns without hardcoding the schema
twice.
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
import rule_engine
from auth_routes import get_current_user, require_admin

router = APIRouter(prefix="/api/rules", tags=["rules"])


class RuleCreate(BaseModel):
    name: str
    condition_type: str
    condition_params: dict = {}
    action_type: str
    action_params: dict = {}
    enabled: bool = True
    fund_id: int
    scope: str = "fund"
    session_id: Optional[int] = None


class RuleUpdate(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    condition_type: Optional[str] = None
    condition_params: Optional[dict] = None
    action_type: Optional[str] = None
    action_params: Optional[dict] = None
    fund_id: Optional[int] = None
    scope: Optional[str] = None
    session_id: Optional[int] = None


def _validate_types(condition_type, action_type):
    if condition_type is not None and condition_type not in rule_engine.CONDITION_TYPES:
        raise HTTPException(status_code=400, detail=f"unknown condition_type: {condition_type!r}")
    if action_type is not None and action_type not in rule_engine.ACTION_TYPES:
        raise HTTPException(status_code=400, detail=f"unknown action_type: {action_type!r}")


def _validate_action_params(action_type, action_params):
    """Two action types carry an ID reference inside the free-form
    action_params JSON blob rather than a typed Pydantic field, so
    neither was ever checked against real data the way fund_id/
    session_id are:
    - switch_session_risk_profile's risk_profile_id: a rule could be
      saved pointing at a deleted/nonexistent profile and would only
      fail silently (core/risk_engine.py's compute_position_size
      already falls back to static sizing on a missing lookup) the
      next time the rule actually fired.
    - disable_channel's channel_id: core/database.py's
      set_channel_enabled is an UPDATE ... WHERE id = ? with no
      existence check, so a bad id here is a silent no-op forever.
    Both rejected at save time instead, same as every other ID
    reference this router validates."""
    if action_type == "switch_session_risk_profile":
        risk_profile_id = (action_params or {}).get("risk_profile_id")
        if risk_profile_id is not None and database.get_risk_profile(risk_profile_id) is None:
            raise HTTPException(status_code=404, detail="risk profile not found")
    elif action_type == "disable_channel":
        channel_id = (action_params or {}).get("channel_id")
        if channel_id is not None and database.get_channel(channel_id) is None:
            raise HTTPException(status_code=404, detail="channel not found")


def _validate_condition_params(condition_type, condition_params):
    """channel_disabled/source_win_rate_below's channel_id has the same
    unvalidated-JSON-blob shape as switch_session_risk_profile's
    risk_profile_id above, but lower stakes - core/rule_engine.py's
    _cond_channel_disabled/_cond_source_win_rate_below already fail
    closed on a bad channel_id (return False, condition just never
    matches), so this isn't a correctness bug the way the action-side
    one was. Still worth rejecting at save time rather than leaving an
    operator with a rule that silently never fires and no indication
    why - same reasoning, lower severity."""
    if condition_type not in ("channel_disabled", "source_win_rate_below"):
        return
    channel_id = (condition_params or {}).get("channel_id")
    if channel_id is not None and database.get_channel(channel_id) is None:
        raise HTTPException(status_code=404, detail="channel not found")


def _get_or_404(rule_id):
    rule = database.get_rule(rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="rule not found")
    return rule


@router.get("/catalog")
def get_catalog(user=Depends(get_current_user)):
    """Condition/action types with their label + param schema, so the
    builder UI can render dropdowns and the right inputs per param type
    (number/percent/channel/risk_profile) without any hardcoded copy."""
    return {
        "conditions": {
            key: {"label": v["label"], "params": v["params"]}
            for key, v in rule_engine.CONDITION_TYPES.items()
        },
        "actions": {
            key: {"label": v["label"], "params": v["params"]}
            for key, v in rule_engine.ACTION_TYPES.items()
        },
    }


@router.get("")
def list_rules(fund_id: Optional[int] = None, user=Depends(get_current_user)):
    return database.list_rules(fund_id=fund_id)


@router.post("")
def create_rule(body: RuleCreate, user=Depends(require_admin)):
    _validate_types(body.condition_type, body.action_type)
    _validate_action_params(body.action_type, body.action_params)
    _validate_condition_params(body.condition_type, body.condition_params)
    if database.get_fund(body.fund_id) is None:
        raise HTTPException(status_code=404, detail="fund not found")
    if body.scope == "session":
        if body.session_id is None:
            raise HTTPException(status_code=400, detail="a session-scoped rule needs a session_id")
        session = database.get_trading_session(body.session_id)
        if session is None or session["fund_id"] != body.fund_id:
            raise HTTPException(status_code=400, detail="session_id must belong to this fund")
    try:
        rule_id = database.create_rule(
            body.name, body.condition_type, body.condition_params,
            body.action_type, body.action_params, enabled=body.enabled,
            fund_id=body.fund_id, scope=body.scope, session_id=body.session_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return database.get_rule(rule_id)


@router.get("/{rule_id}")
def get_rule(rule_id: int, user=Depends(get_current_user)):
    return _get_or_404(rule_id)


@router.get("/{rule_id}/firings")
def get_rule_firings(rule_id: int, user=Depends(get_current_user)):
    _get_or_404(rule_id)
    return database.list_rule_firings(rule_id)


@router.patch("/{rule_id}")
def update_rule(rule_id: int, body: RuleUpdate, user=Depends(require_admin)):
    rule = _get_or_404(rule_id)
    _validate_types(body.condition_type, body.action_type)
    if body.fund_id is not None and database.get_fund(body.fund_id) is None:
        raise HTTPException(status_code=404, detail="fund not found")

    # Same cross-check create_rule already does, applied to the EFFECTIVE
    # post-update fund_id/scope/session_id (a partial PATCH might only
    # touch one of the three) - found missing here during a review of
    # every ID-typed field across the API for the same pattern. Without
    # this, a rule's session_id could end up pointing at a session
    # belonging to a DIFFERENT fund than the rule itself, and
    # core/rule_engine.py's _resolve_session_for_rule would then let a
    # scope='session' rule's actions (stop/emergency-stop/resize/vault)
    # fire against that unrelated Fund's session - a real cross-Fund
    # correctness issue, not just a display glitch. _resolve_session_for_rule
    # also independently re-checks this at evaluation time now (defense
    # in depth), but rejecting the bad data here too means it's never
    # created in the first place.
    effective_fund_id = body.fund_id if body.fund_id is not None else rule["fund_id"]
    effective_scope = body.scope if body.scope is not None else rule["scope"]
    effective_session_id = body.session_id if body.session_id is not None else rule["session_id"]
    if effective_scope == "session":
        if effective_session_id is None:
            raise HTTPException(status_code=400, detail="a session-scoped rule needs a session_id")
        session = database.get_trading_session(effective_session_id)
        if session is None or session["fund_id"] != effective_fund_id:
            raise HTTPException(status_code=400, detail="session_id must belong to this fund")

    effective_action_type = body.action_type if body.action_type is not None else rule["action_type"]
    effective_action_params = body.action_params if body.action_params is not None else rule["action_params"]
    _validate_action_params(effective_action_type, effective_action_params)

    effective_condition_type = body.condition_type if body.condition_type is not None else rule["condition_type"]
    effective_condition_params = body.condition_params if body.condition_params is not None else rule["condition_params"]
    _validate_condition_params(effective_condition_type, effective_condition_params)

    updates = body.model_dump(exclude_unset=True, exclude={"condition_params", "action_params"})
    try:
        database.update_rule(rule_id, condition_params=body.condition_params, action_params=body.action_params, **updates)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return database.get_rule(rule_id)


@router.delete("/{rule_id}")
def delete_rule(rule_id: int, user=Depends(require_admin)):
    _get_or_404(rule_id)
    database.delete_rule(rule_id)
    return {"status": "deleted"}


@router.post("/{rule_id}/evaluate-now")
def evaluate_now(rule_id: int, user=Depends(require_admin)):
    """Manual "test this rule against current state" trigger for the
    builder UI - runs the exact same evaluate_rule() path the live
    trade.closed event uses, so what the user sees here is guaranteed to
    match real behavior."""
    rule = _get_or_404(rule_id)
    fired = rule_engine.evaluate_rule(rule)
    return {"fired": fired, "rule": database.get_rule(rule_id)}
