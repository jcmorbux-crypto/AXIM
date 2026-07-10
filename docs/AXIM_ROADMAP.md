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

## Desktop client shipped with unedited Tauri scaffold metadata (fixed)
`axim-desktop/package.json`, `src-tauri/Cargo.toml`, and `src-tauri/
tauri.conf.json` all still had their original `create-tauri-app`
scaffold defaults: `version: "0.1.0"` in all three (while the API itself
reports `0.9.0-dev` via `/api/version`, shown in `web/login.html`'s
footer - a real, visible mismatch between the desktop shell and the app
it launches), plus `Cargo.toml`'s `description = "A Tauri App"` and
`authors = ["you"]` - literal, unedited placeholder text that would ship
into the installer's package metadata as-is.

Synced all three version strings to `0.9.0-dev`, replaced the
placeholder description/authors with real ones, and added `publisher`/
`copyright`/`shortDescription`/`longDescription` to `tauri.conf.json`'s
bundle config - previously entirely absent, meaning Windows' "Add/Remove
Programs" would have shown a blank or generic entry for the installed
app instead of identifying it as AXIM TradeStation.

No Rust toolchain available in this environment to run a full `cargo
build`/`tauri build`, so verified what could be: `tauri.conf.json`/
`package.json` parse as valid JSON, `Cargo.toml` parses as valid TOML
(via Python's `tomllib`) with the expected fields present. All three
changes are pure string/metadata edits - no code, no dependency, no
build-config-affecting field touched - so this is a low-risk change even
without a full build to confirm it.

## Missing favicon and empty root README fixed (done)
Found while checking for more of the same "unedited scaffold" pattern
that turned up the Tauri metadata gap above: the web app had no favicon
at all (no `.ico`/`.png` file, no `<link rel="icon">` anywhere) - every
browser tab showing any of the 20 pages fell back to a blank/default tab
icon, one of the more commonly-noticed "this looks unfinished" signals
in a web product. Added `web/favicon.svg` - a small inline SVG matching
the sidebar's existing "blue rounded-square A" mark exactly (same
`#2452eb` blue from `theme.css`'s `--blue` token, same shape) - and a
`<link rel="icon">` to all 20 pages' `<head>`, right before the existing
`theme.css` link.

Also found the repository's root `README.md` was completely empty (0
bytes) - the first thing anyone browsing the repo or evaluating the
project sees, despite genuinely thorough documentation existing
everywhere else (`INSTALL.md`, `USER_GUIDE.md`, `DEPLOYMENT.md`, a dozen
files under `docs/`). Wrote a real one: what AXIM does, the client/
server architecture in brief, links out to the existing detailed docs
rather than duplicating them, and an honest project-status line
(`0.9.0-dev`, pointing at the readiness/release-checklist docs).

Verified live against a real running server: `GET /web/favicon.svg`
returns `200` with `content-type: image/svg+xml` and the expected SVG
body through the existing `/web` static mount (no new route needed).
Confirmed all 20 pages picked up the `<link rel="icon">` (grepped for
files missing it - zero). Full regression suite re-run clean after this
change (no backend logic touched - static asset + two doc files).

## Standard HTTP security headers added (done)
No security-headers middleware existed anywhere in `api/main.py` -
confirmed by grep, only the CORS middleware was present. The one that
matters most: no `X-Frame-Options`, meaning nothing prevented AXIM's
login page (or any page) from being loaded inside an `<iframe>` on an
attacker-controlled page and clickjacked - tricking an already-logged-in
user into clicking what looks like the attacker's own UI but is
actually a real AXIM action underneath. Tailscale-only network reach
doesn't prevent this on its own: the attacking page just needs to be
open in the same browser as an authenticated AXIM session, not on the
Tailscale network itself.

