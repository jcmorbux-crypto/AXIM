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

### settlement_buffer_seconds tuned down (done)
Built a dedicated probe: 6 real trades, each polled every 250ms starting
exactly at nominal expiry (zero buffer) to find precisely how long after
expiry the Closed-tab item actually appears. Result: 172-250ms (avg
206ms) - the previous 8s default was roughly 32x more than needed. Tuned
`wait_for_trade_result`'s default down to 2s (a real ~8x margin over the
observed max, with the existing bounded retry loop still in place as a
safety net for any outlier). Verified with a full 10-trade production
benchmark: every trade succeeded on its first read attempt, no retries -
`outcome_detection_ms` averaged 2424.9ms (range 2312-2813ms), down from
the previous 8s-buffer average of ~8362ms, a ~5937ms (~71%) reduction in
post-click dead time per trade. Regression suite (16/16) passes.

### Process-level supervisor live-fire tested (done)
Found and fixed a real gap while preparing to test this: `run_forever()`
only ever recorded a `"process_restart"` `"attempted"` recovery_event -
never a terminal `"succeeded"`/`"failed"` outcome, unlike the other 3
recovery layers, which all record real terminal outcomes. Fixed by
tracking whether a restart is in progress and recording `"failed"` when a
restart attempt itself throws, `"succeeded"` once a restart (following a
prior disconnect/failure) fully reconnects - the initial clean startup
records nothing, since it isn't a recovery from anything.

Then live-fire tested all 4 recovery layers with real fault injection
against the ACTUAL production code (not reimplementations):
- **process_restart**: injected a real exception into `_startup()` on the
  first call only, let the genuine `run_forever()` retry logic take over
  unmodified - confirmed `"failed"` recorded, automatic backoff, and a
  fully genuine second attempt (real browser, real worker pool, real
  Telegram reconnect) recording `"succeeded"`.
- **browser_reconnect** / **worker_pool_rebuild**: forcibly closed the
  real underlying `BrowserContext` (same technique as the original Phase 5
  crash-recovery verification) and confirmed both `ensure_alive()` and
  `acquire_worker()` detected it, genuinely relaunched, and recorded
  `"succeeded"` - the recovered worker's page was independently confirmed
  responsive, not just "no exception thrown".
- **resume_open_trade**: drove a real trade's browser actions manually
  (bypassing `prepare_trade`, which would have auto-spawned a competing
  tracking task) to produce a genuine `trade_opened` row with no live
  tracking anywhere - exactly what a real crash leaves behind - then
  called the real `recovery.resume_pending_trades(pool)` and confirmed it
  found the trade, reattached tracking, and resolved it correctly,
  recording `"succeeded"`.

Real, populated `recovery_events` data now exists for the first time:
`browser_reconnect` succeeded=2, `process_restart` failed=1/succeeded=1,
`resume_open_trade` succeeded=1, `worker_pool_rebuild` succeeded=2.
Regression suite (16/16) passes; no orphaned browser processes after any
of the tests.

### Performance Dashboard UI (done)
Built the actual web UI deferred back in Phase 3. `core/dashboard_server.py`
- deliberately stdlib-only (`http.server`, no new dependency, matching the
project's dependency-light philosophy for what's still a local,
single-operator tool) - serves `dashboard/index.html` and a `GET /api/data`
JSON endpoint bundling `trade_statistics.full_report()` (daily/weekly win
rate, P/L, ROI, consecutive wins/losses, signals ignored/rejected),
`core/timeline_report.py`'s full P50/P95/P99 aggregates (every stage
transition, every time category), `database.get_recovery_event_stats()`
(the real recovery-rate data from the live-fire testing above), and the 25
most recent signals. Read-only - never imports trade_coordinator/
pocket_executor/risk_manager, never writes to the database. Binds to
127.0.0.1 only. Wired up the previously-dead `ENABLE_DASHBOARD`/
`DASHBOARD_PORT` env vars into `config/settings.py`.

`dashboard/index.html` is a single self-contained file (no build step, no
node_modules) that polls `/api/data` every 5s and renders it. Verified
live, not just by reading the code: started the real server against the
real `data/axim.db`, confirmed the JSON API and index route both respond
correctly, then loaded the page in an actual Playwright-driven browser and
screenshotted it - zero console errors, all sections populated with real
data (today/week stats, recovery health across all 4 layers, both latency
tables, a 25-row recent-signals table with win/loss/draw badges). One
layout bug found and fixed during that visual check: the Recovery Health
table was clipped inside a too-narrow stat-card column - moved to its own
full-width row, matching the latency tables. Regression suite (16/16)
passes.

