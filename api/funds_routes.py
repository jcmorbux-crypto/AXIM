"""Funds / Portfolios API (docs/AXIM_APP_PLAN.md) - HTTP surface over
core/database.py's funds/fund_sources CRUD and core/fund_manager.py's
balance/performance aggregation.
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

router = APIRouter(prefix="/api/funds", tags=["funds"])


class FundCreate(BaseModel):
    name: str
    starting_balance: float = 0
    assigned_broker_label: Optional[str] = None
    default_risk_profile_id: Optional[int] = None
    default_session_profile_id: Optional[int] = None
    profit_target: float = 0
    loss_limit: float = 0
    max_trades: int = 0
    status: str = "active"
    live_enabled: bool = False


class FundUpdate(BaseModel):
    name: Optional[str] = None
    starting_balance: Optional[float] = None
    assigned_broker_label: Optional[str] = None
    default_risk_profile_id: Optional[int] = None
    default_session_profile_id: Optional[int] = None
    profit_target: Optional[float] = None
    loss_limit: Optional[float] = None
    max_trades: Optional[int] = None
    status: Optional[str] = None
    live_enabled: Optional[bool] = None


class DuplicateFundRequest(BaseModel):
    new_name: str


class AddSourceRequest(BaseModel):
    channel_id: int


class AssignBrokerAccountRequest(BaseModel):
    broker_account_id: int
    is_primary: bool = True


class CapitalTransferRequest(BaseModel):
    from_fund_id: Optional[int] = None
    to_fund_id: Optional[int] = None
    amount: float
    broker_account_id: Optional[int] = None
    note: Optional[str] = None


def _get_or_404(fund_id):
    fund = database.get_fund(fund_id)
    if fund is None:
        raise HTTPException(status_code=404, detail="fund not found")
    return fund


@router.get("")
def list_funds(status: Optional[str] = None, user=Depends(get_current_user)):
    return fund_manager.list_funds_with_balances(status=status)


@router.post("")
def create_fund(body: FundCreate, user=Depends(require_admin)):
    try:
        fund_id = database.create_fund(body.name, starting_balance=body.starting_balance,
                                        **body.model_dump(exclude={"name", "starting_balance"}))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return fund_manager.get_fund_report(fund_id)


@router.get("/{fund_id}")
def get_fund(fund_id: int, user=Depends(get_current_user)):
    report = fund_manager.get_fund_report(fund_id)
    if report is None:
        raise HTTPException(status_code=404, detail="fund not found")
    return report


@router.patch("/{fund_id}")
def update_fund(fund_id: int, body: FundUpdate, user=Depends(require_admin)):
    _get_or_404(fund_id)
    if body.default_risk_profile_id is not None and database.get_risk_profile(body.default_risk_profile_id) is None:
        raise HTTPException(status_code=404, detail="risk profile not found")
    if body.default_session_profile_id is not None and database.get_session_profile(body.default_session_profile_id) is None:
        raise HTTPException(status_code=404, detail="session profile not found")
    try:
        database.update_fund(fund_id, **body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return fund_manager.get_fund_report(fund_id)


@router.post("/{fund_id}/archive")
def archive_fund(fund_id: int, user=Depends(require_admin)):
    _get_or_404(fund_id)
    database.update_fund(fund_id, status="archived")
    return fund_manager.get_fund_report(fund_id)


@router.post("/{fund_id}/pause")
def pause_fund(fund_id: int, user=Depends(require_admin)):
    """Pausing a Fund blocks new sessions from starting (fund_manager.
    can_trade) and stops its currently active session's channels from
    processing new signals (core/telegram_listener.py) - without
    stopping the session outright, so resuming picks back up exactly
    where it left off rather than requiring a fresh start."""
    _get_or_404(fund_id)
    database.update_fund(fund_id, status="paused")
    return fund_manager.get_fund_report(fund_id)


@router.post("/{fund_id}/resume")
def resume_fund(fund_id: int, user=Depends(require_admin)):
    _get_or_404(fund_id)
    database.update_fund(fund_id, status="active")
    return fund_manager.get_fund_report(fund_id)


@router.post("/{fund_id}/duplicate")
def duplicate_fund(fund_id: int, body: DuplicateFundRequest, user=Depends(require_admin)):
    _get_or_404(fund_id)
    new_id = database.duplicate_fund(fund_id, body.new_name)
    return fund_manager.get_fund_report(new_id)


@router.post("/{fund_id}/sources")
def add_source(fund_id: int, body: AddSourceRequest, user=Depends(require_admin)):
    _get_or_404(fund_id)
    if database.get_channel(body.channel_id) is None:
        raise HTTPException(status_code=404, detail="channel not found")
    database.add_fund_source(fund_id, body.channel_id)
    return {"sources": database.list_fund_source_channel_ids(fund_id)}


@router.delete("/{fund_id}/sources/{channel_id}")
def remove_source(fund_id: int, channel_id: int, user=Depends(require_admin)):
    _get_or_404(fund_id)
    database.remove_fund_source(fund_id, channel_id)
    return {"sources": database.list_fund_source_channel_ids(fund_id)}


@router.post("/{fund_id}/broker-account")
def assign_broker_account(fund_id: int, body: AssignBrokerAccountRequest, user=Depends(require_admin)):
    _get_or_404(fund_id)
    if database.get_broker_account(body.broker_account_id) is None:
        raise HTTPException(status_code=404, detail="broker account not found")
    database.assign_broker_account_to_fund(fund_id, body.broker_account_id, is_primary=body.is_primary)
    return fund_manager.get_fund_report(fund_id)


@router.delete("/{fund_id}/broker-account/{broker_account_id}")
def unassign_broker_account(fund_id: int, broker_account_id: int, user=Depends(require_admin)):
    _get_or_404(fund_id)
    database.unassign_broker_account_from_fund(fund_id, broker_account_id)
    return fund_manager.get_fund_report(fund_id)


@router.get("/{fund_id}/sessions")
def get_fund_sessions(fund_id: int, user=Depends(get_current_user)):
    _get_or_404(fund_id)
    return database.list_fund_sessions(fund_id)


@router.get("/{fund_id}/backtests")
def get_fund_backtests(fund_id: int, user=Depends(get_current_user)):
    _get_or_404(fund_id)
    return database.list_fund_backtest_runs(fund_id)


@router.post("/transfer-capital")
def transfer_capital(body: CapitalTransferRequest, user=Depends(require_admin)):
    """Moves capital between two Funds, or between a Fund and Reserve
    (whichever side is omitted) - see core/fund_manager.transfer_capital
    for the real validation (a fund can't give up more than it currently
    has, Reserve can't give up more than the broker account actually
    holds unallocated)."""
    try:
        transfer_id = fund_manager.transfer_capital(
            from_fund_id=body.from_fund_id, to_fund_id=body.to_fund_id, amount=body.amount,
            broker_account_id=body.broker_account_id, note=body.note, created_by=user["email"],
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "transfer_id": transfer_id,
        "from_fund": fund_manager.get_fund_report(body.from_fund_id) if body.from_fund_id else None,
        "to_fund": fund_manager.get_fund_report(body.to_fund_id) if body.to_fund_id else None,
    }


@router.get("/{fund_id}/transfers")
def get_fund_transfers(fund_id: int, user=Depends(get_current_user)):
    _get_or_404(fund_id)
    return database.list_capital_transfers(fund_id=fund_id)
