"""Backtest Engine / Strategy Lab API (docs/AXIM_APP_PLAN.md) - HTTP
surface over core/backtest_engine.py and the imported_signals/
backtest_* tables in core/database.py. Run simulation happens
synchronously inside POST /api/backtest/runs (in-process, no job
queue) - fine for the signal-pool sizes this feature targets; a very
large pool could make that request slow, a known/documented limit
rather than a silent one.
"""
import csv
import io
import sys
from datetime import datetime
from pathlib import Path

API_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = API_DIR.parent
CORE_DIR = PROJECT_ROOT / "core"
sys.path.insert(0, str(CORE_DIR))

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

import database
import backtest_engine
import ai_analysis
from auth_routes import get_current_user, require_admin

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


# ---------------------------------------------------------------------
# Historical signals
# ---------------------------------------------------------------------

class ManualSignalCreate(BaseModel):
    source_label: str
    asset: str
    direction: str
    expiry: Optional[str] = None
    received_at: str
    result: Optional[str] = None
    payout_percent: Optional[float] = None
    notes: Optional[str] = None


class GradeSignalRequest(BaseModel):
    result: str
    payout_percent: Optional[float] = None
    profit_loss: Optional[float] = None


class CsvImportRequest(BaseModel):
    csv_text: str
    import_batch: Optional[str] = None


@router.get("/sources")
def list_sources(user=Depends(get_current_user)):
    return database.list_historical_signal_sources()


# Caps how many candidate profiles a scorecard tests per source - each
# one is a real simulate_strategy() run over that source's full signal
# history, so an unbounded profile count would make this endpoint slow
# for no real benefit (a scorecard needs "the best fit", not every
# profile ever created tested against every source).
_SCORECARD_MAX_CANDIDATES = 15


@router.get("/scorecard/{source_label}")
def get_scorecard(source_label: str, user=Depends(get_current_user)):
    """Signal Provider Scorecard (docs/AXIM_APP_PLAN.md) - runs a real
    backtest across every available risk profile (templates + the
    user's own, capped) restricted to this source's own graded signal
    history, and reports the result via core/ai_analysis.py. Returns 404
    if the source has no graded history at all - never a fabricated
    scorecard for a source with no evidence."""
    candidates = database.list_risk_profiles(include_templates=True)[:_SCORECARD_MAX_CANDIDATES]
    card = ai_analysis.generate_signal_provider_scorecard(source_label, candidates)
    if card is None:
        raise HTTPException(status_code=404, detail=f"no graded signal history for {source_label!r}")
    return card


@router.get("/signals")
def list_pool(source: str = "both", channel: Optional[str] = None, date_from: Optional[str] = None,
              date_to: Optional[str] = None, user=Depends(get_current_user)):
    if source not in ("live", "imported", "both"):
        raise HTTPException(status_code=400, detail="source must be live, imported, or both")
    channel_filter = [channel] if channel else None
    return database.get_historical_signal_pool(source, channel_filter=channel_filter, date_from=date_from, date_to=date_to)


@router.get("/signals/imported")
def list_imported(import_batch: Optional[str] = None, graded_only: bool = False, user=Depends(get_current_user)):
    return database.list_imported_signals(import_batch=import_batch, graded_only=graded_only)


@router.post("/signals/manual")
def create_manual(body: ManualSignalCreate, user=Depends(require_admin)):
    if body.result is not None and body.result not in ("win", "loss", "draw"):
        raise HTTPException(status_code=400, detail="result must be win, loss, or draw")
    signal_id = database.create_imported_signal(
        body.source_label, body.asset, body.direction, body.expiry, body.received_at,
        result=body.result, payout_percent=body.payout_percent, notes=body.notes,
        import_batch="manual",
    )
    return {"id": signal_id}


