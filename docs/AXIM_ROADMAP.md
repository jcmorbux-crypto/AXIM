# AXIM Roadmap

## Completed milestones

### Phase 1 — Browser Execution Layer (v1.0, VERIFIED)
- Verified Playwright selectors for all 6 trade-panel categories: asset search, asset selection (with explicit OTC/non-OTC disambiguation), expiration, amount, BUY, SELL.
- All interaction functions use Playwright locator waiting / `expect()` assertions / `wait_for_function` polling — no hard-coded sleeps.
- Every verification failure captures screenshot + full HTML + URL to `logs/failures/<timestamp>_<action>/` and aborts immediately (`PocketDomError`).
- Structured per-selector logging (`selector`, `found`, `visible`, `enabled`, `timeout`, `retry`) to `logs/pocket_dom.log`.
- Real click-and-confirmation validated once, deliberately, on the demo account ($1 EUR/USD OTC BUY, confirmed via toast + Opened trades list + balance change).
- Files: `execution/pocket_dom.py`, `execution/browser_session.py`, `execution/pocket_executor.py` (browser layer), `tests/test_pocket_execution_dryrun.py`.

### Phase 2 — Outcome Tracking and Risk Foundation (VERIFIED)
- Trade lifecycle model (`core/trade_lifecycle.py`): `signal_received → trade_prepared → trade_clicked → trade_opened → trade_closed → result_win/result_loss/result_draw`, plus `error`.
- Database schema extended safely (additive `ALTER TABLE`, no data loss): `execution_status`, `opened_at`, `closed_at`, `profit_loss`, `screenshot_paths`, plus previously-unused `channel`/`sender`/`message_id`/`payout` now populated.
- Outcome tracking: after `ARMED=true` opens a trade, a background task waits for the trade to close and classifies win/loss/draw by reading the Closed trades tab, without blocking the Telegram listener from handling new signals.
- Risk rules (`core/risk_manager.py`): max trade amount, max trades/hour, max consecutive losses, cooldown after loss, duplicate signal prevention, demo-only enforcement (hard fail unless `ACCOUNT=DEMO`).
- Structured lifecycle logging to `logs/lifecycle.log` for every state transition and every risk rejection.
- Unit tests for all risk rules (`tests/test_risk_manager.py`, no browser required) — 12/12 passing.

**Known limitation carried forward:** win/loss classification in `wait_for_trade_result()` is confirmed against one directly-observed **loss** sample only. A win case has not yet been directly observed on the real DOM — the classification logic (`final_value >= stake → win`) is a reasonable inference from the confirmed structure, not yet independently verified the way the click layer was. Should be validated against a real win before this feeds risk decisions that matter (e.g. `max_consecutive_losses`).