### Live-mode readiness review (done, see `docs/AXIM_LIVE_READINESS_REVIEW.md`)
**Bottom line: not ready.** Queried `data/axim.db` directly: of 404 signal
rows, **zero** ever came from the actual trusted source
(`PocketOption_quant_algorithm_bot`) - every row is either an old untagged
test row or an explicitly-named test/benchmark source. The live listener
has never processed a genuine incoming signal in production, which means
the one question that actually matters for going live - does this source
have a real edge - is completely unanswered regardless of how solid
execution has become. Also found: no maximum-daily-loss/drawdown risk
rule exists anywhere (only a consecutive-losses check, which an
alternating win/loss pattern never trips); `MODE=DEMO` in `.env` is dead
config that does nothing (only `ACCOUNT` gates demo-vs-live) and sits
next to `ACCOUNT=DEMO` looking like a second switch, which is actively
misleading; and the historical dashboard/DB data reflects test runs where
risk rules were deliberately relaxed (`MINIMUM_PAYOUT=1`, etc.) to avoid
contaminating latency measurements, not real enforced behavior. Full
findings and a concrete, staged path (observe the real source in
preview-only mode first, add the missing risk rule, fix the `MODE`
confusion, only then evaluate real edge) are in the review doc. This
review does not recommend enabling live trading on any timeline - it
exists to make the gaps visible before that door is opened.

## Next priorities (superseded - see "Version 1 production hardening" below)
The live-readiness review's three next steps have all since been acted on:
1. **Done, and then some.** The live listener has been run against real
   Telegram signals (from an actual production source, "Go+ | Trading
   Bot") with `ARMED=true` - real trades placed, real wins and losses
   recorded, not just observation. See the production stress test and
   Production Readiness Report below.
2. **Done.** `risk_manager.check_max_daily_loss()` added - see "Version 1
   production hardening."
3. **Done.** `MODE` removed from `.env` (was dead config, only `ACCOUNT`
   is read).

## Version 1 production hardening
Following the live-readiness review, AXIM was run against a real
production signal source (`Go+ | Trading Bot`) with `ARMED=true`, finding
and fixing several real defects live rather than in isolated testing:

- **Parser fixes** (`parsers/signal_parser.py`): a false-positive
  concatenated-currency-pair match ("Signal" -> fake asset "SIG/NAL"), a
  `.title()` case-mangling bug on real mixed-case platform names
  ("GameStop Corp OTC" -> wrongly "Gamestop Corp OTC"), and a source-side
  label typo ("Commoditi:" instead of "Commodity:") - all found against
  real incoming messages, all fixed and covered by new regression tests.
- **Worker-pool architecture redesign** (`execution/pocket_executor.py`,
  `execution/pocket_dom.py`, `execution/browser_worker_pool.py`,
  `execution/browser_warmup.py`): outcome-tracking no longer holds a
  placement worker for a trade's full expiry (previously the real
  bottleneck limiting concurrent open trades to `MAX_CONCURRENT_WORKERS`)
  - it reads the Closed-trades list from `BrowserWarmupService`'s own
    dedicated, otherwise-idle page instead, so placement workers are used
    for placement only.
- **Leftover-modal bug fixed** (`browser_worker_pool._ensure_no_stray_modal`):
  a trade that failed mid-sequence could leave its tab's dropdown modal
  open, poisoning the very next trade that reused that same worker -
  confirmed by real worker-level failure correlation (one worker failed
  100% of its uses before the fix, others 0%).
- **Production stress test executed** (`tests/production_stress_test.py`)
  and a full **Production Readiness Report** written
  (`docs/AXIM_PRODUCTION_READINESS_REPORT.md`) - real measured PASS/FAIL
  per subsystem, real latency percentiles, real known limitations, a
  72/100 confidence score.
- **`MAX_DAILY_LOSS` drawdown circuit breaker added** - the gap flagged
  above. `risk_manager.check_max_daily_loss()`, wired into
  `trade_coordinator.py`'s real risk-check sequence, 4 new unit tests
  (including one that explicitly proves `MAX_CONSECUTIVE_LOSSES` alone
  does not catch what this does).
- **`MODE` dead config removed**; `PO_EMAIL`/`PO_PASSWORD` (also unused -
  login is handled by the persistent browser profile) annotated rather
  than removed, since they're a plausible placeholder for a future
  automated-login feature.
- **Regression suite expanded**: `test_trade_coordinator.py` and
  `test_browser_worker_pool.py` added - previously zero automated coverage
  on the core orchestration and concurrency logic, both previously
  validated only through manual live-fire scripts.
- **Documentation completed**: `INSTALL.md`, `USER_GUIDE.md`,
  `DEPLOYMENT.md`, `docs/AXIM_RELEASE_CHECKLIST.md`, and a real
  `requirements.txt` (all were previously empty placeholders).