@router.post("/signals/import-csv")
def import_csv(body: CsvImportRequest, user=Depends(require_admin)):
    rows, errors = backtest_engine.parse_signal_csv(body.csv_text)
    batch = body.import_batch or f"csv-{datetime.now().isoformat()}"
    imported = 0
    for row in rows:
        database.create_imported_signal(
            row["source_label"], row["asset"], row["direction"], row["expiry"], row["received_at"],
            result=row["result"], payout_percent=row["payout_percent"], notes=row["notes"],
            import_batch=batch,
        )
        imported += 1
    return {"imported": imported, "errors": errors, "import_batch": batch}


@router.patch("/signals/{signal_id}/grade")
def grade_signal(signal_id: int, body: GradeSignalRequest, user=Depends(require_admin)):
    if body.result not in ("win", "loss", "draw"):
        raise HTTPException(status_code=400, detail="result must be win, loss, or draw")
    database.grade_imported_signal(signal_id, body.result, payout_percent=body.payout_percent, profit_loss=body.profit_loss)
    return {"status": "graded"}


@router.delete("/signals/{signal_id}")
def delete_signal(signal_id: int, user=Depends(require_admin)):
    database.delete_imported_signal(signal_id)
    return {"status": "deleted"}


# ---------------------------------------------------------------------
# Backtest runs
# ---------------------------------------------------------------------

class RunCreateRequest(BaseModel):
    name: str
    source: str = "both"
    channel_filter: Optional[list] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    starting_bankroll: float
    default_payout_percent: float = 85
    session_window: str = "daily"
    risk_profile_ids: list[int]


@router.post("/runs")
def create_run(body: RunCreateRequest, user=Depends(require_admin)):
    if body.source not in ("live", "imported", "both"):
        raise HTTPException(status_code=400, detail="source must be live, imported, or both")
    if body.session_window not in ("daily", "all"):
        raise HTTPException(status_code=400, detail="session_window must be daily or all")
    if not body.risk_profile_ids:
        raise HTTPException(status_code=400, detail="select at least one strategy to compare")

    signal_pool = {
        "source": body.source, "channel_filter": body.channel_filter,
        "date_from": body.date_from, "date_to": body.date_to,
    }
    run_id = database.create_backtest_run(
        body.name, signal_pool, body.starting_bankroll,
        default_payout_percent=body.default_payout_percent, session_window=body.session_window,
        created_by=user["email"],
    )

    for profile_id in body.risk_profile_ids:
        profile = database.get_risk_profile(profile_id)
        if profile is None:
            database.update_backtest_run_status(run_id, "failed", error_message=f"risk profile {profile_id} not found")
            raise HTTPException(status_code=404, detail=f"risk profile {profile_id} not found")
        database.create_backtest_strategy(run_id, profile_id, profile["name"], profile)

    try:
        backtest_engine.run_backtest(run_id)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    return database.get_backtest_report(run_id)


@router.get("/runs")
def list_runs(user=Depends(get_current_user)):
    return database.list_backtest_runs()


@router.get("/runs/{run_id}")
def get_run(run_id: int, user=Depends(get_current_user)):
    report = database.get_backtest_report(run_id)
    if report is None:
        raise HTTPException(status_code=404, detail="backtest run not found")
    return report


@router.get("/runs/{run_id}/ai-summary")
def get_run_ai_summary(run_id: int, user=Depends(get_current_user)):
    """The AI Strategy Lab's analyst layer over one run's real, already-
    computed metrics (core/ai_analysis.py) - narrative synthesis, direct
    answers to the standard comparison questions, and ranking categories
    beyond the four backtest_engine.rank_strategies already covers."""
    report = database.get_backtest_report(run_id)
    if report is None:
        raise HTTPException(status_code=404, detail="backtest run not found")
    extended_ranks = ai_analysis.generate_extended_rankings(report)
    return {
        "run_narrative": ai_analysis.generate_run_narrative(report),
        "strategy_narratives": {
            s["id"]: ai_analysis.generate_strategy_narrative(s["label"], s["metrics"])
            for s in report["strategies"]
        },
        "questions": ai_analysis.answer_strategy_questions(report),
        "extended_rankings": extended_ranks,
    }


