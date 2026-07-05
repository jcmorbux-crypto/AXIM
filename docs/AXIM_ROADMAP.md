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

**Known limitation at the time (resolved in Phase 3, see below):** win/loss classification in `wait_for_trade_result()` was confirmed against one directly-observed **loss** sample only - no win case had been directly observed on the real DOM yet.

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

**Two real defects found and fixed while validating the ARMED=true path** (via `tests/manual_click_test_warm.py`, ARMED forced to `true` for that one isolated test process only, `.env` never touched):
1. The Opened/Closed sub-tabs in the Trades panel persist whichever was last active *across page loads* (leftover from earlier probing). Both `click_direction`'s confirmation and `wait_for_trade_result`'s initial wait depend on `.no-deals`, which only has meaning on the Opened tab - if Closed was left active, `wait_for_trade_result` waited the full timeout and failed, and `click_direction`'s check was passing *trivially* (the element just doesn't exist on Closed, which Playwright treats as "hidden") rather than genuinely confirming anything. Fixed: `_ensure_opened_tab_active()` now runs before both checks. Verified fixed via a second real click.
2. **This test also produced AXIM's first directly-observed win** ($1 stake, 92% payout, $1.92 returned) - closing out the "win case unverified" limitation flagged since Phase 2. It immediately exposed a real classification bug: `wait_for_trade_result` used the *last* displayed value (the profit-only delta, e.g. "+$0.92") to decide win/loss, comparing it against the stake - since $0.92 < $1 stake, a genuine win was misclassified as a "draw". Fixed: classification now uses the *total amount returned* (the middle value, e.g. "$1.92"), confirmed correct against both the win and the earlier loss sample. The historical DB row was corrected from `draw` to `win`.

### Research Module — Source Observer & Profiler (observation only, no execution)
`core/source_observer.py` passively logs messages from a Telegram signal source (default `@PocketOption_quant_algorithm_bot`) your account already has access to, into a new `source_observations` table in the same `data/axim.db`. Uses a separate Telethon session (`axim_observer_session`) from the live listener, so it can run alongside it without sqlite lock contention. Structurally cannot execute trades - it does not import `trade_coordinator`, `pocket_executor`, or `risk_manager`. `core/source_profiler.py` analyzes the collected data (cadence, common assets, expiry mix, OTC ratio, best-effort immediate-vs-delayed entry classification, duplicate patterns) and cross-references it against AXIM's own real measured execution latency (from the `signals` table) to recommend whether warm-browser execution is sufficient or something faster (multiple tabs, websocket/API-level integration) is warranted. Timing/lead-time analysis is explicitly heuristic - accuracy depends on how much temporal language the source's own messages contain.

### WATCH_CHANNELS enforcement (FIXED)
`core/telegram_listener.py` previously processed every message in every chat the account could see - flagged as a real safety gap since the Phase 1 engineering report. Now enforces `WATCH_CHANNELS` as a case-insensitive substring allowlist against the chat title, checked before any latency tracking, parsing, or coordinator call. **Fails closed**: if `WATCH_CHANNELS` is unset/empty (as it currently is), the listener processes zero messages from any chat and prints a loud startup warning explaining why, rather than silently falling back to "process everything." This is a real behavior change from before, done deliberately - `WATCH_CHANNELS` must now be set in `.env` for the live listener to act on anything. Verified: filtering logic tested directly (case-insensitive substring match, correct allow/block on real and empty/None chat titles) and fail-closed behavior confirmed when the list is empty.

