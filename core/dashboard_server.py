"""
AXIM Performance Dashboard - read-only, local-only web UI.

Serves dashboard/index.html and a JSON API (GET /api/data) bundling:
- core/trade_statistics.py's daily/weekly stats: win rate, profit/loss,
  ROI, consecutive wins/losses, signals ignored/rejected.
- core/timeline_report.py's full-observability aggregates: P50/P95/P99
  per stage transition and per time category (waiting/browser/database/
  logging/active), across every trade with timeline data.
- database.get_recovery_event_stats() - real recovery-rate data across
  all 4 recovery layers (browser_reconnect, worker_pool_rebuild,
  resume_open_trade, process_restart).
- The most recent signals (any status), for a live activity table.

Deliberately stdlib-only (http.server) - no new dependency for a
single-operator, local-only tool. Read-only: never imports
trade_coordinator/pocket_executor/risk_manager, never writes to the
database, never executes trades. Binds to 127.0.0.1 only.

Run: python core/dashboard_server.py
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CORE_DIR.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DASHBOARD_DIR = PROJECT_ROOT / "dashboard"
INDEX_HTML_PATH = DASHBOARD_DIR / "index.html"

sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(CONFIG_DIR))

import database
import trade_statistics
import timeline_report
from settings import DASHBOARD_PORT
from logger import get_logger

logger = get_logger("axim.dashboard", filename="dashboard.log")


def build_dashboard_data():
    _, timeline_aggregate = timeline_report.generate_report(limit=200)
    return {
        "statistics": trade_statistics.full_report(),
        "recovery": database.get_recovery_event_stats(),
        "timeline": timeline_aggregate,
        "recent_trades": database.get_recent_signals(25),
    }


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Quiet by default - the UI polls every few seconds and stdout
        # noise from that isn't useful. Real errors still go through
        # logger.error() in _serve_data below.
        pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._serve_index()
        elif self.path == "/api/data":
            self._serve_data()
        else:
            self.send_response(404)
            self.end_headers()

    def _serve_index(self):
        try:
            body = INDEX_HTML_PATH.read_bytes()
        except FileNotFoundError:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"dashboard/index.html not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_data(self):
        try:
            data = build_dashboard_data()
            body = json.dumps(data, default=str).encode("utf-8")
            status = 200
        except Exception as e:
            logger.error("dashboard: failed to build /api/data response: %s", e)
            body = json.dumps({"error": str(e)}).encode("utf-8")
            status = 500
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    server = ThreadingHTTPServer(("127.0.0.1", DASHBOARD_PORT), DashboardHandler)
    print(f"AXIM Dashboard (read-only) - http://127.0.0.1:{DASHBOARD_PORT}")
    print("Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down dashboard server...")
        server.shutdown()


if __name__ == "__main__":
    main()