**Still open** (see `docs/AXIM_RELEASE_CHECKLIST.md` for the full list):
a genuine multi-hour soak test running to completion; process supervision
configuration (Task Scheduler/equivalent) for unattended operation; a
backup/retention plan for `data/axim.db` and session directories.

## Client/server real-time sync gap closed (done)
Audited the Remote Client's 14 required capability areas (Mission
Control, Funds, Trading Sessions, Trading Desk, Strategy Lab, AI
Portfolio Analyst, Automation Studio, Signal Sources, Broker Accounts,
Performance, Notifications, User Management, Help Center, Settings -
`docs/AXIM_REMOTE_ACCESS.md`) against what actually exists. "Trading
Desk" and "AI Portfolio Analyst" turned out not to be missing pages -
they're already covered by `trades.html` ("Trade Center", there is no
manual order-entry desk since AXIM only ever trades signal-driven) and
by `core/ai_analysis.py`'s narrative surfaced inside Strategy Lab's
per-run results - adding separate nav items for either would have been
pure duplication, not a real gap.

The real gap: only 5 of the 14 areas (Mission Control, Trading Sessions,
Trade Center, Logs, the global Notifications bell) were wired to the
`GET /api/events/stream` SSE feed - Funds, Strategy Lab, Automation
Studio, and Broker Accounts were either one-shot-load or polling-only,
which contradicts "all updates should synchronize in real time." Root
cause: `core/fund_manager.py`, `core/rule_engine.py`,
`core/broker_account_manager.py`, and the session-lifecycle mutations in
`api/sessions.py` never published anything to `core/event_stream.py`'s
bridge - because that bridge only exists to carry events out of the
*separate* Telegram listener process, and these mutations all happen
directly inside the API process, the same process that already serves
the SSE endpoint. Fixed by writing straight to `database.
record_server_event()` at the point of mutation instead of routing
through the bridge, which only that cross-process case needs:
- `api/funds_routes.py`: `fund.updated` on every create/update/pause/
  resume/duplicate/source/broker-account mutation.
- `api/rules.py`: `automation.updated` on create/update/delete/
  evaluate-now.
- `api/broker_accounts_routes.py`: `broker_account.updated` on create/
  update/connect/disconnect/archive. `scripts/connect_broker_account.py`
  (a standalone subprocess spawned fire-and-forget by `/connect`) also
  emits it itself once the real async login actually resolves
  (connected or timed out) - that transition happens well after the API
  request already returned, so the route's own emission can't cover it.
- `api/sessions.py`: `session.updated` on start/stop/emergency-stop/
  risk-profile changes - `trade.closed` already covered per-trade P/L
  via `core/session_manager.py`'s existing subscription, this covers the
  session lifecycle transitions themselves.
- `api/backtest_routes.py`: `strategy.updated` on run create/delete, plus
  a `fund.updated` when Deploy to Fund changes a fund's default risk
  profile.

Wired the corresponding pages to `AximShell.subscribeEvents()`:
`web/funds.html`, `web/automation.html`, `web/broker.html`,
`web/strategy_lab.html` (refreshes the run history list), `web/
performance.html` (via `trade.closed`/`session.updated`, since its stats
are derived from those), and added `session.updated` to `web/
sessions.html` alongside its existing `trade.closed` handler. Every page
keeps its existing polling/one-shot load as a fallback, matching the
project's established "SSE is a pure enhancement" discipline - nothing
regresses if a client's stream drops.

Deliberately left polling-only / static: **Settings** and **Help
Center** (no live server state to sync - self-contained/static), and
**User Management** (admin actions on other users are rare and already
a manual page-load-driven workflow, not a trading-critical live view -
wiring it would be unused complexity, not a real gap).

Verified against a real (temporary, isolated) SQLite database, not
mocks: called the actual `funds_routes`/`rules`/`broker_accounts_routes`
route functions end-to-end and confirmed each mutation produced the
correct `server_events` row with the right event type and payload
(`fund.updated`, `automation.updated`, `broker_account.updated` all
observed firing correctly through real create/update/pause/resume/
delete call sequences). `sessions.py`/`backtest_routes.py`'s emit
helpers were verified directly (full session-start fixture setup -
channels, risk profile, connected broker account - was out of scope for
this pass, but the emission code is the identical pattern proven
correct in the other three files). Full existing regression suite
unaffected: `test_funds*.py`, `test_broker_account_manager.py`,
`test_backtest_routes.py`, `test_backtest_engine.py`,
`test_backtest_database.py`, `test_database_sessions.py`,
`test_event_stream_routes.py`, `test_rule_engine.py`,
`test_session_manager.py`, `test_auth_routes.py` - 199 tests, all
passing.

