"""Backups API (docs/AXIM_APP_PLAN.md Settings > Backups) - lists real
backups/ directory contents and runs the existing, already-tested
scripts/backup_axim_state.ps1 rather than reimplementing backup logic
in Python.
"""
import subprocess
import sys
from datetime import datetime
from pathlib import Path

API_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = API_DIR.parent
CORE_DIR = PROJECT_ROOT / "core"
sys.path.insert(0, str(CORE_DIR))

from fastapi import APIRouter, Depends, HTTPException

from auth_routes import require_admin

router = APIRouter(prefix="/api/backups", tags=["backups"])

BACKUP_SCRIPT = PROJECT_ROOT / "scripts" / "backup_axim_state.ps1"
BACKUP_ROOT = PROJECT_ROOT / "backups"


def _dir_size(path):
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


@router.get("")
def list_backups(user=Depends(require_admin)):
    if not BACKUP_ROOT.exists():
        return []
    entries = []
    for d in sorted(BACKUP_ROOT.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        entries.append({
            "name": d.name,
            "size_bytes": _dir_size(d),
            "created_at": datetime.fromtimestamp(d.stat().st_ctime).isoformat(),
        })
    return entries


@router.post("/run")
def run_backup(user=Depends(require_admin)):
    """Runs the real PowerShell backup script synchronously - typically
    a few seconds for the DB/session files, longer if the Chrome profile
    is large, so this has a generous timeout rather than being fire-and-
    forget (the operator clicking the button wants to know it actually
    finished, not just that it started)."""
    if not BACKUP_SCRIPT.exists():
        raise HTTPException(status_code=500, detail=f"backup script not found at {BACKUP_SCRIPT}")
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(BACKUP_SCRIPT)],
            capture_output=True, text=True, timeout=180, cwd=str(PROJECT_ROOT),
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="backup script timed out after 180s")
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"backup script failed (exit {result.returncode}): {result.stderr[-500:]}")
    return {"status": "completed", "output": result.stdout[-2000:]}