Added a single `@app.middleware("http")` applied to every response:
`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
`Referrer-Policy: same-origin`, and `Strict-Transport-Security` -
conditionally, only when the request actually arrived over HTTPS (same
`X-Forwarded-Proto`-aware check `_request_is_https()` elsewhere already
uses), since asserting HSTS unconditionally on a plain-HTTP local/
Tailscale deployment would be actively wrong, not just unnecessary.

Verified live against a real running server, including the one case
most likely to break under a global response middleware - the SSE
stream (`GET /api/events/stream`, a long-lived `StreamingResponse`):
confirmed the new security headers appear correctly alongside the
stream's own existing headers (`cache-control: no-cache`,
`x-accel-buffering: no`, `content-type: text/event-stream`) without
disrupting the stream itself (status 200, connection stayed open,
`Transfer-Encoding: chunked` intact). Also confirmed on a plain JSON
endpoint. Full regression suite re-run clean after this change.

## Audit logging for financial/risk-critical actions (done)
Checked whether the existing `admin_actions` accountability mechanism
(`api/admin.py`, `database.record_admin_action`) actually covered the
actions that matter most for a multi-admin commercial product - it
doesn't. It's scoped specifically to user-management (requires a
`target_user_id`), and a grep for `logger.info`/`logger.warning` across
`api/funds_routes.py`, `api/broker_accounts_routes.py`, `api/sessions.py`,
`api/backtest_routes.py`, and `api/rules.py` returned **zero matches** -
none of them even had a `logger` instance. Fund creation, either half of
the Live-trading double-switch (a Fund's own `live_enabled` and a broker
account's own `live_enabled` - both must agree before a real-money trade
can place, per `docs/AXIM_APP_PLAN.md`'s Safety design), session starts,
strategy deployment, and Automation Studio rule create/update/delete all
left **zero trace** of who did it or when - if something went wrong (a
Fund unexpectedly went Live, a rule someone didn't intend fired), there
was no way to answer "who did this."

Added `logger = get_logger("axim.ui", filename="ui.log")` to all five
files - same name/filename `api/main.py` already uses for its own
UI-triggered actions, so these interleave into the same, already-existing
`ui.log` (and the unified `axim.log` every logger propagates to) with no
new file or Logs-page wiring needed. Logged: fund create/update/archive,
broker account create/update/connect/disconnect, session start,
session-scoped emergency-stop, strategy deploy, and rule create/update/
delete. The two Live-mode toggles specifically log at `WARNING`, not
`INFO` - same visibility precedent the global Emergency Stop already
set - since those are the two switches that most directly gate whether
real money can move. A LIVE-mode session start also logs at `WARNING`;
a Demo one stays at `INFO`.

Verified live against a real running server, not by reading the code:
created a real fund, toggled its `live_enabled`, created a real broker
account, toggled its `live_enabled`, created a real rule - all through
actual HTTP POST/PATCH requests - then queried the real
`GET /api/logs` endpoint (the same one the Logs page calls) and
confirmed all of it actually appears, with the correct level (`WARNING`
for both `live_enabled` toggles, `INFO` for the creates), the correct
acting user's email, and the correct field-level detail. Targeted
regression suite (funds/broker-accounts/backtest/rules/session_manager,
105 tests) and the full suite both re-run clean after this change.

## Privilege escalation: a plain "admin" could grant itself "owner" (fixed)
Found while reviewing the audit-logging gap above and asking a follow-up
question: does the "owner" vs "admin" distinction the rest of the app
relies on actually hold up? `api/admin.py`'s `create_user`/`edit_user`
both validate the `role` field only against `VALID_ROLES` (which
includes `"owner"`) and gate the whole endpoint with `require_admin`
(owner **or** admin) - `require_owner` exists in `api/auth_routes.py`
but was used **zero** times anywhere in the API. Nothing stopped a plain
`"admin"` account from `PATCH`-ing their own user record to
`role: "owner"`, or creating a brand-new account with `role: "owner"`
directly, or stripping the real owner's role out from under them.

Verified live against a real running server *before* writing any fix,
not assumed: bootstrapped a real owner, had them create an ordinary
`"admin"` user, logged in as that admin, and successfully
self-promoted to `"owner"` via `PATCH /api/admin/users/{id}` - `200 OK`,
role actually changed in the database. A real, exploitable bug, not a
theoretical one.

Fixed with `_forbid_owner_grant_by_non_owner()`, called from both
`create_user` and `edit_user`: granting the `"owner"` role to anyone
(including yourself) now requires the acting user to already be an
`owner` themselves - a plain admin gets a `403`. Also closed the reverse
case in `edit_user`: a plain admin can no longer change an *existing*
owner's role to anything else either (stripping ownership without
consent is the same trust violation as granting it uninvited) - "full,
permanent control" (the bootstrap flow's own description of what Owner
means) shouldn't be revocable by a lesser role. Deliberately still
allows a real owner to grant ownership to a successor - `"owner"` isn't
made completely immutable, just no longer grantable/revokable by
anyone else.

Verified both directions live after the fix, over real HTTP against a
real running server: a plain admin's self-promotion attempt now returns
`403` and the database role is unchanged; the same admin's attempt to
demote the real owner also returns `403` with the owner's role
unchanged; creating a new account with `role: "owner"` directly also
returns `403`; the real owner successfully granting ownership to the
admin still returns `200` and actually changes the role (proving the
fix isn't just blanket-blocking the field); and an ordinary non-owner
role edit (`user` -> `admin`) by the plain admin still works
unaffected. Added `tests/test_admin_privilege_escalation.py` (5 tests,
new file - `api/admin.py` had zero test coverage before this) covering
all five of those cases permanently, calling the real route functions
directly. Full regression suite re-run clean after this change.

Same audit also found `VALID_TIERS` allows `access_tier: "owner"` - a
separate field from `role`, reserved for a future Stripe integration
per `core/database.py`'s own comment - with the identical
no-restriction shape, across three separate call sites
(`create_user`, `edit_user`, and a third dedicated `POST
/users/{id}/set-tier` endpoint the role audit hadn't touched). Not
currently read by any real enforcement path (confirmed: `role` is what
actually gates `require_admin`/`require_owner`, `access_tier` isn't
checked anywhere for authorization today), so not a live exploit the
way the role bug was - but it's the exact same unrestricted-'owner'-
value shape, cheap to close with the identical pattern
(`_forbid_owner_tier_by_non_owner`), and worth closing now rather than
risk a future billing integration inheriting the same mistake once this
field actually starts being trusted. 5 more tests added to the same
file (10 total) covering all three call sites plus the legitimate-
owner-can-still-grant-it and ordinary-tier-still-works cases. Full
regression suite: 521 passed, 1 skipped, 0 failed (role fix) confirmed
separately, then re-run clean again after the tier fix.

## Mission Control stuck on "Loading..." for 5-8s on every page load (fixed)
Found by actually looking at the app, not just reading code: took real
Playwright screenshots of the main pages (visual QA hadn't been done
this session, everything else was functional/security testing) and
Mission Control's "Today's Performance"/"Risk" cards were visibly stuck
showing "Loading..." well past when they should have resolved. Confirmed
100% reproducible across 5 fresh page loads, not a flake - and confirmed
it eventually DID resolve, just after 5-8 real seconds every time,
ruling out a hard failure and pointing at something genuinely slow
rather than broken.

Root-caused with real timing instrumentation (not guessed): `api/
process_control.py`'s `find_listener_pids()` spawns an actual
`powershell.exe` process and runs a WMI query
(`Get-CimInstance Win32_Process`) to check whether the listener is
running - measured directly at **1.4-1.65 seconds per call**, purely
from Windows process-spawn + WMI overhead, nothing AXIM itself computes
slowly. `web/dashboard.html`'s `refreshGlobal()` fires `/api/status` and
`/api/pocket-option/status` concurrently in one `Promise.all` - both
call this same expensive function independently, and `Promise.all`
waits for the slowest of all 6 concurrent calls, so the whole dashboard
blocked on it every single load.

(First tried a different theory - that the SSE poller loop and the
session-recheck code added earlier this session were blocking the
asyncio event loop with un-offloaded synchronous DB calls, a real and
separately-worth-fixing issue, offloaded via `asyncio.to_thread` - but
verified live that this alone did NOT fix the reported symptom, which
is what led to actually timing `get_status()` directly and finding the
real cause. Kept the event-loop fix anyway since it's still correct and
was a real, if secondary, issue.)

Added a short-TTL cache (`CACHE_TTL_SECONDS = 3`) to
`find_listener_pids()` - a few seconds of staleness on "is the listener
running" is an acceptable trade-off for a background status display,
the same tolerance the app already extends to heartbeat staleness.
`start_listener()`/`stop_listener()` explicitly bypass the cache
(`use_cache=False`) for their own action-gating checks immediately
before actually starting/stopping, where a stale read matters more, and
both invalidate the cache afterward so the next status read is fresh.
Added a single-flight lock (double-checked locking) so two concurrent
callers hitting a cold cache at the same moment don't each independently
pay the ~1.4s cost - the second reuses the first's fresh result.

Verified live, before and after, with the exact same reproduction:
before the fix, 0/5 fresh page loads resolved within 2.5s (all 5-8s);
after, most resolve near-instantly and the worst case dropped
substantially - residual variance in this specific sandboxed
environment traced to login-request timing noise unrelated to this fix,
not a further instance of the same bug (confirmed by direct,
repeated timing of `get_status()` in isolation: 1.35s uncached, 0.00s on
every cached repeat call). Added `tests/test_process_control_cache.py`
(6 tests, new file) covering cache hit/miss/expiry, the
use-cache-false bypass, and that `start_listener()` genuinely sees fresh
data rather than trusting a stale cache. Full regression suite re-run
clean after this change.

## Second full visual QA pass and a mobile re-check found no UI bugs; a click-through of Settings found a real session-hijack gap (fixed)

Continued the gap-hunting loop after the Mission Control fix by reviewing
the remaining screenshots from that same batch (Risk Engine, Users,
Performance, Trade Center, Logs, Settings, Signal Inspector, Plan &
Billing, Trading Sessions, Strategy Lab) plus a fresh batch covering
every page not yet screenshotted this session (Mission Control, Funds,
Signal Sources, Automation Studio, Broker, Notifications, the onboarding
wizard) - all clean, zero console errors. Extended this to a 375px
mobile-viewport pass across all 18 authenticated pages (zero page-level
overflow); the Users table's apparent "clipping" in a static mobile
screenshot was confirmed live to be its designed horizontal
scroll-within-card behavior (`scrollWidth` 806px > `clientWidth` 289px,
`overflow-x: auto`), not a bug.

While clicking through Settings' actual tabs (the earlier visual pass
had only screenshotted the default General tab), found a real gap by
reading `api/auth_routes.py`'s `change_password` next to
`reset_password`: the forgot-password flow already revokes every
session on a password reset ("a reset is a credential-compromise-
recovery action... every existing session must be invalidated"), but
the self-service Settings > Security > Change Password route did not -
a stolen/attacker session survived the legitimate owner changing their
own password. Zero test coverage existed on `change_password` before
this (confirmed via search).

Fixed with `database.revoke_other_sessions(user_id, keep_raw_token)` -
deliberately excludes the session actively making the change (unlike
`revoke_all_sessions`, used elsewhere for admin-initiated "revoke access
immediately"), so the user isn't logged out of their own device by the
same action that's supposed to secure their account.
`api/auth_routes.py`'s `change_password` now extracts its own raw
session token (mirroring `get_current_user`'s Bearer-then-cookie
resolution) and calls it after a successful password change.

Verified live end-to-end with two real browser contexts (one "owner",
one "attacker" - both logged in as the same account before the fix):
before restarting the test server with the fix, the attacker's
`/api/auth/me` still returned 200 after the owner changed their
password; after, it returned 401 while the owner's own active session
stayed at 200. Added `tests/test_change_password_session_revocation.py`
(4 new tests: the database function keeps only the named session and
never touches another user's sessions; the route revokes the other
session but keeps the active one; a wrong current-password attempt
revokes nothing). Full regression suite re-run clean (536 passed, 1
skipped, 14 subtests passed) after this change.

## change_password's own brute-force lockout was missing (fixed)

Immediately after fixing the session-revocation gap above, re-read
`change_password` next to `login()` and noticed a second, related gap:
`login()` explicitly calls `database.record_failed_login(email)` on a
wrong password and checks `is_account_locked` up front (the brute-force
lockout added earlier this session); `change_password`'s own password
check - `database.verify_user_credentials(...)` on the "current
password" field - did neither. Since this endpoint only requires an
already-valid session (not the password itself) to reach, a
hijacked/stolen session or a device left logged in could brute-force the
real account password with completely unlimited attempts, bypassing the
lockout mechanism that exists specifically to prevent exactly that.

Fixed by mirroring login()'s exact pattern: check `is_account_locked`
first (429 if locked), call `record_failed_login` on a wrong current-
password guess. (`set_user_password` already clears the lockout counter
on success, so no separate `reset_failed_login` call was needed there.)

Verified live: 6 requests to `/api/auth/change-password` with a wrong
`current_password` against a real running server - the first 5 each
returned 401, the 6th returned 429 with a `locked until` timestamp,
exactly matching `/login`'s own lockout behavior. Added 3 more tests to
`tests/test_change_password_session_revocation.py` (wrong attempts count
toward lockout; a locked account rejects even the correct password; a
successful change clears the counter) - 7 tests total in that file now.
Full regression suite re-run clean (539 passed, 1 skipped, 14 subtests
passed) after this change.

## bootstrap_owner() had a real, easily-reproducible owner-creation race (fixed)

Kept auditing every `verify_user_credentials` call site (confirmed there
are only two - `login()` and `change_password()`, both now correctly
lockout-protected) and every other auth-adjacent endpoint for the same
"looks-safe-until-you-check-the-transaction-boundary" pattern that
produced the two fixes above. `bootstrap_owner()`'s
`count_users() > 0` guard and the `create_user()` call that follows it
were two separate, non-atomic database connections, not one operation -
two concurrent first-run requests (e.g. two devices on the Tailscale
network both reaching AXIM for the first time at the same moment) could
each pass the "no owner yet" check before either had inserted.

Proved this wasn't theoretical before treating it as a bug: 10 threads
racing `bootstrap_owner()` against a fresh in-memory-equivalent test
database, with the guard temporarily reduced to its pre-fix (unlocked)
form, produced **10 successful owner creations out of 10** on the very
first trial - not an occasional double, a complete failure of the
"exactly one owner" invariant under real concurrency.

Fixed with a plain `threading.Lock()` (`api/auth_routes.py`'s
`_bootstrap_lock`) around the check-then-create sequence - AXIM's API
always runs as a single uvicorn process (confirmed: no `--workers` flag
anywhere in `scripts/install_api_scheduled_task.ps1` or the deployment
docs), so an in-process lock is a complete fix, not a partial mitigation.
Re-ran the exact same 10-thread race with the lock restored: exactly 1
`ok`, 9 `409 an account already exists`, `count_users() == 1`. Added
`tests/test_bootstrap_owner_race.py` (2 tests: the race itself, and that
a normal single bootstrap still works unchanged).

## Duplicate concurrent trading sessions on the same broker account (fixed)

Dispatched a focused audit (Explore agent) of trading/risk-critical code
for the same check-then-act race class as the two fixes above, scoped to
core/session_manager.py, core/trade_coordinator.py, api/main.py's
control endpoints, core/risk_manager.py, and core/fund_manager.py. Found
`core/database.py`'s `start_trading_session()`: its "no other active
session on this broker account" check
(`get_active_trading_session_for_broker_account`) and the `INSERT` that
follows are two separate DB connections with no lock or transaction
spanning both - and the function's own docstring calls this check "the
real concurrency boundary," i.e. the code believed it was already safe.
Two near-simultaneous `POST /api/sessions/start` calls (an operator
double-clicking Start, or two open browser tabs) could each read "no
active session" before either inserted, leaving two sessions
independently driving trade execution against one physical Pocket
Option login - each with its own `trades_count`/`realized_pnl` limits,
doubling real risk exposure and corrupting the single-account browser
automation state.

Unlike `bootstrap_owner`'s race (10/10 threads succeeded on the first
try, because password hashing is slow enough to widen the window on its
own), this one didn't reproduce under plain unlocked threading - the
check-then-insert gap here is just two fast SQLite calls, too narrow for
the GIL to reliably interleave in a quick test. Proved the underlying
logic was unsafe anyway by forcibly widening the window (a 50ms sleep
injected right after the check, simulating realistic real-world latency
- disk I/O, WAL contention, or just normal per-request network/threadpool
scheduling): with the window forced open and no lock, all 5 concurrent
threads succeeded, creating 5 duplicate active sessions on one broker
account. With the fix's `_start_session_lock` in place, the exact same
forced-interleaving test produces exactly 1 success and 4 clean
rejections.

Fixed the same way as the other two: a plain in-process `threading.Lock`
around the check-then-insert sequence inside `start_trading_session`
itself (not the API route), so every call site is protected, not just
`api/sessions.py`. Confirmed `fund_manager.can_trade()` (the other gate
before a session starts) doesn't duplicate this check outside the lock -
it only verifies fund/broker-account status, so `start_trading_session`
remains the single authoritative enforcement point. Added a concurrency
test to `tests/test_database_sessions.py` alongside the existing
sequential "cannot start second session" test.

## A session's max_trades cap could be exceeded by concurrent confirmed trades (fixed)

The second finding from the same trading-safety audit that produced the
fix above. `core/trade_coordinator.py` checks a session's `max_trades`
cap once (`session_manager.check_session_limits`, ~line 126) - but for
a `require_confirmation` session, the very next stage
(`wait_for_trade_confirmation`, ~line 243) then blocks waiting on a
human, for up to `TRADE_CONFIRMATION_TIMEOUT_SECONDS`. Two signals
arriving close together could each pass the cap check while trades_count
was still one below the limit, both sit in the confirmation queue, both
get confirmed, and both proceed to `database.record_session_trade` -
which was an unconditional `trades_count = trades_count + 1`, with no
re-check against `max_trades`. Each confirmed trade beyond the cap also
went on to real worker-pool acquisition and (in a live, non-demo/preview
run) actual broker execution, not just a wrong counter.

Proved this decisively rather than assuming it: monkeypatched
`database.record_session_trade` back to its old unconditional-increment
form and raced 10 threads against a session capped at `max_trades=3` -
all 10 succeeded, `trades_count` ended at 10, more than 3x the
configured cap.

Fixed by making the increment itself the enforcement point: `database.
record_session_trade` is now a single atomic
`UPDATE ... SET trades_count = trades_count + 1 WHERE id = ? AND
(max_trades = 0 OR trades_count < max_trades)`, returning whether it
actually incremented. `session_manager.record_trade_started` now raises
the same `SessionLimitReached` `check_session_limits` already raises for
this exact condition if the atomic increment reports the cap was already
reached by a concurrent trade (and ends the session the same way, same
`stopped_max_trades` status/reason - safe to call from more than one
losing racer since `stop_trading_session`'s own `WHERE status = 'active'`
guard makes it idempotent). `trade_coordinator.py`'s call site now wraps
this in the same try/except-and-reject pattern already used for every
other rejection stage in the pipeline, so a trade that loses this race
is rejected before worker-pool acquisition, not after real execution.

Re-ran the exact same 10-thread race against a `max_trades=3` session
with the real fix in place: exactly 3 `ok`, 7 clean `SessionLimitReached`
rejections, `trades_count` stops precisely at 3. Added 3 new tests to
`tests/test_session_manager.py` (cap enforcement after the slot is
consumed, unlimited when `max_trades=0`, and the 10-thread concurrency
proof).

## Automation Studio rules could double-fire on a race (fixed)

A second focused audit (Explore agent), this time scoped to
`core/rule_engine.py`, notification creation, and broker-account/fund
attach-detach routes, for the same race class. Found:
`rule_engine.evaluate_rule()`'s edge-trigger decision
(`fired = condition_now and not rule["last_condition_state"]`) compared
against `rule["last_condition_state"]` - a value already read by
`database.list_rules()` at the *start* of `evaluate_all()`, a separate,
earlier DB connection. `evaluate_all()` runs once per Fund's own closed
trade (`core/session_manager.py`'s event_bus subscription), not globally
serialized - the module's own docstring is explicit that different Funds
can each have their own concurrently-active session. Two trades on two
different Funds closing within milliseconds of each other each trigger
their own `evaluate_all()` call, each iterating every enabled rule
app-wide, each with its own stale pre-evaluation snapshot.

Real consequence, not just a wrong counter: `_act_move_profit_to_vault`
computes `unvaulted = session["realized_pnl"] - session["vaulted_amount"]`
and calls `database.add_to_vault()` - two racing firings of the same
rule both compute the same unvaulted amount and both add it, double-
vaulting the same profit. `_act_emergency_stop`/`_act_notify_owner` would
equally double-fire (redundant `end_session` calls, duplicate
notifications).

Proved it before fixing: monkeypatched `database.record_rule_evaluation`
back to its old shape (read `last_condition_state`, decide `fired`, then
write) and raced 10 threads evaluating the same rule on the same
false->true edge - 2 of 10 fired (should be at most 1), `trigger_count`
ended at 2, the action's log line ("stopped active session 1") appeared
twice.

Fixed by making the edge-trigger claim itself atomic:
`database.record_rule_evaluation` is now a single conditional `UPDATE
rules SET last_condition_state = 1, trigger_count = trigger_count + 1,
... WHERE id = ? AND last_condition_state = 0` when the condition is
true (an unconditional reset to 0 when it's false, since nothing depends
on winning a race for that write) - returning whether THIS call's UPDATE
actually flipped the state (rowcount > 0), which is what
`evaluate_rule` now uses to decide whether to fire, instead of comparing
against the caller's possibly-stale `rule["last_condition_state"]`.
Re-ran the identical 10-thread race with the fix: exactly 1 fires,
`trigger_count` stays at 1. The existing sequential test
(`test_fires_once_then_not_again_while_condition_stays_true`, which
re-fetches the rule fresh between calls rather than racing) still
passes unchanged, confirming the fix didn't alter normal single-threaded
behavior. Added `test_concurrent_evaluations_only_fire_once_on_the_same_edge`
to `tests/test_rule_engine.py`.

The same audit checked `api/funds_routes.py`'s broker-account
attach/detach and `api/admin.py`'s `disable_user`/`revoke_session` for
the same pattern - neither qualified (single-connection sequential
writes with no Python-level check-then-branch, and/or admin-gated
idempotent mutations), so no further changes there.

## Double-clicking "Connect" on a broker account could spawn two login processes (fixed)

A third audit into fresh territory (Signal Sources/channel management,
broker-account connect/disconnect, trial expiration, signal recording)
found `api/broker_accounts_routes.py`'s `POST /{account_id}/connect`:
read `connection_status != "connecting"` (a SELECT), then - in a
separate, later DB connection - spawned `scripts/connect_broker_account.py`
via `subprocess.Popen` and only afterward wrote `connection_status =
"connecting"`. Two near-simultaneous `/connect` calls for the same
account (a double-click on "Connect," or a frontend retry after a slow
response - ordinary operator UI use, not an attack) could both pass the
check and both spawn a login process against the *same* Chrome profile
directory (`scripts/connect_broker_account.py`'s `user_data_dir`) at
once - Playwright/Chrome profile locking doesn't handle two processes
sharing one profile gracefully, and both processes would independently
write back `connection_status` later, so the final state ends up being
whichever finished last, not necessarily reality.

Fixed the same way as the other five: `database.
claim_broker_account_connecting(account_id)` makes the claim itself a
single atomic conditional `UPDATE ... WHERE connection_status !=
'connecting'`, returning whether this call won and may spawn the
subprocess - replacing the separate read-then-Popen-then-write sequence.
Added `tests/test_broker_account_connect_race.py` (4 tests, including a
10-thread concurrency proof: exactly 1 claim succeeds, 9 fail cleanly,
final state is "connecting" not something inconsistent).

The same audit's second, lower-severity finding (a broker account could
theoretically be archived while still assigned to a fund, if two admins
race an assign and an archive within milliseconds) was left as-is:
`fund_manager.can_trade` gates on `connection_status`, not `status`, so
this can't itself enable an unintended live trade, and the scenario
requires two admins deliberately acting in opposite directions on the
same account at the same instant - a real but low-value edge case
relative to the fix above, not pursued given diminishing returns. Also
cleared by this audit: `assign_broker_account_to_fund` (its demote-then-
insert runs on one un-committed connection, so SQLite's own writer lock
already serializes it - not a real race), `telegram_channels.
upsert_channel` (a real atomic `INSERT ... ON CONFLICT DO UPDATE`), and
`check_and_expire_trial` (check-then-act in shape, but the act is
idempotent with no side-channel, so a double-fire is harmless).

## Manually disconnecting a broker account mid-login could be silently undone (fixed)

Shifted technique after three consecutive audits found progressively
fewer issues (3, 1, 1 fixable findings) - a sign this specific bug class
was thinning out. Went broader instead: reviewed the desktop client's
Rust concurrency handling (clean - the one place it needed a lock, it
already used one correctly), event-loop-blocking architecture (clean -
FastAPI's sync routes are already threadpool-isolated by design),
SQL-injection exposure across every f-string-built query (clean - every
generic field-updater validates keys against a hardcoded allowlist
first; the two that don't - `save_backtest_metrics`,
`finalize_broker_account_connection` below - only ever receive
internally-computed dicts, never request bodies), debug-mode stack-
trace leakage (clean - no `debug=True`), and the SSE resync/gap-recovery
signal end-to-end (clean - every subscribing page implements `onResync`,
not just `onEvent`).

That last check into `scripts/connect_broker_account.py` (the login-flow
script spawned by the connect race fix above) surfaced a real, different
kind of bug: `disconnect_broker_account` only ever updates the DB row -
it never tracks or kills the connect subprocess, so an operator clicking
"Disconnect" while a login attempt is still running leaves that script
(and its browser window) running unaffected in the background. When the
script later finishes - detects a successful login, or times out - it
wrote its outcome (`connection_status = "connected"` or `"error"`)
unconditionally, silently overwriting the operator's own explicit
disconnect. Proved it directly: disconnect the account, then simulate
the script's old unconditional final write - result was "connected"
even though the operator had just disconnected it.

Fixed with `database.finalize_broker_account_connection(account_id,
connection_status, **extra_fields)` - the same atomic-conditional-write
shape as `claim_broker_account_connecting`, just guarding the other end
of the same state machine: it only writes if the account is still in
the exact `"connecting"` state the script itself started (`WHERE
connection_status = 'connecting'`), returning whether the write
happened. The script now logs and discards its result instead of
overwriting when it didn't. 4 new tests added to
`tests/test_broker_account_connect_race.py`.

## soak_snapshot.py silently stopped detecting errors after log rotation (fixed)

Checked for the same "fire-and-forget subprocess, no cancellation path"
pattern that produced the fix above elsewhere in the codebase - found
`api/broker_accounts_routes.py`'s connect flow was the only
`subprocess.Popen` call in the entire API, and the only other standalone
script in `scripts/` (besides the now-fixed connect script) is
`soak_snapshot.py` - the tool used for the one still-open item on this
roadmap (a genuine multi-hour soak test). Read it end to end since it's
directly relevant to that outstanding work, and found a real bug in its
own error-tracking logic: `count_new_error_lines()` compares the current
`logs/axim.log` line count against `last_count` (saved from the
previous run) to find newly-appended lines - but `core/logger.py` rotates
`axim.log` via `RotatingFileHandler` once it hits `MAX_BYTES`, exactly
the kind of event a real multi-hour run will hit. After a rotation, the
fresh `axim.log` is far shorter than `last_count`, so
`lines[last_count:]` silently returns `[]` on every subsequent run -
reporting `new_error_lines=0` forever afterward even while real errors
are happening, defeating the entire purpose of the script during the one
scenario (a long soak test) it exists to catch.

Proved it directly: wrote 5000 lines, ran the script to establish
`last_count=5000`, then replaced the log with a short 2-line file
(simulating rotation) containing one real `ERROR` line - the old code
reported 0 new errors instead of 1.

Fixed by treating `last_count` exceeding the current line count as "the
file was rotated," not "there are no new lines" - resets to 0 so every
currently-present line counts as new. Added
`tests/test_soak_snapshot.py` (4 tests, including the exact rotation
scenario above) - zero test coverage existed on this script before this.

## The API process's Scheduled Task had the same silent-non-restart gap already found and fixed for the listener (fixed)

Reviewed every PowerShell deployment script this session hadn't touched
yet (`backup_axim_state.ps1`, `cleanup_axim_chrome.ps1`, the two
Scheduled Task installers, `uninstall_startup_tasks.ps1`) - most were
already solid, but `install_api_scheduled_task.ps1` had a real,
documented-elsewhere-but-not-here gap.

`install_scheduled_task.ps1` (the listener's installer) explicitly
documents a live-fire finding: Windows Task Scheduler's own
`RestartOnFailure` setting does NOT trigger when a process is forcibly
terminated (an OOM-kill, a native crash, `Stop-Process -Force`) - Task
Scheduler logs that exit as a "successful completion" (event ID 201,
Information level), not a failure, so the configured restart silently
never engages for exactly the crash scenarios it exists to cover. That's
exactly why the listener's Task action is `run_listener_supervised.ps1`
(an explicit outer while-loop) rather than `python.exe` directly.

`install_api_scheduled_task.ps1` - the Scheduled Task for `api/main.py`,
i.e. the entire control plane and every Remote Client's only way in -
was never updated to match. It called `python.exe -m uvicorn ...`
directly, relying solely on Task Scheduler's `RestartOnFailure`, the
exact mechanism already proven (for the listener, and Task Scheduler's
behavior is process-agnostic - there's no reason to think uvicorn would
be classified any differently) to silently fail to restart after a
forced termination. Given this session's original mandate opened with
"a permanent 24/7 AXIM Server," a control-plane process that can die and
never come back on its own is a real gap against that goal, not a
cosmetic inconsistency.

Fixed by adding `scripts/run_api_supervised.ps1` - the exact same
supervisor-loop pattern as `run_listener_supervised.ps1`, adapted to
launch uvicorn with the bind host/port resolved from `.env`
(`API_BIND_HOST`/`API_BIND_PORT`, the same keys `config/settings.py`
reads) - and updating `install_api_scheduled_task.ps1` to register this
supervisor script as the Task's action instead of `python.exe` directly,
mirroring `install_scheduled_task.ps1` exactly (including keeping
Task Scheduler's own `RestartCount`/`RestartInterval` as the documented
second, independent layer for if the supervisor script itself dies).

Verified live (this can't be pytest-covered - it's a Windows Scheduled
Task deployment script): syntax-checked both files with
`PSParser.Tokenize`, then smoke-tested the actual `Start-Process`
mechanics directly - launched uvicorn via the exact pattern the new
script uses (real venv Python, this worktree's code via
`-WorkingDirectory`, an isolated scratch port to avoid touching any real
AXIM installation) and confirmed a genuine `200 OK` from `/login` after
cold-start, then confirmed `Stop-Process -Force` cleanly terminates it
(what the supervisor loop needs to detect via `$proc.ExitCode` to log
and restart). Did not register any real Scheduled Task or touch
`C:\AXIM`'s actual installation/data - confirmed no stray processes or
listening ports were left behind after testing.

## docs/AXIM_PRODUCTION_READINESS_REPORT.md's Bottom Line was actively misleading (fixed)

Checked this report - dated 2026-07-05, from the backend-only stress-
testing phase - against current reality, since its §9 "Bottom line"
makes specific, checkable claims. Found it stale in a way that actively
misdirects rather than just being out of date: it lists the max-daily-
loss/drawdown circuit breaker as "flagged missing, not yet built"
(verified it exists: `core/risk_manager.py`'s `check_max_daily_loss()`),
and its explicit recommendation is to **hold AXIM Desktop UI development**
until that breaker and the soak test both land - but the entire
client/server Remote Client architecture this whole multi-session effort
built is the UI that recommendation says to hold on. Someone reading
this report today without cross-checking the roadmap would be told to
not do work that's not only done, but has already been through multiple
rounds of security hardening.

Added a status banner at the top (not a full rewrite - the original
report's measurements and findings remain valid as a dated historical
record of the 2026-07-05 stress test) correcting the two stale claims
and pointing to `docs/AXIM_ROADMAP.md`/`docs/AXIM_RELEASE_CHECKLIST.md`
as the current, actively-maintained sources of truth.

## docs/AXIM_LIVE_READINESS_REVIEW.md had the same stale-guidance problem, predating the report above (fixed)

Checked further back, since the readiness report above itself referenced
findings from an even earlier review. `AXIM_LIVE_READINESS_REVIEW.md`'s
own "Bottom line up front" states plainly: "no real signal from the
trusted source has ever been processed" - its single most load-bearing
claim, and the one its whole "not ready" verdict rests on. That's
resolved (the same `Go+ | Trading Bot` production run already confirmed
above). Three of its seven numbered "Critical gaps" are also resolved
(#2 drawdown breaker, #3 dead `MODE` config, #5 process supervision -
all independently re-verified, not assumed from the other doc's banner).

Added a similar status banner, but deliberately narrower than the other
one: explicitly did NOT claim items #4, #6, #7 were resolved without
checking, and #6 specifically (no automated regression suite for
`pocket_dom.py`'s actual DOM selector functions) was checked and still
looks accurate - `tests/test_browser_worker_pool.py`/`test_browser_
warmup.py` exist (testing the orchestration around DOM interaction) but
no `test_pocket_dom.py` exists (testing the DOM interaction itself).
Overclaiming "everything's fine now" would have been its own new stale-
documentation problem, the same class of issue this fix and the one
before it exist to close.

## Added the missing pocket_dom.py test coverage its own docs flagged as absent (partial - fixed what's fixable)

Immediately followed up on the gap the live readiness review banner
just flagged rather than leaving it purely documented. Most of
`execution/pocket_dom.py` genuinely can't be unit-tested without a real
browser (real Playwright `page.locator()` DOM interaction) - that part
of the gap is real and stays open. But the file also has several pure,
dependency-free functions with zero prior test coverage:
`expiry_to_seconds`/`_expiry_to_hms`, `_format_amount`,
`_asset_search_term`, `_wants_otc`, and - most safety-relevant -
`_closest_closed_item`, the exact disambiguation logic
`docs/AXIM_PRODUCTION_READINESS_REPORT.md` section 4.5 identifies as a
real, residual source of trade-outcome-matching ambiguity under
concurrency.

Added `tests/test_pocket_dom_pure_functions.py` (22 tests). Beyond
straightforward parsing/formatting cases, specifically tested
`_closest_closed_item`'s day-boundary handling: a trade expected to
close at 23:59 with a Closed-list item showing 00:01 the next day is
genuinely only 2 minutes away, not ~24 hours - a naive same-calendar-day
`.replace(hour, minute)` would get this wrong without the function's
existing +/-1-day check, and the new test verifies that check actually
works, not just that the function runs. Also covered the malformed-
time-text fallback (doesn't crash, just loses the tiebreak) and
asset/direction filtering. Full suite re-run clean.

This doesn't close the real remaining gap (no test coverage for the
actual DOM-interaction functions, which stays accepted/open per the
banner above - live-fire testing plus operator discipline, not an
automated regression net) - it closes the *fixable* part of it without
overclaiming the unfixable-without-a-browser part is now covered too.

## AXIM Core directive: audit + two safety-critical Emergency Stop / Live-mode gaps closed

Received a major new directive restructuring the project into two
coordinated products sharing one backend: AXIM Core (a private,
immediate-live-use build, now the urgent priority) and AXIM
TradeStation (the commercial build, continuing in parallel). Dispatched
three parallel audits covering the full AXIM Core requirements list
(auth/Telegram/parser/broker; Funds/Sessions/Money-Management;
Mission-Control/Trade-Center/Logs/Remote/Safety) against the current
codebase. Full findings recorded in this session's work; two were
safety-critical and fixed immediately:

**1. Mission Control's Emergency Stop button didn't actually stop
sessions.** Two Emergency Stop entry points existed:
`POST /api/sessions/{id}/emergency-stop` (session-scoped, already
correctly ended every active session) and `POST
/api/control/emergency-stop` (global) - but `web/dashboard.html`'s
Emergency Stop button, the one Mission Control's own spec requires and
the most prominent "stop everything now" control in the app, called the
global route, which only flipped `ui_control_state` flags and never
ended any session. An emergency-stopped Fund's session stayed `status =
"active"` in the DB indefinitely. Fixed by adding
`session_manager.end_all_active_sessions()` (a shared helper) and
calling it from both routes, so either Emergency Stop entry point now
produces the identical, correct end state.

**2. A signal already inside the trade pipeline could still execute
after Emergency Stop was pressed.** `core/telegram_listener.py`
correctly checks `emergency_stop` before ever calling
`trade_coordinator.handle_signal()` for a brand-new incoming message -
but nothing re-checked it once a signal was already inside the
pipeline, and none of the existing `risk_manager` checks look at
control state at all (confirmed by grep). A signal that entered before
the stop, especially one sitting in a `require_confirmation` session's
human-approval queue (which can wait a real amount of time), would
sail through every other check and reach real execution. Fixed with
`risk_manager.check_not_stopped()`, called first in the preflight
sequence (before any other check) AND re-checked immediately after the
confirmation-wait gate - the pipeline's only genuinely long, unbounded
wait. Proved the confirmation-wait race specifically: a test that
presses Emergency Stop the instant a pending confirmation appears, then
confirms it anyway, now correctly gets rejected with
`rule="emergency_stop"` and never reaches the worker pool.

**3. The Live-mode confirmation was a bare browser `confirm()`/`prompt()`
missing required disclosures.** The spec requires showing Fund, Pocket
Option account, balance, trade size, loss limit, and max Martingale
exposure before Live mode, with explicit confirmation - the existing
flow showed channel count/profit-target/loss-limit/max-trades and a
"type START LIVE" `prompt()`, missing account/balance/trade-size/
Martingale-exposure entirely. Replaced with a proper modal
(`web/sessions.html`) reusing already-built backend data
(`GET /api/sessions/pre-start-summary/{fund_id}` for
fund/account/balance, `GET /api/risk-profiles/{id}` +
`GET /api/risk-profiles/{id}/projected-exposure` for trade size and
the real Martingale ladder total) - no new backend endpoints needed,
all the data already existed. Kept the "type START LIVE" explicit-
confirmation requirement, moved into the modal instead of a bare
`prompt()`. DEMO-mode session starts are unchanged (the spec is
specifically about Live mode).

Verified live end-to-end via Playwright against a real bootstrapped
Fund/broker-account/Martingale-enabled risk profile: modal correctly
showed Fund name, account name+connection status, $1000.00 balance,
"$25.00 (fixed)" trade size, "$100" loss limit, and "$175.00" max
Martingale exposure (fixed $25 × 3 steps × 2.0x multiplier = 25+50+100 =
175, confirmed the real ladder math, not a placeholder); submitting
with an empty/wrong confirmation phrase correctly blocked with a visible
error and did not start the session; typing "START LIVE" correctly
started a real `active`/`LIVE`-mode session in the DB.

9 new regression tests added (`test_risk_manager.py`'s
`check_not_stopped`, `test_session_manager.py`'s
`end_all_active_sessions`, `test_trade_coordinator.py`'s emergency-stop-
before-worker-pool and mid-confirmation-wait-race cases). Full suite
re-run clean.

## AXIM Core: interactive Telegram bot trigger-command workflow built (the single biggest gap from the audit)

The audit's clearest finding: `core/database.py`'s schema already had
`source_type='bot_command'`, `trigger_command`, `command_wait_for_result`,
`max_requests_per_session` columns, and `web/telegram.html` already had
UI to configure most of them - but grepping the whole repo for
`send_message` found nothing except SMTP email. Zero runtime code ever
sent a trigger command to a bot or awaited its reply. Today's system was
100% passive/reactive.

Built `core/telegram_bot_trigger.py` per
`docs/AXIM_SESSION_ARCHITECTURE.md` section 5's own spec (send command
-> wait for reply -> parse -> execute -> wait for result if configured
-> request next -> stop at any session limit):

- `run_session_loop(client, session_id, channel_row, coordinator)` -
  the per-session request/response/execute cycle, using Telethon's
  `client.conversation()` (a temporary, conversation-scoped listener -
  entirely separate from `telegram_listener.py`'s main event handler,
  so it doesn't interfere with or get interfered with by passive
  channels). Re-checks `trading_sessions.status` fresh every iteration,
  so any of the session's existing stop conditions (profit target, loss
  limit, max trades, manual stop, emergency stop - all already
  transition status away from "active") end the loop on the very next
  cycle, without this module needing its own separate stop-condition
  logic. Routes every parsed reply through
  `broker_account_manager.route_signal()` - the exact same multi-
  broker-account-aware entry point passive channels use, so a bot-
  command session gets identical Fund/broker-account resolution, risk
  checks (including this session's own new `check_not_stopped()`), and
  outcome tracking as any other signal.
- `supervisor_tick(client, coordinator)` - polls active sessions every
  3s (same interval as the existing test-trade poll loop) and starts
  exactly one request loop per session that covers a Bot Command
  Channel, tracked in a `session_id -> Task` dict so a session already
  being driven never gets a second loop.

**Found and fixed a real double-processing bug while building this**:
`telegram_listener.py`'s main passive `handler()` doesn't check
`source_type` at all - it would try to passively parse and execute
EVERY message from an allowed/session-covered chat, including a bot's
reply to a trigger command this new module just sent and is actively
awaiting via its own conversation-scoped listener. Without excluding
`source_type == "bot_command"` channels from the passive handler, every
interactive reply would have been processed twice: once as the awaited
response, once again as if it were an ordinary pushed signal. Added the
exclusion; the passive handler now returns immediately for any
bot_command channel, full stop - those channels are never valid passive
sources by definition.

Also added `database.get_channel(channel_id)` (a plain single-row
getter that didn't exist - `list_channels()` was the only way to look
one up, by filtering client-side) and a missing UI control: `max_requests
_per_session` had a DB column, backend validation, and a value used by
this new loop, but zero way for an operator to actually set it -
`web/telegram.html`'s bot-fields block only had the trigger command
input and the wait-for-result checkbox. Added the missing number input,
verified live it saves through the existing (unchanged)
`PATCH /api/channels/{id}/config` endpoint.

**What could and couldn't be verified**: the actual Telegram send/
receive interaction cannot be live-tested in this environment - no real
Telegram API credentials are available here, the same class of
limitation `execution/pocket_dom.py`'s DOM-interaction functions
already have and document (live-fire tested by an operator against the
real service, not covered by an automated net that touches the real
network). Built the module so its Telegram client and coordinator are
both parameters, not imported globals, specifically to make it testable
without one - `tests/test_telegram_bot_trigger.py` (12 tests) uses a
fake client whose `conversation()` returns scripted replies/timeouts,
covering: trigger-command sent and parsed reply correctly routed
(verified via a mocked `route_signal`, asserting the exact signal dict
and `session_id` passed); stops at `max_requests_per_session`; stops
when session status is no longer active (session ended by any other
stop condition); an unparseable reply or a reply timeout logs and moves
on rather than crashing the loop; removes itself from the active-loops
registry when done; the supervisor never starts a second loop for a
session already running one; ignores sessions with no bot-command
channel. Plus 2 new `database.get_channel` tests and live UI
verification of the new max-requests-per-session field via Playwright.

Full suite re-run clean: 603 passed, 1 skipped, 14 subtests passed.