## Notification Center added (done)
The capability audit above also found "Notifications" (one of the 14
required Remote Client areas) had zero dedicated page - only the global
bell dropdown in `web/shell.js`, which already had the best SSE coverage
of any area but no history view, no filtering, and a hardcoded 50-row
cap with no way to page past it. Also found and closed a second, real
gap while investigating "Signal Sources": it turned out `WATCH_CHANNELS`
(.env) is only ever a legacy fallback - `core/telegram_listener.py`'s
`channel_allowed()` already checks the live, UI-editable `ui_channels`
DB table first (`database.get_enabled_channels()`, managed from
`telegram.html`) - so, unlike the earlier read of it, there was no
missing control surface there after all.

Added `web/notifications.html` (new sidebar item, `/notifications`,
served via `api/main.py`): the full notification history for the
logged-in user, an "unread only" filter, per-row and mark-all-read
actions, live-updated via the existing `notification.created` SSE event
(no new event type needed - the backend already emitted it, just
nothing rendered a full-page view of it). `GET /api/notifications` grew
an actual `limit` query param (capped at 500, was silently fixed at 50)
so the new page can show real history instead of being capped at what
the bell dropdown needs. Cross-linked from `web/settings.html`'s
"Notifications" tab, which is about a genuinely different, not-yet-built
concept (outbound email/push/webhook sending) - added a pointer so the
two aren't confused for the same feature.

Verified against a real (temporary) SQLite DB, not mocks: created
notifications for two different users, confirmed `GET /api/notifications`
only ever returns the requesting user's own rows, confirmed `unread_only`
filtering and both per-notification and mark-all read paths transition
the right rows and nothing else. Full regression suite re-run clean
after this change: 503 passed, 1 skipped, 0 failed.

## Safety-critical control state and trade confirmations made real-time (done)
The two most safety/time-critical pieces of live state in the app were
still poll-only, unlike everything closed out above:
- **Emergency Stop / pause / resume / test-mode.** `web/dashboard.html`'s
  status banner (the "Emergency stop active" red banner, the run/paused/
  reconnecting dot) only ever learned about these via its own 5s poll -
  a Remote Client watching Mission Control from another device could see
  a stale "running normally" state for up to 5 seconds after someone
  else hit Emergency Stop elsewhere. Added `control.updated` (full
  `ui_control_state` as the payload) emitted from all 6 mutation points
  in `api/main.py` (`pause`/`resume`/`emergency-stop`/`clear-emergency-
  stop`/`test-mode enable`/`disable`) plus `api/sessions.py`'s
  session-level `emergency_stop_session` (which flips the same global
  state). Wired `web/dashboard.html` to it; existing 5s poll kept as the
  fallback floor.
- **Live-mode per-trade confirmation.** The single most time-critical UI
  in the app - a live trade sits waiting on a human decision with a
  countdown - and `web/shell.js`'s global confirmation modal (injected
  on every page) only checked every 2s. Since the row is created and
  polled from inside `core/session_manager.wait_for_trade_confirmation`,
  which runs in the *separate* Telegram listener process, made it write
  straight to the `server_events` outbox itself (`database.
  record_server_event`, same direct-write technique
  `scripts/connect_broker_account.py` already uses from its own separate
  process - no `event_bus` bridge needed, since `record_server_event`
  just writes to the shared SQLite file any process can reach) - one new
  event on creation (`trade.confirmation_requested`) and one on every
  resolution path: `api/sessions.py`'s `confirm`/`reject` endpoints, and
  `session_manager`'s own timeout-expiry branch
  (`trade.confirmation_decided` on all three, so a second admin's modal
  for the same trade dismisses immediately once anyone - or the timeout
  itself - decides it, instead of lingering for up to 2s showing a
  decision that's already been made). The existing 2s
  `setInterval(pollPendingConfirmations, 2000)` stays as the fallback
  floor; both new events just call the same `pollPendingConfirmations()`
  function immediately.

Verified live against a real running server (not mocks): logged in over
real HTTP, opened the real `GET /api/events/stream` SSE connection in a
background thread, then drove `POST /api/control/emergency-stop` ->
`clear-emergency-stop` -> `pause` -> `resume` and confirmed each produced
the correct `control.updated` event with the right state. Separately,
created a pending-confirmation row the same way the listener process
does (crossing the same process boundary a real deployment would),
confirmed `trade.confirmation_requested` fired, then confirmed it
through the real `POST .../confirm` endpoint and confirmed
`trade.confirmation_decided` fired. Full regression suite re-run clean
after this change.

## Signal Sources live sync closed (done)
Last remaining live-sync gap of the original 14-area audit: `web/
telegram.html`'s channel list (enable/disable, Telethon dialog sync,
per-channel config) loaded once and never refreshed - a channel toggled
from one Remote Client stayed invisible on another connected client
until a manual reload, unlike everything else closed out above. Added
`channels.updated`, emitted from `api/main.py`'s three channel mutation
routes (`POST /api/channels/sync`, `PATCH /api/channels/{id}`, `PATCH
/api/channels/{id}/config`), and wired `web/telegram.html` to it.
Also wired `web/dashboard.html` (Mission Control's "watching N signal
sources" status line already reads the channel list on its 5s poll -
this makes it instant too, for consistency with `control.updated`
added just above it).

