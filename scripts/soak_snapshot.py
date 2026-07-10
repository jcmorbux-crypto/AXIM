"""Soak-test snapshot (docs/AXIM_RELEASE_CHECKLIST.md section "Functional",
docs/AXIM_PRODUCTION_READINESS_REPORT.md section 6) - captures one
point-in-time health reading of the live listener process, its Chrome
workers, and real trade/error counts from the DB, appended to
logs/soak_test_log.csv. Run repeatedly (not continuously) over a
multi-hour window via a scheduler - the CSV built up across many runs is
the actual soak-test evidence, not any single snapshot.

Listener PID/uptime/memory and Chrome count/memory are read from
core/database.py's ui_listener_heartbeat table (self-reported by the
listener process itself, see core/telegram_listener.py's
_query_own_process_health) rather than discovered here via WMI process
enumeration - confirmed by live testing that when THIS script runs via
Windows Task Scheduler, Windows blocks it from reading another process's
CommandLine/Path across the logon-session boundary (even under the same
user account), which silently zeroed out every process-health column
while the listener was genuinely healthy the whole time. Self-reporting
sidesteps that boundary entirely instead of working around it.
"""
import csv
import datetime
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CORE_DIR = PROJECT_ROOT / "core"
sys.path.insert(0, str(CORE_DIR))
import database

LOG_CSV = PROJECT_ROOT / "logs" / "soak_test_log.csv"
AXIM_LOG = PROJECT_ROOT / "logs" / "axim.log"
STATE_FILE = PROJECT_ROOT / "logs" / ".soak_state"

# Matches api/main.py's own HEARTBEAT_STALE_THRESHOLD_SECONDS (3x the
# listener's 30s heartbeat interval) - a heartbeat row can exist but be
# stale if the listener crashed without updating it since.
HEARTBEAT_STALE_THRESHOLD_SECONDS = 45

FIELDS = [
    "timestamp", "listener_pid", "listener_uptime_min", "listener_mem_mb",
    "chrome_count", "chrome_mem_mb", "signals_total", "wins", "losses",
    "draws", "errors_total", "rejected_total", "recovery_events_total",
    "new_error_lines", "heartbeat_stale",
]


def get_listener_health():
    """Reads the listener's own self-reported health from the heartbeat
    row rather than querying the OS directly - see module docstring.
    Returns (pid, uptime_min, mem_mb, chrome_count, chrome_mem_mb,
    heartbeat_stale). All process-health fields are None (and
    heartbeat_stale True) if no heartbeat row exists at all yet."""
    hb = database.get_listener_heartbeat()
    if hb is None:
        return None, None, None, None, None, True

    stale = True
    updated_at = hb.get("updated_at")
    if updated_at:
        age_seconds = (datetime.datetime.now() - datetime.datetime.fromisoformat(updated_at)).total_seconds()
        stale = age_seconds > HEARTBEAT_STALE_THRESHOLD_SECONDS

    return (
        hb.get("listener_pid"), hb.get("listener_uptime_min"), hb.get("listener_mem_mb"),
        hb.get("chrome_count"), hb.get("chrome_mem_mb"), stale,
    )


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
    """core/logger.py rotates axim.log via RotatingFileHandler once it
    hits MAX_BYTES - exactly the kind of event a multi-hour soak test
    will genuinely run into. When that happens, the fresh axim.log is
    far shorter than last_count (the line count from before rotation),
    so lines[last_count:] silently returns [] every run afterward -
    reporting new_error_lines=0 (and re-saving that same too-high
    last_count) even while real errors are happening, defeating the
    exact thing this script exists to catch. Treat last_count exceeding
    the current line count as "the file was rotated/truncated", not
    "there are no new lines" - every currently-present line is new
    relative to whatever came before the rotation."""
    if not AXIM_LOG.exists():
        return 0
    lines = AXIM_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    last_count = int(STATE_FILE.read_text()) if STATE_FILE.exists() else 0
    if last_count > len(lines):
        last_count = 0
    new_lines = lines[last_count:]
    new_errors = sum(1 for l in new_lines if " ERROR " in l or " CRITICAL " in l)
    STATE_FILE.write_text(str(len(lines)))
    return new_errors


def main():
    now = datetime.datetime.now().isoformat(timespec="seconds")

    pid, uptime, mem, chrome_count, chrome_mem, stale = get_listener_health()
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
        "heartbeat_stale": stale,
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
          f"recovery_events={recovery} | new_error_lines_in_log={new_errors} | "
          f"heartbeat_stale={stale}")

    if pid is None or stale:
        print("WARNING: listener heartbeat is missing or stale - soak test vehicle may be down!")


if __name__ == "__main__":
    main()
