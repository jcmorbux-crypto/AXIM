"""Real running-build identification (2026-07-25 Execution Reliability
lesson): a long-running process (core/telegram_listener.py, the API
server) keeps executing whatever code was already loaded in memory when
it started - Python does not hot-reload on `git pull`/a new commit
landing on disk. Confirmed live this session: the listener ran 5 days
of stale code across this entire session's fixes (parser hardening,
duplicate-detection scoping, the whole per-broker-account safety
hierarchy) before this was caught, purely because nobody had restarted
it since 7/20. Every long-running AXIM process should record which
real commit it started from, so "is this actually running current
code" is a fact you can query, not an assumption.
"""
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def get_repo_head_commit():
    """The commit currently on disk (what a fresh process would load if
    started right now) - NOT necessarily what any already-running
    process actually has in memory. Returns None if git isn't available
    or this isn't a git checkout (e.g. a packaged deployment) - callers
    should treat that as "unknown", never as "definitely stale"."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT,
            capture_output=True, text=True, timeout=5, check=True,
        )
        return result.stdout.strip()
    except Exception:
        return None


def record_process_startup(database, process_name, changed_by="system"):
    """Called once, at the very start of a long-running process
    (telegram_listener.py's run_forever, api/main.py's module import) -
    persists the real commit this process actually loaded, so a later
    "is the running build current" check can compare it against
    get_repo_head_commit() instead of guessing from process uptime."""
    from datetime import datetime
    commit = get_repo_head_commit()
    database.set_setting(f"{process_name}_running_commit", commit,
                          changed_by=changed_by, source="process_startup")
    database.set_setting(f"{process_name}_started_at", datetime.now().isoformat(),
                          changed_by=changed_by, source="process_startup")
    return commit