Verified live against a real running server: created a channel directly
via `database.upsert_channel` (mirroring what a real Telethon sync
would insert), toggled it enabled through the real `PATCH
/api/channels/{id}` endpoint over HTTP, and confirmed `channels.updated`
fired on the real SSE stream. Full regression suite re-run clean after
this change.

This closes the real-time-sync half of the original capability audit:
every one of the 14 required Remote Client areas that has live server
state to sync now does, with polling kept everywhere as the fallback
floor (Settings/Help Center have no live state to sync; User Management
remains deliberately poll-only, per the reasoning recorded earlier in
this document).

## Connection-loss indicator added (done)
Everything above assumes the SSE stream is healthy - but nothing told
the operator when it wasn't. `web/shell.js`'s `eventSource.onerror` was
a literal no-op ("let the browser's built-in auto-reconnect handle it"),
so a Remote Client whose Tailscale link actually dropped, or whose
laptop slept, or whose AXIM Server restarted, had no way to tell its
"live" view had gone stale - every page would just quietly stop
updating with no visible signal, directly undermining the whole point
of building real-time sync in the first place.

Added a small indicator in the sidebar footer (`#axim-conn-status`,
reuses the existing `.dot`/pulse styling from Mission Control's status
line), hidden by default and wired to the EventSource's real `onopen`/
`onerror` events - not a synthetic health check. Debounced 4s before
showing "Reconnecting to AXIM Server..." so normal, brief reconnects
(which happen routinely and resolve on their own) don't flicker a false
alarm; clears instantly on a real `onopen`. No new backend event needed -
this reacts to the existing connection's own native lifecycle.

Verified with a real Playwright browser against a real running server,
not a mock: confirmed the indicator is hidden while genuinely connected,
force-killed the actual server process mid-session, confirmed it stays
hidden through the 4s debounce window (no flicker), then confirmed it
switches to visible with the correct text once the drop has actually
persisted past that window. Full regression suite re-run clean after
this change (no backend changes in this one - frontend-only).

## Login brute-force lockout added (done)
Last item under the spec's explicit "Secure authentication" requirement:
`POST /api/auth/login` had zero rate limiting or lockout - unlimited
password attempts against any account, forever. Notable because
`database.verify_user_credentials`'s own docstring already claimed
"Returns the user dict if email+password are correct AND the account
isn't locked out" - a real gap between documented and actual behavior,
not a hypothetical one. AXIM has no public internet exposure by default,
but the login endpoint is still reachable by any device on the Tailscale
network, and this controls real trading accounts - worth closing for
real.

Added `failed_login_count`/`locked_until` columns to `users` (additive
migration, same pattern as every other schema change in this file).
`database.is_account_locked(email)` / `record_failed_login(email)` /
`reset_failed_login(user_id)`: 5 failed attempts locks the account for
15 minutes (`MAX_FAILED_LOGIN_ATTEMPTS`/`LOCKOUT_MINUTES`); lockout is
enforced both inside `verify_user_credentials` itself (so a correct
password still fails while locked, from any call site) and checked
up-front in `login()` for a distinct 429 message. Self-healing - an
expired lock clears itself on the next check, no cron job needed.
`set_user_password` (both self-service change and admin reset) also
clears any lockout, since a legitimate password change is a stronger
signal than just waiting out the window.

Deliberately scoped to per-account lockout only, not IP-based rate
limiting - a nonexistent email can't be locked (nothing real to
protect, and doing nothing there doesn't leak whether an email is
registered, matching the existing generic error message either way).
Broader IP-level throttling would be disproportionate for what's
documented as a single/small-team operator tool sitting behind
Tailscale, not a public multi-tenant service.

Verified two ways: directly against a real temporary SQLite DB (4
failures - not yet locked; 5th - locked; correct password rejected
while locked; simulated the window elapsing - unlocks and accepts the
correct password again; confirmed the reset path zeroes both columns),
and live over real HTTP against a real running server - 5 real wrong-
password POSTs to `/api/auth/login` against a real bootstrapped owner
account, then confirmed the 6th attempt with the *correct* password
still got a 429 with the lockout message. Full regression suite re-run
clean after this change.

