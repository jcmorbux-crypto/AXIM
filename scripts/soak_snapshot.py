"""Soak-test snapshot (docs/AXIM_RELEASE_CHECKLIST.md section "Functional",
docs/AXIM_PRODUCTION_READINESS_REPORT.md section 6) - captures one
point-in-time health reading of the live listener process, its Chrome
workers, and real trade/error counts from the DB, appended to
logs/soak_test_log.csv. Run repeatedly (not continuously) over a
multi-hour window via a scheduler - the CSV built up across many runs is
the actual soak-test evidence, not any single snapshot.
"""
import csv
import datetime
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CORE_DIR = PROJECT_ROOT / "core"
sys.path.insert(0, str(CORE_DIR))
import database

LOG_CSV = PROJECT_ROOT / "logs" / "soak_test_log.csv"
AXIM_LOG = PROJECT_ROOT / "logs" / "axim.log"
STATE_FILE = PROJECT_ROOT / "logs" / ".soak_state"

FIELDS = [
    "timestamp", "listener_pid", "listener_uptime_min", "listener_mem_mb",
    "chrome_count", "chrome_mem_mb", "signals_total", "wins", "losses",
    "draws", "errors_total", "rejected_total", "recovery_events_total",
    "new_error_lines",
]


def _ps(cmd):
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", cmd],
        capture_output=True, text=True, timeout=30,
    )
    return out.stdout.strip()


def get_listener_stats():
    out = _ps(
        "$p = Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "Where-Object { $_.CommandLine -like '*telegram_listener.py*' } | Select-Object -First 1; "
        "if ($p) { $proc = Get-Process -Id $p.ProcessId; "
        "$uptime = (New-TimeSpan -Start $p.CreationDate -End (Get-Date)).TotalMinutes; "
        "\"$($p.ProcessId)|$([math]::Round($proc.WorkingSet64/1MB,1))|$([math]::Round($uptime,1))\" }"
    )
    if not out:
        return None, None, None
    pid, mem, uptime = out.split("|")
    return int(pid), float(mem), float(uptime)


def get_chrome_stats():
    out = _ps(
        "$procs = Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        "Where-Object { $_.CommandLine -like '*sessions\\pocket_browser*' }; "
        "$total = 0; foreach ($p in $procs) { try { $total += (Get-Process -Id $p.ProcessId).WorkingSet64 } catch {} }; "
        "\"$($procs.Count)|$([math]::Round($total/1MB,1))\""
    )
    if not out:
        return 0, 0.0
    count, mem = out.split("|")
    return int(count), float(mem)


def get_db_stats():
    conn = database.get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM signals")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM signals WHERE result = 'win'")
    wins = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM signals WHERE result = 'loss'")
    losses = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM signals WHERE result = 'draw'")
    draws = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM signals WHERE result LIKE 'error:%'")
    errors = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM signals WHERE result LIKE 'rejected:%'")
    rejected = cur.fetchone()[0]
    try:
        cur.execute("SELECT COUNT(*) FROM recovery_events")
        recovery = cur.fetchone()[0]
    except Exception:
        recovery = None
    conn.close()
    return total, wins, losses, draws, errors, rejected, recovery


def count_new_error_lines():
    if not AXIM_LOG.exists():
        return 0
    lines = AXIM_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    last_count = int(STATE_FILE.read_text()) if STATE_FILE.exists() else 0
    new_lines = lines[last_count:]
    new_errors = sum(1 for l in new_lines if " ERROR " in l or " CRITICAL " in l)
    STATE_FILE.write_text(str(len(lines)))
    return new_errors


def main():
    now = datetime.datetime.now().isoformat(timespec="seconds")

    pid, mem, uptime = get_listener_stats()
    chrome_count, chrome_mem = get_chrome_stats()
    total, wins, losses, draws, errors, rejected, recovery = get_db_stats()
    new_errors = count_new_error_lines()

    row = {
        "timestamp": now,
        "listener_pid": pid,
        "listener_uptime_min": uptime,
        "listener_mem_mb": mem,
        "chrome_count": chrome_count,
        "chrome_mem_mb": chrome_mem,
        "signals_total": total,
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "errors_total": errors,
        "rejected_total": rejected,
        "recovery_events_total": recovery,
        "new_error_lines": new_errors,
    }

    is_new = not LOG_CSV.exists()
    with open(LOG_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow(row)

    print(f"[{now}] listener pid={pid} uptime={uptime}min mem={mem}MB | "
          f"chrome: {chrome_count} procs, {chrome_mem}MB | "
          f"signals: {total} (win={wins} loss={losses} draw={draws} "
          f"error={errors} rejected={rejected}) | "
          f"recovery_events={recovery} | new_error_lines_in_log={new_errors}")

    if pid is None:
        print("WARNING: telegram_listener.py process not found - soak test vehicle is down!")


if __name__ == "__main__":
    main()