### Phase 3 — Autonomous Trade Engine (in progress)
- `core/event_bus.py` (previously an empty Phase 1 stub) — minimal async pub/sub. Stage transitions publish events (`trade.signal_received`, `trade.prepared`, `trade.closed`, `trade.error`, `signal.ignored`) instead of anything polling the database.
- `core/trade_coordinator.py` — `TradeCoordinator`, the new single orchestration entry point. Pipeline: **Validation** (signal freshness, `MAX_SIGNAL_AGE`, now finally used) → **Risk Manager** → **Duplicate Detection** → **Trade Lifecycle** → **Pocket Executor** (unchanged browser layer) → **Outcome Tracking** → **Statistics**. Every stage logs `trade_id`, `status`, `elapsed_time`, and failure reason to `logs/lifecycle.log`. The signal is recorded to the database immediately, before any gate runs, so a rejected/ignored signal still has a row to log against and count in statistics.
- `execution/pocket_executor.py` — orchestration (signal recording, risk checks) moved up into `TradeCoordinator`; `execute_trade()` removed as redundant. `prepare_trade()` (the actual browser call sequence) is **byte-for-byte unchanged**. `_track_outcome` renamed to `track_outcome` (visibility only, no behavior change) so `core/recovery.py` can reuse it.
- `core/trade_statistics.py` — daily/weekly win rate, profit/loss, average payout, consecutive wins/losses, ROI, signals ignored, signals rejected. All computed from the existing `signals` table; "ignored" vs "rejected" distinguished by a `result` column prefix convention (`ignored:*` / `rejected:*`). (Named to avoid shadowing Python's stdlib `statistics` module, since this codebase's `sys.path.insert(0, ...)` import style would otherwise make every other file's `import statistics` resolve to this one.)
- `core/recovery.py` — `run_recovery()`, called once at listener startup: marks any trade abandoned at `trade_prepared` (never clicked, nothing to resume) as `error:abandoned_on_restart`, and re-attaches outcome tracking to any trade left at `trade_clicked`/`trade_opened` for its *remaining* time until expiry. Session "restore" is inherent to the existing profile-based persistence — no new mechanism needed there.
- **Dashboard scope decision:** "Performance Dashboard" in this phase means the statistics engine plus an event-bus subscriber that logs structured dashboard-ready events — not an actual web UI. `dashboard/` remains empty; building the UI is a separate follow-up.
- Verified end-to-end against the live demo account: full pipeline (Validation → Risk Manager → Duplicate Detection → Trade Lifecycle → Pocket Executor) with real per-stage timing/logging, correctly halting before the click since `ARMED=false`; recovery correctly found and closed out an abandoned `trade_prepared` row from Phase 2 testing.

**Defect found and fixed during Phase 3 testing:** a test signal for `GBP/JPY` (non-OTC) failed `select_asset`'s verification — not a code regression, but a genuine market-closed condition (the live forex market for that pair was showing `N/A` payout, while the OTC synthetic equivalent stays open 24/7). `select_asset` correctly detected the click didn't change the active instrument and aborted with full diagnostics rather than reporting false success — but it wasted two retries and a failure capture to discover this. **Fixed:** each asset row in the search results carries a `.alist__schedule-info` element when unavailable (found via direct DOM inspection, not guessed); `select_asset` now checks this before clicking and raises a distinct `AssetUntradeableError` immediately, no retry. `pocket_executor.prepare_trade` catches it and returns a clean `{"status": "rejected", "rule": "asset_untradeable"}` instead of an unhandled error. `tests/test_pocket_execution_dryrun.py` now treats this as a skip, not a failure — the suite is no longer environmentally flaky on forex market hours. Verified live: the coordinator now rejects a closed-market signal cleanly instead of crashing.

### Phase 4 — Warm Execution Architecture (VERIFIED)
The old flow opened and closed a fresh Chromium instance on every single trade - the dominant cost, measured at ~12.8s for one `prepare_trade` call in Phase 3. Redesigned for a persistent, always-on session:
- `execution/browser_warmup.py` — `BrowserWarmupService`: launches the browser once at startup, verifies `document.body` carries `is-chart-demo` (hard-fails startup otherwise - an extra safety layer beyond `ARMED`), exposes the one live page via `get_page()`, health-checks and auto-reconnects if the browser crashes. `lock` (an `asyncio.Lock`) serializes access to the single page - one trade at a time, by design; a queued signal waits for the lock rather than getting a second page (explicit tradeoff, not multi-tab concurrency, per this phase's scope).
- `execution/asset_cache.py` — scans every asset category once at startup, caching display name + live tradeable status. Used only as a fast-path pre-rejection (a known-untradeable asset is rejected before touching the browser/lock at all) - `select_asset`'s own live DOM check remains the authoritative source of truth, since the cache can go stale mid-session.
- `core/latency.py` — `LatencyTracker`, millisecond checkpoints logged for every trade: `telegram_received → parsed → risk_approved → asset_selected → expiry_set → amount_set → click_completed → confirmation_detected`.
- `execution/pocket_dom.py` — `select_expiry`/`set_amount` gained the same "already correct, no-op" early-return `select_asset` already had (verified: no-op paths measured at 0-15ms vs multi-second DOM round-trips).
- `execution/pocket_executor.py` — `prepare_trade`/`track_outcome` no longer open or close a browser session; they operate on the persistent page supplied by `BrowserWarmupService` and manage the shared lock explicitly (held through the synchronous prepare+click, transferred to the background outcome tracker on a real click, released when the trade closes).
- `core/recovery.py` and `core/trade_coordinator.py` updated to use the warm page/lock instead of opening their own sessions.

**Verified against the live demo, end-to-end, with real timing:** fully-warm execution (asset/expiry/amount already correct) completed in **1.03s** (target: under 2s); an execution requiring a real asset change completed in **1.76s** (target: under 5s). One-time startup cost (browser launch + demo verification + full asset-cache scan) measured at 10.88s - a single cost paid once at process start, not per trade. `ARMED` remained `false` throughout; no regressions in the Phase 1-3 test suites.

## Current phase
Phase 4, warm execution verified and meeting both latency targets. `ARMED` remains `false`; all validation to date has been on the Pocket Option demo account only.

## Next priorities
- Validate `wait_for_trade_result()` against a real win (currently loss-only confirmed).
- True concurrency (multiple simultaneous trades) is explicitly deferred - the current warm-session model is one-page/one-lock by design for this phase, not a placeholder oversight, but it remains the next scaling step if needed.
- Full logging architecture (`core/logger.py` is still an empty stub — today's logging is per-module, not unified, though `axim.lifecycle` now unifies orchestration-level logs across `risk_manager`, `trade_coordinator`, `recovery`, and `pocket_executor`).
- Fix: `core/telegram_listener.py` still does not enforce `WATCH_CHANNELS` — it processes every message in every chat the account can see. Flagged since the Phase 1 engineering report, not yet addressed.
- `MINIMUM_PAYOUT` is still defined in `config/settings.py` but not yet enforced as a risk rule — payout is now tracked in the DB (Phase 2) and read live (Phase 2), so this is a natural, low-effort next addition, possibly combined with the "tradeable now" check above.
- Build the actual Performance Dashboard UI (deferred from Phase 3 per the scope decision above).
- Live-mode readiness review before `ARMED` is ever considered for anything beyond deliberate, watched demo validation.
