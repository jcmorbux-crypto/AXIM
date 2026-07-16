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

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import provider_onboarding
from auth_routes import require_admin

router = APIRouter(prefix="/api/provider-onboarding", tags=["provider-onboarding"])


class AnalyzeProviderRequest(BaseModel):
    chat_id: int
    source_label: Optional[str] = None


@router.post("/analyze")
async def analyze_provider(body: AnalyzeProviderRequest, user=Depends(require_admin)):
    """Requires axim_ui_session.session to already be authenticated,
    same requirement POST /api/channels/sync and
    /api/backtest/signals/import-telegram-history already have."""
    try:
        result = await provider_onboarding.analyze_and_onboard_provider(
            body.chat_id, source_label=body.source_label, created_by=user["email"],
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"could not analyze this provider: {e}")
    return result
