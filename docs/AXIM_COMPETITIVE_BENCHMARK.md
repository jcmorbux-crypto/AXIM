# AXIM Competitive Benchmark

**Status:** Analysis only. No code changed to produce this document.
**Method:** AXIM's own codebase (read directly), AXIM's own historical execution
data (`data/axim.db`), and generally-known patterns in browser-automation /
trading-bot engineering. **No specific competitor source code has been
reviewed** - none has been provided yet. Where this document compares AXIM
against "common practice," that means publicly-documented Playwright/browser-
automation technique, not a specific competitor's verified implementation.
This document is ready to be sharpened with real comparisons the moment
specific public repos/docs are provided.

Scope boundary (per explicit instruction): only AXIM's own code, the user's
own authorized Telegram observation, public repos/docs the user supplies, and
AXIM's own measured latency. No reverse-engineering of Pocket Option's
proprietary client or backend.

---

## 1. What AXIM already does that matches strong common practice

| Technique | AXIM's current implementation | Status |
|---|---|---|
| Persistent authenticated session | `PocketBrowserSession` uses `launch_persistent_context` against a fixed `user_data_dir` (`sessions/pocket_browser`) - login/cookies survive restarts | **In place** |
| Warm browser (no cold launch per trade) | `BrowserWarmupService` launches once at startup and stays alive for the process lifetime; `ensure_alive()`/generation-counter reconnects on a whole-browser crash | **In place** |
| Warm, pre-opened pages (not one page reused sequentially) | `BrowserWorkerPool` maintains N pages (tabs) in the same context, each with its own `asyncio.Lock`, `MAX_CONCURRENT_WORKERS` (default 2) | **In place** |
| Pre-scanned/cached asset availability | `asset_cache.build_cache()` scans every category once at startup; `TradeCoordinator` fast-rejects a known-untradeable asset without touching the browser at all | **In place** |
| Static selector table (no runtime selector discovery) | All selectors are module-level constants in `pocket_dom.py`, verified against the live DOM once, reused everywhere | **In place** |
| No-op skip when state already matches | `select_asset`/`select_expiry`/`set_amount` each check current DOM state first and skip entirely if already correct - the actual mechanism behind the "<2s when nothing changes" path | **In place** |
| Explicit, structured verification before every click | Locator visibility/enabled/hit-testing checks before every interaction, with screenshot+HTML+URL capture on failure | **In place**, but see §3 - some of this capture work currently sits on the critical path |
| Fail-closed safety gates | Demo-mode verification, `WATCH_CHANNELS` allow-list, `MINIMUM_PAYOUT`, tradeable-now pre-check all reject rather than silently proceed on missing/ambiguous data | **In place** (a genuine differentiator - "fast" and "fails open" are usually the same failure mode in this space) |

AXIM's warm-execution architecture (Phase 4/5) already implements the core
of what's normally the *entire* pitch of a "fast" Pocket Option bot: don't
re-authenticate, don't cold-launch a browser, don't re-discover selectors,
don't re-scan assets you already know about. That baseline is solid. The gap
is in what happens *inside* the already-warm path (§3) and in whether DOM
automation is even the fastest possible signal for confirmation (§4).

---

## 2. Measured baseline (real data, small sample)

From `data/axim.db`, all trades with both `received_at` and `opened_at`
recorded (signal-received -> trade-button-clicked-and-confirmed-open):

| Metric | Value |
|---|---|
| Sample size | 6 |
| Min | 1.97s |
| Max | 3.62s |
| Average | 3.12s |
| Median | 3.46s |

