"""Real log reading for the Logs page (docs/AXIM_APP_PLAN.md Phase 5) -
parses the actual rotating log files core/logger.py writes (format:
"YYYY-MM-DD HH:MM:SS,mmm LEVEL [logger.name] message") plus the
admin_actions table, normalized into one shape so the UI doesn't need to
know these are two different sources. No log aggregation service, no
new logging infrastructure - just reads what's already on disk.
"""
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import database

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"

# Every log file core/logger.py currently writes - see get_logger() calls
# across core/. Reading all of them (not just lifecycle.log/ui.log) is
# what makes the spec's "Broker responses" category real rather than
# missing pocket_dom.log's own entries.
LOG_FILES = [
    "lifecycle.log", "ui.log", "axim.log", "dashboard.log", "pocket_dom.log",
    "source_observer.log", "parser.log",
]

_LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) (\w+) \[([\w.]+)\] (.*)$", re.DOTALL)


def _parse_file(filename, max_lines=2000):
    """Reads the last max_lines physical lines (cheap tail, not a full
    file scan - these files rotate at core/logger.py's MAX_BYTES so they
    stay bounded anyway) and groups multi-line messages (e.g. a captured
    accessibility-tree dump in an error log) under the entry that started
    them, rather than treating each physical line as its own log entry."""
    path = LOG_DIR / filename
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-max_lines:]
    except OSError:
        return []

    entries = []
    current = None
    for line in lines:
        match = _LINE_RE.match(line.rstrip("\n"))
        if match:
            if current:
                entries.append(current)
            timestamp_str, level, module, message = match.groups()
            try:
                timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S,%f").isoformat()
            except ValueError:
                timestamp = timestamp_str
            current = {"timestamp": timestamp, "level": level, "module": module,
                       "message": message, "source_file": filename}
        elif current is not None:
            current["message"] += "\n" + line.rstrip("\n")
    if current:
        entries.append(current)
    return entries


def _admin_actions_as_entries(limit=500):
    entries = []
    for action in database.list_admin_actions(limit=limit):
        target = f" target_user_id={action['target_user_id']}" if action["target_user_id"] else ""
        entries.append({
            "timestamp": action["created_at"],
            "level": "INFO",
            "module": "admin_actions",
            "message": f"{action['action']}{target} - {action['detail'] or ''}".strip(),
            "source_file": "admin_actions",
        })
    return entries


def read_logs(since=None, until=None, level=None, module=None, search=None, limit=200):
    """Merges every log file + admin_actions, filters, and returns the
    most recent `limit` entries newest-first. All filters are optional
    and combine with AND."""
    entries = []
    for filename in LOG_FILES:
        entries.extend(_parse_file(filename))
    entries.extend(_admin_actions_as_entries())

    if since:
        entries = [e for e in entries if e["timestamp"] >= since]
    if until:
        entries = [e for e in entries if e["timestamp"] <= until]
    if level:
        entries = [e for e in entries if e["level"].upper() == level.upper()]
    if module:
        entries = [e for e in entries if module.lower() in e["module"].lower()]
    if search:
        search_lower = search.lower()
        entries = [e for e in entries if search_lower in e["message"].lower()]

    entries.sort(key=lambda e: e["timestamp"], reverse=True)
    return entries[:limit]