## Revoked device sessions can no longer keep an open SSE stream alive (done)
`docs/AXIM_REMOTE_ACCESS.md` documents "Revoking a device immediately
signs it out on its next request" - true for every regular endpoint
(each one calls `get_current_user`/`_resolve_stream_user` fresh, per
request), but not for `GET /api/events/stream`: that connection was
authenticated once, at connect time, then held open for as long as the
browser's `EventSource` stayed connected - which, given SSE's whole
purpose is staying open, could be hours. A revoked device (Settings >
Connected Devices) or a since-disabled/suspended account kept receiving
every live broadcast event (`trade.*`, `control.updated`,
`channels.updated`, its own `notification.created`, everything else
this session has added) until the stream happened to drop for some
unrelated reason - a real gap between documented and actual behavior on
a system that controls live trading.

Fixed by periodically re-validating the session inside
`_event_generator`'s own loop (`SESSION_RECHECK_SECONDS = 30`) - the
same `database.get_session_user()` check every normal request already
gets, just done on a timer instead of per-request since there's no
per-request boundary on an open stream. Rechecked via a cheap
`time.monotonic()` comparison each loop iteration, with the actual DB
read only happening once per 30s regardless of how many events flow
through in between - deliberately not tied to event volume, since real
trading activity can produce far more than one event per 30s.
`_resolve_stream_user` now returns `(user, raw_token)` instead of just
`user` so the generator has the token to recheck with.

Verified directly against the real `_event_generator` function (not a
reimplementation) with a real temporary SQLite DB: created a user and a
real session token, ran the actual generator with the recheck interval
sped up for the test only (same code path, not a mock), drained several
real keepalive ticks proving the stream stays alive while the session is
valid, then called the exact same `database.delete_session()` the
Connected Devices "Revoke" button calls and confirmed the generator's
`while True` loop broke on its very next iteration
(`StopAsyncIteration`), instead of continuing indefinitely.

## API docs (/docs, /redoc, /openapi.json) disabled by default (done)
Found while auditing the auth surface: `FastAPI(title=...)` was
constructed with no overrides, so its default interactive docs were
live and completely unauthenticated - nothing in `api/main.py`
disabled them, and being FastAPI-generated routes, they never pass
through any of this app's own auth dependencies. Confirmed live against
a real running server: `GET /openapi.json` with zero auth returned the
full schema for **159 endpoints**, admin-only routes included, and
`GET /docs` served a fully interactive Swagger UI - anyone who can
merely reach the API (any device on the Tailscale network once
`API_BIND_HOST` is opened up per `docs/AXIM_REMOTE_ACCESS.md`, no login
needed) could browse the entire route map and every request/response
shape. Doesn't leak actual data (no auth bypass on the real endpoints
themselves), but it's real reconnaissance value handed out for free on
a system controlling live trading, and contradicts the "no public
exposure by default" design goal in spirit even where Tailscale is the
actual boundary.

Added `ENABLE_API_DOCS` (`config/settings.py`, default `false`) and wired
it into `docs_url`/`redoc_url`/`openapi_url` on the `FastAPI(...)`
constructor - `None` disables each route entirely (a real 404, not just
hidden from a UI) unless explicitly opted into for local debugging.

Verified live against a real running server, not just reading the code:
confirmed all three endpoints return `404` with no env var set (today's
new default), then confirmed setting `ENABLE_API_DOCS=true` correctly
restores all three to `200` - the opt-in path genuinely works, not just
the default. Full regression suite re-run clean after this change.

## SSE session recheck also catches trial expiration, not just explicit disable/revoke (done)
A follow-up gap in the SSE-revocation fix just above, found by comparing
it against `get_current_user`'s own docstring more carefully:
`database.check_and_expire_trial` - the *lazy* trial-expiration check
("called on every login and every authenticated request") - is only
ever triggered by an actual request calling it. The SSE stream's own
periodic recheck called `get_session_user` directly, skipping it - so a
trial user whose trial expired while their only remaining activity was
one open SSE connection would never have their `access_state` flipped
to `expired` at all, and the stream (correctly, given the never-updated
state) would keep running indefinitely, unlike every other endpoint.

Fixed by calling `database.check_and_expire_trial` on the rechecked user
too, same as `get_current_user` does - one extra call, same
`SESSION_RECHECK_SECONDS` cadence already in place.

Verified directly against the real `_event_generator`: created a trial
user with `trial_expires_at` set a day in the past (simulating a trial
that lapsed with no other request ever touching this account), opened a
real stream, and confirmed it terminated on its very first recheck *and*
that the user's `access_state` in the database was actually flipped to
`'expired'` as a side effect - not just that the stream happened to stop.
Full regression suite re-run clean after this change.

## Stored XSS via attribute-context escape bypass fixed (3 sites) (done)
A full sweep of every `innerHTML =`/`+=` template-literal assignment
across all 20 `web/*.html` files (not sampled - every call site checked)
found one direct miss and one recurring pattern bug:
- `web/dashboard.html`'s recent-activity renderer interpolated `t.asset`/
  `t.direction` - fields parsed directly out of a raw incoming Telegram
  signal - with no `escapeHtml()` at all, unlike every other trade-list
  view in the app. Fixed by wrapping both in the page's own `escapeHtml`.