### Phase 5 — Multi-Worker Concurrency (DEMO only, VERIFIED)
Previously one persistent page + one global lock, meaning a second signal always waited for the first to fully resolve (Phase 4's explicit, disclosed tradeoff). Now `BrowserWorkerPool` (`execution/browser_worker_pool.py`) opens N pages (tabs) within the same demo-verified browser context, each with its **own** `asyncio.Lock`. Worker selection, queueing, and reject-vs-queue are one mechanism: an `asyncio.Queue` pre-filled with all workers - `acquire_worker(timeout=0)` rejects instantly if all busy, `timeout=N` queues (FIFO) up to N seconds, `timeout=None` queues indefinitely. `TradeCoordinator` now acquires a worker (default timeout `WORKER_ACQUIRE_TIMEOUT_SECONDS=5`) as its own pipeline stage, rejecting cleanly (`rejected:all_workers_busy`) rather than hanging if none free up in time. `pocket_executor.prepare_trade`/`track_outcome` operate on a `worker` + release back to the `pool` instead of a raw page/lock - same acquire-hold-transfer-release discipline as Phase 4, now per-worker. `MAX_CONCURRENT_WORKERS` (default 2) and `WORKER_ACQUIRE_TIMEOUT_SECONDS` (default 5) added to `config/settings.py`. Demo-mode enforcement is inherited from the shared context (not re-verified per worker, since they share the same login session and cannot diverge).

**Real, concurrency-specific defect found and fixed while validating**: `wait_for_trade_result`'s wait for the Opened tab to show empty failed under real concurrency even though the earlier (Phase 4) tab-active fix ran at the start of the wait - a worker finishing its own trade and switching to Closed (to read its result) could reactively flip *another* worker's page away from Opened mid-wait, plausibly via a cross-tab shared UI preference (e.g. localStorage) that never surfaced in the single-page design. Fixed: the wait now re-asserts the Opened tab is active every ~3 seconds throughout, not just once at the start, still using real Playwright waits (not blind sleep) - each re-assertion is itself a bounded `expect()` call.

**Verified live, with two genuinely concurrent real trades** (EUR/USD OTC BUY + GBP/USD OTC SELL, `ARMED=true` forced for one isolated test process only): both clicked in 5.19s combined wall-clock (not sequential), `available_qsize` correctly dropped to 0 while both tracked outcomes in parallel, both resolved correctly through the fixed wait, both workers correctly returned to the pool afterward. Also confirmed `risk_manager` integration (specifically `cooldown_after_loss`) still correctly gates both workers through the shared coordinator.

### MINIMUM_PAYOUT enforcement (FIXED)
`config/settings.py` had defined `MINIMUM_PAYOUT` (default 90) since Phase 1 but nothing ever read it. Added `risk_manager.check_minimum_payout(payout)` - unlike every other risk rule, payout is only known *after* the browser has already selected the asset/expiry and read it live (`pocket_dom.read_payout_percent`), so it can't be pre-checked from signal/DB data alone the way the other rules are, and a cached value would go stale (payout fluctuates continuously, unlike "is this asset tradeable"). Called from `pocket_executor.prepare_trade` right after that live read, before the trade is marked `trade_prepared` - a rejected-for-payout trade never gets that status at all. **Fails closed**: a missing reading (`payout=None`) is rejected, not silently allowed, consistent with every other safety check in this codebase. 4 new unit tests (16/16 passing). Verified live end-to-end: forced a rejection (temporarily raised the threshold for one isolated validation call, not `.env`) and confirmed the full path - live payout read (92%), clean rejection with the real value preserved in the DB row for audit (`result=rejected:minimum_payout, payout=92`), worker correctly released back to the pool. Real-world note: at the default 90% threshold, this is a meaningful behavior change - some assets observed during testing (e.g. GBP/USD OTC) have shown payouts as low as 76-77% at times, which would now be rejected rather than proceeding to `prepared_not_armed`.

### "Tradeable now" pre-check combined with the payout read (FIXED)
`pocket_dom.read_payout_and_check_tradeable(page)` - one combined DOM read replacing the standalone `read_payout_percent()` call in `pocket_executor.prepare_trade`, closing the narrow window between `select_asset`'s pre-selection tradeable check (via the search-results row) and the actual click. Re-ran into, and this time correctly fixed, the exact false-positive that was found and removed from `verify_direction_controls_ready` back in Phase 1: the `.asset-inactive` overlay is **always** present in the DOM with non-zero width/height, `display: block`, `visibility: visible` - even when completely inert. Confirmed by direct measurement on a known-tradeable asset (GBP/USD OTC, payout +71% at the time): `opacity: 0`, `pointer-events: none`. Those two properties are what actually distinguish a genuinely-blocking overlay from dormant CSS-transition scaffolding; checking width/display/visibility alone (the original approach) always reports "blocking" regardless of real state. If this check ever does trigger, it raises the existing `AssetUntradeableError` - reusing the same, already-proven rejection path (worker released, clean rejection, no retry) rather than adding new handling.

**Scope of what was actually verified, stated plainly:** the "still tradeable" branch was confirmed correct on real, currently-tradeable assets (no false positive, unlike the Phase 1 attempt). The genuine trigger case - an asset that passes `select_asset`'s picker-level check but becomes untradeable moments later, before the click - is an inherently narrow race that wasn't directly reproduced in testing; the logic is sound and reuses an already-live-tested exception path, but that specific transition has not been directly observed. Also confirmed live: the combined read correctly gathers both facts in one DOM round-trip (e.g. real observed reading: `payout=69, tradeable=True`, which then correctly fed into the `MINIMUM_PAYOUT` rejection).

### Whole-browser-crash recovery (FIXED)
The Phase 5 gap: `BrowserWorkerPool`'s per-worker health check (`_ensure_worker_healthy`) only handled a single dead tab - if the *entire* browser process died, every worker's page pointed into a browser context that no longer existed, and nothing told the pool to rebuild itself. Fixed with a generation-counter design:
- `BrowserWarmupService.generation` increments every successful `start()` (including via reconnect); new public `ensure_alive()` health-checks and reconnects if needed, returning the current generation. `_reconnect()` is now guarded by its own lock with a re-check after acquiring it, so concurrent callers don't each trigger a redundant relaunch.
- `BrowserWorkerPool` remembers the warmup generation it last built workers from. Every `acquire_worker()` call first calls `_ensure_pool_healthy()` (also lock-guarded): if the generation changed, the whole pool is rebuilt from the new context rather than patched. Each `BrowserWorker` carries the pool generation it was built at, so a worker that was mid-trade during the crash and gets released afterward (via its own existing try/except) is recognized as stale and discarded instead of corrupting the freshly-rebuilt pool's queue.

**Verified with an actual simulated crash** (force-closed the underlying Playwright `BrowserContext` directly, bypassing normal shutdown, to reproduce what a real browser death looks like): `acquire_worker()` correctly detected it, the browser relaunched (generation 1→2), the pool detected the mismatch and rebuilt all workers from the new context, the returned worker was proven genuinely functional (`page.title()` succeeded), and a stale pre-crash worker reference was correctly discarded on release rather than being re-added (`available_qsize` stayed at 2, not 3). Measured overhead of the added health-check on the normal, non-crashed path: 0-31ms per `acquire_worker()` call - negligible against the multi-second selector interactions that follow, so this doesn't meaningfully affect the Phase 4 "near-instant execution" targets.

### Full logging architecture (FIXED)
`core/logger.py` was a 0-byte stub since Phase 1; every other module hand-rolled the same ~7-line handler setup, and it had drifted into three inconsistent variants: `axim.lifecycle` (shared across `risk_manager`, `trade_coordinator`, `recovery`, `latency`, `event_bus`, `pocket_executor`, `browser_warmup`, `browser_worker_pool`, `asset_cache`) used a plain `logging.StreamHandler()`; `axim.pocket_dom` had its own one-off Unicode-safe `_ReplacingStreamHandler` (added after console crashes on non-ASCII DOM text); `axim.source_observer` used the plain handler again, unprotected. None of the log files rotated, and `LOG_LEVEL` in `.env` was dead config nothing read.

Replaced all of it with a single `get_logger(name, filename=None, console=True)` in `core/logger.py`, now the only place that constructs a handler:
- Generalizes `pocket_dom.py`'s Unicode-safe handler (`_SafeStreamHandler`) to every logger, so no console output path can crash on emoji/non-ASCII text (this had bitten `source_observer.py` and the `axim.lifecycle` group specifically, since only `pocket_dom.py` had the fix before).
- Every file handler is now a `RotatingFileHandler` (5MB, 5 backups by default, overridable via `LOG_MAX_BYTES`/`LOG_BACKUP_COUNT`) — previously all four log files grew unbounded for the life of the process.
- `LOG_LEVEL` from `.env` is now actually read (defaults to `INFO`).
- Every module logger propagates to one root `axim` logger, which writes every record — regardless of origin — to a new `logs/axim.log`, giving one genuinely unified stream in addition to each existing per-topic file (`lifecycle.log`, `pocket_dom.log`, `source_observer.log`, unchanged names/filenames). The root has no console handler of its own, so this doesn't double-print.

All 11 call sites migrated to `from logger import get_logger`; verified every affected module still imports cleanly, the existing 16/16 `test_risk_manager` suite still passes, and a live check confirmed an emoji-containing message lands intact in both the per-module file and the new unified `axim.log` while printing safely (replaced, not crashed) to console.

## Current phase
Phase 5 complete, plus a P0 latency/reliability sprint and an immediate P1
follow-up (see `docs/AXIM_COMPETITIVE_BENCHMARK.md`,
`docs/AXIM_LATENCY_SPRINT.md`, `docs/AXIM_P0_SPRINT_REPORT.md`):
`WATCH_CHANNELS` now set to the trusted research source
(`PocketOption_quant_algorithm_bot`, matched by username); per-stage latency
now persisted to the database (not just logged) via a new `worker_acquired`
checkpoint and `latency_checkpoints_json`/`outcome_detection_ms` columns;
screenshot capture moved off the trade critical path (measured at ~856ms
each, previously synchronous, twice per trade) and now respects
`SAVE_SCREENSHOTS`; a TTL added to the worker health-check; a
process-level 24/7 auto-restart supervisor added to `telegram_listener.py`;
a live investigation found no evidence that asset selection is
cross-tab-shared (unlike the already-confirmed Opened/Closed tab-state
sharing); and the sprint's largest finding - outcome-detection overhead of
up to 28s under concurrent load - was root-caused (`.no-deals` is a
system-wide "zero open positions" signal, not per-trade, confirmed by
firing a 15s and a 60s trade concurrently and watching the 15s trade's own
`.no-deals` stay false for the full ~60s) and fixed (`wait_for_trade_result`
now sleeps for the trade's own known expiry and matches its specific closed
item by asset+direction+closest time, instead of polling that signal) -
re-measured overhead dropped to a tight 8.3s band, independent of
concurrent load. All changes benchmarked before/after on real demo trades;
regression suite (16/16) passes; `ARMED` remains `false`.

### Full observability (done, see `docs/AXIM_OBSERVABILITY.md`)
Replaced the P0 sprint's `LatencyTracker` with `core/timeline.py`'s
`TradeTimeline`: 10 named stages (absolute timestamps, cross-process safe)
plus 4 genuinely measured time categories - waiting/browser/database/logging
- with "active" computed as the residual, persisted per trade
(`signals.trade_timeline_json`/`category_timings_json`), and a new
`core/timeline_report.py` producing per-trade timelines plus P50/P95/P99
aggregates. Closed the confirmation-latency instrumentation gap (`clicked`
and `confirmation_detected` are now marked around real, separate work, not
back-to-back). Found and fixed two real double-counting bugs while
verifying the category arithmetic actually holds up against measured
totals (fire-and-forget screenshot work bleeding into a trade's categories
despite running concurrently with it; `persist()` re-summing an already-
cumulative total on a second call) - both caught because "active" landing
at exactly 0.0 on every trade was implausible enough to investigate rather
than accept. Verified end to end with real demo trades; regression suite
(16/16) passes.

### Asset-selection latency (done)
Added temporary fine-grained per-step timing inside `select_asset` and ran
8 forced real asset changes to find the actual bottleneck rather than
guessing: `open_picker` (~199ms) and `click_row` (~138ms) and
`close_dropdown_modal` (~87ms) are real, necessary browser interactions -
but three `_probe_state()` calls (`probe_search` ~56ms, `probe_row` ~31ms,
`probe_symbol_confirmed` ~20ms = ~107ms, ~15% of the total) were pure
diagnostic overhead: their found/visible/enabled results were only ever
fed into a log line, never a control-flow decision - the actual
correctness checks are separate `expect()` calls that are unchanged and
still run every time. Removed those three (plus one more diagnostic-only
`inner_text()` call and an unused `match_count`), keeping every real
verification intact. Measured before/after with the production benchmark:
asset-change latency dropped from an average of 1284.9ms (1340.3, 1198.0,
1325.9, 1275.4) to 1173.4ms (1168.8, 1168.3, 1176.1, 1180.3) - a 111.5ms
(~8.7%) reduction, matching the predicted ~107ms almost exactly. Regression
suite (16/16) passes.

Applied the same removal to `select_expiry` (probe was called 3 times per
call - once per hours/minutes/seconds field) and `set_amount`. Measured
before/after by stashing the change, re-measuring the pre-optimization
code with a dedicated forced-change probe (8 real expiry/amount changes),
then restoring and re-measuring: `select_expiry` dropped from an average
of 323.6ms to 232.1ms (91.5ms, ~28.3%), `set_amount` from 104.9ms to
76.0ms (28.9ms, ~27.5%) - both larger relative wins than `select_asset`,
consistent with the field-loop calling the probe more times.
Applied the same removal to the last two functions using the idiom:
`verify_direction_controls_ready` (4 probe calls: buy/sell before the
visible+enabled checks, buy/sell again after the hit-testing
`wait_for_function`) and `click_direction` (1 probe call, before the
button click). Measured before/after with a dedicated probe (8 repeated
calls to the safe, repeatable `verify_direction_controls_ready`; 4 real
$1 demo trades for `click_direction`, which necessarily has side effects):
`verify_direction_controls_ready` dropped from an average of 173.9ms to
64.5ms (109.4ms, ~62.9%) - the largest relative win of this whole series,
and it runs on **every single trade**, not only when asset/expiry/amount
changes, so its aggregate impact is the broadest of the four.
`click_direction` showed no clear win (476.5ms before vs 496.2ms after,
within noise) - its one removed probe was small relative to the
network/server-bound `.no-deals`-hidden confirmation wait that dominates
the measurement (documented in the function's own docstring as not
something client-side optimization can shrink). Reported honestly rather
than forcing a story: the change is still correct and harmless (pure
diagnostic overhead removed, zero behavior change), it just doesn't show
a measurable speedup here. Regression suite (16/16) and a full 10-trade
production benchmark both pass end to end with no errors.

This closes out the `_probe_state`/`_log_selector_event` cleanup across
all of `pocket_dom.py` - every remaining call to those helpers is now
either on a genuine failure-diagnostic path or gone.

## Next priorities
The full-observability data (not assumptions) points to, in order:
1. Tune `wait_for_trade_result`'s `settlement_buffer_seconds` down from its
   current 8s default - every trade in recent re-measurements succeeded on
   its first read attempt, suggesting real settlement completes faster
   than that.
2. Live-fire-test the process-level supervisor (deliberately kill the
   listener/browser) to get real recovery-rate data instead of "no data yet".
3. Build the actual Performance Dashboard UI (deferred from Phase 3 per the
   scope decision above) - now has a real data source to draw from
   (`core/timeline_report.py`'s per-trade/aggregate data).
4. Live-mode readiness review before `ARMED` is ever considered for
   anything beyond deliberate, watched demo validation.
