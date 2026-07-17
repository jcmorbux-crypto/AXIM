"""Automatic Provider Onboarding API (Phase 2 Priority #1/#2) - HTTP
surface over core/provider_onboarding.py. One action: point it at a
Telegram channel the account can already see, and it fetches history,
auto-detects the signal language, imports decided trades, backtests
all 4 official strategies, and generates a capital recommendation.
"""
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = API_DIR.parent
CORE_DIR = PROJECT_ROOT / "core"
sys.path.insert(0, str(CORE_DIR))

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import provider_onboarding
from auth_routes import require_admin

router = APIRouter(prefix="/api/provider-onboarding", tags=["provider-onboarding"])


class PreviewProviderRequest(BaseModel):
    chat_id: int
    source_label: Optional[str] = None
    days: int = provider_onboarding.DEFAULT_HISTORY_DAYS


class AnalyzeProviderRequest(BaseModel):
    chat_id: int
    source_label: Optional[str] = None
    days: int = provider_onboarding.DEFAULT_HISTORY_DAYS
    excluded_message_ids: Optional[List[int]] = None


@router.post("/preview")
async def preview_provider(body: PreviewProviderRequest, user=Depends(require_admin)):
    """Provider Onboarding Wizard Step 4 (Preview and Validate) - runs
    detection against this provider's real history and returns a sample
    of parsed signal/result pairs for review, WITHOUT writing anything
    to the database. Requires axim_ui_session.session to already be
    authenticated, same requirement POST /api/channels/sync already has."""
    try:
        result = await provider_onboarding.preview_provider(
            body.chat_id, source_label=body.source_label, days=body.days,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"could not preview this provider: {e}")
    return result


@router.post("/analyze")
async def analyze_provider(body: AnalyzeProviderRequest, user=Depends(require_admin)):
    """Requires axim_ui_session.session to already be authenticated,
    same requirement POST /api/channels/sync and
    /api/backtest/signals/import-telegram-history already have."""
    excluded = set(body.excluded_message_ids) if body.excluded_message_ids else None
    try:
        result = await provider_onboarding.analyze_and_onboard_provider(
            body.chat_id, source_label=body.source_label, created_by=user["email"],
            days=body.days, excluded_message_ids=excluded,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"could not analyze this provider: {e}")
    return result