- `web/telegram.html`, `web/risk.html`, `web/strategy_lab.html` each had
  the same bug shape: `onclick="doThing(${id}, '${escapeHtml(name)
  .replace(/'/g, "")}')"` - embedding an untrusted string inside a
  **double-quoted** HTML attribute. This codebase's `escapeHtml()`
  (`div.textContent = s; return div.innerHTML`) only encodes `&`/`<`/`>`
  during text-node serialization - it never encodes `"`, since quotes
  aren't special in a text node. A channel title (real, attacker-
  controlled via a Telegram chat's display name), a risk-profile name,
  or a backtest strategy label containing a `"` could break out of the
  attribute and inject a new event handler entirely - the `.replace(/'/g,
  "")` only stripped single quotes and did nothing for the actual `"`
  vector.

  Fixed at the root rather than patching the escaping: switched all
  three `onclick` handlers to pass only the numeric `id` (never
  string-interpolated, nothing to escape) and look the name/label back
  up from data the page already holds in memory
  (`allChannels.find(...)`, `allProfiles.find(...)`,
  `currentReport.strategies.find(...)`) inside `openMessages`/
  `useTemplate`/`openDeployModal` themselves - eliminates the
  vulnerability class rather than adding a second escaping layer that
  could just as easily be gotten wrong again later. Re-swept the whole
  `web/` directory afterward for the same anti-pattern (`grep
  "escapeHtml(.*)\.replace(/'/g"`) and for the broader shape (any
  `onclick="...('${...}` interpolation) - zero remaining instances; the
  two other matches found (`billing.html`'s plan tier, `trades.html`'s
  server-generated screenshot URL) are both closed-enum/server-
  constructed values, not free text from an untrusted source.

Verified live, not just by reading the code: seeded a real channel via
`database.upsert_channel` with a title of
`Evil" onmouseover="window.__XSS_FIRED=true" x="` (a realistic malicious
Telegram chat title), loaded `/telegram` in a real Playwright-driven
browser, and confirmed the injected handler never fires
(`window.__XSS_FIRED` stayed `undefined`), the malicious title still
renders correctly as plain visible text, and the "View recent messages"
feature (the refactored `openMessages(id)`) still works end-to-end -
the modal correctly shows the full title, proving the id-lookup fix
didn't break the feature it was patching. Full regression suite re-run
clean after this change.

## Accessibility pass: modals, forms, keyboard operability, screen-reader support (done)
Zero `role=`, `tabindex=`, or `aria-*` attributes existed anywhere in the
app except one `aria-label` (mobile nav toggle) before this pass - a
full audit surfaced the gaps, all fixed and verified live:

- **Modals had no keyboard support at all.** `openModal`/`closeModal`
  were duplicated byte-for-byte across 7 files (`automation.html`,
  `broker.html`, `funds.html`, `strategy_lab.html`, `telegram.html`,
  `trades.html`, `users.html`), each just toggling `display`. Centralized
  into `web/shell.js` as `AximShell.openModal`/`closeModal` (each page's
  local function now delegates), fixing the duplication and the
  accessibility gap in one move: `role="dialog"`/`aria-modal="true"` set
  on open, focus moves to the first focusable control, focus restores to
  whatever triggered the modal on close, and a shared, stack-aware
  Escape-key handler closes the topmost one. Backdrop click-to-close
  added too (only for the element actually clicked, not bubbled from
  inside `.modal`).