@router.get("/runs/{run_id}/strategies/{strategy_id}/sessions")
def get_strategy_sessions(run_id: int, strategy_id: int, user=Depends(get_current_user)):
    strategy = database.get_backtest_strategy(strategy_id)
    if strategy is None or strategy["backtest_run_id"] != run_id:
        raise HTTPException(status_code=404, detail="strategy not found in this run")
    return database.list_backtest_sessions(strategy_id)


@router.get("/runs/{run_id}/strategies/{strategy_id}/trades")
def get_strategy_trades(run_id: int, strategy_id: int, session_id: Optional[int] = None,
                         user=Depends(get_current_user)):
    strategy = database.get_backtest_strategy(strategy_id)
    if strategy is None or strategy["backtest_run_id"] != run_id:
        raise HTTPException(status_code=404, detail="strategy not found in this run")
    if session_id is not None:
        return database.list_backtest_trades(session_id)
    return database.list_backtest_trades_for_strategy(strategy_id)


class DeployRequest(BaseModel):
    fund_id: int
    new_profile_name: Optional[str] = None


@router.post("/runs/{run_id}/strategies/{strategy_id}/deploy")
def deploy_strategy(run_id: int, strategy_id: int, body: DeployRequest, user=Depends(require_admin)):
    """Deploy to Fund (docs/AXIM_APP_PLAN.md) - closes the Strategy Lab
    loop. Takes this backtest strategy's point-in-time profile_snapshot,
    creates a fresh, independent risk profile from it (never silently
    reuses/mutates a shared template - see
    database.create_risk_profile_from_snapshot), and sets it as the
    target Fund's default money management profile. Does not touch the
    fund's broker account, sources, or Live-enablement - deploying a
    strategy is a sizing decision, not a full fund reconfiguration."""
    strategy = database.get_backtest_strategy(strategy_id)
    if strategy is None or strategy["backtest_run_id"] != run_id:
        raise HTTPException(status_code=404, detail="strategy not found in this run")
    fund = database.get_fund(body.fund_id)
    if fund is None:
        raise HTTPException(status_code=404, detail="fund not found")

    profile_name = body.new_profile_name or f"{fund['name']} - {strategy['label']} (deployed)"
    new_profile_id = database.create_risk_profile_from_snapshot(profile_name, strategy["profile_snapshot"])
    database.update_fund(body.fund_id, default_risk_profile_id=new_profile_id)

    return {
        "fund": database.get_fund(body.fund_id),
        "deployed_profile_id": new_profile_id,
        "deployed_profile_name": profile_name,
    }


@router.delete("/runs/{run_id}")
def delete_run(run_id: int, user=Depends(require_admin)):
    database.delete_backtest_run(run_id)
    return {"status": "deleted"}


@router.get("/runs/{run_id}/export")
def export_run(run_id: int, format: str = "json", user=Depends(get_current_user)):
    report = database.get_backtest_report(run_id)
    if report is None:
        raise HTTPException(status_code=404, detail="backtest run not found")

    if format == "json":
        import json as json_module
        return Response(content=json_module.dumps(report, indent=2), media_type="application/json",
                         headers={"Content-Disposition": f"attachment; filename=backtest_{run_id}.json"})

    if format == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["strategy", "final_bankroll", "roi_percent", "win_rate", "max_drawdown_percent",
                          "risk_score", "best_for_label", "rank_overall"])
        for s in report["strategies"]:
            m = s.get("metrics") or {}
            writer.writerow([s["label"], m.get("final_bankroll"), m.get("roi_percent"), m.get("win_rate"),
                              m.get("max_drawdown_percent"), m.get("risk_score"), m.get("best_for_label"),
                              m.get("rank_overall")])
        return Response(content=buffer.getvalue(), media_type="text/csv",
                         headers={"Content-Disposition": f"attachment; filename=backtest_{run_id}.csv"})

    raise HTTPException(status_code=400, detail="format must be json or csv")
