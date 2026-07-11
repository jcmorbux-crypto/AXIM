"""AXIM Capital Strategies (tm) API - catalog browsing and a basic demo
simulation endpoint. Strategy INSTANCE configuration (creating/editing a
risk_profile with a strategy_key, its apex-ascension/drawdown-protection/
cashflow/strike sub-config) is exposed by api/risk_engine_routes.py -
this router is only the read-only catalog + simulate-before-you-commit
surface, matching the spec's own separation ("review strategy cards" ->
"configure the strategy" is a distinct step from browsing).
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

import capital_strategies_catalog as catalog
import capital_strategies as engine
from auth_routes import get_current_user

router = APIRouter(prefix="/api/capital-strategies", tags=["capital-strategies"])


@router.get("/catalog")
def get_catalog(user=Depends(get_current_user)):
    return catalog.get_catalog()


@router.get("/{strategy_key}")
def get_strategy(strategy_key: str, user=Depends(get_current_user)):
    strategy = catalog.get_strategy(strategy_key)
    if strategy is None:
        raise HTTPException(status_code=404, detail=f"no strategy {strategy_key!r} in the catalog")
    return strategy


class SimulateRequest(BaseModel):
    settings: dict
    num_trades: int = 100
    win_rate: float = 0.5
    avg_payout_percent: float = 90
    starting_bankroll: Optional[float] = None
    seed: Optional[int] = None


@router.post("/{strategy_key}/simulate")
def simulate(strategy_key: str, body: SimulateRequest, user=Depends(get_current_user)):
    """Basic single-path demo simulation (Phase 1 scope - see
    core/capital_strategies.py's own docstring on why this is
    deliberately not the full Monte Carlo Strategy Lab)."""
    strategy = catalog.get_strategy(strategy_key)
    if strategy is None:
        raise HTTPException(status_code=404, detail=f"no strategy {strategy_key!r} in the catalog")
    try:
        return engine.simulate_strategy(
            strategy_key, body.settings, body.num_trades, body.win_rate,
            body.avg_payout_percent, starting_bankroll=body.starting_bankroll, seed=body.seed,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