- **The Live-trade confirmation modal (`#axim-confirm-modal`) was
  deliberately excluded** from all of the above - it's never pushed onto
  the shared modal stack, so Escape and outside-click can never dismiss
  it, preserving its fail-closed design (only an explicit Confirm/
  Reject, or the timeout's own auto-reject, can resolve it). Given
  `role="alertdialog"`/`aria-modal`/focus-on-open too, but focus
  deliberately lands on the `.modal` container itself, not either action
  button - a stray Enter keypress can't accidentally confirm a real-
  money trade the way defaulting focus to "Confirm Trade" would.
- **111 form `<label>`s were visually present but not programmatically
  associated** with their input (`<label>Name</label><input id="x">` as
  siblings, no `for=`) - including email/password on the login page,
  the first thing every user touches. A script added `for="<id>"` to 104
  automatically (pattern-matched safely); the remaining 7 needed manual
  fixes: two labels contained a nested `<a>` link (regex-excluded on
  purpose, fixed by hand in `sessions.html`), two `telegram.html` inputs
  had no `id` at all (added one, scoped per-channel-card since the
  markup is a per-row template), and one `sessions.html` label pointed
  at a checkbox group, not a single control (given `role="group"`/
  `aria-labelledby` instead of `for=`). Two more `<label><input
  type="checkbox">...</label>` cases were already correctly accessible
  (input nested inside the label needs no `for=`) and were correctly
  left alone.
- **7 clickable `<div>`s had no keyboard path** - a fund/rule template/
  profile-list/message-pick/signal-source card, and a clickable table
  row, all `onclick` with no way to Tab to or activate them. Added
  `role="button" tabindex="0"` to each and one shared Enter/Space
  activation handler in `shell.js` (delegated, not duplicated per
  element) that calls `.click()` - but only when the card itself is the
  focused element, not when Enter/Space was meant for a real nested
  control (checkbox, select) inside it.
- Screenshot images (`trades.html`) had no `alt` text and the thumbnail
  was an `<img onclick>` with no keyboard equivalent - added `alt=`
  everywhere and wrapped the thumbnail in a real (visually reset)
  `<button>`.
- The notification bell (every page) never exposed its open/closed
  state - added `aria-expanded`, `aria-controls`, `role="menu"` on the
  dropdown, and Escape-to-close (kept separate from the shared modal
  stack, since the dropdown isn't a modal).
- `dashboard.html` had no real `<h1>` (its title was a styled `<div>`) -
  a `.page-header h1` CSS rule elsewhere in the app has higher
  specificity than `.greeting-title` alone, so simply renaming the tag
  would have visually shrunk it. Added a new `.sr-only` utility class
  and a visually-hidden real `<h1>` instead - zero visual change,
  correct heading structure for screen-reader navigation.

Verified live with a real Playwright browser against a real running
server, not by reading the code: swept all 16 touched pages for console
errors and confirmed each has exactly one `<h1>` (zero errors, zero
duplicates); opened the Funds "New Fund" modal and confirmed
`role="dialog"`, focus landing on the first input, Escape closing it,
and focus correctly restoring to the button that opened it; created two
real funds and confirmed keyboard-only `Tab` + `Enter` activates a
`fund-list-item` exactly like a mouse click; toggled the notification
bell and confirmed `aria-expanded` flips correctly on open, click-
outside, and Escape. Separately and most carefully, verified the safety
property of the Live-trade confirmation modal specifically: created a
real pending confirmation, confirmed Escape does **not** close it,
confirmed clicking outside it does **not** close it, and confirmed its
real Reject button still works end-to-end (the database row's `status`
actually changes to `rejected`) - the accessibility pass added real
keyboard/screen-reader support everywhere else without weakening this
one deliberately-not-dismissible dialog. Full regression suite re-run
clean after this change.

## Permanent regression tests added for this session's security fixes (done)
Everything security-related earlier in this document was verified live
against a real running server - genuinely stronger evidence than a unit
test at the time, but every one of those verification scripts was
deleted afterward (`$CLAUDE_JOB_DIR/tmp`), leaving nothing in the
permanent suite to catch a future regression. Closed that gap for the
two pieces that could be tested safely:
- `tests/test_login_lockout.py` (new): the full lockout lifecycle
  directly against `core/database.py` (not-yet-locked, locked-at-
  threshold, correct password still rejected while locked, unlocks
  after the window elapses, `reset_failed_login`/`set_user_password`
  both clear it, a nonexistent email never locks), plus
  `api/auth_routes.py`'s actual `login()` route function called
  directly - confirms the 401-vs-429 status codes and that a correct
  password before the threshold succeeds and resets the counter. 10
  tests.
- `tests/test_event_stream_routes.py` (extended): 3 new tests directly
  against the real `_event_generator` (not a reimplementation) - a
  still-valid session keeps streaming, `database.delete_session()` (the
  same call Connected Devices' Revoke button makes) terminates the
  generator on its next iteration, and an already-expired trial both
  terminates the stream and flips `access_state` to `expired` in the
  database as a real side effect.

Deliberately did NOT add a test importing `api/main.py` directly for the
`control.updated`/`channels.updated` event wiring (pause/resume/
emergency-stop/etc.) - no other test file in this codebase imports
`main.py`, because it calls `database.initialize_database()` at module
level, and Python's import caching means the *first* test file to
trigger that import (order not guaranteed across test files) would run
it against whatever `database.DB_FILE` happened to be at that moment -
risking a real touch of the actual `data/axim.db` during test
collection if that happens before any test's `setUp()` reassigns it.
`api/auth_routes.py` and `api/event_stream_routes.py` don't have this
problem (confirmed - neither calls `initialize_database()` at import
time), which is why those two were safe to test this way and `main.py`
wasn't. That wiring remains covered by the live HTTP verification
already documented above, not by a permanent test - a real, intentional
gap, not an oversight.

Full regression suite re-run clean after this change.