**Caveat stated plainly:** n=6 is too small to be a real distribution - it's
six manual/demo test trades, not production volume. It's directionally
consistent with the Phase 4/5 roadmap notes ("~1-2s warm, ~2-5s on an asset
change") but should not be treated as a reliable percentile estimate. Part of
the Sprint plan (see companion doc) is instrumenting enough per-stage,
per-trade data that this baseline stops being six data points.

One structural note: **this number currently can't be broken down by stage**
in a queryable way. `LatencyTracker` computes per-checkpoint deltas
(`telegram_received`, `parsed`, `risk_approved`, `asset_selected`,
`expiry_set`, `amount_set`, `click_completed`, `confirmation_detected`) but
only ever writes them as one text line to `logs/lifecycle.log` - never to the
database. Those log files also rotate/truncate (confirmed - they were
regenerated during this session's logging-architecture work), so historical
per-stage detail doesn't survive. This is the top item in the Sprint plan's
recommended changes: persist the checkpoints structurally so "top bottleneck"
can be answered from real data across every trade, not just log-tailing one
run.

---

## 3. Where AXIM likely lags common practice

These are structural findings from reading the current code, not measured
regressions (no per-stage historical data exists yet - see above) - each is
flagged with how confident the finding is.

1. **RESOLVED.** `core/trade_coordinator.py`'s `handle_signal` no longer runs
   any blocking `sqlite3` call directly on the event loop thread. The
   Validation/Risk Manager/Session limits/Duplicate Detection sequence
   (previously 5+ inline blocking round trips) was extracted verbatim into
   `_run_preflight_checks` and now runs via one `await asyncio.to_thread(...)`
   call - same logic/ordering/short-circuit behavior, just off the loop
   thread. Every other scattered `database.*`/`timeline.persist`/
   `session_manager.record_trade_started` call in the method is individually
   wrapped the same way. Proven, not just asserted:
   `tests/test_trade_coordinator.py`'s `EventLoopNotBlockedDuringRiskChecksTests`
   runs a lightweight ticker coroutine concurrently with an artificially
   slowed risk check and asserts the ticker kept getting scheduled during the
   delay - verified this test genuinely fails (3 ticks instead of 5+) when
   temporarily reverted to a direct (non-threaded) call, confirming it's a
   real regression guard, not a tautology. Original finding, for reference:
   every risk check (`check_max_trades_per_hour`, `check_max_consecutive_losses`,
   `check_cooldown_after_loss`, `check_duplicate_signal`) plus
   `record_signal_received`/`update_trade_status` opened a brand-new
   `sqlite3.connect()` and ran a blocking query, all directly inside `async
   def` functions with no `asyncio.to_thread` offload, sequentially (5+
   separate connect/query/close round trips per signal), **blocking the
   single event loop thread** - under real
   concurrency (multiple workers reacting to simultaneous signals) this
   serializes work that should be independent. *Confidence: high (read
   directly from `database.py`/`risk_manager.py`; impact estimate is
   structural, not yet measured in isolation).*

2. **RESOLVED** (see `docs/AXIM_ROADMAP.md`'s P0 latency sprint entry) -
   screenshot capture moved off the trade critical path and now genuinely
   respects `SAVE_SCREENSHOTS` (`config/settings.py:53` reads it via
   `os.getenv` like every neighboring setting; `execution/pocket_executor.py`
   checks it before capturing). This finding predated that fix.

3. **`select_expiry` makes 3 sequential Playwright round trips** (hours/
   minutes/seconds inputs, each `fill()` + `expect().to_have_value()`) where
   the underlying DOM operation could plausibly be done as one `page.
   evaluate()` setting all three native input values and dispatching input
   events once. Each round trip is IPC + a real browser paint/validation
   cycle, not free. *Confidence: medium - plausible optimization, not yet
   tested for whether Pocket Option's own input handlers tolerate a
   synthetic multi-field batch write instead of sequential user-like input.*

4. **INVESTIGATED - real signal confirmed, production integration deferred
   pending a scope decision.** Captured real WebSocket traffic two ways:
   (a) passive observation of the already-authenticated demo cabinet with no
   trade placed, and (b) the same capture during one real $1 demo trade
   (`EUR/USD OTC`, `BUY`, 15s expiry, `ARMED` forced to `"true"` for that one
   isolated process only, `.env` never touched - the same established
   pattern as `tests/manual_click_test_warm.py`).

   **Finding:** the trading socket (`wss://demo-api-eu.po.market/socket.io/`)
   is Socket.IO (a public, well-documented open-source protocol, not
   proprietary to Pocket Option) and sends named JSON event-type frames
   alongside a continuous binary price-tick stream. Two of those event names
   fired at exactly the moments that matter:
   - `successopenOrder` fired within the same ~100ms window
     `pocket_executor` itself logged `status=trade_opened` (real timestamps
     captured: order clicked ~21.7s into the run, `successopenOrder` at
     21.70s).
   - `successcloseOrder` fired at 36.92s - the trade's own 15-second expiry
     from the ~21.7s open point lands almost exactly there. `successupdateBalance`
     followed immediately after both events (stake deducted on open, payout
     credited on close - consistent with the real win result AXIM's own DOM
     read independently confirmed: `$1 stake -> $1.92 returned`).

   **Scope boundary held deliberately**: the actual event *payload* (asset,
   direction, stake, result) is Socket.IO's binary-attachment convention
   (`{"_placeholder":true,"num":0}` + a follow-up raw binary frame) - decoding
   that binary content would mean reverse-engineering a Pocket-Option-specific
   format, which is out of scope per this project's own established boundary.
   Only the **event type name** (public Socket.IO framing, not proprietary
   content) and **timing** were used here - matching "the same authorized
   browser session simply being asked what it already received," not
   inspecting anything beyond that.

   **What this would enable, if built**: an *additive, fallback-safe*
   optimization - watch for the `successcloseOrder` event name as an early
   wake-up trigger to check the DOM sooner, while keeping the existing,
   proven DOM read (`wait_for_trade_result`) as the sole source of truth for
   the actual result. If the WS event never fires (site change, wrong tab,
   anything), the existing fixed-wait-then-poll path is untouched and still
   works exactly as today - this could only ever make outcome detection
   faster or unchanged, never less reliable, if implemented with that
   fallback discipline.

   **Not built in this pass.** The realistic upside is now more modest than
   when this item was first written: `settlement_buffer_seconds` tuning (see
   `docs/AXIM_ROADMAP.md`) already cut outcome-detection overhead from ~8.4s
   to a ~2.4s average, so a WS-triggered early check would be shaving time off
   an already-optimized number, not off the original 8s. Properly integrating
   this into the live confirmation path (execution/pocket_dom.py's
   `wait_for_trade_result`) touches the trade-outcome-recording pipeline
   directly and would need its own extensive live-fire verification (multiple
   real trades, the no-WS-event fallback path, a race where the WS event
   fires before the DOM has actually updated) before it should be trusted -
   a genuinely separate, larger piece of work from the investigation itself,
   not something to build without an explicit go-ahead given what it touches.

5. **RESOLVED** - `execution/browser_worker_pool.py` now has exactly the TTL
   cache this item recommended: `HEALTH_CHECK_TTL_SECONDS` (default 2s,
   `.env`-overridable) skips the redundant `ensure_alive()`/`page.evaluate`
   round trip on `acquire_worker()` if the pool/worker was already confirmed
   healthy within that window - a genuinely stale page is still always
   caught, just not re-verified on every single call. This finding predated
   that fix.

None of the above required touching Pocket Option's private code or backend -
they're all read from AXIM's own source or are standard Playwright/CDP
capabilities against the user's own session.

---

## 4. Where public repos would sharpen this

This document currently compares AXIM against generally-known patterns, not
a specific competitor. If/when the user provides specific public
repositories or documentation, the highest-value additions would be:
- Confirming whether other implementations actually do WebSocket/network
  interception for confirmation (vs. also being DOM-based - possible AXIM
  is already at parity here and the DOM approach is simply the common
  ceiling for this platform).
- Any documented Pocket Option-specific quirks (rate limits, anti-automation
  detection, session/tab-sharing behavior) that would explain or contradict
  the cross-tab state-sharing risk flagged in the companion Sprint doc.
- Concrete published latency figures to compare against, rather than only
  AXIM's own n=6 baseline.

---

*No files were modified to produce this report. See
`docs/AXIM_LATENCY_SPRINT.md` for the prioritized action plan awaiting
approval.*
